"""HTTP layer. Business logic stays in TrustService; this file only authenticates and renders.

Two identities: an AI agent (bearer API key, can only propose / poll its own intents) and a
human (login session cookie, can approve, decline, revoke and read audit trails).
"""
import hashlib
import hmac
import html
import json
import secrets
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .landing import landing_page
from .service import ApprovalError, AuthError
from .texts import AGENT_CANNOT_APPROVE, EVENT_TEXT, REASON_TEXT, reason_text  # noqa: F401

esc = html.escape
COOKIE = "tg_session"

CSS = """:root{--bg:#fafafa;--fg:#111827;--mut:#6b7280;--line:#e5e7eb;--card:#fff;--acc:#4f46e5;--ok:#047857;--warn:#b45309;--bad:#b91c1c}
@media(prefers-color-scheme:dark){:root{--bg:#0b0d12;--fg:#e5e7eb;--mut:#9ca3af;--line:#1f2430;--card:#12151c;--acc:#818cf8;--ok:#34d399;--warn:#fbbf24;--bad:#f87171}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:760px;margin:0 auto;padding:32px 20px}h1{font-size:22px;margin:0 0 4px}.sub{color:var(--mut);margin:0 0 24px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:20px;margin-bottom:16px}
dl{display:grid;grid-template-columns:150px 1fr;gap:8px 16px;margin:0}dt{color:var(--mut)}dd{margin:0;font-variant-numeric:tabular-nums}
.amt{font-size:28px;font-weight:600;font-variant-numeric:tabular-nums;margin:0 0 12px}
.chip{display:inline-block;padding:1px 10px;border-radius:99px;font-size:12px;font-weight:600;border:1px solid currentColor}
.ALLOW,.CAPTURED,.APPROVED,.NOT_REQUIRED,.valid{color:var(--ok)}.APPROVAL_REQUIRED,.HELD_FOR_APPROVAL,.PENDING{color:var(--warn)}
.BLOCK,.BLOCKED,.DECLINED,.EXPIRED,.REFUSED,.PAYMENT_MISMATCH,.PAYMENT_FAILED,.invalid{color:var(--bad)}
button,.btn{font:inherit;padding:9px 16px;border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer;text-decoration:none}
button.primary{background:var(--acc);border-color:var(--acc);color:#fff}.row{display:flex;gap:10px;margin-top:16px}
table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:8px 6px;border-bottom:1px solid var(--line);font-size:14px;vertical-align:top}
code{font:12px ui-monospace,Menlo,Consolas,monospace;word-break:break-all}a{color:var(--acc)}
input{font:inherit;padding:8px;border:1px solid var(--line);border-radius:8px;background:var(--card);color:var(--fg);width:100%;margin:4px 0 12px}
@keyframes rise{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.chk{list-style:none;padding:0;margin:8px 0 0}.chk li{display:flex;gap:10px;padding:7px 0;border-bottom:1px solid var(--line);opacity:0;animation:rise .4s ease forwards;animation-delay:calc(var(--i)*.28s)}
.chk .ic{width:18px;font-weight:700}.chk .pass .ic,.chk li.pass .ic{color:var(--ok)}.chk li.fail .ic{color:var(--bad)}.chk li.review .ic{color:var(--warn)}
.flow{display:flex;align-items:center;gap:8px;margin:4px 0 16px}.node{flex:0 0 auto;min-width:118px;text-align:center;padding:10px 12px;border:1.5px solid var(--acc);border-radius:10px;font-weight:600;background:var(--card)}
.node small{display:block;font-weight:400;color:var(--mut);font-size:12px}.node.ok{border-color:var(--ok)}.node.warn{border-color:var(--warn)}.node.bad{border-color:var(--bad)}.node.dim{border-color:var(--line);color:var(--mut)}
.lane{flex:1;height:2px;background:var(--acc)}.lane.ok{background:var(--ok)}.lane.warn{background:repeating-linear-gradient(90deg,var(--warn) 0 6px,transparent 6px 12px)}.lane.off{background:var(--line)}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}.chk li{opacity:1}}"""


class PurchaseIntentIn(BaseModel):
    """The agent proposes references only. extra=forbid rejects amount, payee, user_id, approver..."""
    model_config = ConfigDict(extra="forbid")
    merchant_reference: str = Field(max_length=100)
    product_reference: str = Field(max_length=100)
    quantity: StrictInt = 1
    source_url: Optional[str] = Field(default=None, max_length=500)
    request_id: str = Field(min_length=1, max_length=64)


