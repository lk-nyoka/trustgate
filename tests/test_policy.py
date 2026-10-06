import random
from decimal import Decimal

import pytest

from tests.conftest import Env, make_policy
from trust_mw.policy import SEVERITY, TrustedFacts, evaluate
from trust_mw.scanner import ContextFlag


def test_allow_below_auto_approve_executes_payment(env):
    r = env.propose("cpt-jnb-economy-180")
    assert (r["decision"], r["state"]) == ("ALLOW", "CAPTURED")
    assert len(env.payments.calls) == 1


def test_above_threshold_needs_approval_and_does_not_pay(env):
    r = env.propose("cpt-jnb-flex-320")
    assert r["decision"] == "APPROVAL_REQUIRED" and r["state"] == "HELD_FOR_APPROVAL"
    assert env.payments.calls == []


def test_unapproved_merchant_blocked_with_trusted_facts(env):
    r = env.propose("activation-fee-3", merchant="merchant_activation_services")
    assert r["decision"] == "BLOCK"
    assert r["reason_codes"][0] == "MERCHANT_NOT_IN_POLICY"
    assert "CATEGORY_NOT_IN_POLICY" in r["reason_codes"]
    assert r["trusted_purchase"]["payee_id"] == "paypal_sandbox_activation"
    assert env.payments.calls == []


def test_category_not_allowed():
    e = Env(make_policy(category_allowlist=frozenset({"digital_services"})))
    assert "CATEGORY_NOT_IN_POLICY" in e.propose("cpt-jnb-economy-180")["reason_codes"]


def test_currency_not_allowed(env):
    assert "CURRENCY_NOT_IN_POLICY" in env.propose("cpt-jnb-eur-100")["reason_codes"]


def test_max_single_purchase(env):
    r = env.propose("cpt-jnb-business-900")
    assert r["decision"] == "BLOCK" and "EXCEEDS_MAX_SINGLE_PURCHASE" in r["reason_codes"]


def test_cumulative_budget():
    e = Env(make_policy(max_total_spend=Decimal("400")))
    assert e.propose("cpt-jnb-economy-180")["state"] == "CAPTURED"
    assert e.propose("cpt-jnb-economy-180")["state"] == "CAPTURED"
    r = e.propose("cpt-jnb-economy-180")
    assert r["decision"] == "BLOCK" and "EXCEEDS_TOTAL_BUDGET" in r["reason_codes"]


def test_unknown_merchant_and_product_fail_closed(env):
    assert env.propose("x", merchant="merchant_nope")["reason_codes"] == ["UNKNOWN_MERCHANT"]
    assert env.propose("nope")["reason_codes"] == ["UNKNOWN_PRODUCT"]


def test_product_must_belong_to_merchant(env):
    r = env.propose("activation-fee-3", merchant="merchant_demo_airlines")
    assert r["reason_codes"] == ["PAYEE_NOT_BOUND_TO_PRODUCT"]


@pytest.mark.parametrize("qty", [0, -1, 11, 1.5, True, "2"])
def test_invalid_quantity_blocked(env, qty):
    assert env.propose("cpt-jnb-economy-180", qty=qty)["reason_codes"] == ["INVALID_QUANTITY"]


def test_draft_policy_is_inactive_until_user_confirms():
    e = Env(make_policy(status="DRAFT"))
    assert "POLICY_NOT_ACTIVE" in e.propose("cpt-jnb-economy-180")["reason_codes"]
    e.svc.confirm_policy(e.user1, "policy_trip")
    assert e.propose("cpt-jnb-economy-180")["decision"] == "ALLOW"


def test_context_policy_cannot_disable_flags():
    with pytest.raises(ValueError):
        make_policy(context_actions=(("HIDDEN_PAYMENT_INSTRUCTION", "IGNORE"),))


def test_context_can_only_tighten_property():
    """Random policies, facts and flags: the final decision is never looser than hard policy."""
    rng = random.Random(7)
    flag_names = ["HIDDEN_PAYMENT_INSTRUCTION", "PAYMENT_INSTRUCTION_IN_PAGE", "SOURCE_URL_OFF_DOMAIN"]
    for _ in range(500):
        single = Decimal(rng.randint(10, 600))
        pol = make_policy(max_single_purchase=single, auto_approve_up_to=Decimal(rng.randint(0, int(single))),
                          max_total_spend=Decimal(rng.randint(10, 2000)),
                          merchant_allowlist=frozenset(rng.sample(["m1", "m2", "m3"], rng.randint(0, 3))),
                          context_actions=tuple((f, rng.choice(["REQUIRE_APPROVAL", "BLOCK"])) for f in flag_names))
        facts = TrustedFacts(rng.choice(["m1", "m2", "m3"]), "n", "p", rng.choice(["travel", "x"]), "sku", "s",
                             Decimal(1), 1, Decimal(rng.randint(1, 700)), rng.choice(["USD", "EUR"]))
        flags = [ContextFlag("t", f, "s", "e") for f in rng.sample(flag_names, rng.randint(0, 3))]
        spent = Decimal(rng.randint(0, 1500))
        base = evaluate(pol, facts, spent, [])
        full = evaluate(pol, facts, spent, flags)
        assert SEVERITY[full.decision] >= SEVERITY[base.decision]
        if base.decision == "BLOCK":
            assert full.decision == "BLOCK"
