"""Runnable demo server.  uvicorn trust_mw.demo_app:app --port 8000

PAYPAL_MODE=fake (default): hosted review mode, per-session workspaces, simulated payments.
PAYPAL_MODE=sandbox: local single-user mode with the real PayPal Sandbox adapter (.env + .vault_token.json).

On startup, three demo purchase intents are seeded automatically:
  1. $180 CPT→JNB economy   → ALLOW → CAPTURED
  2. $320 CPT→JNB flexible  → APPROVAL_REQUIRED → HELD_FOR_APPROVAL (live pending)
  3. $3   activation fee     → BLOCK
"""
import os
import secrets

from .api import create_app
from .demo_data import DictPages, make_policy
from .registry import demo_registry
from .service import AuthStore, TrustService
from .workspaces import seed_demo_intents


def build():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    mode = os.getenv("PAYPAL_MODE", "fake")
    password = os.getenv("DEMO_PASSWORD") or secrets.token_urlsafe(8)
    admin_key = os.getenv("ADMIN_KEY") or None
    cookie_secure = os.getenv("COOKIE_SECURE") == "1"

    anthropic_client = None
    if os.getenv("ANTHROPIC_API_KEY"):
        try:
            from anthropic import Anthropic
            anthropic_client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        except ImportError:
            print("[assistant] Install requirements to enable the Anthropic assistant.")
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5-5")

    def make_runner(svc, agent_key):
        if anthropic_client is None:
            return None
        from .assistant import AssistantRunner
        return AssistantRunner(anthropic_client, svc, agent_key, model)

    if mode != "sandbox":
        # Hosted / review mode: every login gets its own isolated workspace with fake payments.
        # This path never loads PayPal credentials.
        from .workspaces import DemoWorkspaces, make_review_workspace_factory
        workspaces = DemoWorkspaces(make_review_workspace_factory(
            make_runner if anthropic_client else None))
        app = create_app(
            None,
            {"demo": ("user_1", password)},
            csrf_secret=secrets.token_urlsafe(32),
            admin_key=admin_key,
            cookie_secure=cookie_secure,
            paypal_mode="fake",
            workspaces=workspaces,
        )
        print(f"\n{'='*56}")
        print("  TrustGate REVIEW DEMO (per-session workspaces)")
        print("  Payments : SIMULATED (fake adapter, no PayPal calls)")
        print(f"  Assistant: {'Anthropic' if anthropic_client else 'scripted demo'}")
        print(f"  Login    : demo / {password}  (each login = fresh workspace)")
        print("  URL      : http://localhost:8000")
        print(f"{'='*56}\n")
        return app

    # Local PayPal Sandbox integration: one shared service backed by the real adapter.
    from .paypal_adapter import PayPalAdapter
    payments = PayPalAdapter.from_env()
    policy = make_policy()
    auth = AuthStore()
    agent_key = os.getenv("AGENT_API_KEY") or "agent_" + secrets.token_urlsafe(16)
    auth.register_agent(agent_key, policy.user_id, policy.policy_id)
    svc = TrustService(demo_registry(), {policy.policy_id: policy}, auth, payments, DictPages())
    assistant_runner = make_runner(svc, agent_key)
    seed_demo_intents(svc, agent_key)

    app = create_app(
        svc,
        {"demo": (policy.user_id, password)},
        csrf_secret=secrets.token_urlsafe(32),
        admin_key=admin_key,
        cookie_secure=cookie_secure,
        paypal_mode="sandbox",
        demo_agent_key=agent_key,
        assistant_runner=assistant_runner,
    )
    print(f"\n{'='*56}")
    print("  TrustGate (local) - PayPal Sandbox")
    print(f"  Assistant: {'Anthropic' if assistant_runner else 'scripted demo'}")
    print(f"  Login    : demo / {password}")
    print(f"  API key  : {agent_key}")
    if admin_key:
        print(f"  Admin key: {admin_key}")
    print("  URL      : http://localhost:8000")
    print(f"{'='*56}\n")
    return app


app = build()
