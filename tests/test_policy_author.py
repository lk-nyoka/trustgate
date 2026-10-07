"""Natural-language policy authoring: AI proposes, code validates, a human activates."""
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tests.conftest import PAGES, Env
from tests.test_api import AGENT, approve, login, make, propose
from tests.test_workspaces import Judge, make_app
from trust_mw.api import create_app
from trust_mw.policy_author import (CONTEXT_FLAGS, DraftError, GeminiPolicyDrafter, LLMDrafter,
                                    ScriptedDrafter, apply_draft, validate_draft)
from trust_mw.registry import demo_registry

TEXT = ("Let the agent book flights from approved airlines under $500. "
        "Ask me before spending more than $250. Never pay unrelated activation fees.")


class FakeModel:
    def __init__(self, tool_input=None, boom=False, text_only=False):
        self.tool_input, self.boom, self.text_only = tool_input, boom, text_only
        self.messages = SimpleNamespace(create=self.create)
        self.requests = []

    def create(self, **kw):
        self.requests.append(kw)
        if self.boom:
            raise RuntimeError("model unavailable")
        if self.text_only:
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="sure")])
        return SimpleNamespace(content=[SimpleNamespace(type="tool_use", name="submit_policy_draft",
                                                        input=self.tool_input)])


class FakeGeminiModel:
    def __init__(self, arguments):
        self.arguments = arguments
        self.requests = []
        self.interactions = SimpleNamespace(create=self.create)

    def create(self, **request):
        self.requests.append(request)
        return SimpleNamespace(steps=[SimpleNamespace(
            type="function_call", name="submit_policy_draft", arguments=self.arguments)])


def client(drafter=None, env=None):
    env = env or Env()
    app = create_app(env.svc, {"alice": ("user_1", "pw1"), "bob": ("user_2", "pw2")},
                     csrf_secret="s", demo_agent_key="agent_key_1", policy_drafter=drafter)
    c = TestClient(app, follow_redirects=False)
    login(c)
    return env, c, c.get("/api/me").json()["csrf"]


def draft(c, csrf, text=TEXT):
    return c.post("/api/policy/draft", json={"text": text, "csrf": csrf})


# ------------------------------------------------------------------------ validation
def test_validator_accepts_registry_values_and_normalises():
    out = validate_draft({"category_allowlist": ["travel"], "max_single_purchase": "500",
                          "context_actions": {"HIDDEN_PAYMENT_INSTRUCTION": "BLOCK"}}, demo_registry())
    assert out["max_single_purchase"] == Decimal("500.00") and out["category_allowlist"] == frozenset({"travel"})


@pytest.mark.parametrize("bad", [
    {}, [], "x",
    {"status": "ACTIVE"},                                   # field the model may never set
    {"version": 9}, {"policy_id": "p"}, {"user_id": "user_2"},
    {"merchant_allowlist": ["merchant_evil"]},               # not in trusted registry
    {"category_allowlist": ["gambling"]},
    {"currency_allowlist": ["BTC"]},
    {"merchant_allowlist": []},
    {"max_single_purchase": -1}, {"max_single_purchase": "abc"}, {"max_single_purchase": "NaN"},
    {"max_single_purchase": 99999}, {"max_total_spend": 10 ** 7},
    {"context_actions": {"HIDDEN_PAYMENT_INSTRUCTION": "ALLOW"}},   # context may only tighten
    {"context_actions": {"MADE_UP_FLAG": "BLOCK"}},
    {"context_actions": ["BLOCK"]},
])
def test_validator_rejects_everything_outside_the_trusted_envelope(bad):
    with pytest.raises(DraftError):
        validate_draft(bad, demo_registry())


def test_apply_draft_enforces_policy_invariants():
    env = Env()
    pol = env.policy
    with pytest.raises(DraftError, match="auto_approve_up_to cannot exceed"):
        apply_draft(pol, {"auto_approve_up_to": Decimal("600")})
    with pytest.raises(DraftError, match="max_single_purchase cannot exceed"):
        apply_draft(pol, {"max_single_purchase": Decimal("1500")})


