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
from .policy_author import GeminiPolicyDrafter, LLMDrafter
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

    model_client = None
    provider = "scripted"
    gemini_key = os.getenv("GEMINI_API_KEY")
    anthropic_key = os.getenv("ANTHROPIC_API_KEY")
    if gemini_key:
        try:
            from google import genai
            model_client = genai.Client(api_key=gemini_key)
            provider = "gemini"
        except ImportError:
            print("[assistant] Install google-genai to enable Gemini; using scripted fallback.")
    elif anthropic_key:
        try:
            from anthropic import Anthropic
            model_client = Anthropic(api_key=anthropic_key)
            provider = "anthropic"
        except ImportError:
            print("[assistant] Install anthropic to enable Claude; using scripted fallback.")
    default_model = "gemini-3.8-flash" if provider == "gemini" else "claude-sonnet-5-5"
    model = os.getenv("GEMINI_MODEL" if provider == "gemini" else "ANTHROPIC_MODEL", default_model)

    def make_runner(svc, agent_key):
        if model_client is None:
            return None
        from .assistant import AssistantRunner, GeminiAssistantRunner
        runner_type = GeminiAssistantRunner if provider == "gemini" else AssistantRunner
        return runner_type(model_client, svc, agent_key, model)

    def make_policy_drafter():
        if model_client is None:
            return None
        drafter_type = GeminiPolicyDrafter if provider == "gemini" else LLMDrafter
        return drafter_type(model_client, model)

    if mode != "sandbox":
        # Hosted / review mode: every login gets its own isolated workspace with fake payments.
        # This path never loads PayPal credentials.
        from .workspaces import DemoWorkspaces, make_review_workspace_factory
        workspaces = DemoWorkspaces(make_review_workspace_factory(
            make_runner if model_client else None))
        drafter = make_policy_drafter()
        app = create_app(
            None,
            {"demo": ("user_1", password)},
            csrf_secret=secrets.token_urlsafe(32),
            admin_key=admin_key,
            cookie_secure=cookie_secure,
            paypal_mode="fake",
            workspaces=workspaces,
            policy_drafter=drafter,
            trusted_proxy_hops=int(os.getenv("TRUSTED_PROXY_HOPS", "0") or 0),
        )
        print(f"\n{'='*56}")
        print("  TrustGate REVIEW DEMO (per-session workspaces)")
        print("  Payments : SIMULATED (fake adapter, no PayPal calls)")
        print(f"  Assistant: {'Gemini' if provider == 'gemini' else 'Claude' if provider == 'anthropic' else 'scripted demo'}")
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
        policy_drafter=make_policy_drafter(),
    )
    print(f"\n{'='*56}")
    print("  TrustGate (local) - PayPal Sandbox")
    print(f"  Assistant: {'Gemini' if provider == 'gemini' else 'Claude' if provider == 'anthropic' else 'scripted demo'}")
    print(f"  Login    : demo / {password}")
    print(f"  API key  : {agent_key}")
    if admin_key:
        print(f"  Admin key: {admin_key}")
    print("  URL      : http://localhost:8000")
    print(f"{'='*56}\n")
    return app


app = build()
