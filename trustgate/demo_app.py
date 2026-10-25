"""Runnable demo server.  uvicorn trustgate.demo_app:app --port 8000

PAYPAL_MODE=fake (default): hosted review mode, per-session workspaces, simulated payments.
PAYPAL_MODE=sandbox: local single-user mode with the real PayPal Sandbox adapter (.env + .vault_token.json).

On startup, three demo purchase intents are seeded automatically:
  1. $180 CPT→JNB economy   → ALLOW → CAPTURED
  2. $320 CPT→JNB flexible  → APPROVAL_REQUIRED → HELD_FOR_APPROVAL (live pending)
  3. $3   activation fee     → BLOCK
"""
import logging
import os
import secrets

from .api import create_app
from .demo_data import DictPages, make_policy
from .fake_paypal import FakePayPal
from .playground import Playground
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

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    mode = os.getenv("PAYPAL_MODE", "fake")
    prod = os.getenv("APP_ENV") == "production"
    if prod:  # fail closed: never run production with generated or default secrets
        need = ["DEMO_PASSWORD", "ADMIN_KEY", "CSRF_SECRET"] + (["AGENT_API_KEY"] if mode == "sandbox" else [])
        missing = [k for k in need if not os.getenv(k)]
        if missing:
            raise RuntimeError("APP_ENV=production requires: " + ", ".join(missing))
        if len(os.environ["DEMO_PASSWORD"]) < 12 or len(os.getenv("AGENT_API_KEY") or "x" * 24) < 24:
            raise RuntimeError("DEMO_PASSWORD must be >=12 chars and AGENT_API_KEY >=24 chars")
    password = os.getenv("DEMO_PASSWORD") or secrets.token_urlsafe(8)
    admin_key = os.getenv("ADMIN_KEY") or None
    cookie_secure = prod or os.getenv("COOKIE_SECURE") == "1"
    csrf_secret = os.getenv("CSRF_SECRET") or secrets.token_urlsafe(32)
    hardening = {"api_limit": int(os.getenv("RATE_LIMIT_PER_MIN", "120")),
                 "login_limit": int(os.getenv("LOGIN_LIMIT_PER_MIN", "10")),
                 "trust_proxy": os.getenv("TRUST_PROXY") == "1" or prod, "hsts": cookie_secure}
    repo_url = os.getenv("REPO_URL")

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

    def make_playground(payments):
        """Public /try page: its own isolated service, one user/policy/agent key per visitor."""
        if os.getenv("PLAYGROUND", "1") == "0":
            return None
        pol = make_policy()
        psvc = TrustService(demo_registry(), {pol.policy_id: pol}, AuthStore(), payments, DictPages())
        cap = os.getenv("PLAYGROUND_MAX_RUNS")
        return Playground(psvc, pol, mode, drafter=make_policy_drafter(),
                          max_runs_total=int(cap) if cap else None)

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
            csrf_secret=csrf_secret,
            admin_key=admin_key,
            cookie_secure=cookie_secure,
            paypal_mode="fake",
            workspaces=workspaces,
            policy_drafter=drafter,
            trusted_proxy_hops=int(os.getenv("TRUSTED_PROXY_HOPS", "0") or 0),
            hardening=hardening, playground=make_playground(FakePayPal()), repo_url=repo_url,
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
    svc = TrustService(demo_registry(), {policy.policy_id: policy}, auth, payments, DictPages(),
                       audit_path=os.getenv("AUDIT_PATH") or None)
    assistant_runner = make_runner(svc, agent_key)
    seed_demo_intents(svc, agent_key)

    app = create_app(
        svc,
        {"demo": (policy.user_id, password)},
        csrf_secret=csrf_secret,
        admin_key=admin_key,
        cookie_secure=cookie_secure,
        paypal_mode="sandbox",
        demo_agent_key=agent_key,
        assistant_runner=assistant_runner,
        policy_drafter=make_policy_drafter(),
        hardening=hardening, playground=make_playground(payments), repo_url=repo_url,
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
