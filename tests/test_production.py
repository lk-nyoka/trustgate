import importlib
import sys

import pytest
from fastapi.testclient import TestClient

from tests.conftest import Env
from trustgate.api import create_app
from trustgate.service import AuditLog, TrustService


def client(env, **hard):
    app = create_app(env.svc, {"demo": ("user_1", "pw")}, csrf_secret="s", hardening=hard or {})
    return TestClient(app)


def test_security_headers_and_request_id(tmp_path):
    r = client(Env()).get("/healthz")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert "connect-src 'self'" in r.headers["content-security-policy"]  # the /try page fetches its own API
    assert r.headers["x-request-id"]


def test_login_is_rate_limited():
    c = client(Env(), login_limit=3)
    codes = [c.post("/login", data={"username": "demo", "password": "bad"}, follow_redirects=False).status_code
             for _ in range(5)]
    assert codes[-1] == 429 and 429 not in codes[:3]
    assert c.post("/login", data={"username": "x", "password": "y"}).headers["retry-after"]


def test_api_rate_limit_and_healthz_exempt():
    c = client(Env(), api_limit=2)
    statuses = [c.get("/v1/intents/x").status_code for _ in range(4)]
    assert statuses[-1] == 429
    assert all(c.get("/healthz").status_code == 200 for _ in range(10))


def test_forwarded_for_only_trusted_behind_proxy():
    c = client(Env(), api_limit=1, trust_proxy=False)
    c.get("/v1/intents/x", headers={"x-forwarded-for": "1.1.1.1"})
    assert c.get("/v1/intents/x", headers={"x-forwarded-for": "2.2.2.2"}).status_code == 429  # header ignored
    c = client(Env(), api_limit=1, trust_proxy=True)
    c.get("/v1/intents/x", headers={"x-forwarded-for": "1.1.1.1"})
    assert c.get("/v1/intents/x", headers={"x-forwarded-for": "2.2.2.2"}).status_code != 429


def test_readyz_reports_chain():
    assert client(Env()).get("/readyz").json() == {"ok": True, "audit_chain_valid": True}


def test_audit_survives_restart_and_detects_tampering(tmp_path):
    path = str(tmp_path / "audit.jsonl")
    env = Env()
    env.svc.audit = AuditLog(env.clock, path)
    env.propose("cpt-jnb-economy-180")
    n = len(env.svc.audit.events)
    assert n >= 3
    reloaded = AuditLog(env.clock, path)
    assert len(reloaded.events) == n and reloaded.verify()
    reloaded.append(None, "X", {})  # chain continues after reload
    assert reloaded.verify() and AuditLog(env.clock, path).verify()
    text = open(path).read().replace("economy", "ECONOMY", 1).replace("180", "999", 1)
    open(path, "w").write(text)
    with pytest.raises(RuntimeError):
        AuditLog(env.clock, path)


def test_production_refuses_missing_or_weak_secrets(monkeypatch):
    monkeypatch.setenv("APP_ENV", "production")
    for k in ("AGENT_API_KEY", "DEMO_PASSWORD", "ADMIN_KEY", "CSRF_SECRET"):
        monkeypatch.delenv(k, raising=False)
    def load():  # demo_app builds the app at import time
        sys.modules.pop("trustgate.demo_app", None)
        return importlib.import_module("trustgate.demo_app")

    with pytest.raises(RuntimeError, match="requires"):
        load()
    for k, v in dict(AGENT_API_KEY="a" * 30, DEMO_PASSWORD="short", ADMIN_KEY="k", CSRF_SECRET="c").items():
        monkeypatch.setenv(k, v)
    with pytest.raises(RuntimeError, match="12 chars"):
        load()
    monkeypatch.setenv("DEMO_PASSWORD", "a-long-enough-password")
    app = load().app  # valid secrets: starts, with secure cookies and headers on
    assert TestClient(app).get("/healthz").headers["strict-transport-security"]
    sys.modules.pop("trustgate.demo_app", None)


def test_console_links_match_state():
    env = Env()
    env.propose("cpt-jnb-flex-320")   # held
    env.propose("activation-fee-3", merchant="merchant_activation_services")  # blocked
    env.propose("cpt-jnb-economy-180")  # captured
    app = create_app(env.svc, {"demo": ("user_1", "pw")}, csrf_secret="s")
    c = TestClient(app)
    c.post("/login", data={"username": "demo", "password": "pw"})
    html = c.get("/console").text
    assert html.count("Review and approve") == 1
    assert "View security decision" in html and "View transaction" in html


def test_every_reason_code_has_plain_english():
    import re
    from trustgate.api import REASON_TEXT
    src = "".join(open(f"trustgate/{n}.py").read() for n in ("policy", "service", "scanner"))
    codes = set(re.findall(r'"((?:EXCEEDS|UNKNOWN|NOT|MERCHANT|CATEGORY|CURRENCY|POLICY|PAYEE|SOURCE|INVALID|TRUSTED|AMOUNT|WITHIN)_[A-Z_]+)"', src))
    codes -= {"NOT_APPLICABLE", "NOT_REQUIRED", "UNKNOWN_INTENT", "PAYEE_DIFFERS_FROM_PAGE", "POLICY_CONFIRMED", "POLICY_REVOKED", "POLICY_REPLACED"}  # last three: scanner flag (shown as CONTEXT_*) and event names
    assert codes - set(REASON_TEXT) == set(), codes - set(REASON_TEXT)


def test_blocked_page_is_honest_and_audit_is_readable():
    env = Env()
    blocked = env.propose("activation-fee-3", merchant="merchant_activation_services")
    c = TestClient(create_app(env.svc, {"demo": ("user_1", "pw")}, csrf_secret="s"))
    c.post("/login", data={"username": "demo", "password": "pw"})
    page = c.get(f"/approvals/{blocked['intent_id']}").text
    assert "Payment blocked before PayPal was called" in page
    assert "Approval required above" not in page and "Not created" in page
    audit = c.get(f"/intents/{blocked['intent_id']}").text
    assert "Trusted facts resolved" in audit and "adapter was never invoked" in audit
