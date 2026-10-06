"""Kill-switch screens and honest payment labels (simulated vs PayPal Sandbox)."""
import re

import pytest
from fastapi.testclient import TestClient

from tests.conftest import Env
from tests.test_api import AGENT, approve, login, make, propose
from trust_mw.api import create_app
from trust_mw.service import ApprovalError


def csrf_in(page, action):
    return re.search(rf'action="{action}">\s*<input type="hidden" name="csrf" value="([^"]+)"', page).group(1)


def paused_console():
    env, c = make()
    iid = propose(c, "cpt-jnb-flex-320").json()["intent_id"]
    login(c)
    page = c.get("/console").text
    assert c.post("/policy/revoke", data={"csrf": csrf_in(page, "/policy/revoke")}).status_code == 303
    return env, c, iid


def test_console_after_pause_shows_paused_state_everywhere():
    env, c, iid = paused_console()
    page = c.get("/console").text
    assert "Policy paused" in page and "Agent spending is disabled" in page
    assert "POLICY PAUSED" in page and "Agent spending: DISABLED" in page
    assert "Resume spending" in page and "Pause all agent spending" not in page
    assert "Review and approve" not in page          # no approve link for a paused policy
    assert "Bounded delegation" not in page          # no "still active" posture
    assert "cannot be approved" in page


def test_active_console_offers_pause_not_resume():
    _, c = make()
    login(c)
    page = c.get("/console").text
    assert "Pause all agent spending" in page and "Resume spending" not in page
    assert "Bounded delegation" in page and "Policy paused" not in page
    assert ">v1<" in page.replace(" ", "") or "v1</span>" in page  # no stray ".1" suffix
    assert "v1.1" not in page


def test_approval_page_while_paused_disables_approve():
    env, c, iid = paused_console()
    page = c.get(f"/approvals/{iid}").text
    assert "POLICY PAUSED" in page and "can no longer be approved" in page
    assert re.search(r'<button class="btn btn-primary" disabled', page)
    assert 'name="decision" value="APPROVE"' not in page


def test_paused_server_still_refuses_approval_and_new_proposals():
    env, c, iid = paused_console()
    r = approve(c, iid)
    assert r.status_code == 409 and r.json()["detail"] == "POLICY_REVOKED_OR_CHANGED"
    assert env.payments.calls == []
    blocked = propose(c, "cpt-jnb-economy-180", rid="after-pause").json()
    assert blocked["state"] == "BLOCKED" and "POLICY_NOT_ACTIVE" in blocked["reason_codes"]


def test_resume_reactivates_with_new_version_and_old_holds_stay_dead():
    env, c, iid = paused_console()
    page = c.get("/console").text
    assert c.post("/policy/resume", data={"csrf": csrf_in(page, "/policy/resume")}).status_code == 303
    page = c.get("/console").text
    assert "Pause all agent spending" in page and "Policy paused" not in page
    assert env.svc.policies["policy_trip"].version == 2 and env.svc.policies["policy_trip"].status == "ACTIVE"
    assert approve(c, iid).status_code == 409        # pre-pause hold cannot be approved after resume
    assert "Review and approve" not in c.get("/console").text
    fresh = propose(c, "cpt-jnb-flex-320", rid="after-resume").json()
    assert fresh["state"] == "HELD_FOR_APPROVAL"
    assert approve(c, fresh["intent_id"]).status_code == 303
    assert any(e["event"] == "POLICY_RESUMED" for e in env.svc.audit.events)


def test_resume_is_human_only_csrf_protected_and_needs_a_pause():
    env, c = make()
    with pytest.raises(Exception):
        env.svc.resume_policy("agent_key_1", "policy_trip")
    login(c)
    page = c.get("/console").text
    assert c.post("/policy/resume", data={"csrf": "bad"}).status_code == 403
    token = c.cookies.get("tm_session")
    with pytest.raises(ApprovalError, match="POLICY_NOT_PAUSED"):
        env.svc.resume_policy(token, "policy_trip")
    assert TestClient(c.app).post("/policy/resume", data={"csrf": "x"}).status_code == 401
    assert TestClient(c.app).post("/api/policy/resume", json={"csrf": "x"}).status_code == 401


def test_api_pause_then_resume_round_trip():
    env, c = make()
    login(c)
    csrf = c.get("/api/me").json()["csrf"]
    assert c.post("/api/policy/revoke", json={"csrf": csrf}).status_code == 200
    assert c.get("/api/me").json()["policy"]["status"] == "REVOKED"
    assert c.post("/api/policy/resume", json={"csrf": csrf}).status_code == 200
    me = c.get("/api/me").json()["policy"]
    assert me["status"] == "ACTIVE" and me["version"] == 2


# ---------------------------------------------------------------- honest payment labels
def sandbox_client():
    env = Env()
    app = create_app(env.svc, {"alice": ("user_1", "pw1")}, csrf_secret="s", paypal_mode="sandbox",
                     demo_agent_key="agent_key_1")
    return env, TestClient(app, follow_redirects=False)


def test_simulated_mode_never_claims_a_paypal_capture():
    _, c = make()
    iid = propose(c, "cpt-jnb-economy-180").json()["intent_id"]
    login(c)
    for url in (f"/approvals/{iid}", f"/intents/{iid}/detail", "/console", "/audit"):
        page = c.get(url).text
        assert "PayPal order created and captured" not in page
        assert "Order captured" not in page
    detail = c.get(f"/intents/{iid}/detail").text
    assert "Simulated payment captured" in detail and "SIMULATED" in detail
    assert "Payment execution" in c.get("/console").text and "PayPal execution" not in c.get("/console").text


def test_sandbox_mode_labels_real_adapter_and_ids():
    env, c = sandbox_client()
    iid = env.propose("cpt-jnb-economy-180")["intent_id"]
    c.post("/login", data={"username": "alice", "password": "pw1"})
    page = c.get(f"/approvals/{iid}").text
    assert "Order captured" in page and "Simulated payment captured" not in page
    assert "PAYPAL SANDBOX" in c.get(f"/intents/{iid}/detail").text
    assert "PayPal Sandbox" in c.get("/console").text


def test_hosted_console_does_not_call_itself_local():
    _, c = make()
    login(c)
    assert "local walkthrough" not in c.get("/console").text
