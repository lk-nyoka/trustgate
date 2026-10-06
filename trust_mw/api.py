"""HTTP layer. Business logic stays in TrustService; this file only authenticates and renders.

Two identities:
  - AI agent  (bearer API key)  → can only propose / poll its own intents
  - Human     (session cookie)  → can approve, decline, revoke, read audit trails
"""
import hashlib
import hmac
import html
import json
import secrets
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .landing import landing_page
from .service import ApprovalError, AuthError
from .ui_shell import (CSS, EVENT_LABELS, BLOCK_LABELS, chip, checks_html,
                       flow_html, fmt_expiry, layout, reason_label, esc)

COOKIE = "tm_session"


class PurchaseIntentIn(BaseModel):
    """Agent proposes references only. extra=forbid rejects amount, payee, user_id, approver."""
    model_config = ConfigDict(extra="forbid")
    merchant_reference: str = Field(max_length=100)
    product_reference:  str = Field(max_length=100)
    quantity:           StrictInt = 1
    source_url:         Optional[str] = Field(default=None, max_length=500)
    request_id:         str = Field(min_length=1, max_length=64)


# ── Helpers ──────────────────────────────────────────────────────────────────

def _timer_script(elem_id: str, seconds: int) -> str:
    if seconds <= 0:
        return ""
    return f"""<script>
(function(){{
  var s={seconds},el=document.getElementById("{elem_id}");
  if(!el)return;
  var iv=setInterval(function(){{
    s--;
    if(s<=0){{el.textContent="Expired";el.style.color="var(--bad)";clearInterval(iv);return;}}
    var m=Math.floor(s/60),sc=s%60;
    el.textContent=(m<10?"0":"")+m+":"+(sc<10?"0":"")+sc;
  }},1000);
}})();
</script>"""


def _flags_html(flags):
    if not flags:
        return ""
    parts = []
    for fl in flags:
        parts.append(
            f'<div class="evidence">'
            f'<div class="evidence-title">{esc(fl["flag"].replace("_", " ").title())}</div>'
            f'<div class="evidence-body">Source: <code>{esc(fl["source"])}</code> — '
            f'<em>{esc(fl["excerpt"][:160])}</em></div>'
            f'</div>'
        )
    return (f'<div class="mb-8 mt-16" style="font-size:11px;font-weight:700;text-transform:uppercase;'
            f'letter-spacing:.08em;color:var(--fg3)">Context evidence</div>'
            + "".join(parts))


def _timeline_items(events):
    dot_map = {
        "PAYMENT_CAPTURED": "ok", "APPROVED": "ok", "POLICY_CONFIRMED": "ok",
        "HELD_FOR_APPROVAL": "warn",
        "PAYMENT_FAILED": "bad", "PAYMENT_MISMATCH": "bad", "APPROVAL_EXPIRED": "bad",
        "APPROVAL_REFUSED_POLICY_CHANGED": "bad", "APPROVAL_REFUSED_FACTS_CHANGED": "bad",
        "DECLINED": "bad", "POLICY_REVOKED": "bad",
    }
    items = []
    for i, e in enumerate(events):
        label, _ = EVENT_LABELS.get(e["event"], (e["event"].replace("_", " ").title(), "acc"))
        dot = dot_map.get(e["event"], "acc")
        d = e.get("data", {})
        desc = ""
        if e["event"] == "INTENT_RECEIVED":
            uc = d.get("agent_claimed_untrusted", {})
            desc = f"{uc.get('merchant_reference','?')} · {uc.get('product_reference','?')}"
        elif e["event"] == "FACTS_RESOLVED":
            desc = f"{d.get('merchant','?')} · {d.get('amount','?')} {d.get('currency','?')}"
        elif e["event"] == "CONTEXT_SCANNED":
            n = len(d) if isinstance(d, list) else 0
            desc = f"{n} flag(s) detected" if n else "No flags — page is clean"
        elif e["event"] == "DECISION":
            dec = d.get("decision","?")
            reasons = d.get("reasons") or []
            desc = dec + (" · " + reason_label(reasons[0]) if reasons else "")
        elif e["event"] == "HELD_FOR_APPROVAL":
            desc = f"Expires {d.get('expires_at','?')}"
        elif e["event"] == "APPROVED":
            desc = f"By {d.get('by','?')}"
        elif e["event"] == "DECLINED":
            desc = f"By {d.get('by','?')}"
        elif e["event"] == "PAYMENT_CAPTURED":
            desc = f"Order {d.get('order_id','?')} · Capture {d.get('capture_id','?')}"
        elif e["event"] == "PAYMENT_FAILED":
            desc = d.get("error","")
        items.append(
            f'<li class="tl-item" style="--i:{i}">'
            f'<div class="tl-dot tl-dot-{dot}"></div>'
            f'<div class="tl-ts">{esc(e["ts"][11:19])}</div>'
            f'<div class="tl-body">'
            f'<div class="tl-ev">{esc(label)}</div>'
            f'{f"<div class=\\'tl-desc\\'>{esc(desc)}</div>" if desc else ""}'
            f'</div></li>'
        )
    return "".join(items)


# ── App factory ───────────────────────────────────────────────────────────────

def create_app(svc, users, csrf_secret, admin_key=None, cookie_secure=False,
               paypal_mode="fake"):
    """users: {username: (user_id, password)}."""
    app = FastAPI(title="Trust Middleware", docs_url=None, redoc_url=None)
    auth = svc.auth

    # CORS — allow the React dev server (port 5173) and same origin
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://localhost:8000",
                       "http://127.0.0.1:5173", "http://127.0.0.1:8000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def csrf_for(token):
        return hmac.new(csrf_secret.encode(), token.encode(), hashlib.sha256).hexdigest()

    def csrf_ok(token, supplied):
        return hmac.compare_digest(csrf_for(token), supplied or "")

    def bearer(request):
        h = request.headers.get("authorization", "")
        return h[7:].strip() if h.lower().startswith("bearer ") else None

    def human(request):
        token = request.cookies.get(COOKIE)
        if not token:
            return None, None
        try:
            return token, auth.session_user(token)
        except AuthError:
            return None, None

    def err(detail, status):
        return JSONResponse({"detail": detail}, status_code=status)

    def page(title, body, active_nav, token):
        stats = svc.stats()
        # append logout form link into the topbar via extra_head JS is messy —
        # instead we put a tiny logout link inside the sidebar user block
        logout_form = (f'<form method="post" action="/logout" id="logout-form">'
                       f'<input type="hidden" name="csrf" value="{csrf_for(token)}"></form>')
        body_with_logout = body + logout_form
        return layout(title, body_with_logout, active_nav=active_nav,
                      paypal_mode=paypal_mode, stats=stats)

    # ── Public routes ──────────────────────────────────────────────────────
    @app.get("/", response_class=HTMLResponse)
    def landing():
        return landing_page(svc.stats())

    @app.get("/login", response_class=HTMLResponse)
    def login_form():
        return HTMLResponse(f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in · TrustMiddleware</title>
<style>
{CSS}
body{{display:flex;align-items:center;justify-content:center;min-height:100vh;
  background:var(--sidebar)}}
.login-box{{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius-lg);
  padding:36px 40px;width:360px;box-shadow:0 20px 60px rgba(0,0,0,.3)}}