# ------------------------------------------------------------------------ scripted drafter
def test_scripted_drafter_understands_the_demo_wording():
    raw = ScriptedDrafter().draft(TEXT, demo_registry())
    fields = validate_draft(raw, demo_registry())
    assert fields["max_single_purchase"] == Decimal("500.00") and fields["auto_approve_up_to"] == Decimal("250.00")
    assert fields["category_allowlist"] == frozenset({"travel"})
    assert fields["merchant_allowlist"] == frozenset({"merchant_demo_airlines"})
    assert dict(fields["context_actions"])["HIDDEN_PAYMENT_INSTRUCTION"] == "BLOCK"


def test_scripted_drafter_refuses_text_it_cannot_parse():
    with pytest.raises(DraftError):
        ScriptedDrafter().draft("hello there", demo_registry())


# ------------------------------------------------------------------------ draft / activate flow
def test_draft_changes_nothing_until_a_human_activates_it():
    env, c, csrf = client()
    before = env.svc.policies["policy_trip"]
    r = draft(c, csrf, "Allow flights under $400. Ask me before spending more than $100.")
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "scripted" and body["draft_id"].startswith("pd_")
    assert {ch["field"] for ch in body["changes"]} == {"max_single_purchase", "auto_approve_up_to"}
    assert env.svc.policies["policy_trip"] == before                     # untouched
    assert any(e["event"] == "POLICY_DRAFTED" for e in env.svc.audit.events)
    assert not any(e["event"] == "POLICY_ACTIVATED_FROM_DRAFT" for e in env.svc.audit.events)


def test_activation_creates_new_version_and_new_limits_govern_the_agent():
    env, c, csrf = client()
    held = propose(c, "cpt-jnb-flex-320").json()["intent_id"]
    d = draft(c, csrf, "Allow flights under $400. Ask me before spending more than $100.").json()
    r = c.post("/api/policy/activate", json={"draft_id": d["draft_id"], "csrf": csrf})
    assert r.status_code == 200 and r.json()["version"] == 2
    pol = env.svc.policies["policy_trip"]
    assert pol.auto_approve_up_to == Decimal("100.00") and pol.max_single_purchase == Decimal("400.00")
    assert approve(c, held).status_code == 409                           # pre-activation hold is dead
    econ = propose(c, "cpt-jnb-economy-180", rid="after").json()
    assert econ["state"] == "HELD_FOR_APPROVAL"                          # $180 > new $100 auto limit
    assert propose(c, "cpt-jnb-business-900", rid="big").json()["state"] == "BLOCKED"
    assert any(e["event"] == "POLICY_ACTIVATED_FROM_DRAFT" for e in env.svc.audit.events)
    assert c.post("/api/policy/activate", json={"draft_id": d["draft_id"], "csrf": csrf}).status_code == 409  # single use


def test_drafted_context_rule_turns_an_injection_into_a_hard_block():
    env, c, csrf = client()
    PAGES["https://demo-airlines.test/injected-upgrade"] = PAGES.get("https://demo-airlines.test/injected-upgrade")
    url = "https://demo-airlines.test/injected-upgrade"
    before = propose(c, "cpt-jnb-economy-180", url=url, rid="a").json()
    assert before["decision"] == "APPROVAL_REQUIRED"                     # default: friction only
    d = draft(c, csrf, "Never pay unrelated activation fees.").json()
    c.post("/api/policy/activate", json={"draft_id": d["draft_id"], "csrf": csrf})
    after = propose(c, "cpt-jnb-economy-180", url=url, rid="b").json()
    assert after["decision"] == "BLOCK" and "CONTEXT_HIDDEN_PAYMENT_INSTRUCTION" in after["reason_codes"]


