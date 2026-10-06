"""Per-session review workspaces: isolation is an enforced guarantee, not a convention."""
import pytest
from fastapi.testclient import TestClient

from trust_mw.api import create_app
from trust_mw.workspaces import DemoWorkspaces, RateLimited, make_review_workspace_factory


def make_app(**kw):
    workspaces = DemoWorkspaces(make_review_workspace_factory(), **kw)
    app = create_app(None, {"demo": ("user_1", "pw")}, csrf_secret="secret",
                     admin_key="admin-1", workspaces=workspaces)
    return app, workspaces


class Judge:
    """One reviewer = one browser = one cookie jar."""
    def __init__(self, app):
        self.c = TestClient(app, follow_redirects=False)
        r = self.c.post("/api/login", json={"username": "demo", "password": "pw"})
        assert r.status_code == 200, r.text
        self.csrf = r.json()["csrf"]
        self.me = self.c.get("/api/me").json()

    def intents(self):
        return self.c.get("/api/intents").json()

    def held(self):
        return next(i for i in self.intents() if i["state"] == "HELD_FOR_APPROVAL")

    def approve(self, intent_id):
        return self.c.post(f"/api/intents/{intent_id}/approve", json={"csrf": self.csrf})

    def revoke(self):
        return self.c.post("/api/policy/revoke", json={"csrf": self.csrf})

    def reset(self):
        r = self.c.post("/api/demo/reset", json={"csrf": self.csrf})
        if r.status_code == 200:
            self.csrf = r.json()["csrf"]
        return r

    def policy_status(self):
        return self.c.get("/api/me").json()["policy"]["status"]


@pytest.fixture
def app():
    return make_app()[0]


def test_new_login_gets_fresh_seeded_state(app):
    a = Judge(app)
    states = sorted(i["state"] for i in a.intents())
    assert states == ["BLOCKED", "CAPTURED", "HELD_FOR_APPROVAL"]
    assert a.me["policy"]["status"] == "ACTIVE" and a.me["review_workspace"] is True
    assert a.me["adapter"] == "SIMULATED" and a.me["paypal_mode"] == "fake"
    # wreck workspace A, then a brand-new login must not inherit any of it
    assert a.approve(a.held()["intent_id"]).status_code == 200
    assert a.revoke().status_code == 200
    b = Judge(app)
    assert sorted(i["state"] for i in b.intents()) == ["BLOCKED", "CAPTURED", "HELD_FOR_APPROVAL"]
    assert b.policy_status() == "ACTIVE"


def test_session_a_revokes_policy_session_b_still_active(app):
    a, b = Judge(app), Judge(app)
    assert a.revoke().status_code == 200
    assert a.policy_status() == "REVOKED"
    assert b.policy_status() == "ACTIVE"


def test_session_a_approves_320_session_b_still_pending(app):
    a, b = Judge(app), Judge(app)
    before_b = b.intents()
    held_a = a.held()
    r = a.approve(held_a["intent_id"])
    assert r.status_code == 200 and r.json()["state"] == "CAPTURED"
    assert b.held()["state"] == "HELD_FOR_APPROVAL"
    assert b.intents() == before_b


def test_session_a_reset_leaves_session_b_unchanged_and_restores_a(app):
    a, b = Judge(app), Judge(app)
    b_held = b.held()
    b.approve(b_held["intent_id"])
    before_b = b.intents()
    a.approve(a.held()["intent_id"])
    a.revoke()
    assert a.reset().status_code == 200
    assert a.policy_status() == "ACTIVE"
    assert sorted(i["state"] for i in a.intents()) == ["BLOCKED", "CAPTURED", "HELD_FOR_APPROVAL"]
    assert b.intents() == before_b  # B's captured 320 and everything else untouched


def test_reset_keeps_session_usable_and_invalidates_old_cookie(app):
    a = Judge(app)
    old_cookie = a.c.cookies.get("tm_session")
    assert a.reset().status_code == 200
    assert a.c.cookies.get("tm_session") != old_cookie
    assert a.c.get("/api/me").status_code == 200
    stale = TestClient(app)
    stale.cookies.set("tm_session", old_cookie)
    assert stale.get("/api/me").status_code == 401


def test_session_a_cannot_read_or_act_on_session_b_intents(app):
    a, b = Judge(app), Judge(app)
    b_ids = {i["intent_id"] for i in b.intents()}
    assert b_ids and not b_ids & {i["intent_id"] for i in a.intents()}
    for iid in b_ids:
        assert a.c.get(f"/api/intents/{iid}").status_code == 404
        assert a.c.get(f"/v1/intents/{iid}").status_code == 404
        assert a.c.get(f"/api/intents/{iid}/audit").status_code == 404
    b_held = b.held()["intent_id"]
    assert a.approve(b_held).status_code == 409  # UNKNOWN_INTENT in A's workspace
    assert b.held()["intent_id"] == b_held  # still pending for B