ICON = {"pass": "&#10003;", "fail": "&#10005;", "review": "!"}


def checks_html(title, items):
    if not items:
        return ""
    lis = "".join(f'<li class="{esc(s)}" style="--i:{i}"><span class="ic">{ICON[s]}</span>{esc(n)}</li>'
                  for i, (n, s) in enumerate(items))
    return f'<h3 style="margin:18px 0 0">{esc(title)}</h3><ul class="chk">{lis}</ul>'


def flow_html(v):
    """Where authority actually went, derived from the intent's real state."""
    if v["order_id"]:
        mid, mcls, lane, end, ecls = "Authorized", "ok", "ok", "Order captured", "ok"
    elif v["state"] == "HELD_FOR_APPROVAL":
        mid, mcls, lane, end, ecls = "Held for approval", "warn", "warn", "Not reached yet", "dim"
    elif v["decision"] == "BLOCK":
        mid, mcls, lane, end, ecls = "Blocked", "bad", "off", "Not reached", "dim"
    else:
        mid, mcls, lane, end, ecls = v["state"].replace("_", " ").title(), "bad", "off", "Not reached", "dim"
    return (f'<div class="flow"><div class="node">AI agent<small>proposed</small></div><div class="lane ok"></div>'
            f'<div class="node {mcls}">TrustGate<small>{esc(mid)}</small></div><div class="lane {lane}"></div>'
            f'<div class="node {ecls}">PayPal<small>{esc(end)}</small></div></div>')


def chip(value):
    return f'<span class="chip {esc(str(value))}">{esc(str(value))}</span>'


def layout(title, body, nav=""):
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1"><title>{esc(title)}</title><style>{CSS}</style>'
            f"</head><body><main>{nav}{body}</main></body></html>")


