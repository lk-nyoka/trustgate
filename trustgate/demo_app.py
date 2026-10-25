"""Runnable demo server.  uvicorn trustgate.demo_app:app --port 8000

PAYPAL_MODE=fake (default, offline) or sandbox (needs .env and .vault_token.json).
Credentials are printed at startup unless you set AGENT_API_KEY / DEMO_PASSWORD / ADMIN_KEY.
"""
import logging
import os
import secrets

from .api import create_app
from .demo_data import DictPages, make_policy
from .fake_paypal import FakePayPal
from .llm import llm_from_env
from .playground import Playground
from .registry import demo_registry
from .service import AuthStore, TrustService


def build():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    mode = os.getenv("PAYPAL_MODE", "fake")
    if mode == "sandbox":
        from .paypal_adapter import PayPalAdapter
        payments = PayPalAdapter.from_env()
    else:
        payments = FakePayPal()
    policy = make_policy()
    auth = AuthStore()
    prod = os.getenv("APP_ENV") == "production"
    agent_key, password = os.getenv("AGENT_API_KEY"), os.getenv("DEMO_PASSWORD")
    if prod:  # fail closed: never run production with generated or default secrets
        missing = [k for k in ("AGENT_API_KEY", "DEMO_PASSWORD", "ADMIN_KEY", "CSRF_SECRET") if not os.getenv(k)]
        if missing:
            raise RuntimeError("APP_ENV=production requires: " + ", ".join(missing))
        if len(password) < 12 or len(agent_key) < 24:
            raise RuntimeError("DEMO_PASSWORD must be >=12 chars and AGENT_API_KEY >=24 chars")
    agent_key = agent_key or "agent_" + secrets.token_urlsafe(16)
    password = password or secrets.token_urlsafe(8)
    auth.register_agent(agent_key, policy.user_id, policy.policy_id)
    svc = TrustService(demo_registry(), {policy.policy_id: policy}, auth, payments, DictPages(),
                       audit_path=os.getenv("AUDIT_PATH") or None)
    pg = None if os.getenv("PLAYGROUND", "1") == "0" else Playground(
        svc, policy, mode, llm=llm_from_env(), max_runs_total=int(os.getenv("PLAYGROUND_MAX_RUNS")) if os.getenv("PLAYGROUND_MAX_RUNS") else None)
    secure = prod or os.getenv("COOKIE_SECURE") == "1"
    app = create_app(svc, {"demo": (policy.user_id, password)},
                     csrf_secret=os.getenv("CSRF_SECRET") or secrets.token_urlsafe(32),
                     admin_key=os.getenv("ADMIN_KEY") or None, cookie_secure=secure,
                     playground=pg, repo_url=os.getenv("REPO_URL"),
                     hardening={"api_limit": int(os.getenv("RATE_LIMIT_PER_MIN", "120")),
                                "login_limit": int(os.getenv("LOGIN_LIMIT_PER_MIN", "10")),
                                "trust_proxy": os.getenv("TRUST_PROXY") == "1" or prod, "hsts": secure})
    if prod:
        print(f"Payments: {mode} | production mode | audit file: {os.getenv('AUDIT_PATH') or 'memory only'}")
    else:
        print(f"\nPayments: {mode} | Human login: demo / {password}\nAgent API key: {agent_key}\n")
    return app


app = build()