def test_stale_draft_cannot_be_activated_after_the_policy_changed():
    env, c, csrf = client()
    d = draft(c, csrf).json()
    assert c.post("/api/policy/revoke", json={"csrf": csrf}).status_code == 200
    assert c.post("/api/policy/resume", json={"csrf": csrf}).status_code == 200      # version 2
    r = c.post("/api/policy/activate", json={"draft_id": d["draft_id"], "csrf": csrf})
    assert r.status_code == 409 and r.json()["detail"] == "DRAFT_STALE"


def test_only_the_owner_human_can_draft_or_activate():
    env, c, csrf = client()
    d = draft(c, csrf).json()
    assert TestClient(c.app).post("/api/policy/draft", json={"text": TEXT, "csrf": "x"}).status_code == 401
    assert TestClient(c.app).post("/api/policy/activate", json={"draft_id": d["draft_id"], "csrf": "x"}).status_code == 401
    assert c.post("/api/policy/draft", json={"text": TEXT, "csrf": "bad"}).status_code == 403
    assert c.post("/api/policy/activate", json={"draft_id": d["draft_id"], "csrf": "bad"}).status_code == 403
    assert c.post("/api/policy/draft", json={"text": TEXT, "csrf": csrf}, headers=AGENT).status_code == 403
    assert c.post("/api/policy/activate", json={"draft_id": d["draft_id"], "csrf": csrf}, headers=AGENT).status_code == 403
    bob = TestClient(c.app, follow_redirects=False)
    login(bob, "bob", "pw2")
    bcsrf = bob.get("/api/me").json()["csrf"]
    assert bob.post("/api/policy/activate", json={"draft_id": d["draft_id"], "csrf": bcsrf}).status_code == 409
    assert env.svc.policies["policy_trip"].version == 1


def test_invalid_text_lengths_rejected():
    _, c, csrf = client()
    assert draft(c, csrf, "").status_code == 422
    assert draft(c, csrf, "x" * 601).status_code == 422
    assert draft(c, csrf, "hello there").status_code == 422


# ------------------------------------------------------------------------ AI drafter
def test_ai_drafter_output_is_a_proposal_that_still_gets_validated():
    model = FakeModel({"category_allowlist": ["travel"], "max_single_purchase": 500, "auto_approve_up_to": 250})
    env, c, csrf = client(LLMDrafter(model, "test-model"))
    r = draft(c, csrf)
    assert r.status_code == 200 and r.json()["source"] == "ai"
    req = model.requests[0]
    assert req["tool_choice"] == {"type": "tool", "name": "submit_policy_draft"}
    assert [t["name"] for t in req["tools"]] == ["submit_policy_draft"]     # no payment / approval tools
    assert "merchant_demo_airlines" in str(req["tools"][0]["input_schema"])  # constrained to the registry


def test_gemini_drafter_uses_supported_schema_and_server_validation():
    model = FakeGeminiModel({
        "category_allowlist": ["travel"],
        "max_single_purchase": 500,
        "auto_approve_up_to": 250,
    })
    drafter = GeminiPolicyDrafter(model, "test-gemini")
    raw = drafter.draft(TEXT, demo_registry())
    schema = model.requests[0]["tools"][0]["parameters"]

    assert drafter.provider_name == "Gemini"
    assert raw["category_allowlist"] == ["travel"]
    assert model.requests[0]["store"] is False
    assert "propertyNames" not in str(schema)
    assert set(schema["properties"]["context_actions"]["properties"]) == set(CONTEXT_FLAGS)
    with pytest.raises(DraftError, match="unsupported fields"):
        validate_draft({**raw, "status": "ACTIVE"}, demo_registry())