def test_agent_key_is_scoped_to_its_own_workspace(app):
    a, b = Judge(app), Judge(app)
    key_a = a.me["review_agent_key"]
    assert key_a and key_a != b.me["review_agent_key"]
    r = TestClient(app).post("/v1/purchase-intents", headers={"Authorization": f"Bearer {key_a}"},
                             json={"merchant_reference": "merchant_demo_airlines",
                                   "product_reference": "cpt-jnb-economy-180", "request_id": "x1"})
    assert r.status_code == 200
    assert len(a.intents()) == 4 and len(b.intents()) == 3
    assert TestClient(app).post("/v1/purchase-intents", headers={"Authorization": "Bearer agent_nope"},
                                json={"merchant_reference": "m", "product_reference": "p",
                                      "request_id": "x"}).status_code == 401


def test_each_workspace_has_an_independent_audit_chain(app):
    a, b = Judge(app), Judge(app)
    a_id = a.held()["intent_id"]
    a.approve(a_id)
    audit_a = a.c.get(f"/api/intents/{a_id}/audit").json()
    assert audit_a["chain_valid"] and any(e["event"] == "APPROVED" for e in audit_a["events"])
    b_id = b.held()["intent_id"]
    audit_b = b.c.get(f"/api/intents/{b_id}/audit").json()
    assert not any(e["event"] == "APPROVED" for e in audit_b["events"])


def test_reset_requires_login_and_csrf_and_post_only(app):
    a = Judge(app)
    assert TestClient(app).post("/api/demo/reset", json={"csrf": "x"}).status_code == 401
    assert a.c.post("/api/demo/reset", json={"csrf": "wrong"}).status_code == 403
    assert a.c.get("/api/demo/reset").status_code in (404, 405)
    assert a.c.get("/demo/reset").status_code in (404, 405)
    assert a.policy_status() == "ACTIVE"


def test_logout_discards_the_workspace():
    app, ws = make_app()
    a = Judge(app)
    n = len(ws)
    assert a.c.post("/api/logout", json={"csrf": a.csrf}).status_code == 200
    assert len(ws) == n - 1


def test_login_and_reset_are_rate_limited():
    app, _ = make_app(login_limit=(3, 60), reset_limit=(2, 60))
    judges = [Judge(app) for _ in range(3)]
    r = TestClient(app).post("/api/login", json={"username": "demo", "password": "pw"})
    assert r.status_code == 429
    assert judges[0].reset().status_code == 200 and judges[0].reset().status_code == 200
    assert judges[0].reset().status_code == 429


def test_idle_workspaces_expire_after_ttl():
    now = [0.0]
    ws = DemoWorkspaces(make_review_workspace_factory(), ttl_seconds=100, max_workspaces=3,
                        clock=lambda: now[0], login_limit=(1000, 60))
    for _ in range(3):
        ws.create()
    now[0] += 101
    ws.create()
    assert len(ws) == 1  # idle past the TTL; capacity-eviction rules are in test_workspace_capacity.py


def test_unauthenticated_request_sees_no_workspace(app):
    c = TestClient(app)
    assert c.get("/api/me").status_code == 401
    assert c.get("/api/intents").status_code == 401
    assert c.get("/").status_code == 200
    assert c.get("/healthz").json() == {"ok": True}


def test_server_rendered_console_shows_banner_and_reset_in_review_mode(app):
    a = Judge(app)
    page = a.c.get("/console").text
    assert "REVIEW DEMO" in page and "Simulated payments" in page
    assert "/demo/reset" in page
    held = a.held()["intent_id"]
    a.approve(held)
    detail = a.c.get(f"/intents/{held}/detail").text
    assert "SIMULATED" in detail and "Adapter" in detail
    assert "PayPal captured" not in detail
    form = a.c.post("/login", data={"username": "demo", "password": "pw"})
    assert form.status_code == 303


def test_form_reset_redirects_and_restores(app):
    a = Judge(app)
    a.approve(a.held()["intent_id"])
    r = a.c.post("/demo/reset", data={"csrf": a.csrf})
    # form route expects the cookie-bound csrf of the current session
    assert r.status_code in (303, 403)
    if r.status_code == 403:
        import re
        csrf = re.search(r'action="/demo/reset".*?name="csrf" value="([^"]+)"', a.c.get("/console").text, re.S).group(1)
        r = a.c.post("/demo/reset", data={"csrf": csrf})
    assert r.status_code == 303 and r.headers["location"] == "/console"
    assert a.held()["state"] == "HELD_FOR_APPROVAL"
