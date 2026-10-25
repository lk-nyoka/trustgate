"""Public, sign-in-free playground (/try): a visitor plays the owner of an AI agent.

Every visitor gets an isolated user, spending policy, agent key and human session, so budgets and
approvals never leak between people. All decisions come from the real TrustService; this module only
maps button clicks to the same calls the HTTP API makes. Caps bound abuse and Sandbox usage.
"""
import threading
import time
import uuid
from dataclasses import replace

from fastapi import Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import ValidationError

from decimal import Decimal

from .demo_data import PAGES
from .policy_author import DraftError, ScriptedDrafter, public_json, validate_draft
from .service import ApprovalError, AuthError
from .texts import AGENT_CANNOT_APPROVE, EVENT_TEXT, reason_text

COOKIE = "tg_try"
SCENARIOS = {
    "allow": ("merchant_demo_airlines", "cpt-jnb-economy-180", "https://demo-airlines.test/checkout"),
    "hold": ("merchant_demo_airlines", "cpt-jnb-flex-320", "https://demo-airlines.test/checkout"),
    "inject": ("merchant_activation_services", "activation-fee-3", "https://demo-airlines.test/injected-fee"),
    "overlimit": ("merchant_demo_airlines", "cpt-jnb-business-900", "https://demo-airlines.test/injected-upgrade"),
}


CUSTOM_PRODUCTS = {
    "economy": ("merchant_demo_airlines", "cpt-jnb-economy-180"),
    "flex": ("merchant_demo_airlines", "cpt-jnb-flex-320"),
    "business": ("merchant_demo_airlines", "cpt-jnb-business-900"),
    "fee": ("merchant_activation_services", "activation-fee-3"),
}
MAX_PAGE = 3000
MAX_TEXT = 400
PRESETS = {
    "strict": ("Strict: flights up to $300, ask me for anything above $100",
               dict(max_single_purchase=300, auto_approve_up_to=100, max_total_spend=600)),
    "default": ("Balanced: flights up to $500, ask me above $250",
                dict(max_single_purchase=500, auto_approve_up_to=250, max_total_spend=1000)),
    "generous": ("Generous: flights up to $1000, ask me above $500",
                 dict(max_single_purchase=1000, auto_approve_up_to=500, max_total_spend=3000)),
}


def summarize(f, registry):
    by_id = {m: registry.merchant(m).name for m in registry.merchant_ids()}
    cur = "/".join(f["currency_allowlist"])
    return [f"Merchants: {', '.join(by_id.get(m, m) for m in f['merchant_allowlist'])}",
            f"Categories: {', '.join(f['category_allowlist'])}  ·  Currency: {cur}",
            f"Up to {f['max_single_purchase']} {cur} per purchase, {f['max_total_spend']} {cur} in total",
            f"Automatic up to {f['auto_approve_up_to']} {cur}; above that you approve (expires in {f['approval_expiry_minutes']} min)"]


def _change_lines(changes):
    def val(x):
        if isinstance(x, dict):
            return ", ".join(f"{k.replace('_', ' ').lower()} \u2192 {v.replace('_', ' ').lower()}" for k, v in x.items()) or "none"
        return ", ".join(map(str, x)) if isinstance(x, list) else str(x)
    names = {"context_actions": "when a page looks suspicious"}
    return [f"{names.get(c['field'], c['field'].replace('_', ' '))}: {val(c['from'])} \u2192 {val(c['to'])}" for c in changes] or ["No change from the current rules."]


class Busy(Exception):
    pass


class Visitor:
    def __init__(self, user_id, policy_id, agent_key, session):
        self.user_id, self.policy_id, self.agent_key, self.session = user_id, policy_id, agent_key, session
        self.runs = 0
        self.llm_calls = 0
        self.draft = None          # validated policy draft awaiting the human's activation
        self.custom_urls = []
        self.last_request = None  # (scenario, request_id)
        self.seen = time.time()


