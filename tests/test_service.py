import pytest

from tests.conftest import Env, make_policy
from trust_mw.fake_paypal import FakePayPal
from trust_mw.service import ApprovalError, AuthError


def test_human_approval_executes_exactly_once(env):
    r = env.propose("cpt-jnb-flex-320")
    done = env.svc.approve(env.user1, r["intent_id"])
    assert done["state"] == "CAPTURED" and len(env.payments.calls) == 1
    with pytest.raises(ApprovalError):  # an approval cannot be reused
        env.svc.approve(env.user1, r["intent_id"])
    assert len(env.payments.calls) == 1


def test_agent_key_cannot_approve(env):
    r = env.propose("cpt-jnb-flex-320")
    with pytest.raises(AuthError):
        env.svc.approve("agent_key_1", r["intent_id"])
    assert env.payments.calls == []


def test_other_user_cannot_approve(env):
    r = env.propose("cpt-jnb-flex-320")
    with pytest.raises(ApprovalError, match="NOT_DELEGATING_USER"):
        env.svc.approve(env.user2, r["intent_id"])


def test_agent_cannot_confirm_or_revoke_policy(env):
    with pytest.raises(AuthError):
        env.svc.revoke_policy("agent_key_1", "policy_trip")
    with pytest.raises(AuthError):
        env.svc.confirm_policy("agent_key_1", "policy_trip")


def test_approval_expires(env):
    r = env.propose("cpt-jnb-flex-320")
    env.clock.advance(11)
    with pytest.raises(ApprovalError, match="EXPIRED"):
        env.svc.approve(env.user1, r["intent_id"])
    assert env.payments.calls == []


def test_revoked_policy_stops_pending_purchase(env):
    r = env.propose("cpt-jnb-flex-320")
    env.svc.revoke_policy(env.user1, "policy_trip")
    with pytest.raises(ApprovalError, match="POLICY_REVOKED"):
        env.svc.approve(env.user1, r["intent_id"])
    assert env.payments.calls == []
    assert "POLICY_NOT_ACTIVE" in env.propose("cpt-jnb-economy-180")["reason_codes"]


def test_retry_with_same_request_id_does_not_double_charge(env):
    a = env.propose("cpt-jnb-economy-180", rid="same")
    b = env.propose("cpt-jnb-economy-180", rid="same")
    assert a == b and len(env.payments.calls) == 1


def test_decline_blocks_payment(env):
    r = env.propose("cpt-jnb-flex-320")
    assert env.svc.decline(env.user1, r["intent_id"])["state"] == "DECLINED"
    with pytest.raises(ApprovalError):
        env.svc.approve(env.user1, r["intent_id"])


def test_payment_mismatch_is_not_marked_captured():
    e = Env(payments=FakePayPal(tamper_amount="999.00"))
    assert e.propose("cpt-jnb-economy-180")["state"] == "PAYMENT_MISMATCH"


def test_payment_adapter_failure_is_recorded():
    e = Env(payments=FakePayPal(fail=True))
    assert e.propose("cpt-jnb-economy-180")["state"] == "PAYMENT_FAILED"


def test_order_spec_is_built_from_trusted_facts(env):
    env.propose("cpt-jnb-economy-180")
    assert env.payments.calls[0] == {"intent": "CAPTURE", "payee_id": "paypal_sandbox_demo_airlines",
                                     "amount": "180.00", "currency": "USD",
                                     "reference_id": env.payments.calls[0]["reference_id"]}


def test_agent_view_has_no_credentials_or_approval_tokens(env):
    r = env.propose("cpt-jnb-flex-320")
    assert set(r) == {"intent_id", "state", "decision", "approval_status", "reason_codes",
                      "trusted_purchase"}


def test_audit_log_is_complete_and_tamper_evident(env):
    r = env.propose("cpt-jnb-flex-320")
    env.svc.approve(env.user1, r["intent_id"])
    events = [e["event"] for e in env.svc.get_audit(r["intent_id"])]
    assert events == ["INTENT_RECEIVED", "FACTS_RESOLVED", "CONTEXT_SCANNED", "DECISION",
                      "HELD_FOR_APPROVAL", "APPROVED", "PAYMENT_CAPTURED"]
    assert env.svc.audit.verify()
    env.svc.audit.events[1]["data"]["amount"] = "1.00"
    assert not env.svc.audit.verify()


def test_price_change_after_hold_blocks_approval(env):
    from dataclasses import replace
    from decimal import Decimal
    r = env.propose("cpt-jnb-flex-320")
    reg = env.svc.registry
    reg._products["cpt-jnb-flex-320"] = replace(reg.product("cpt-jnb-flex-320"), unit_amount=Decimal("321.00"))
    with pytest.raises(ApprovalError, match="TRUSTED_FACTS_CHANGED"):
        env.svc.approve(env.user1, r["intent_id"])
    assert env.payments.calls == []


def test_payee_change_after_hold_blocks_approval(env):
    from dataclasses import replace
    r = env.propose("cpt-jnb-flex-320")
    reg = env.svc.registry
    reg._merchants["merchant_demo_airlines"] = replace(reg.merchant("merchant_demo_airlines"), payee_id="someone_else")
    with pytest.raises(ApprovalError, match="TRUSTED_FACTS_CHANGED"):
        env.svc.approve(env.user1, r["intent_id"])
    assert env.payments.calls == []


def test_policy_version_change_after_hold_blocks_approval(env):
    from dataclasses import replace
    r = env.propose("cpt-jnb-flex-320")
    env.svc.policies["policy_trip"] = replace(env.svc.policies["policy_trip"], version=2)
    with pytest.raises(ApprovalError, match="POLICY_REVOKED_OR_CHANGED"):
        env.svc.approve(env.user1, r["intent_id"])
    assert env.payments.calls == []


def test_approval_status_is_reported_separately_from_the_decision(env):
    allowed = env.propose("cpt-jnb-economy-180")
    held = env.propose("cpt-jnb-flex-320")
    assert (allowed["decision"], allowed["approval_status"]) == ("ALLOW", "NOT_REQUIRED")
    assert (held["decision"], held["approval_status"]) == ("APPROVAL_REQUIRED", "PENDING")
    done = env.svc.approve(env.user1, held["intent_id"])
    assert (done["decision"], done["approval_status"], done["state"]) == ("APPROVAL_REQUIRED", "APPROVED", "CAPTURED")


def test_capture_id_is_recorded_in_the_audit_log(env):
    r = env.propose("cpt-jnb-economy-180")
    captured = [e for e in env.svc.get_audit(r["intent_id"]) if e["event"] == "PAYMENT_CAPTURED"][0]
    assert captured["data"]["capture_id"]
