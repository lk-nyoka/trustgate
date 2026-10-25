from fastapi.testclient import TestClient

from tests.conftest import Env
from trustgate.api import PurchaseIntentIn, create_app
from trustgate.playground import Playground

H = {"X-Try": "1"}


def make(**kw):
    env = Env()
    pg = Playground(env.svc, env.policy, **kw)
    app = create_app(env.svc, {"demo": ("user_1", "pw")}, csrf_secret="s", playground=pg)
    return env, pg, TestClient(app)


def test_pages_render_and_set_cookie():
    _, _, c = make()
    r = c.get("/try")
    assert r.status_code == 200 and "Watch what it can" in r.text and "tg_try" in r.cookies
    assert "Offline mode" in r.text
    assert "Why we built it" in c.get("/how").text


def test_api_needs_session_and_header():
    _, _, c = make()
    assert c.post("/try/api/run", json={"scenario": "allow"}, headers=H).status_code == 401
    c.get("/try")
    assert c.post("/try/api/run", json={"scenario": "allow"}).status_code == 400
    assert c.post("/try/api/run", json={"scenario": "nope"}, headers=H).status_code == 400


def test_three_scenarios_match_policy():
    _, _, c = make()
    c.get("/try")
    a = c.post("/try/api/run", json={"scenario": "allow"}, headers=H).json()["intent"]
    assert (a["decision"], a["state"]) == ("ALLOW", "CAPTURED") and a["order_id"]
    h = c.post("/try/api/run", json={"scenario": "hold"}, headers=H).json()["intent"]
    assert (h["decision"], h["state"], h["order_id"]) == ("APPROVAL_REQUIRED", "HELD_FOR_APPROVAL", None)
    assert h["seconds_left"] > 0
    x = c.post("/try/api/run", json={"scenario": "inject"}, headers=H).json()
    assert x["intent"]["decision"] == "BLOCK" and x["intent"]["order_id"] is None
    assert any("hidden" in r["text"].lower() for r in x["intent"]["reason_text"])
    assert "activation fee" in x["page"]
    o = c.post("/try/api/run", json={"scenario": "overlimit"}, headers=H).json()["intent"]
    assert o["decision"] == "BLOCK"


def test_human_decision_captures_only_after_approval():
    env, _, c = make()
    c.get("/try")
    h = c.post("/try/api/run", json={"scenario": "hold"}, headers=H).json()["intent"]
    r = c.post("/try/api/decide", json={"intent_id": h["intent_id"], "decision": "APPROVE"}, headers=H).json()["intent"]
    assert r["state"] == "CAPTURED" and r["approval_status"] == "APPROVED" and r["binding_checks"]
    d = c.post("/try/api/run", json={"scenario": "hold"}, headers=H).json()["intent"]
    r = c.post("/try/api/decide", json={"intent_id": d["intent_id"], "decision": "DECLINE"}, headers=H).json()["intent"]
    assert r["state"] == "DECLINED" and r["order_id"] is None


def test_visitors_are_isolated():
    _, _, c1 = make()
    c1.get("/try")
    h = c1.post("/try/api/run", json={"scenario": "hold"}, headers=H).json()["intent"]
    c2 = TestClient(c1.app)
    c2.get("/try")
    r = c2.post("/try/api/decide", json={"intent_id": h["intent_id"], "decision": "APPROVE"}, headers=H)
    assert r.status_code == 409
    assert c2.get(f"/try/api/audit/{h['intent_id']}", headers=H).status_code == 409
    assert c2.get("/try/api/state", headers=H).json()["intents"] == []


def test_budget_is_per_visitor():
    _, _, c1 = make()
    c1.get("/try")
    for _ in range(5):  # 5 x 180 = 900 of a 1000 budget
        assert c1.post("/try/api/run", json={"scenario": "allow"}, headers=H).json()["intent"]["state"] == "CAPTURED"
    c2 = TestClient(c1.app)
    c2.get("/try")
    assert c2.post("/try/api/run", json={"scenario": "allow"}, headers=H).json()["intent"]["state"] == "CAPTURED"


def test_cheats():
    _, _, c = make()
    c.get("/try")
    s = c.post("/try/api/cheat", json={"kind": "self_approve"}, headers=H).json()
    assert s["status"] == 403 and s["intent"]["state"] == "HELD_FOR_APPROVAL" and s["intent"]["order_id"] is None
    t = c.post("/try/api/cheat", json={"kind": "tamper"}, headers=H).json()
    assert t["status"] == 422 and {"payee_id", "amount"} <= {e["field"] for e in t["errors"]}
    r = c.post("/try/api/cheat", json={"kind": "replay"}, headers=H).json()
    assert r["new_intents_created"] == 0
    assert c.post("/try/api/cheat", json={"kind": "revoke"}, headers=H).json()["policy"] == "REVOKED"
    b = c.post("/try/api/run", json={"scenario": "allow"}, headers=H).json()["intent"]
    assert b["decision"] == "BLOCK" and b["order_id"] is None
    assert c.post("/try/api/cheat", json={"kind": "restore"}, headers=H).json()["policy"] == "ACTIVE"
    assert c.post("/try/api/run", json={"scenario": "allow"}, headers=H).json()["intent"]["state"] == "CAPTURED"
    assert c.post("/try/api/cheat", json={"kind": "bogus"}, headers=H).status_code == 400


def test_audit_endpoint_and_chain():
    _, _, c = make()
    c.get("/try")
    i = c.post("/try/api/run", json={"scenario": "inject"}, headers=H).json()["intent"]["intent_id"]
    a = c.get(f"/try/api/audit/{i}", headers=H).json()
    assert a["chain_valid"] and a["no_order"] and a["events"][0]["event"] == "INTENT_RECEIVED"


def test_caps():
    _, _, c = make(max_runs_per_visitor=2)
    c.get("/try")
    for _ in range(2):
        assert c.post("/try/api/run", json={"scenario": "inject"}, headers=H).status_code == 200
    assert c.post("/try/api/run", json={"scenario": "inject"}, headers=H).status_code == 429
    _, _, c = make(max_runs_total=1)
    c.get("/try")
    assert c.post("/try/api/run", json={"scenario": "inject"}, headers=H).status_code == 200
    assert c.post("/try/api/run", json={"scenario": "inject"}, headers=H).status_code == 429
    _, _, c = make(max_visitors=1)
    assert c.get("/try").status_code == 200
    assert TestClient(c.app).get("/try").status_code == 503


def test_idle_visitors_are_purged():
    env, pg, c = make(max_visitors=1, idle_seconds=-1)
    c.get("/try")
    before = len(env.svc.policies)
    assert TestClient(c.app).get("/try").status_code == 200  # purged to make room
    assert len(env.svc.policies) == before