class Playground:
    def __init__(self, svc, template_policy, mode="fake", max_visitors=300, max_runs_per_visitor=30,
                 max_runs_total=None, idle_seconds=3 * 3600, drafter=None, max_llm_per_visitor=6, max_llm_total=300):
        self.svc, self.template, self.mode = svc, template_policy, mode
        self.drafter, self.max_llm_v, self.max_llm_total, self.llm_total = drafter or ScriptedDrafter(), max_llm_per_visitor, max_llm_total, 0
        self.max_visitors, self.max_runs = max_visitors, max_runs_per_visitor
        self.max_total = max_runs_total if max_runs_total is not None else (150 if mode == "sandbox" else 2000)
        self.idle, self.total = idle_seconds, 0
        self._v, self._lock = {}, threading.Lock()

    # ---- visitors ------------------------------------------------------------
    def get(self, token, create=False):
        with self._lock:
            v = self._v.get(token) if token else None
            if v:
                v.seen = time.time()
                return token, v
            if not create:
                return None, None
            self._purge()
            if len(self._v) >= self.max_visitors:
                raise Busy("The demo is at capacity. Please try again in a few minutes.")
            tag = uuid.uuid4().hex[:10]
            policy = replace(self.template, user_id=f"guest_{tag}", policy_id=f"policy_{tag}", status="ACTIVE")
            self.svc.policies[policy.policy_id] = policy
            key = f"agent_guest_{uuid.uuid4().hex}"
            self.svc.auth.register_agent(key, policy.user_id, policy.policy_id)
            v = Visitor(policy.user_id, policy.policy_id, key, self.svc.auth.issue_session(policy.user_id))
            token = uuid.uuid4().hex
            self._v[token] = v
            return token, v

    def _purge(self):
        cutoff = time.time() - self.idle
        for t in [t for t, v in self._v.items() if v.seen < cutoff]:
            v = self._v.pop(t)
            self.svc.auth._agents.pop(v.agent_key, None)
            self.svc.auth._sessions.pop(v.session, None)
            self.svc.policies.pop(v.policy_id, None)
            for u in v.custom_urls:
                getattr(self.svc.pages, "extra", {}).pop(u, None)

    def _charge_run(self, v):
        if v.runs >= self.max_runs:
            raise Busy("You have used this demo session's requests. Reload the page for a fresh session.")
        with self._lock:
            if self.total >= self.max_total:
                raise Busy("The shared demo request budget is used up. Run it locally (see the README).")
            self.total += 1
        v.runs += 1

    # ---- policy drafting (AI drafts, human activates, code enforces) ----------------------
    def draft_policy(self, v, text=None, preset_name=None):
        if preset_name:
            if preset_name not in PRESETS:
                raise KeyError(preset_name)
            label, raw = PRESETS[preset_name]
            source = "preset"
        else:
            text = (text or "").strip()
            if not text:
                raise DraftError("Describe your rules first.")
            if len(text) > MAX_TEXT:
                raise DraftError(f"Keep it under {MAX_TEXT} characters.")
            if self.drafter.name != "scripted":  # a real model call costs money: cap it
                with self._lock:
                    if v.llm_calls >= self.max_llm_v or self.llm_total >= self.max_llm_total:
                        raise Busy("AI drafting limit reached for this session. Choose a preset or reload.")
                    v.llm_calls += 1
                    self.llm_total += 1
            raw, label, source = self.drafter.draft(text, self.svc.registry), text, self.drafter.name
        fields = validate_draft(raw, self.svc.registry)  # code, not the model, decides what is allowed
        draft_id, changes = self.svc.create_policy_draft(v.session, v.policy_id, fields, label, source)
        v.draft = draft_id
        return {"label": label, "summary": _change_lines(changes), "ignored": [], "source": source,
                "model": getattr(self.drafter, "provider_name", None) if source not in ("preset", "scripted") else None}

    def activate_policy(self, v):
        if not v.draft:
            raise DraftError("Draft a policy first.")
        draft_id, v.draft = v.draft, None
        updated = self.svc.activate_policy_draft(v.session, draft_id)
        return {"policy_version": updated.version, **self.state(v)}

    def custom(self, v, html, product):
        if product not in CUSTOM_PRODUCTS:
            raise KeyError(product)
        html = str(html or "")
        if not html.strip() or len(html) > MAX_PAGE:
            raise DraftError(f"Write a page between 1 and {MAX_PAGE} characters.")
        extra = getattr(self.svc.pages, "extra", None)
        if extra is None:
            raise DraftError("Custom pages are not available on this server.")
        self._charge_run(v)
        m, p = CUSTOM_PRODUCTS[product]
        url = f"https://demo-airlines.test/custom/{v.policy_id[7:]}-{len(v.custom_urls)}"
        extra[url] = html
        v.custom_urls.append(url)
        rid = uuid.uuid4().hex[:12]
        v.last_request = ("custom", rid)
        sent = {"merchant_reference": m, "product_reference": p, "quantity": 1, "source_url": url, "request_id": rid}
        out = self.svc.propose_purchase(v.agent_key, m, p, 1, url, rid)
        return {"agent_sent": sent, "intent": self.view(v, out["intent_id"]), "page": html}

    # ---- actions ---------------------------------------------------------------
    def view(self, v, intent_id):
        return jsonable_encoder(self._decorate(self.svc.intent_for_user(v.user_id, intent_id)))

    def _decorate(self, view):
        view["reason_text"] = [{"code": r, "text": reason_text(r)} for r in view["reasons"]]
        view["mode"] = self.mode
        if view.get("expires_at"):
            view["seconds_left"] = max(0, int((view["expires_at"] - self.svc.clock()).total_seconds()))
        return view

    def run(self, v, scenario):
        m, p, url = SCENARIOS[scenario]
        self._charge_run(v)
        rid = uuid.uuid4().hex[:12]
        v.last_request = (scenario, rid)
        sent = {"merchant_reference": m, "product_reference": p, "quantity": 1, "source_url": url, "request_id": rid}
        out = self.svc.propose_purchase(v.agent_key, m, p, 1, url, rid)
        return {"agent_sent": sent, "intent": self.view(v, out["intent_id"]), "page": PAGES.get(url)}

    def decide(self, v, intent_id, choice):
        self.svc.intent_for_user(v.user_id, intent_id)  # must be this visitor's intent
        if choice == "APPROVE":
            self.svc.approve(v.session, intent_id)
        else:
            self.svc.decline(v.session, intent_id)
        return {"intent": self.view(v, intent_id)}

    def audit(self, v, intent_id):
        self.svc.intent_for_user(v.user_id, intent_id)
        events = [{"ts": e["ts"], "event": e["event"], "text": EVENT_TEXT.get(e["event"], e["event"]),
                   "hash": e["hash"][:10], "data": e["data"]} for e in self.svc.get_audit(intent_id)]
        return {"events": jsonable_encoder(events), "chain_valid": self.svc.audit.verify(),
                "no_order": self.svc.intent_for_user(v.user_id, intent_id)["order_id"] is None}

    def cheat(self, v, kind, model):
        if kind == "self_approve":
            run = self.run(v, "hold")
            iid = run["intent"]["intent_id"]
            try:
                self.svc.approve(v.agent_key, iid)  # the service refuses an agent key even if the route did not
                service = "UNEXPECTED: accepted"
            except AuthError as exc:
                service = f"refused by the service as well: {exc}"
            return {"kind": kind, "status": 403, "detail": AGENT_CANNOT_APPROVE, "service": service,
                    "intent": self.view(v, iid)}
        if kind == "tamper":
            body = {"merchant_reference": "merchant_demo_airlines", "product_reference": "cpt-jnb-economy-180",
                    "request_id": uuid.uuid4().hex[:12], "payee_id": "attacker@evil.test", "amount": "0.01"}
            try:
                model.model_validate(body)
                return {"kind": kind, "status": 200, "errors": []}
            except ValidationError as exc:
                return {"kind": kind, "status": 422, "sent": body,
                        "errors": [{"field": ".".join(map(str, e["loc"])), "msg": e["msg"]} for e in exc.errors()]}
        if kind == "replay":
            if not v.last_request:
                self.run(v, "allow")
            scenario, rid = v.last_request
            if scenario not in SCENARIOS:
                self.run(v, "allow")
                scenario, rid = v.last_request
            m, p, url = SCENARIOS[scenario]
            before = len(self.svc._intents)
            out = self.svc.propose_purchase(v.agent_key, m, p, 1, url, rid)
            return {"kind": kind, "request_id": rid, "intent": self.view(v, out["intent_id"]),
                    "new_intents_created": len(self.svc._intents) - before}
        if kind == "revoke":
            self.svc.revoke_policy(v.session, v.policy_id)
            return {"kind": kind, "policy": "REVOKED"}
        if kind == "restore":
            self.svc.confirm_policy(v.session, v.policy_id)
            return {"kind": kind, "policy": "ACTIVE"}
        raise KeyError(kind)

    def state(self, v):
        pol = self.svc.policies[v.policy_id]
        fields = dict(merchant_allowlist=sorted(pol.merchant_allowlist), category_allowlist=sorted(pol.category_allowlist),
                      currency_allowlist=sorted(pol.currency_allowlist), max_single_purchase=str(pol.max_single_purchase),
                      max_total_spend=str(pol.max_total_spend), auto_approve_up_to=str(pol.auto_approve_up_to),
                      approval_expiry_minutes=pol.approval_expiry_minutes)
        return {"policy": pol.status, "policy_version": pol.version, "policy_summary": summarize(fields, self.svc.registry),
                "ai_available": True, "drafter": self.drafter.name, "runs": v.runs, "mode": self.mode,
                "intents": [jsonable_encoder(self._decorate(i)) for i in self.svc.intents_for_user(v.user_id)]}


