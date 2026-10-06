"""Regression tests for the two release-blocking bugs:
1. held intents must reserve cumulative budget (and approval must re-check it atomically);
2. an idempotency key is bound to the canonical request payload.
"""
import threading
import time
from dataclasses import replace
from decimal import Decimal

import pytest

from tests.conftest import Env, make_policy
from tests.test_api import AGENT, make, propose
from trust_mw.service import ApprovalError, IdempotencyConflict

FLEX = "cpt-jnb-flex-320"
URL = "https://demo-airlines.test/checkout"


# ---------------------------------------------------------------- budget reservation
def test_four_320_intents_under_1000_budget_only_three_reserve_and_capture(env):
    results = [env.propose(FLEX) for _ in range(4)]
    assert [r["state"] for r in results] == ["HELD_FOR_APPROVAL"] * 3 + ["BLOCKED"]
    assert "EXCEEDS_TOTAL_BUDGET" in results[3]["reason_codes"]
    for r in results[:3]:
        env.svc.approve(env.user1, r["intent_id"])
    captured = sum(Decimal(c["amount"]) for c in env.payments.calls)
    assert captured == Decimal("960") <= Decimal("1000")


def test_reservation_counts_toward_allow_path_too(env):
    for _ in range(3):
        env.propose(FLEX)  # 960 reserved
    r = env.propose("cpt-jnb-economy-180")  # would be auto-allowed, but only 40 remain
    assert r["state"] == "BLOCKED" and "EXCEEDS_TOTAL_BUDGET" in r["reason_codes"]
    assert env.payments.calls == []


def test_declined_intent_releases_reservation(env):
    held = [env.propose(FLEX) for _ in range(3)]
    assert env.propose(FLEX)["state"] == "BLOCKED"
    env.svc.decline(env.user1, held[0]["intent_id"])
    assert env.propose(FLEX)["state"] == "HELD_FOR_APPROVAL"


def test_expired_intent_releases_reservation(env):
    for _ in range(3):
        env.propose(FLEX)
    env.clock.advance(11)
    assert env.propose(FLEX)["state"] == "HELD_FOR_APPROVAL"


def test_revoked_policy_reservations_are_unusable(env):
    r = env.propose(FLEX)
    env.svc.revoke_policy(env.user1, "policy_trip")
    assert env.svc._reserved("policy_trip") == Decimal("0")
    with pytest.raises(ApprovalError, match="POLICY_REVOKED_OR_CHANGED"):
        env.svc.approve(env.user1, r["intent_id"])
    assert env.payments.calls == []


def test_approval_rejected_when_budget_no_longer_fits(env):
    r = env.propose(FLEX)
    # Budget is tightened (or other spend lands) after the hold was created.
    env.svc.policies["policy_trip"] = replace(env.svc.policies["policy_trip"],
                                              max_total_spend=Decimal("300"))
    with pytest.raises(ApprovalError, match="CUMULATIVE_BUDGET_EXCEEDED"):
        env.svc.approve(env.user1, r["intent_id"])
    assert env.payments.calls == []
    assert env.svc.get_audit(r["intent_id"])[-1]["event"] == "APPROVAL_REFUSED_BUDGET_EXCEEDED"


def test_approval_rejected_after_other_purchase_consumes_budget(env):
    r = env.propose(FLEX)
    # Simulate another capture landing on the same policy outside this hold's knowledge.
    other = env.propose("cpt-jnb-economy-180")
    assert other["state"] == "CAPTURED"  # 180 captured + 320 reserved = 500 <= 1000: fine
    env.svc.policies["policy_trip"] = replace(env.svc.policies["policy_trip"],
                                              max_total_spend=Decimal("450"))
    with pytest.raises(ApprovalError, match="CUMULATIVE_BUDGET_EXCEEDED"):
        env.svc.approve(env.user1, r["intent_id"])


