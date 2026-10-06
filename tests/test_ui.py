import re

from tests.test_api import AGENT, approve, login, make, propose


def decision_checks(env, iid):
    return [e for e in env.svc.get_audit(iid) if e["event"] == "DECISION"][0]["data"]["checks"]


def test_landing_page_is_public_self_contained_and_motion_aware():
    _, c = make()
    page = c.get("/")
    assert page.status_code == 200 and "Only policy may authorize" in page.text
    assert "prefers-reduced-motion" in page.text
    assert "<script src" not in page.text and 'href="http' not in page.text and "{{" not in page.text


def test_landing_stats_come_from_the_running_server():
    _, c = make()
    propose(c, "cpt-jnb-economy-180", rid="a")
    propose(c, "cpt-jnb-flex-320", rid="b")
    propose(c, "activation-fee-3", merchant="merchant_activation_services", rid="c")
    page = c.get("/").text
    for label in ("payments captured", "awaiting a human", "blocked before PayPal"):
        assert re.search(rf"<b>1</b><span>{label}</span>", page)


def test_policy_checks_are_the_real_evaluation_results():
    env, c = make()
    attack = propose(c, "activation-fee-3", merchant="merchant_activation_services", rid="x").json()
    checks = dict(map(tuple, decision_checks(env, attack["intent_id"])))
    assert checks["Merchant is on the allowlist"] == "fail" and checks["Category is allowed"] == "fail"
    assert checks["Within the per-purchase limit"] == "pass"
    held = propose(c, "cpt-jnb-flex-320", rid="y").json()
    assert dict(map(tuple, decision_checks(env, held["intent_id"])))["Below the automatic-approval threshold"] == "review"


def test_blocked_page_shows_paypal_was_not_reached():
    _, c = make()
    iid = propose(c, "activation-fee-3", merchant="merchant_activation_services", rid="x").json()["intent_id"]
    login(c)
    page = c.get(f"/approvals/{iid}").text
    assert "Not reached" in page and "Blocked" in page and 'class="ic">&#10005;' in page
    assert "Order captured" not in page


def test_held_page_shows_amber_hold_not_a_payment():
    _, c = make()
    iid = propose(c, "cpt-jnb-flex-320").json()["intent_id"]
    login(c)
    page = c.get(f"/approvals/{iid}").text
    assert "Held for approval" in page and "Not reached yet" in page and "Approve purchase" in page


def test_approval_binding_checks_are_shown_only_after_a_real_approval():
    env, c = make()
    iid = propose(c, "cpt-jnb-flex-320").json()["intent_id"]
    login(c)
    assert "Approval bound to these verified facts" not in c.get(f"/approvals/{iid}").text
    approve(c, iid)
    page = c.get(f"/approvals/{iid}").text
    for name in ("Merchant verified", "Product verified", "Configured payee binding verified", "Amount verified",
                 "Currency verified", "Policy version verified", "Approval window valid"):
        assert name in page
    assert "Order captured" in page
    assert len([e for e in env.svc.get_audit(iid) if e["event"] == "APPROVED"][0]["data"]["binding_checks"]) == 7


def test_allowed_purchase_page_shows_authorized_flow():
    _, c = make()
    iid = propose(c, "cpt-jnb-economy-180").json()["intent_id"]
    login(c)
    page = c.get(f"/approvals/{iid}").text
    assert "Authorized" in page and "Order captured" in page and "Policy evaluation" in page
