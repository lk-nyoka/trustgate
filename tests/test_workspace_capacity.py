"""Capacity safety: active sessions are never evicted; spoofed X-Forwarded-For can't dodge limits."""
from fastapi.testclient import TestClient

from trust_mw.api import create_app
from trust_mw.workspaces import DemoBusy, DemoWorkspaces, client_ip, make_review_workspace_factory

PROTECT = 30 * 60


def mgr(now, **kw):
    kw.setdefault("login_limit", (1000, 60))
    kw.setdefault("max_workspaces", 3)
    kw.setdefault("ttl_seconds", 10 * 3600)
    return DemoWorkspaces(make_review_workspace_factory(), clock=lambda: now[0], **kw)


def resolve_holds(ws):
    """Decline every pending hold so the workspace has no live pending approval."""
    svc = ws.svc
    tok = svc.auth.issue_session(ws.user_id)
    for r in list(svc._intents.values()):
        if r["state"] == "HELD_FOR_APPROVAL":
            svc.decline(tok, r["intent_id"])


def test_fresh_workspace_has_live_hold_and_is_not_evictable():
    now = [0.0]
    m = mgr(now)
    ws = m.create()
    now[0] += PROTECT + 60  # idle past protection window, but its hold is still live (60 min expiry)
    assert m._has_live_hold(ws) and not m._evictable(ws)


def test_at_capacity_with_all_sessions_active_raises_busy_and_removes_nothing():
    now = [0.0]
    m = mgr(now)
    made = [m.create(f"c{i}") for i in range(3)]
    tokens = [m.issue_session(w) for w in made]
    now[0] += 60  # all recently active
    try:
        m.create("newcomer")
        assert False, "expected DemoBusy"
    except DemoBusy:
        pass
    assert len(m) == 3
    assert all(m.by_token(t) is w for t, w in zip(tokens, made))


def test_idle_session_without_live_hold_is_evicted_oldest_first():
    now = [0.0]
    m = mgr(now)
    a, b, c = m.create("c1"), m.create("c2"), m.create("c3")
    for w in (a, b, c):
        resolve_holds(w)
    now[0] = 10; m.touch(b); m.touch(c)
    now[0] = PROTECT + 100  # a idle 1900s; b, c idle ~1890s too -> all evictable; oldest is a
    m.touch(c)
    d = m.create("c4")
    assert m.by_agent_key(a.agent_key) is None
    assert m.by_agent_key(b.agent_key) is b and m.by_agent_key(d.agent_key) is d


def test_recently_active_session_is_protected_even_without_hold():
    now = [0.0]
    m = mgr(now)
    ws = [m.create(f"c{i}") for i in range(3)]
    for w in ws:
        resolve_holds(w)
    now[0] = PROTECT - 1
    try:
        m.create("x")
        assert False
    except DemoBusy:
        pass
    assert len(m) == 3


def test_idle_session_with_live_pending_approval_is_protected():
    now = [0.0]
    m = mgr(now)
    ws = [m.create(f"c{i}") for i in range(3)]
    for w in ws[:2]:
        resolve_holds(w)  # ws[2] keeps its live pending approval
    now[0] = PROTECT + 100
    m.create("x")  # evicts an unprotected one, never the one with a live hold
    assert m.by_agent_key(ws[2].agent_key) is ws[2]


def test_per_client_cap_blocks_only_that_client():
    now = [0.0]
    m = mgr(now, max_workspaces=50, max_per_client=2)
    m.create("greedy"); m.create("greedy")
    try:
        m.create("greedy")
        assert False
    except DemoBusy:
        pass
    assert m.create("other") is not None
    assert len(m) == 3


def test_api_busy_page_and_json_while_existing_sessions_keep_working():
    now = [0.0]
    m = mgr(now, max_workspaces=1)
    app = create_app(None, {"demo": ("user_1", "pw")}, csrf_secret="s", admin_key="a", workspaces=m)
    first = TestClient(app, follow_redirects=False)
    assert first.post("/api/login", json={"username": "demo", "password": "pw"}).status_code == 200
    second = TestClient(app, follow_redirects=False)
    form = second.post("/login", data={"username": "demo", "password": "pw"})
    assert form.status_code == 503
    assert ("Review demo is currently busy. Please try again in a few minutes. "
            "No existing review sessions were removed.") in form.text
    api = TestClient(app).post("/api/login", json={"username": "demo", "password": "pw"})
    assert api.status_code == 503 and api.headers["retry-after"] == "120"
    assert first.get("/api/me").status_code == 200  # the existing reviewer is untouched
    assert len(m) == 1


# -- forwarded-IP handling ---------------------------------------------------------------
def test_hops_zero_ignores_forwarded_header_entirely():
    assert client_ip({"x-forwarded-for": "9.9.9.9"}, "10.0.0.5", 0) == "10.0.0.5"


def test_spoofed_leftmost_entry_is_ignored_with_one_trusted_proxy():
    # attacker sent "9.9.9.9"; the trusted proxy appended the real peer it saw
    assert client_ip({"x-forwarded-for": "9.9.9.9, 1.2.3.4"}, "10.0.0.5", 1) == "1.2.3.4"


def test_multiple_proxy_addresses_parse_from_the_right():
    h = {"x-forwarded-for": "6.6.6.6, 1.2.3.4, 172.16.0.9"}
    assert client_ip(h, "10.0.0.5", 2) == "1.2.3.4"
    assert client_ip(h, "10.0.0.5", 1) == "172.16.0.9"


def test_missing_short_or_malformed_header_falls_back_to_peer():
    assert client_ip({}, "10.0.0.5", 1) == "10.0.0.5"
    assert client_ip({"x-forwarded-for": "1.2.3.4"}, "10.0.0.5", 2) == "10.0.0.5"
    assert client_ip({"x-forwarded-for": "not-an-ip"}, "10.0.0.5", 1) == "10.0.0.5"
    assert client_ip({"x-forwarded-for": ""}, None, 1) == "anon"


def test_rate_limit_key_is_stable_under_header_spoofing():
    keys = {client_ip({"x-forwarded-for": f"{n}.{n}.{n}.{n}, 1.2.3.4"}, "10.0.0.5", 1) for n in range(1, 30)}
    assert keys == {"1.2.3.4"}


def test_login_rate_limit_cannot_be_dodged_by_rotating_forwarded_header():
    m = DemoWorkspaces(make_review_workspace_factory(), login_limit=(3, 60))
    app = create_app(None, {"demo": ("user_1", "pw")}, csrf_secret="s", admin_key="a",
                     workspaces=m, trusted_proxy_hops=0)
    codes = [TestClient(app).post("/api/login", json={"username": "demo", "password": "pw"},
                                  headers={"X-Forwarded-For": f"8.8.8.{i}"}).status_code
             for i in range(5)]
    assert codes[:3] == [200] * 3 and codes[3:] == [429, 429]