def test_simultaneous_approvals_competing_for_remaining_budget_only_one_succeeds():
    env = Env(policy=make_policy(max_total_spend=Decimal("700")))
    a, b = env.propose(FLEX), env.propose(FLEX)  # 640 reserved of 700
    # Shrink so only one of the two fits, then race the approvals.
    env.svc.policies["policy_trip"] = replace(env.svc.policies["policy_trip"],
                                              max_total_spend=Decimal("400"))
    original = env.payments.charge

    def slow(spec, idempotency_key):
        time.sleep(0.1)
        return original(spec, idempotency_key)
    env.payments.charge = slow
    outcomes, barrier = [], threading.Barrier(2)

    def go(intent_id):
        barrier.wait()
        try:
            env.svc.approve(env.user1, intent_id)
            outcomes.append("ok")
        except ApprovalError as exc:
            outcomes.append(str(exc))
    threads = [threading.Thread(target=go, args=(x["intent_id"],)) for x in (a, b)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(outcomes) == ["CUMULATIVE_BUDGET_EXCEEDED", "ok"]
    assert len(env.payments.calls) == 1


def test_concurrent_proposals_cannot_oversubscribe_budget(env):
    out, barrier = [], threading.Barrier(8)

    def go(i):
        barrier.wait()
        out.append(env.svc.propose_purchase("agent_key_1", "merchant_demo_airlines", FLEX, 1, URL, f"c{i}")["state"])
    threads = [threading.Thread(target=go, args=(i,)) for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert out.count("HELD_FOR_APPROVAL") == 3 and out.count("BLOCKED") == 5


# ---------------------------------------------------------------- idempotency binding
def test_same_request_id_identical_payload_returns_same_intent(env):
    a = env.propose(FLEX, rid="same")
    b = env.propose(FLEX, rid="same")
    assert a == b and len(env.svc._intents) == 1


@pytest.mark.parametrize("kwargs", [
    {"product": "cpt-jnb-economy-180"},                      # different product
    {"product": FLEX, "qty": 2},                             # different quantity
    {"product": FLEX, "merchant": "merchant_activation_services"},  # different merchant
    {"product": FLEX, "url": "https://demo-airlines.test/injected-fee"},  # different source context
])
def test_same_request_id_different_payload_conflicts(env, kwargs):
    env.propose(FLEX, rid="k1")
    with pytest.raises(IdempotencyConflict, match="IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD"):
        env.propose(rid="k1", **kwargs)
    assert len(env.svc._intents) == 1


def test_canonical_hash_is_order_independent_and_field_specific():
    h = Env().svc.request_hash
    assert h("m", "p", 1, "u") == h("m", "p", 1, "u")
    assert h("m", "p", 1, None) == h("m", "p", 1, "")
    assert len({h("m", "p", 1, "u"), h("m", "q", 1, "u"), h("m", "p", 2, "u"), h("n", "p", 1, "u")}) == 4


def test_concurrent_identical_requests_create_one_intent(env):
    out, barrier = [], threading.Barrier(6)

    def go():
        barrier.wait()
        out.append(env.propose(FLEX, rid="dup")["intent_id"])
    threads = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(set(out)) == 1 and len(env.svc._intents) == 1


def test_concurrent_different_payloads_one_succeeds_other_conflicts(env):
    out, barrier = [], threading.Barrier(2)

    def go(product):
        barrier.wait()
        try:
            out.append(("ok", env.propose(product, rid="race")["intent_id"]))
        except IdempotencyConflict:
            out.append(("conflict", None))
    threads = [threading.Thread(target=go, args=(p,)) for p in (FLEX, "cpt-jnb-economy-180")]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(x[0] for x in out) == ["conflict", "ok"] and len(env.svc._intents) == 1


def test_http_reused_request_id_with_different_payload_returns_409():
    env, c = make()
    assert propose(c, FLEX, rid="h1").status_code == 200
    r = propose(c, "cpt-jnb-economy-180", rid="h1")
    assert r.status_code == 409 and r.json()["detail"] == "IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD"
    assert propose(c, FLEX, rid="h1").status_code == 200  # identical retry still fine