def create_app(svc, users, csrf_secret, admin_key=None, cookie_secure=False, hardening=None,
               playground=None, repo_url=None):
    """users: {username: (user_id, password)}. Set cookie_secure=True when served over HTTPS."""
    app = FastAPI(title="TrustGate", docs_url=None, redoc_url=None)
    if hardening is not None:  # rate limits, security headers, request logs (see hardening.py)
        from .hardening import Hardening
        app.add_middleware(Hardening, **hardening)
    auth = svc.auth

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

    def nav(token):
        return (f'<form method="post" action="/logout" class="row" style="justify-content:flex-end;margin:0 0 12px">'
                f'<a class="btn" href="/console">Console</a><input type="hidden" name="csrf" value="{csrf_for(token)}">'
                f"<button>Log out</button></form>")

    # ---- agent API ------------------------------------------------------
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
    def get_intent(intent_id: str, request: Request):
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

    # ---- human decision endpoints -----------------------------------------
    @app.post("/v1/approvals/{intent_id}")
    async def decide(intent_id: str, request: Request):
        if request.headers.get("authorization"):
            return err(AGENT_CANNOT_APPROVE, 403)
        token, user = human(request)
        if not user:
            return err("human login required", 401)
        form = await request.form()
        if not csrf_ok(token, form.get("csrf")):
            return err("bad CSRF token", 403)
        choice = form.get("decision")  # nothing else in the body is read: approver comes from the session
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
            return err(code, 403 if code == "NOT_DELEGATING_USER" else 404 if code == "UNKNOWN_INTENT" else 409)
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

    # ---- human pages ------------------------------------------------------
    @app.get("/login", response_class=HTMLResponse)
    def login_form():
        return layout("Sign in", '<h1>Sign in</h1><p class="sub">Approve or decline purchases your agent proposes.</p>'
                      '<form class="card" method="post" action="/login"><label>Username<input name="username" '
                      'autocomplete="username"></label><label>Password<input name="password" type="password" '
                      'autocomplete="current-password"></label><button class="primary">Sign in</button></form>')

    @app.post("/login")
    async def login(request: Request):
        form = await request.form()
        entry = users.get(form.get("username") or "")
        if not entry or not hmac.compare_digest(entry[1], form.get("password") or ""):
            return HTMLResponse(layout("Sign in", '<h1>Sign in</h1><p class="sub">Wrong username or password.</p>'
                                       '<a class="btn" href="/login">Try again</a>'), status_code=401)
        resp = RedirectResponse("/console", status_code=303)
        resp.set_cookie(COOKIE, auth.issue_session(entry[0]), httponly=True, samesite="strict", secure=cookie_secure)
        return resp

    @app.post("/logout")
    async def logout(request: Request):
        token, user = human(request)
        resp = RedirectResponse("/login", status_code=303)
        form = await request.form()
        if user and csrf_ok(token, form.get("csrf")):
            resp.delete_cookie(COOKIE)
        return resp

    @app.get("/", response_class=HTMLResponse)
    def landing():
        return landing_page(svc.stats())

    @app.get("/console", response_class=HTMLResponse)
    def dashboard(request: Request):
        token, user = human(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        rows = ""
        for v in svc.intents_for_user(user):
            f = v["facts"]
            what = f"{esc(f['product'])}<br><code>{esc(f['amount'])} {esc(f['currency'])}</code>" if f else "-"
            iid = esc(v["intent_id"])
            pending = v["state"] == "HELD_FOR_APPROVAL" and v["approval_status"] == "PENDING"
            # Only a still-pending request links to the approve page; finished or blocked ones never look approvable.
            action = (f'<a class="btn primary" href="/approvals/{iid}">Review and approve</a>' if pending else
                      f'<a href="/intents/{iid}">View {"security decision" if v["state"] == "BLOCKED" else "transaction"}</a>')
            rows += (f'<tr><td><code>{iid}</code></td>'
                     f'<td>{what}</td><td>{chip(v["decision"])}</td><td>{chip(v["approval_status"])}</td>'
                     f'<td>{chip(v["state"])}</td><td>{action}</td></tr>')
        body = (f'{nav(token)}<h1>Purchases proposed by your agent</h1><p class="sub">Only you can approve spending. '
                f'The agent can only propose.</p><div class="card" style="overflow-x:auto"><table><tr><th>Intent</th><th>Purchase</th>'
                f'<th>Policy decision</th><th>Approval</th><th>Payment</th><th></th></tr>'
                f'{rows or "<tr><td colspan=6>No proposals yet.</td></tr>"}</table></div>'
                f'<form method="post" action="/policy/revoke"><input type="hidden" name="csrf" value="{csrf_for(token)}">'
                f'<button>Revoke spending policy (kill switch)</button></form>')
        return layout("Dashboard", body)

    @app.get("/approvals/{intent_id}", response_class=HTMLResponse)
    def approval_page(intent_id: str, request: Request):
        token, user = human(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        try:
            v = svc.intent_for_user(user, intent_id)
        except ApprovalError:
            return HTMLResponse(layout("Not found", "<h1>Not found</h1>"), status_code=404)
        f, held = v["facts"], v["state"] == "HELD_FOR_APPROVAL"
        if f:
            left = int((v["expires_at"] - svc.clock()).total_seconds()) if v["expires_at"] else None
            expiry = (f"{left // 60:02d}:{left % 60:02d} remaining" if left and left > 0 else "expired") if held else "-"
            if v["decision"] == "APPROVAL_REQUIRED":
                rule = f"Approval required above {v['auto_approve_up_to']} {f['currency']}"
            elif v["decision"] == "ALLOW":
                rule = f"Automatic up to {v['auto_approve_up_to']} {f['currency']}"
            else:
                rule = "Blocked by policy"
            rows = [("Merchant", f["merchant"]), ("Product", f["product"]), ("Payee", f["payee_id"]),
                    ("Policy rule", rule), ("Policy version", v["policy_version"])]
            if held:
                rows.append(("Expires", f'<span id="left" data-left="{max(left or 0, 0)}">{esc(expiry)}</span>'))
            reasons = "".join(f'<li>{esc(reason_text(r))}<br><code style="color:var(--mut)">{esc(r)}</code></li>' for r in v["reasons"])
            detail = (f'<div class="amt">{esc(f["amount"])} {esc(f["currency"])}</div><dl>'
                      + "".join(f"<dt>{esc(k)}</dt><dd>{val if k == 'Expires' else esc(str(val))}</dd>" for k, val in rows)
                      + f'<dt>Why</dt><dd><ul style="margin:0;padding-left:18px">{reasons}</ul></dd></dl>')
        else:
            detail = "<p>No trusted purchase details (the request was refused before resolution).</p>"
        signals = "".join(f"<li><code>{esc(fl['source'])}</code>: {esc(fl['excerpt'])}</li>" for fl in v["flags"])
        if signals:
            detail += f"<h3>Context signals (untrusted page content, shown as text only)</h3><ul>{signals}</ul>"
        actions = ""
        if held:
            c = csrf_for(token)
            actions = (f'<div class="row"><form method="post" action="/v1/approvals/{esc(intent_id)}">'
                       f'<input type="hidden" name="csrf" value="{c}"><input type="hidden" name="decision" value="DECLINE">'
                       f'<button>Decline</button></form><form method="post" action="/v1/approvals/{esc(intent_id)}">'
                       f'<input type="hidden" name="csrf" value="{c}"><input type="hidden" name="decision" value="APPROVE">'
                       f'<button class="primary">Approve purchase</button></form></div>')
        none = "Not created" if not v["order_id"] else None
        status = (f'<div class="row" style="margin-top:0">{chip(v["decision"])}{chip(v["approval_status"])}{chip(v["state"])}</div>'
                  f'{flow_html(v)}{checks_html("Policy evaluation", v["checks"])}'
                  f'{checks_html("Approval bound to these verified facts", [(n, "pass") for n in v["binding_checks"]])}'
                  f'<dl style="margin-top:12px"><dt>PayPal order</dt><dd><code>{esc(str(v["order_id"] or "Not created"))}</code></dd>'
                  f'<dt>Capture ID</dt><dd><code>{esc(str(v["capture_id"] or "Not created"))}</code></dd></dl>'
                  f'<p><a href="/intents/{esc(intent_id)}">View audit trail</a></p>')
        title = ("Purchase awaiting approval" if held else
                 "Payment blocked before PayPal was called" if v["state"] == "BLOCKED" else
                 "Transaction complete" if v["state"] == "CAPTURED" else "Purchase")
        countdown = ("<script>(()=>{const e=document.getElementById('left');if(!e)return;let n=+e.dataset.left;"
                     "const t=()=>{e.textContent=n>0?String(Math.floor(n/60)).padStart(2,'0')+':'+String(n%60).padStart(2,'0')+' remaining':'expired';"
                     "if(n>0)n--;else clearInterval(i)};const i=setInterval(t,1000);t()})()</script>") if held else ""
        return layout(title, f'{nav(token)}<h1>{title}</h1><p class="sub"><code>{esc(intent_id)}</code></p>'
                      f'<div class="card">{detail}{actions}</div><div class="card">{status}</div>{countdown}')

    @app.get("/intents/{intent_id}", response_class=HTMLResponse)
    def audit_page(intent_id: str, request: Request):
        token, user = human(request)
        if not user:
            return RedirectResponse("/login", status_code=303)
        try:
            svc.intent_for_user(user, intent_id)
        except ApprovalError:
            return HTMLResponse(layout("Not found", "<h1>Not found</h1>"), status_code=404)
        events = svc.get_audit(intent_id)
        rows = "".join(
            f"<tr><td><code>{esc(e['ts'][11:19])}</code></td><td>{esc(EVENT_TEXT.get(e['event'], e['event']))}"
            f"<br><code>{esc(e['event'])}</code></td>"
            f"<td><details><summary>data</summary><code>{esc(json.dumps(e['data'], default=str))}</code></details></td></tr>"
            for e in events)
        v = svc.intent_for_user(user, intent_id)
        note = ("" if v["order_id"] else '<p class="sub" style="margin:12px 0 0">No PayPal order exists for this '
                "intent, so the PayPal adapter was never invoked for it.</p>")
        ok = svc.audit.verify()
        body = (f'{nav(token)}<h1>Audit trail</h1><p class="sub"><code>{esc(intent_id)}</code> &middot; hash chain '
                f'<span class="chip {"valid" if ok else "invalid"}">{"valid" if ok else "INVALID"}</span></p>'
                f'<div class="card" style="overflow-x:auto"><table><tr><th>Time (UTC)</th><th>Event</th><th></th></tr>{rows}</table>{note}</div>'
                f'<p><a href="/approvals/{esc(intent_id)}">Back to decision</a></p>')
        return layout("Audit trail", body)

    @app.get("/how", response_class=HTMLResponse)
    def how():
        from .how_page import how_page
        return how_page(repo_url)

    if playground is not None:  # public, sign-in-free interactive demo (isolated per visitor)
        from . import playground as _pg
        from .playground_ui import page_html
        _pg.register(app, playground, PurchaseIntentIn, cookie_secure, page_html)

    @app.get("/favicon.ico")
    def favicon():
        from fastapi.responses import Response
        return Response(status_code=204)

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/readyz")
    def readyz():
        ok = svc.audit.verify()
        return JSONResponse({"ok": ok, "audit_chain_valid": ok}, status_code=200 if ok else 503)

    return app