.login-logo{{display:flex;align-items:center;gap:10px;margin-bottom:28px}}
.login-logo .logo-icon{{width:32px;height:32px;border-radius:8px;background:var(--acc);
  display:flex;align-items:center;justify-content:center;font-weight:800;color:#fff;font-size:16px}}
.login-logo .logo-name{{font-weight:700;font-size:15px}}
.login-logo .logo-sub{{font-size:11px;color:var(--fg3)}}
label{{display:block;margin-bottom:16px}}
.label-text{{font-size:12px;font-weight:600;color:var(--fg2);margin-bottom:5px}}
</style></head><body>
<div class="login-box">
  <div class="login-logo">
    <div class="logo-icon">T</div>
    <div><div class="login-logo logo-name" style="margin:0">TrustMiddleware</div>
    <div class="logo-sub">Autonomous commerce</div></div>
  </div>
  <h2 style="font-size:18px;margin-bottom:6px">Sign in</h2>
  <p style="color:var(--fg3);font-size:13px;margin-bottom:24px">
    Approve or decline purchases your agent proposes.</p>
  <form method="post" action="/login">
    <label><div class="label-text">Username</div>
    <input name="username" autocomplete="username" autofocus></label>
    <label><div class="label-text">Password</div>
    <input name="password" type="password" autocomplete="current-password"></label>
    <button class="btn btn-primary" style="width:100%;justify-content:center;margin-top:4px">
      Sign in →</button>
  </form>
</div></body></html>""")

    @app.post("/login")
    async def login(request: Request):
        form = await request.form()
        entry = users.get(form.get("username") or "")
        if not entry or not hmac.compare_digest(entry[1], form.get("password") or ""):
            return HTMLResponse(f"""<!doctype html><html><head><meta charset="utf-8">
<style>{CSS} body{{display:flex;align-items:center;justify-content:center;min-height:100vh;background:var(--sidebar)}}</style>
</head><body><div style="background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:32px 40px;width:340px;text-align:center">
<p style="color:var(--bad);margin-bottom:16px">Wrong username or password.</p>
<a class="btn btn-primary" href="/login">Try again</a></div></body></html>""", status_code=401)
        resp = RedirectResponse("/console", status_code=303)
        resp.set_cookie(COOKIE, auth.issue_session(entry[0]),
                        httponly=True, samesite="strict", secure=cookie_secure)
        return resp

    @app.post("/logout")
    async def logout(request: Request):
        token, user = human(request)
        resp = RedirectResponse("/login", status_code=303)
        form = await request.form()
        if user and csrf_ok(token, form.get("csrf")):
            resp.delete_cookie(COOKIE)
        return resp

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    # ── JSON API for React frontend ────────────────────────────────────────

    @app.post("/api/login")
    async def api_login(request: Request):
        """JSON login — returns session token for React to store in memory."""
        body = await request.json()
        entry = users.get(body.get("username") or "")
        if not entry or not hmac.compare_digest(entry[1], body.get("password") or ""):
            return JSONResponse({"detail": "Invalid credentials"}, status_code=401)
        token = auth.issue_session(entry[0])
        resp = JSONResponse({"ok": True, "csrf": csrf_for(token)})
        resp.set_cookie(COOKIE, token, httponly=True, samesite="lax",
                        secure=cookie_secure)
        return resp

    @app.post("/api/logout")
    async def api_logout(request: Request):
        token, user = human(request)
        resp = JSONResponse({"ok": True})
        if user:
            resp.delete_cookie(COOKIE)
        return resp

    @app.get("/api/me")
    def api_me(request: Request):
        token, user = human(request)
        if not user:
            return JSONResponse({"authenticated": False}, status_code=401)
        csrf = csrf_for(token)
        stats = svc.stats()
        policy = next((p for p in svc.policies.values()), None)
        return {
            "authenticated": True,
            "user_id": user,
            "csrf": csrf,
            "paypal_mode": paypal_mode,
            "stats": stats,
            "policy": {
                "policy_id": policy.policy_id,
                "version": policy.version,
                "status": policy.status,
                "merchant_allowlist": list(policy.merchant_allowlist),
                "category_allowlist": list(policy.category_allowlist),
                "auto_approve_up_to": str(policy.auto_approve_up_to),
                "max_single_purchase": str(policy.max_single_purchase),
                "max_total_spend": str(policy.max_total_spend),
                "approval_expiry_minutes": policy.approval_expiry_minutes,
            } if policy else None,
        }

    @app.get("/api/intents")
    def api_intents(request: Request):
        _, user = human(request)
        if not user:
            return JSONResponse({"detail": "auth required"}, status_code=401)
        intents = svc.intents_for_user(user)
        # Attach expiry_seconds_left for timer
        now = svc.clock()
        for v in intents:
            if v.get("expires_at") and v["state"] == "HELD_FOR_APPROVAL":
                v["expires_seconds"] = max(0, int((v["expires_at"] - now).total_seconds()))
            else:
                v["expires_seconds"] = None
        return jsonable_encoder(intents)

    @app.get("/api/intents/{intent_id}")
    def api_intent_detail(intent_id: str, request: Request):
        _, user = human(request)
        if not user:
            return JSONResponse({"detail": "auth required"}, status_code=401)
        try:
            v = svc.intent_for_user(user, intent_id)
        except ApprovalError:
            return JSONResponse({"detail": "not found"}, status_code=404)
        now = svc.clock()
        if v.get("expires_at") and v["state"] == "HELD_FOR_APPROVAL":
            v["expires_seconds"] = max(0, int((v["expires_at"] - now).total_seconds()))
        else:
            v["expires_seconds"] = None
        return jsonable_encoder(v)

    @app.post("/api/intents/{intent_id}/approve")
    async def api_approve(intent_id: str, request: Request):
        if request.headers.get("authorization"):
            return JSONResponse({"detail": "agent credentials cannot approve"}, status_code=403)
        token, user = human(request)
        if not user:
            return JSONResponse({"detail": "auth required"}, status_code=401)
        body = await request.json()
        if not csrf_ok(token, body.get("csrf")):
            return JSONResponse({"detail": "bad CSRF token"}, status_code=403)
        try:
            svc.approve(token, intent_id)
        except ApprovalError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409)
        v = svc.intent_for_user(user, intent_id)
        return jsonable_encoder(v)

    @app.post("/api/intents/{intent_id}/decline")
    async def api_decline(intent_id: str, request: Request):
        token, user = human(request)
        if not user:
            return JSONResponse({"detail": "auth required"}, status_code=401)
        body = await request.json()
        if not csrf_ok(token, body.get("csrf")):
            return JSONResponse({"detail": "bad CSRF token"}, status_code=403)
        try:
            svc.decline(token, intent_id)
        except ApprovalError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409)
        v = svc.intent_for_user(user, intent_id)
        return jsonable_encoder(v)

    @app.get("/api/intents/{intent_id}/audit")
    def api_audit(intent_id: str, request: Request):
        _, user = human(request)
        if not user:
            return JSONResponse({"detail": "auth required"}, status_code=401)
        try:
            svc.intent_for_user(user, intent_id)
        except ApprovalError:
            return JSONResponse({"detail": "not found"}, status_code=404)
        events = svc.get_audit(intent_id)
        return jsonable_encoder({"chain_valid": svc.audit.verify(), "events": events})

    @app.post("/api/policy/revoke")
    async def api_revoke(request: Request):
        token, user = human(request)
        if not user:
            return JSONResponse({"detail": "auth required"}, status_code=401)
        body = await request.json()
        if not csrf_ok(token, body.get("csrf")):
            return JSONResponse({"detail": "bad CSRF token"}, status_code=403)
        for policy in list(svc.policies.values()):
            if policy.user_id == user:
                svc.revoke_policy(token, policy.policy_id)
        return {"ok": True}

    # ── Serve React static build (production) ─────────────────────────────
    import os, pathlib
    dist = pathlib.Path(__file__).parent.parent / "frontend" / "dist"
    if dist.exists():
        app.mount("/app", StaticFiles(directory=str(dist), html=True), name="react")


    @app.post("/v1/purchase-intents")
    def create_intent(body: PurchaseIntentIn, request: Request):
        key = bearer(request)
        if not key:
            return err("agent API key required", 401)
        try:
            return svc.propose_purchase(key, body.merchant_reference, body.product_reference,
                                        body.quantity, body.source_url, body.request_id)
        except AuthError:
            return err("invalid agent key", 401)

    @app.get("/v1/intents/{intent_id}")
    def get_intent_api(intent_id: str, request: Request):
        try:
            key = bearer(request)
            if key:
                return svc.intent_for_agent(key, intent_id)
            _, user = human(request)
            if not user:
                return err("authentication required", 401)
            return jsonable_encoder(svc.intent_for_user(user, intent_id))
        except AuthError:
            return err("invalid credentials", 401)
        except ApprovalError:
            return err("not found", 404)

    @app.get("/v1/intents/{intent_id}/audit")
    def audit_json(intent_id: str, request: Request):
        supplied = request.headers.get("x-admin-key")
        if admin_key and supplied and hmac.compare_digest(admin_key, supplied):
            events = svc.get_audit(intent_id)
        else:
            _, user = human(request)
            if not user:
                return err("authentication required", 401)
            try:
                svc.intent_for_user(user, intent_id)
            except ApprovalError:
                return err("not found", 404)
            events = svc.get_audit(intent_id)
        return {"chain_valid": svc.audit.verify(), "events": jsonable_encoder(events)}

    # ── Human decision endpoints ───────────────────────────────────────────
    @app.post("/v1/approvals/{intent_id}")
    async def decide(intent_id: str, request: Request):
        if request.headers.get("authorization"):
            return err("agent credentials cannot approve purchases", 403)
        token, user = human(request)
        if not user:
            return err("human login required", 401)
        form = await request.form()
        if not csrf_ok(token, form.get("csrf")):
            return err("bad CSRF token", 403)
        choice = form.get("decision")
        try:
            if choice == "APPROVE":
                svc.approve(token, intent_id)
            elif choice == "DECLINE":
                svc.decline(token, intent_id)
            else:
                return err("decision must be APPROVE or DECLINE", 422)
        except AuthError:
            return err("human login required", 401)
        except ApprovalError as exc:
            code = str(exc)
            status = 403 if code == "NOT_DELEGATING_USER" else 404 if code == "UNKNOWN_INTENT" else 409
            return err(code, status)
        return RedirectResponse(f"/approvals/{intent_id}", status_code=303)

    @app.post("/policy/revoke")
    async def revoke(request: Request):
        token, user = human(request)
        if not user:
            return err("human login required", 401)
        form = await request.form()
        if not csrf_ok(token, form.get("csrf")):
            return err("bad CSRF token", 403)
        for policy in list(svc.policies.values()):
            if policy.user_id == user:
                svc.revoke_policy(token, policy.policy_id)
        return RedirectResponse("/console", status_code=303)

    # ── Page: Command Center (/console) ────────────────────────────────────
    @app.get("/console", response_class=HTMLResponse)
    def command_center(request: Request):
        token, user = human(request)
        if not user:
            return RedirectResponse("/login", status_code=303)

        stats = svc.stats()
        all_intents = svc.intents_for_user(user)

        # ── pending banner (first HELD intent) ───────────────────────────────
        pending = [v for v in all_intents
                   if v["state"] == "HELD_FOR_APPROVAL" and v["approval_status"] == "PENDING"]
        banner = ""
        if pending:
            p = pending[0]
            f = p["facts"]
            left = int((p["expires_at"] - svc.clock()).total_seconds()) if p["expires_at"] else 0
            banner = f"""
<div style="background:#fffbeb;border:1px solid #fde68a;border-radius:var(--radius-lg);
  padding:16px 20px;margin-bottom:20px;display:flex;justify-content:space-between;align-items:center;gap:16px">
  <div>
    <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;
      color:var(--warn);margin-bottom:6px">⚠ Action required</div>
    <div style="font-weight:700;font-size:16px;margin-bottom:2px">
      {esc(f['product'] if f else '—')}</div>
    <div style="color:var(--fg3);font-size:12px">{esc(f['merchant'] if f else '—')}</div>
  </div>
  <div style="text-align:right">
    <div style="font-size:24px;font-weight:800;font-variant-numeric:tabular-nums;margin-bottom:4px">
      {esc(f['amount'] + ' ' + f['currency'] if f else '—')}</div>
    <div style="font-size:12px;color:var(--warn)">
      Expires in <span id="banner-timer" style="font-weight:700;font-family:ui-monospace">{fmt_expiry(left)}</span>
    </div>
  </div>
  <a href="/approvals/{esc(p['intent_id'])}" class="btn btn-primary" style="flex-shrink:0">
    Review and approve →</a>
</div>
{_timer_script("banner-timer", left)}"""

        # ── stats row ────────────────────────────────────────────────────────
        policy = next((p for p in svc.policies.values()), None)
        policy_label = f"travel.v{policy.version}" if policy else "—"
        blocked = stats.get("blocked", 0)

        stats_html = f"""
<div class="stats-row">
  <div class="stat-card">
    <div class="stat-label">Requests today</div>
    <div class="stat-value">{stats.get('captured',0)+stats.get('awaiting',0)+stats.get('blocked',0):02d}</div>
    <div class="stat-sub">all decisions visible</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">Payments captured</div>
    <div class="stat-value" style="color:var(--ok)">{stats.get('captured',0):02d}</div>
    <div class="stat-sub">via governed flow</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">PayPal bypass attempts</div>
    <div class="stat-value" style="color:var(--bad)">{blocked:02d}</div>
    <div class="stat-sub">blocked before order</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">Active policy</div>
    <div class="stat-value" style="font-size:18px;font-family:ui-monospace">{esc(policy_label)}</div>
    <div class="stat-sub">updated this session</div>
  </div>
</div>"""

        # ── story runner cards ───────────────────────────────────────────────
        scenarios = [
            ("01", "Safe flight", "$180", "ALLOW", "acc", "cpt-jnb-economy-180",
             "CPT → JNB economy · Demo Airlines · Auto-approved"),
            ("02", "Flexible flight", "$320", "APPROVAL REQUIRED", "warn", "cpt-jnb-flex-320",
             "CPT → JNB flexible · Demo Airlines · Awaiting human approval"),
            ("03", "Activation fee", "$3", "BLOCK", "bad", "activation-fee-3",
             "Activation Services Demo · Not in travel policy"),
        ]

        story_cards = []
        for num, name, amt, verdict, color, _, sub in scenarios:
            # find matching intent from seeded data
            match = next((v for v in all_intents
                          if v["facts"] and v["facts"].get("amount","").startswith(amt.replace("$",""))), None)
            href = f"/approvals/{match['intent_id']}" if match and match["state"] == "HELD_FOR_APPROVAL" else \
                   f"/intents/{match['intent_id']}" if match else "#"
            dot_color = f"var(--{'ok' if color=='acc' else color})"
            story_cards.append(f"""
<a href="{href}" class="intent-row" style="border-radius:var(--radius);border:1px solid var(--line);
  background:var(--surface);display:block;text-decoration:none;padding:14px 16px;margin-bottom:8px">
  <div class="row-between mb-4">
    <span style="font-size:10px;font-weight:600;color:var(--fg3);text-transform:uppercase;
      letter-spacing:.08em">{num}</span>
    <span style="width:8px;height:8px;border-radius:50%;background:{dot_color};display:inline-block"></span>
  </div>
  <div style="font-weight:700;font-size:14px;margin-bottom:2px">{esc(name)}</div>
  <div class="row-between">
    <span style="font-size:12px;color:var(--fg3)">{esc(sub)}</span>
    <span style="font-weight:700;font-size:15px;font-variant-numeric:tabular-nums">{esc(amt)}</span>
  </div>
</a>""")

        # ── active policy panel ──────────────────────────────────────────────
        policy_html = ""
        if policy:
            ml = ", ".join(sorted(policy.merchant_allowlist)) or "—"
            cl = ", ".join(sorted(policy.category_allowlist)) or "—"
            policy_html = f"""
<div class="card" style="margin-top:0">
  <div class="row-between mb-12">
    <div class="card-title" style="margin:0">Active policy</div>
    <span style="background:#dbeafe;color:#1e40af;font-size:10px;font-weight:700;
      padding:2px 8px;border-radius:5px;letter-spacing:.04em">v{policy.version}.1</span>
  </div>
  <div class="posture-badge mb-12">
    <div class="pb-label">✓ Bounded delegation</div>
    <div class="pb-sub">Policy rules the authority. AI only supplies the suggestion.</div>
  </div>
  <div class="fact-grid" style="font-size:12px">
    <dt>Policy ID</dt><dd><code>{esc(policy.policy_id)}</code></dd>
    <dt>Approved merchant</dt><dd>{esc(ml)}</dd>
    <dt>Allowed category</dt><dd>{esc(cl)}</dd>
    <dt>Auto-approve up to</dt><dd>${esc(str(policy.auto_approve_up_to))}</dd>
    <dt>Max single purchase</dt><dd>${esc(str(policy.max_single_purchase))}</dd>
    <dt>Total budget</dt><dd>${esc(str(policy.max_total_spend))}</dd>
  </div>
  <div class="sep"></div>
  <form method="post" action="/policy/revoke">
    <input type="hidden" name="csrf" value="{csrf_for(token)}">
    <button class="btn btn-danger btn-sm">⚠ Revoke policy (kill switch)</button>
  </form>
</div>"""

        body = f"""
<div class="page-eyebrow">Trust boundary / Live demo</div>
<h1 class="page-headline">
  The agent can propose.<br>
  <span class="hl-acc">It cannot authorize.</span>
</h1>
<p class="page-lead">Trust Middleware turns an AI recommendation into a governed purchase intent.
Watch trusted facts, deterministic policy, and human authority decide what reaches PayPal.</p>
{banner}
{stats_html}
<div class="split">
  <div>
    <div class="card-title">01 / Run the story</div>
    {"".join(story_cards)}
  </div>
  <div>
    <div class="card-title">02 / Authority</div>
    {policy_html}
  </div>
</div>"""

        return HTMLResponse(page("Command Center", body, "console", token))

    # ── Page: Purchase Intents (/intents) ──────────────────────────────────
    @app.get("/intents", response_class=HTMLResponse)
    def intents_page(request: Request, selected: Optional[str] = None):
        token, user = human(request)
        if not user:
            return RedirectResponse("/login", status_code=303)

        all_intents = svc.intents_for_user(user)

        # auto-select first if none specified
        sel_id = selected or (all_intents[0]["intent_id"] if all_intents else None)

        # ── left: request list ────────────────────────────────────────────
        list_rows = ""
        for v in all_intents:
            f = v["facts"]
            state = v["state"]
            iid = v["intent_id"]
            is_sel = iid == sel_id

            dot_color = ("var(--ok)" if state == "CAPTURED"
                         else "var(--warn)" if state == "HELD_FOR_APPROVAL"
                         else "var(--bad)" if state == "BLOCKED"
                         else "var(--fg3)")
            sub = ("governed payment path" if state == "CAPTURED"
                   else "awaiting human approval" if state == "HELD_FOR_APPROVAL"
                   else "context risk flagged" if state == "BLOCKED"
                   else state.lower().replace("_", " "))

            sel_cls = " selected" if is_sel else ""
            list_rows += f"""
<a href="/intents?selected={esc(iid)}" class="intent-row{sel_cls}" style="text-decoration:none">
  <div class="ir-top">
    <span class="ir-name">{esc(f['merchant'] + ' · ' + f['product'] if f else iid)}</span>
    <span class="ir-amt">{esc(('$'+f['amount']) if f else '—')}</span>
  </div>
  <div class="row-between">
    <span class="ir-sub">
      <span style="width:7px;height:7px;border-radius:50%;background:{dot_color};
        display:inline-block;margin-right:5px"></span>{esc(sub)}
    </span>
    {chip(state)}
  </div>
</a>"""

        left_panel = f"""
<div class="card" style="padding:0;overflow:hidden">
  <div class="card-section">
    Live requests &nbsp;
    <span class="live-dot" style="display:inline-block;width:6px;height:6px;border-radius:50%;
      background:var(--ok);animation:pulse-dot 2s infinite"></span>&nbsp; streaming
  </div>
  <div class="card-title" style="padding:12px 16px 0;margin:0">Purchase intents</div>
  {list_rows or '<div style="padding:20px;color:var(--fg3);font-size:13px">No intents yet.</div>'}
</div>"""

        # ── right: selected detail ────────────────────────────────────────
        right_panel = '<div class="card" style="color:var(--fg3);font-size:13px;padding:28px">Select a request to inspect it.</div>'

        if sel_id:
            try:
                v = svc.intent_for_user(user, sel_id)
                f = v["facts"]
                state = v["state"]

                state_chip = chip(state)
                title_str = (f"{f['merchant']} · {f['product']}" if f else sel_id)

                rows = []
                if f:
                    rows += [
                        ("Merchant", esc(f["merchant"])),
                        ("Product reference", esc(f["product"])),
                        ("Verified payee", f'<code>{esc(f["payee_id"])}</code>'),
                        ("Amount / currency", f'<strong>{esc(f["amount"])} {esc(f["currency"])}</strong>'),
                        ("Policy version", f'<code>{esc("travel.v" + str(v["policy_version"]))}</code>'),
                        ("Approval expiry", "10 min · single-use"),
                        ("Reason codes",
                         " · ".join(esc(r) for r in v["reasons"]) or
                         '<span style="color:var(--fg3)">POLICY_MATCH · CONTEXT_CLEAR</span>'),
                    ]
                if v["order_id"]:
                    rows += [
                        ("PayPal order", f'<code>{esc(str(v["order_id"]))}</code>'),
                        ("Capture ID", f'<code>{esc(str(v["capture_id"]))}</code>'),
                    ]

                detail_rows = "".join(
                    f'<dt style="color:var(--fg3);font-size:12px;padding-top:1px">{k}</dt>'
                    f'<dd style="font-size:13px">{val}</dd>'
                    for k, val in rows)

                # CTA button
                if state == "HELD_FOR_APPROVAL":
                    cta = f'<a href="/approvals/{esc(sel_id)}" class="btn btn-primary" style="margin-top:16px">Run this intent in console →</a>'
                elif state == "BLOCKED":
                    cta = f'<a href="/intents/{esc(sel_id)}/detail" class="btn" style="margin-top:16px">View security decision →</a>'
                else:
                    cta = f'<a href="/intents/{esc(sel_id)}/detail" class="btn" style="margin-top:16px">View transaction →</a>'

                flags_block = _flags_html(v["flags"])

                right_panel = f"""
<div class="card" style="padding:0;overflow:hidden">
  <div class="card-section">
    Selected intent &nbsp; {state_chip}
  </div>
  <div style="padding:20px">
    <div style="font-weight:700;font-size:16px;margin-bottom:14px">{esc(title_str)}</div>
    <dl class="fact-grid" style="row-gap:8px">{detail_rows}</dl>
    {flags_block}
    {cta}
  </div>
</div>"""
            except ApprovalError:
                pass

        body = f"""
<div class="row-between mb-16">
  <div>
    <div class="page-eyebrow">Request queue / {len(all_intents)} intent{"s" if len(all_intents)!=1 else ""}</div>
    <h1 class="page-headline" style="font-size:32px;margin-bottom:6px">Every proposal gets a verdict.</h1>
    <p class="page-lead" style="margin-bottom:0">Inspect the exact input, server-resolved facts, policy result, and payment state for each request.</p>
  </div>
  <a href="/console" class="btn btn-sm" style="flex-shrink:0">↺ Reset demo data</a>
</div>
<div class="split">{left_panel}{right_panel}</div>"""

        return HTMLResponse(page("Purchase Intents", body, "intents", token))

    # ── Page: Intent detail (blocked / captured / etc) ────────────────────
    @app.get("/intents/{intent_id}/detail", response_class=HTMLResponse)
    def intent_detail(intent_id: str, request: Request):
        token, user = human(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        try:
            v = svc.intent_for_user(user, intent_id)
        except ApprovalError:
            return HTMLResponse(layout("Not found", "<h1>Not found</h1>", stats=svc.stats()), status_code=404)

        state = v["state"]
        f = v["facts"]

        if state == "CAPTURED":
            title = "Transaction record"
            header_chip = chip("CAPTURED")
            card_style = "border-color:#a7f3d0;background:#f0fdf4"
            amount_color = "var(--ok)"
            detail_rows = ""
            if f:
                for k, val in [
                    ("Merchant", esc(f["merchant"])),
                    ("Product", esc(f["product"])),
                    ("Payee", f'<code>{esc(f["payee_id"])}</code>'),
                    ("Policy decision", chip(v["decision"])),
                    ("Approval", chip(v["approval_status"])),
                    ("Payment state", chip(state)),
                    ("PayPal order", f'<code>{esc(str(v["order_id"]))}</code>'),
                    ("Capture ID", f'<code>{esc(str(v["capture_id"]))}</code>'),
                ]:
                    detail_rows += f'<dt style="color:var(--fg3);font-size:12px">{k}</dt><dd style="font-size:13px">{val}</dd>'
            extra = f'<dl class="fact-grid" style="row-gap:8px">{detail_rows}</dl>'

        elif state == "BLOCKED":
            title = "Payment blocked"
            header_chip = chip("BLOCKED")
            card_style = "border-color:#fca5a5;background:#fff5f5"
            amount_color = "var(--bad)"
            reasons = v.get("reasons") or []
            primary = reason_label(reasons[0]) if reasons else "Policy block"
            additional = [reason_label(r) for r in reasons[1:]]
            add_html = ""
            if additional:
                lis = "".join(f"<li style='margin-bottom:4px'>{esc(r)}</li>" for r in additional)
                add_html = f'<div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--fg3);margin-top:14px;margin-bottom:6px">Additional restrictions</div><ul style="margin:0;padding-left:18px;color:var(--fg2);font-size:13px">{lis}</ul>'
            merchant_name = f["merchant"] if f else "Unknown merchant"
            amount_str = f"{f['amount']} {f['currency']}" if f else "—"
            extra = f"""
<dl class="fact-grid" style="row-gap:8px;margin-bottom:14px">
  <dt style="color:var(--fg3);font-size:12px">Merchant</dt><dd style="font-size:13px">{esc(merchant_name)}</dd>
  <dt style="color:var(--fg3);font-size:12px">Amount</dt><dd style="font-size:13px">{esc(amount_str)}</dd>
  <dt style="color:var(--fg3);font-size:12px">Product</dt><dd style="font-size:13px">{esc(f['product'] if f else '—')}</dd>
</dl>
<div style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--fg3);margin-bottom:6px">Primary reason</div>
<div style="font-weight:700;color:var(--bad);font-size:14px;margin-bottom:4px">{esc(primary)}</div>
{add_html}
{_flags_html(v["flags"])}
<div class="sep"></div>
<dl class="fact-grid" style="row-gap:8px">
  <dt style="color:var(--fg3);font-size:12px">PayPal order</dt>
  <dd style="color:var(--fg3);font-size:13px">Not created</dd>
  <dt style="color:var(--fg3);font-size:12px">Capture</dt>
  <dd style="color:var(--fg3);font-size:13px">Not created</dd>
  <dt style="color:var(--fg3);font-size:12px">Audit state</dt><dd>{chip("BLOCKED")}</dd>
</dl>"""
        else:
            title = f"Purchase — {state}"
            header_chip = chip(state)
            card_style = ""
            amount_color = "var(--fg)"
            extra = f'<dl class="fact-grid"><dt style="color:var(--fg3)">State</dt><dd>{chip(state)}</dd><dt style="color:var(--fg3)">Decision</dt><dd>{chip(v["decision"])}</dd></dl>'

        amt_display = (f['amount'] + " " + f['currency']) if f else "—"

        body = f"""
<div class="row mb-16">
  <a href="/intents" class="btn btn-sm">← Purchase intents</a>
  <a href="/audit?selected={esc(intent_id)}" class="btn btn-sm">View audit timeline</a>
</div>
<h1 class="page-headline" style="font-size:28px;margin-bottom:6px">{esc(title)}</h1>
<p class="page-lead" style="margin-bottom:16px"><code>{esc(intent_id)}</code></p>
<div class="split" style="align-items:start">
  <div>
    <div class="card" style="{card_style}">
      <div class="row-between mb-12">
        <div class="amt-big" style="color:{amount_color}">{esc(amt_display)}</div>
        {header_chip}
      </div>
      {extra}
    </div>
  </div>
  <div>
    <div class="card">
      {flow_html(v)}
      {checks_html("Policy evaluation", v["checks"])}
      {checks_html("Approval bound to these verified facts", [(n,"pass") for n in v["binding_checks"]])}
    </div>
  </div>
</div>"""

        return HTMLResponse(page(title, body, "intents", token))

    # ── Page: Approvals (/approvals/{id}) ─────────────────────────────────
    @app.get("/approvals/{intent_id}", response_class=HTMLResponse)
    def approval_page(intent_id: str, request: Request):
        token, user = human(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        try:
            v = svc.intent_for_user(user, intent_id)
        except ApprovalError:
            return HTMLResponse(layout("Not found","<h1>Not found</h1>",stats=svc.stats()), status_code=404)

        f = v["facts"]
        state = v["state"]
        held = state == "HELD_FOR_APPROVAL"
        captured = state == "CAPTURED"

        left = int((v["expires_at"] - svc.clock()).total_seconds()) if v["expires_at"] and held else None

        policy = svc.policies.get(v["policy_id"])
        policy_label = f"Travel delegation · Version {v['policy_version']}"
        reason_text = "; ".join(reason_label(r) for r in v["reasons"]) or "Above $250 approval threshold"

        # ── facts card ───────────────────────────────────────────────────
        card_style = ("border-color:#fde68a;background:#fffbeb" if held
                      else "border-color:#a7f3d0;background:#f0fdf4" if captured
                      else "")

        status_badge = ""
        if held:
            status_badge = '<div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:var(--warn);margin-bottom:14px">⚠ Awaiting your decision</div>'
        elif captured:
            status_badge = '<div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:var(--ok);margin-bottom:14px">✓ Payment captured</div>'

        expiry_row = ""
        timer_js = ""
        if held and left is not None:
            expiry_row = f'<dt style="color:var(--fg3);font-size:12px">Expires in</dt><dd style="font-size:13px;color:var(--warn);font-weight:700;font-family:ui-monospace" id="appr-timer">{fmt_expiry(left)}</dd>'
            timer_js = _timer_script("appr-timer", left)

        detail_rows = ""
        if f:
            for k, val in [
                ("Merchant",      esc(f["merchant"])),
                ("Product",       esc(f["product"])),
                ("Payee",         f'<code>{esc(f["payee_id"])}</code>'),
                ("Amount",        f'<strong>{esc(f["amount"])} {esc(f["currency"])}</strong>'),
                ("Policy",        esc(policy_label)),
                ("Reason",        esc(reason_text)),
            ]:
                detail_rows += f'<dt style="color:var(--fg3);font-size:12px">{k}</dt><dd style="font-size:13px">{val}</dd>'

        facts_card = f"""
<div class="card" style="{card_style}">
  {status_badge}
  <div class="amt-big" style="margin-bottom:4px">{esc(f['amount'] + ' ' + f['currency']) if f else '—'}</div>
  <div class="amt-sub">{esc(f['product']) if f else ''}</div>
  <dl class="fact-grid" style="row-gap:8px">{detail_rows}{expiry_row}</dl>
  {_flags_html(v["flags"])}
  {timer_js}"""

        # ── action buttons (only when held) ──────────────────────────────
        if held:
            c = csrf_for(token)
            facts_card += f"""
  <div class="sep"></div>
  <div class="row">
    <form method="post" action="/v1/approvals/{esc(intent_id)}">
      <input type="hidden" name="csrf" value="{c}">
      <input type="hidden" name="decision" value="DECLINE">
      <button class="btn btn-danger">Decline</button>
    </form>
    <form method="post" action="/v1/approvals/{esc(intent_id)}">
      <input type="hidden" name="csrf" value="{c}">
      <input type="hidden" name="decision" value="APPROVE">
      <button class="btn btn-primary">Approve {esc(f['amount'] + ' ' + f['currency']) if f else 'purchase'} →</button>
    </form>
  </div>"""

        facts_card += "</div>"

        # ── receipt card (after capture) ─────────────────────────────────
        receipt = ""
        if captured and v["order_id"]:
            receipt = f"""
<div class="card" style="border-color:#a7f3d0;background:#f0fdf4;margin-top:0">
  <div class="card-title">Receipt</div>
  <dl class="fact-grid" style="row-gap:8px">
    <dt style="color:var(--fg3);font-size:12px">Policy decision</dt><dd>{chip("APPROVAL_REQUIRED")}</dd>
    <dt style="color:var(--fg3);font-size:12px">Approval</dt><dd>{chip("APPROVED")}</dd>
    <dt style="color:var(--fg3);font-size:12px">Payment state</dt><dd>{chip("CAPTURED")}</dd>
    <dt style="color:var(--fg3);font-size:12px">PayPal order</dt>
    <dd><code>{esc(str(v["order_id"]))}</code></dd>
    <dt style="color:var(--fg3);font-size:12px">Capture ID</dt>
    <dd><code>{esc(str(v["capture_id"]))}</code></dd>
  </dl>
</div>"""

        right_col = f"""
<div class="card">
  {flow_html(v)}
  {checks_html("Policy evaluation", v["checks"])}
  {checks_html("Approval bound to these verified facts", [(n,"pass") for n in v["binding_checks"]])}
</div>
<p class="mt-12 text-sm">
  <a href="/audit?selected={esc(intent_id)}">View audit timeline →</a>
</p>"""

        title = "Purchase awaiting approval" if held else ("Approved and captured" if captured else "Purchase")
        body = f"""
<div class="row mb-16">
  <a href="/intents" class="btn btn-sm">← Purchase intents</a>
</div>
<h1 class="page-headline" style="font-size:26px;margin-bottom:4px">{esc(title)}</h1>
<p class="page-lead" style="margin-bottom:16px"><code>{esc(intent_id)}</code></p>
<div class="split" style="align-items:start">
  <div>{facts_card}{receipt}</div>
  <div>{right_col}</div>
</div>"""

        return HTMLResponse(page(title, body, "intents", token))

    # ── Page: Audit Trail (/audit) ─────────────────────────────────────────
    @app.get("/audit", response_class=HTMLResponse)
    def audit_page(request: Request, selected: Optional[str] = None):
        token, user = human(request)
        if not user:
            return RedirectResponse("/login", status_code=303)

        all_intents = svc.intents_for_user(user)
        ok = svc.audit.verify()
        all_events = svc.audit.events  # full log across all intents

        # tab: all events / policy decisions / payment events
        tab = request.query_params.get("tab", "all")

        def event_filter(events, t):
            if t == "policy":
                return [e for e in events if e["event"] in ("DECISION","HELD_FOR_APPROVAL",
                        "APPROVAL_REFUSED_POLICY_CHANGED","APPROVAL_REFUSED_FACTS_CHANGED")]
            if t == "payments":
                return [e for e in events if e["event"] in ("PAYMENT_CAPTURED","PAYMENT_FAILED",
                        "PAYMENT_MISMATCH","APPROVED","DECLINED")]
            return events

        filtered = event_filter(all_events, tab)

        # filter by selected intent
        if selected:
            filtered = [e for e in filtered if e["intent_id"] == selected]

        # last hash snippet for chain indicator
        last_hash = all_events[-1]["hash"][:8] + "..." + all_events[-1]["hash"][-4:] if all_events else "—"

        # build table rows
        def ev_state(event_type):
            if event_type in ("PAYMENT_CAPTURED","APPROVED"):
                return ("CAPTURED", "ok")
            if event_type in ("HELD_FOR_APPROVAL",):
                return ("HELD", "warn")
            if event_type in ("BLOCKED","PAYMENT_FAILED","PAYMENT_MISMATCH",
                               "APPROVAL_EXPIRED","DECLINED"):
                return ("BLOCKED","bad")
            if event_type == "DECISION":
                return ("—","acc")
            return ("RECEIVED","acc")

        rows = ""
        for i, e in enumerate(filtered):
            label, _ = EVENT_LABELS.get(e["event"], (e["event"].replace("_"," ").title(), "acc"))
            state_name, state_cls = ev_state(e["event"])
            ev_id = f"evt_{e['seq']:04d}"
            # source / actor
            actor = ("shopping-agent / scoped key" if e["event"] == "INTENT_RECEIVED"
                     else "registry / product + merchant" if e["event"] == "FACTS_RESOLVED"
                     else "trust-middleware+" if e["event"] in ("PAYMENT_CAPTURED","PAYMENT_FAILED")
                     else f"policy-engine / travel.v{1}" if "DECISION" in e["event"] or "APPROVAL" in e["event"]
                     else "trust-middleware")
            rows += f"""
<tr>
  <td>
    <div style="font-weight:600;font-size:13px">{esc(label)}</div>
    <div style="font-size:11px;color:var(--fg3)">{esc(e["ts"][11:19])} · append only</div>
  </td>
  <td style="font-size:12px;color:var(--fg2)">{esc(actor)}</td>
  <td>{chip(state_name)}</td>
  <td><code style="font-size:10px">{esc(ev_id)}</code></td>
</tr>"""

        def tab_cls(t):
            return ' style="color:var(--acc);border-bottom:2px solid var(--acc);margin-bottom:-1px"' if t == tab else ""

        tab_counts = {
            "all": len(all_events),
            "policy": len(event_filter(all_events, "policy")),
            "payments": len(event_filter(all_events, "payments")),
        }

        sel_param = f"&selected={esc(selected)}" if selected else ""

        chain_badge = (
            f'<div style="background:#d1fae5;border:1px solid #6ee7b7;border-radius:var(--radius);padding:8px 14px;text-align:right">'
            f'<div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--ok);margin-bottom:3px">✓ Chain intact</div>'
            f'<div style="font-size:11px;color:var(--ok-dark);font-family:ui-monospace">hash: {esc(last_hash)}</div>'
            f'</div>'
            if ok else
            f'<div style="background:#fee2e2;border:1px solid #fca5a5;border-radius:var(--radius);padding:8px 14px">'
            f'<div style="font-size:11px;font-weight:700;color:var(--bad)">⚠ Chain invalid</div>'
            f'</div>'
        )

        body = f"""
<div class="row-between mb-8">
  <div>
    <div class="page-eyebrow">Evidence / Append only</div>
    <h1 class="page-headline">Nothing disappears after the verdict.</h1>
    <p class="page-lead">Decision inputs, policy versions, human approvals, and PayPal states are recorded as a traceable chain.</p>
  </div>
  <div style="flex-shrink:0">{chain_badge}</div>
</div>
<div class="card" style="padding:0;overflow:hidden">
  <div style="padding:12px 16px;border-bottom:1px solid var(--line);display:flex;
    gap:24px;align-items:center">
    <a href="/audit?tab=all{sel_param}"{tab_cls("all")} style="font-size:13px;font-weight:600;color:var(--fg2);text-decoration:none;padding-bottom:2px">
      All events&nbsp;<span style="background:var(--surface2);border:1px solid var(--line);
        border-radius:5px;padding:0 6px;font-size:11px">{tab_counts["all"]}</span></a>
    <a href="/audit?tab=policy{sel_param}"{tab_cls("policy")} style="font-size:13px;font-weight:600;color:var(--fg2);text-decoration:none;padding-bottom:2px">
      Policy decisions&nbsp;<span style="background:var(--surface2);border:1px solid var(--line);
        border-radius:5px;padding:0 6px;font-size:11px">{tab_counts["policy"]}</span></a>
    <a href="/audit?tab=payments{sel_param}"{tab_cls("payments")} style="font-size:13px;font-weight:600;color:var(--fg2);text-decoration:none;padding-bottom:2px">
      Payment events&nbsp;<span style="background:var(--surface2);border:1px solid var(--line);
        border-radius:5px;padding:0 6px;font-size:11px">{tab_counts["payments"]}</span></a>
    <span style="margin-left:auto;font-size:11px;color:var(--fg3);font-family:ui-monospace">
      hash: {esc(last_hash)}</span>
  </div>
  <table class="tbl">
    <tr><th>Event</th><th>Action / Source</th><th>State</th><th>Event ID</th></tr>
    {rows or '<tr><td colspan="4" style="padding:20px;color:var(--fg3)">No events.</td></tr>'}
  </table>
</div>"""

        return HTMLResponse(page("Audit Trail", body, "audit", token))

    # ── Page: API Surface (/api-surface) ──────────────────────────────────
    @app.get("/api-surface", response_class=HTMLResponse)
    def api_surface_page(request: Request):
        token, user = human(request)
        if not user:
            return RedirectResponse("/login", status_code=303)

        endpoints = [
            ("POST", "/v1/purchase-intents",        "Propose a purchase for evaluation",       "Agent with scoped API key"),
            ("POST", "/v1/approvals/{intent_id}",   "Approve or decline a held purchase",      "Authenticated human only"),
            ("GET",  "/v1/intents/{intent_id}",     "View transaction and decision details",    "Authorised user / developer"),
            ("GET",  "/v1/intents/{intent_id}/audit","View immutable decision + payment events","Authorised user / developer"),
        ]

        ep_rows = ""
        for method, path, desc, auth_req in endpoints:
            m_color = "var(--ok)" if method == "GET" else "var(--acc)"
            ep_rows += f"""
<div style="display:grid;grid-template-columns:52px 1fr auto;gap:10px 16px;
  align-items:center;padding:14px 20px;border-bottom:1px solid var(--line2)">
  <span style="background:{m_color};color:#fff;font-size:10px;font-weight:700;
    padding:3px 8px;border-radius:5px;text-align:center;font-family:ui-monospace">{esc(method)}</span>
  <div>
    <div style="font-family:ui-monospace;font-size:13px;margin-bottom:2px">{esc(path)}</div>
    <div style="font-size:12px;color:var(--fg3)">{esc(desc)}</div>
  </div>
  <div style="font-size:11px;color:var(--fg3);text-align:right;white-space:nowrap">{esc(auth_req)}</div>
</div>"""

        example_body = json.dumps({
            "merchant_reference": "merchant_demo_airlines",
            "product_reference":  "cpt-jnb-flex-320",
            "quantity": 1,
            "source_url": "https://demo-airlines.test/checkout",
            "request_id": "demo-req-001"
        }, indent=2)

        body = f"""
<div class="row-between mb-16">
  <div>
    <div class="page-eyebrow">Developer surface / V1</div>
    <h1 class="page-headline">Authority has an API boundary.</h1>
    <p class="page-lead">The agent receives a proposal endpoint. Only an authenticated human can call the approval endpoint. PayPal credentials never cross this surface.</p>
  </div>
  <div style="flex-shrink:0;background:var(--acc-light);border:1px solid #93c5fd;
    border-radius:var(--radius);padding:10px 14px;font-size:12px">
    <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;
      color:var(--acc-dark);margin-bottom:3px">Agent key scoped</div>
    <div style="color:var(--acc-dark)">capability: propose_only</div>
  </div>
</div>
<div class="split" style="gap:20px">
  <div>
    <div class="card" style="padding:0;overflow:hidden">
      {ep_rows}
    </div>
    <div class="card mt-12">
      <div class="card-title">Example request</div>
      <pre>POST /v1/purchase-intents
Authorization: Bearer &lt;agent-api-key&gt;

{esc(example_body)}</pre>
    </div>
  </div>
  <div>
    <div class="card" style="height:100%">
      <div class="card-title">The contract</div>
      <h3 style="font-size:20px;font-weight:800;margin-bottom:12px;line-height:1.2">
        Recommendation is not authorization.</h3>
      <p style="font-size:13px;color:var(--fg2);margin-bottom:16px">
        Every request arrives as a governed purchase intent. The middleware resolves
        facts from registered records, evaluates the active policy version, and returns
        a decision that downstream payment code cannot override.</p>
      <div style="display:flex;flex-direction:column;gap:8px;margin-bottom:16px">
        <div style="font-size:13px"><span style="color:var(--acc);font-weight:700">01</span>
          &nbsp;Agent calls <code>POST /v1/purchase-intents</code></div>
        <div style="font-size:13px"><span style="color:var(--acc);font-weight:700">02</span>
          &nbsp;Policy returns <code>ALLOW</code>, <code>APPROVAL_REQUIRED</code>, or <code>BLOCK</code></div>
        <div style="font-size:13px"><span style="color:var(--acc);font-weight:700">03</span>
          &nbsp;Only middleware can create / capture the PayPal order</div>
      </div>
      <div style="background:#fff7ed;border:1px solid #fed7aa;border-radius:var(--radius);
        padding:12px 14px">
        <div style="font-size:11px;font-weight:700;color:#c2410c;margin-bottom:5px">
          ⚠ Why this matters</div>
        <div style="font-size:12px;color:#9a3412">
          A manipulated agent may still recommend the $3 fee; it cannot turn that
          recommendation into an order.</div>
      </div>
    </div>
  </div>
</div>"""

        return HTMLResponse(page("API Surface", body, "api", token))

    return app