@pytest.mark.parametrize("hostile", [
    {"merchant_allowlist": ["merchant_unknown"]},
    {"currency_allowlist": ["BTC"]},
    {"status": "ACTIVE", "max_single_purchase": 500},
    {"context_actions": {"HIDDEN_PAYMENT_INSTRUCTION": "ALLOW"}},
    {"max_single_purchase": 5000},
])
def test_gemini_api_drafts_are_server_validated(hostile):
    env, c, csrf = client(GeminiPolicyDrafter(FakeGeminiModel(hostile), "test-gemini"))
    before = env.svc.policies["policy_trip"]

    response = draft(c, csrf)

    assert response.status_code == 422
    assert response.json()["detail"].startswith("draft rejected")
    assert env.svc.policies["policy_trip"] == before
    assert not env.svc._drafts


@pytest.mark.parametrize("hostile", [
    {"merchant_allowlist": ["merchant_activation_services"], "max_single_purchase": 5000},
    {"status": "ACTIVE", "max_single_purchase": 100},
    {"auto_approve_up_to": 900},
    {"context_actions": {"HIDDEN_PAYMENT_INSTRUCTION": "ALLOW"}},
    {"category_allowlist": ["malware"]},
])
def test_hostile_model_output_is_rejected_and_policy_untouched(hostile):
    env, c, csrf = client(LLMDrafter(FakeModel(hostile), "m"))
    before = env.svc.policies["policy_trip"]
    r = draft(c, csrf)
    assert r.status_code == 422 and r.json()["detail"].startswith("draft rejected")
    assert env.svc.policies["policy_trip"] == before and not env.svc._drafts


def test_model_failures_never_change_policy():
    for model in (FakeModel(boom=True), FakeModel(text_only=True)):
        env, c, csrf = client(LLMDrafter(model, "m"))
        before = env.svc.policies["policy_trip"]
        assert draft(c, csrf).status_code in (422, 502)
        assert env.svc.policies["policy_trip"] == before


# ------------------------------------------------------------------------ UI + hosted isolation
def test_console_shows_the_authoring_panel_with_honest_source_label():
    _, c, _ = client()
    page = c.get("/console").text
    assert "Write your policy in plain language" in page and "A scripted drafter (no model key configured)" in page
    assert "Scripted draft policy" in page and "AI-generated draft policy" not in page
    _, c2, _ = client(LLMDrafter(FakeModel({"max_single_purchase": 400}), "m"))
    page2 = c2.get("/console").text
    assert "AI-generated draft policy" in page2 and "Claude AI drafter" in page2
    assert "Confirm and activate policy" in page2 and "__CSRF__" not in page2 and "__LABEL__" not in page2
    _, gemini_client, _ = client(GeminiPolicyDrafter(
        FakeGeminiModel({"max_single_purchase": 400}), "test-gemini"))
    gemini_page = gemini_client.get("/console").text
    assert "Gemini AI drafter proposes a draft." in gemini_page
    assert "Gemini-generated draft policy" in gemini_page


def test_review_workspaces_keep_drafts_and_activations_isolated():
    app, _ = make_app()
    a, b = Judge(app), Judge(app)
    r = a.c.post("/api/policy/draft", json={"text": "Allow flights under $300. Ask me before spending more than $100.", "csrf": a.csrf})
    assert r.status_code == 200
    assert a.c.post("/api/policy/activate", json={"draft_id": r.json()["draft_id"], "csrf": a.csrf}).status_code == 200
    assert a.c.get("/api/me").json()["policy"]["version"] == 2
    assert b.c.get("/api/me").json()["policy"]["version"] == 1
    assert b.c.post("/api/policy/activate", json={"draft_id": r.json()["draft_id"], "csrf": b.csrf}).status_code == 409


def test_review_workspace_ai_calls_are_capped():
    app, _ = make_app(max_chat_messages=2)
    a = Judge(app)
    ok = lambda: a.c.post("/api/policy/draft", json={"text": TEXT, "csrf": a.csrf}).status_code
    assert (ok(), ok(), ok()) == (200, 200, 429)
