"""Runnable demo server.  uvicorn trust_mw.demo_app:app --port 8000

PAYPAL_MODE=fake (default, offline) or sandbox (needs .env and .vault_token.json).
Credentials are printed at startup unless you set AGENT_API_KEY / DEMO_PASSWORD / ADMIN_KEY.

On startup, three demo purchase intents are seeded automatically:
  1. $180 CPT→JNB economy   → ALLOW → CAPTURED
  2. $320 CPT→JNB flexible  → APPROVAL_REQUIRED → HELD_FOR_APPROVAL (live pending)
  3. $3   activation fee     → BLOCK
"""
import os
import secrets

from .api import create_app
from .demo_data import DictPages, make_policy
from .fake_paypal import FakePayPal
from .registry import demo_registry
from .service import AuthStore, TrustService


def build():
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

    agent_key = os.getenv("AGENT_API_KEY") or "agent_" + secrets.token_urlsafe(16)
    password   = os.getenv("DEMO_PASSWORD")  or secrets.token_urlsafe(8)
    admin_key  = os.getenv("ADMIN_KEY")       or None

    auth.register_agent(agent_key, policy.user_id, policy.policy_id)

    svc = TrustService(
        demo_registry(),
        {policy.policy_id: policy},
        auth,
        payments,
        DictPages(),
    )

    # ── Seed the three demo scenarios ─────────────────────────────────────
    _seed_demo_intents(svc, agent_key)

    app = create_app(
        svc,
        {"demo": (policy.user_id, password)},
        csrf_secret=secrets.token_urlsafe(32),
        admin_key=admin_key,
        cookie_secure=os.getenv("COOKIE_SECURE") == "1",
        paypal_mode=mode,
    )

    print(f"\n{'='*56}")
    print(f"  TrustMiddleware demo server")
    print(f"  Payments : {mode}")
    print(f"  Login    : demo / {password}")
    print(f"  API key  : {agent_key}")
    if admin_key:
        print(f"  Admin key: {admin_key}")
    print(f"  URL      : http://localhost:8000")
    print(f"{'='*56}\n")

    return app


def _seed_demo_intents(svc, agent_key):
    """Inject the three canonical demo scenarios into the service directly.

    We call propose_purchase via the internal agent key so every scenario
    goes through the full policy + scanner + audit pipeline.  The $320
    flexible-flight is left in HELD_FOR_APPROVAL (no approve call) so the
    console shows a live actionable request on first load.
    """
    scenarios = [
        # (merchant_ref, product_ref, source_url, request_id)
        ("merchant_demo_airlines",        "cpt-jnb-economy-180", "https://demo-airlines.test/checkout",    "seed-180"),
        ("merchant_demo_airlines",        "cpt-jnb-flex-320",    "https://demo-airlines.test/checkout",    "seed-320"),
        ("merchant_activation_services",  "activation-fee-3",    "https://demo-airlines.test/injected-fee","seed-fee"),
    ]

    for merchant_ref, product_ref, url, req_id in scenarios:
        try:
            svc.propose_purchase(
                agent_key,
                merchant_ref,
                product_ref,
                quantity=1,
                source_url=url,
                request_id=req_id,
            )
        except Exception as exc:
            # If something goes wrong seeding (e.g. sandbox key error), keep going
            print(f"[seed] warning: {req_id} → {exc}")


app = build()