def register(app, pg, model, cookie_secure, page_html):
    def me(request, create=False):
        return pg.get(request.cookies.get(COOKIE), create)

    def guard(request):
        if request.headers.get("x-try") != "1":
            return JSONResponse({"detail": "missing X-Try header"}, status_code=400)
        _, v = me(request)
        if v is None:
            return JSONResponse({"detail": "session expired; reload the page"}, status_code=401)
        return v

    @app.get("/try", response_class=HTMLResponse)
    def try_page(request: Request):
        try:
            token, _ = me(request, create=True)
        except Busy as exc:
            return HTMLResponse(f"<h1>{exc}</h1>", status_code=503)
        resp = HTMLResponse(page_html(pg.mode))
        resp.set_cookie(COOKIE, token, httponly=True, samesite="strict", secure=cookie_secure, max_age=6 * 3600)
        return resp

    def call(request, fn):
        v = guard(request)
        if isinstance(v, JSONResponse):
            return v
        try:
            return JSONResponse(fn(v))
        except Busy as exc:
            return JSONResponse({"detail": str(exc)}, status_code=429)
        except DraftError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=422)
        except (ApprovalError, AuthError) as exc:
            return JSONResponse({"detail": reason_text(str(exc))}, status_code=409)
        except KeyError:
            return JSONResponse({"detail": "unknown option"}, status_code=400)

    @app.post("/try/api/run")
    async def api_run(request: Request):
        body = await _json(request)
        return call(request, lambda v: pg.run(v, body.get("scenario")) if body.get("scenario") in SCENARIOS
                    else (_ for _ in ()).throw(KeyError()))

    @app.post("/try/api/decide")
    async def api_decide(request: Request):
        body = await _json(request)
        ch = body.get("decision")
        return call(request, lambda v: pg.decide(v, str(body.get("intent_id")), ch) if ch in ("APPROVE", "DECLINE")
                    else (_ for _ in ()).throw(KeyError()))

    @app.post("/try/api/cheat")
    async def api_cheat(request: Request):
        body = await _json(request)
        return call(request, lambda v: pg.cheat(v, body.get("kind"), model))

    @app.post("/try/api/policy/draft")
    async def api_policy_draft(request: Request):
        body = await _json(request)
        return call(request, lambda v: pg.draft_policy(v, str(body.get("text") or "")[:MAX_TEXT + 1], body.get("preset")))

    @app.post("/try/api/policy/activate")
    def api_policy_activate(request: Request):
        return call(request, pg.activate_policy)

    @app.post("/try/api/custom")
    async def api_custom(request: Request):
        body = await _json(request)
        return call(request, lambda v: pg.custom(v, body.get("html"), body.get("product")))

    @app.get("/try/api/audit/{intent_id}")
    def api_audit(intent_id: str, request: Request):
        return call(request, lambda v: pg.audit(v, intent_id))

    @app.get("/try/api/state")
    def api_state(request: Request):
        return call(request, pg.state)


async def _json(request):
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}
