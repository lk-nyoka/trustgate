"""HTTP layer. Business logic stays in TrustService; this file only authenticates and renders.

Two identities:
  - AI agent  (bearer API key)  → can only propose / poll its own intents
  - Human     (session cookie)  → can approve, decline, revoke, read audit trails
"""
import hashlib
import hmac
import html
import json
import re
import contextvars
import secrets
import uuid
from typing import Optional

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .landing import landing_page
from .service import ApprovalError, AuthError, IdempotencyConflict
from .policy_author import DraftError, ScriptedDrafter, describe_changes, public_json, validate_draft
from .workspaces import DemoBusy, RateLimited, client_ip
from .ui_shell import (CSS, EVENT_LABELS, BLOCK_LABELS, chip, checks_html,
                       flow_html, fmt_expiry, layout, reason_label, esc)

COOKIE = "tm_session"

AUTHORING_PANEL = """
<div class="card" style="margin-top:16px">
  <div class="card-title">Write your policy in plain language</div>
  <p style="font-size:12px;color:var(--fg3);margin-bottom:8px">__MODE__ proposes a draft. Deterministic code validates it
    against the trusted registry and hard caps. Only you can activate it, and nothing changes until you do.</p>
  <textarea id="pa-text" rows="3" maxlength="600" style="width:100%;font:inherit;padding:8px 10px;border:1px solid var(--line);border-radius:var(--radius)"
    placeholder="Let the agent book flights from approved airlines under $500. Ask me before spending more than $250. Never pay unrelated activation fees."></textarea>
  <div class="row" style="margin-top:8px"><button type="button" class="btn btn-sm" id="pa-draft">Draft policy</button>
    <span id="pa-msg" style="font-size:12px;color:var(--fg3)"></span></div>
  <div id="pa-out" style="display:none;margin-top:12px;border:1px dashed var(--line);border-radius:var(--radius);padding:12px">
    <div id="pa-label" style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:var(--warn);margin-bottom:8px"></div>
    <table style="width:100%;font-size:12px;border-collapse:collapse" id="pa-table"></table>
    <p style="font-size:11px;color:var(--fg3);margin:8px 0">Activating creates a new policy version. Requests held under the old version can no longer be approved.</p>
    <button type="button" class="btn btn-primary btn-sm" id="pa-activate">Confirm and activate policy</button>
  </div>
</div>
<script>
(function(){
  const csrf="__CSRF__", label="__LABEL__";
  let draftId=null;
  const msg=(t,bad)=>{const m=document.getElementById('pa-msg');m.textContent=t||'';m.style.color=bad?'var(--bad)':'var(--fg3)'};
  const fmt=v=>typeof v==='object'?JSON.stringify(v):String(v);
  async function post(url,body){const r=await fetch(url,{method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const d=await r.json();if(!r.ok)throw new Error(d.detail||'Request failed');return d}
  document.getElementById('pa-draft').addEventListener('click',async()=>{
    const text=document.getElementById('pa-text').value.trim(); if(!text){msg('Describe your policy first.',true);return}
    msg('Drafting...');document.getElementById('pa-out').style.display='none';
    try{const d=await post('/api/policy/draft',{text,csrf});draftId=d.draft_id;
      document.getElementById('pa-label').textContent=label+' · review before activation';
      const t=document.getElementById('pa-table');t.textContent='';
      const head=t.insertRow();['Field','Current','Draft'].forEach(h=>{const c=document.createElement('th');c.textContent=h;c.style.textAlign='left';head.appendChild(c)});
      if(!d.changes.length){const r=t.insertRow();const c=r.insertCell();c.colSpan=3;c.textContent='No change from the current policy.'}
      d.changes.forEach(ch=>{const r=t.insertRow();[ch.field,fmt(ch.from),fmt(ch.to)].forEach(x=>{const c=r.insertCell();c.textContent=x;c.style.padding='3px 6px 3px 0'})});
      document.getElementById('pa-out').style.display='block';msg('')}
    catch(e){msg(e.message,true)}});
  document.getElementById('pa-activate').addEventListener('click',async()=>{
    try{await post('/api/policy/activate',{draft_id:draftId,csrf});location.reload()}catch(e){msg(e.message,true)}});
})();
</script>
"""



class PurchaseIntentIn(BaseModel):
    """Agent proposes references only. extra=forbid rejects amount, payee, user_id, approver."""
    model_config = ConfigDict(extra="forbid")
    merchant_reference: str = Field(max_length=100)
    product_reference:  str = Field(max_length=100)
    quantity:           StrictInt = 1
    source_url:         Optional[str] = Field(default=None, max_length=500)
    request_id:         str = Field(min_length=1, max_length=64)


class AssistantProposalIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_reference: str = Field(max_length=100)
    csrf: str = Field(min_length=1, max_length=128)


class AssistantChatIn(BaseModel):
  model_config = ConfigDict(extra="forbid")
  message: str = Field(min_length=1, max_length=500)
  csrf: str = Field(min_length=1, max_length=128)


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
        "PAYMENT_CAPTURED": "ok", "APPROVED": "ok", "POLICY_CONFIRMED": "ok", "POLICY_RESUMED": "ok",
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


def _login_page(error_message=None, status_code=200):
    error = (f'<div class="login-error" role="alert">{esc(error_message)}</div>'
             if error_message else "")
    return HTMLResponse(f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#f4f5f7">
<link rel="stylesheet" href="/static/glass/glass.css">
<title>Sign in · TrustGate</title>
<style>
{CSS}
:root{{--ease-out:cubic-bezier(.23,1,.32,1)}}
body{{min-height:100%;background:var(--bg);padding:24px}}
.login-stage{{width:min(1180px,100%);min-height:min(760px,calc(100svh - 48px));margin:0 auto;
  display:grid;grid-template-columns:minmax(0,1.08fr) minmax(390px,.92fr);overflow:hidden;
  background:var(--surface);border:1px solid var(--line);border-radius:12px;
  box-shadow:0 24px 70px rgba(15,17,23,.12)}}
.login-story{{position:relative;isolation:isolate;overflow:hidden;display:flex;flex-direction:column;
  justify-content:space-between;padding:38px 42px 30px;background:var(--sidebar);color:#f8fafc}}
.login-story::before{{content:"";position:absolute;inset:0;z-index:-1;opacity:.16;
  background-image:linear-gradient(rgba(226,232,240,.14) 1px,transparent 1px),
    linear-gradient(90deg,rgba(226,232,240,.14) 1px,transparent 1px);background-size:36px 36px;
  mask-image:linear-gradient(135deg,#000 0%,transparent 78%)}}
.login-story::after{{content:"";position:absolute;z-index:-1;width:280px;height:280px;right:-150px;bottom:18%;
  border:1px solid rgba(37,99,235,.36);border-radius:50%;box-shadow:0 0 0 34px rgba(37,99,235,.04),0 0 0 68px rgba(37,99,235,.035)}}
.login-brand{{display:flex;align-items:center;gap:11px;animation:login-enter 620ms var(--ease-out) both}}
.login-mark{{display:grid;place-items:center;width:36px;height:36px;border-radius:8px;background:var(--acc);
  color:#fff;font-size:17px;font-weight:800}}
.login-brand-name{{font-size:14px;font-weight:700;color:#f8fafc}}
.login-brand-sub{{margin-top:1px;color:#94a3b8;font-size:10px;letter-spacing:.08em;text-transform:uppercase}}
.story-copy{{max-width:570px;margin:56px 0 30px;animation:login-enter 680ms var(--ease-out) 70ms both}}
.story-kicker{{display:flex;align-items:center;gap:8px;margin-bottom:16px;color:#93c5fd;
  font-size:10px;font-weight:700;letter-spacing:.14em;text-transform:uppercase}}
.story-kicker::before{{content:"";width:18px;height:2px;background:var(--acc)}}
.story-copy h1{{font-family:Georgia,"Times New Roman",serif;font-size:clamp(34px,4.2vw,56px);font-weight:400;
  line-height:1.04;margin:0 0 15px;color:#f8fafc}}
.story-copy h1 span{{color:#60a5fa}}
.story-copy p{{max-width:440px;color:#b6c0d0;font-size:13px;line-height:1.75}}
.story-tagline{{margin:15px 0 5px;color:#f8fafc;font-size:12px;font-weight:700}}
.story-value{{color:#94a3b8;font-size:11px;line-height:1.6}}
.flow-visual{{max-width:580px;padding:17px 18px 14px;border:1px solid rgba(148,163,184,.2);
  border-radius:8px;background:rgba(255,255,255,.035);animation:login-enter 680ms var(--ease-out) 140ms both}}
.flow-caption{{display:flex;justify-content:space-between;align-items:center;gap:12px;color:#94a3b8;
  font-size:9px;font-weight:700;letter-spacing:.1em;text-transform:uppercase}}
.flow-health{{display:flex;align-items:center;gap:6px;color:#cbd5e1;letter-spacing:.02em;text-transform:none}}
.flow-health i{{width:6px;height:6px;border-radius:50%;background:#34d399;box-shadow:0 0 10px rgba(52,211,153,.55)}}
.flow-route{{position:relative;height:2px;margin:20px 9% 15px;background:rgba(148,163,184,.22)}}
.flow-route::before{{content:"";position:absolute;inset:0;background:linear-gradient(90deg,transparent,rgba(37,99,235,.55),transparent)}}
.flow-packet{{position:absolute;top:-1px;left:0;width:15%;height:4px;border-radius:8px;background:#60a5fa;
  box-shadow:0 0 12px rgba(96,165,250,.9);animation:flow-pass 4.2s ease-in-out infinite}}
.flow-nodes{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;text-align:center}}
.flow-node{{position:relative;display:grid;justify-items:center;gap:3px;color:#e2e8f0}}
.login-story .flow-node{{min-width:0;flex:none;padding:0;border:0;border-radius:0;background:transparent;
  color:#e2e8f0;font-size:inherit;font-weight:inherit;text-align:center}}
.flow-node b{{font-size:11px}}
.flow-node small{{color:#94a3b8;font-size:9px}}
.flow-node-index{{display:grid;place-items:center;width:25px;height:25px;margin-bottom:3px;border:1px solid #475569;
  border-radius:7px;color:#cbd5e1;font:10px ui-monospace,Consolas,monospace;background:#171b24}}
.flow-node-gate .flow-node-index{{color:#bfdbfe;border-color:#2563eb;background:#17233b}}
.story-foot{{display:flex;justify-content:space-between;gap:14px;margin-top:20px;color:#7f8da3;font-size:10px}}
.login-main{{display:flex;align-items:center;justify-content:center;padding:46px 48px;background:var(--surface)}}
.login-form-wrap{{width:min(360px,100%);animation:login-enter 680ms var(--ease-out) 120ms both}}
.form-eyebrow{{margin-bottom:10px;color:var(--acc-dark);font-size:10px;font-weight:700;letter-spacing:.13em;text-transform:uppercase}}
.login-form-wrap h2{{font-family:Georgia,"Times New Roman",serif;font-size:34px;font-weight:400;line-height:1.1;margin:0 0 9px}}
.login-intro{{margin-bottom:28px;color:var(--fg2);font-size:13px;line-height:1.65}}
.login-label{{display:block;margin:0 0 17px}}
.login-label-text{{display:block;margin-bottom:6px;color:var(--fg2);font-size:11px;font-weight:650}}
.login-input{{height:44px;padding:0 12px;border-color:#d9dee7;border-radius:6px;font-size:16px;transition:border-color 160ms ease,box-shadow 160ms ease}}
.login-input:focus{{outline:none;border-color:var(--acc);box-shadow:0 0 0 3px rgba(37,99,235,.13)}}
.login-submit{{width:100%;min-height:45px;justify-content:center;margin-top:4px;border-radius:6px;font-size:13px;font-weight:650}}
.login-submit span{{transition:transform 180ms var(--ease-out)}}
.login-error{{margin:0 0 18px;padding:10px 12px;border:1px solid #fecaca;border-radius:6px;background:var(--bad-light);
  color:var(--bad-dark);font-size:12px}}
.login-security{{display:flex;align-items:flex-start;gap:8px;margin-top:23px;padding-top:17px;border-top:1px solid var(--line2);
  color:var(--fg3);font-size:10px;line-height:1.6}}
.login-security b{{color:var(--fg2);font-weight:650}}
.login-security-mark{{display:grid;place-items:center;width:17px;height:17px;flex:0 0 17px;border:1px solid #a7f3d0;
  border-radius:50%;color:var(--ok);font-size:10px}}
@keyframes login-enter{{from{{opacity:0;transform:translateY(12px)}}to{{opacity:1;transform:translateY(0)}}}}
@keyframes flow-pass{{0%,12%{{opacity:0;transform:translateX(0)}}24%{{opacity:1}}80%{{opacity:.8;transform:translateX(540%)}}92%,100%{{opacity:0;transform:translateX(540%)}}}}
@media(hover:hover) and (pointer:fine){{.login-submit:hover span{{transform:translateX(3px)}}}}
@media(max-width:860px){{body{{padding:14px}}.login-stage{{min-height:calc(100svh - 28px);grid-template-columns:minmax(0,1fr) minmax(340px,.9fr)}}
  .login-story{{padding:30px 28px 24px}}.login-main{{padding:34px 30px}}}}
@media(max-width:680px){{body{{padding:0}}.login-stage{{width:100%;min-height:100svh;grid-template-columns:1fr;border:0;border-radius:0;box-shadow:none}}
  .login-story{{min-height:440px;padding:26px 23px 20px}}.story-copy{{margin:42px 0 22px}}
  .story-copy h1{{font-size:40px}}.flow-visual{{padding:14px 12px 12px}}.flow-caption{{font-size:8px}}
  .login-main{{padding:38px 24px 44px}}.login-form-wrap h2{{font-size:31px}}}}
@media(max-width:390px){{.login-story{{min-height:420px;padding-inline:18px}}.story-copy h1{{font-size:35px}}
  .story-foot{{font-size:9px}}.login-main{{padding-inline:19px}}}}
@media(prefers-reduced-motion:reduce){{.login-brand,.story-copy,.flow-visual,.login-form-wrap{{animation:login-fade 180ms ease-out both}}
  .flow-packet{{animation:none;opacity:.9;transform:translateX(270%)}}}}
@keyframes login-fade{{from{{opacity:0}}to{{opacity:1}}}}
</style></head><body>
<main class="login-stage">
  <section class="login-story" aria-label="TrustGate purchase authorization flow">
    <div class="login-brand"><span class="login-mark" aria-hidden="true">T</span><div>
      <div class="login-brand-name">TrustGate</div><div class="login-brand-sub">Trust middleware</div>
    </div></div>
    <div class="story-copy">
      <div class="story-kicker">Trust infrastructure for autonomous commerce</div>
      <h1>AI may recommend.<br><span>Only policy may authorize.</span></h1>
      <div class="story-tagline">Let AI find it. Let policy decide. Let PayPal pay.</div>
      <p class="story-value">TrustGate governs every AI-proposed purchase before PayPal execution.</p>
    </div>
    <div>
      <div class="flow-visual" data-glass="1" data-glass-tint="0.03">
        <div class="flow-caption"><span>Purchase authorization path</span><span class="flow-health"><i></i>Policy active</span></div>
        <div class="flow-route" aria-hidden="true"><span class="flow-packet"></span></div>
        <div class="flow-nodes">
          <div class="flow-node"><span class="flow-node-index">01</span><b>AI agent</b><small>proposes</small></div>
          <div class="flow-node flow-node-gate"><span class="flow-node-index">02</span><b>TrustGate</b><small>authorizes</small></div>
          <div class="flow-node"><span class="flow-node-index">03</span><b>PayPal</b><small>only if allowed</small></div>
        </div>
      </div>
      <div class="story-foot"><span>DETERMINISTIC POLICY</span><span>HUMAN APPROVAL</span><span>AUDIT EVIDENCE</span></div>
    </div>
  </section>
  <section class="login-main" aria-labelledby="login-heading">
    <div class="login-form-wrap">
      <div class="form-eyebrow">Policy owner access</div>
      <h2 id="login-heading">Welcome back</h2>
      <p class="login-intro">Sign in to review purchase proposals and manage your spending policy.</p>
      {error}
      <form method="post" action="/login">
        <label class="login-label"><span class="login-label-text">Username</span>
          <input class="login-input" name="username" autocomplete="username" required></label>
        <label class="login-label"><span class="login-label-text">Password</span>
          <input class="login-input" name="password" type="password" autocomplete="current-password" required></label>
        <button class="btn btn-primary login-submit" type="submit">Sign in <span aria-hidden="true">→</span></button>
      </form>
      <div class="login-security"><span class="login-security-mark" aria-hidden="true">✓</span>
        <span><b>Payment authority stays protected.</b> An agent can propose a purchase, but only policy and authenticated approval can authorize it.</span>
      </div>
    </div>
  </section>
</main><script src="/static/glass/glass-init.js" defer></script></body></html>""", status_code=status_code)


# ── App factory ───────────────────────────────────────────────────────────────

def create_app(svc, users, csrf_secret, admin_key=None, cookie_secure=False,
               paypal_mode="fake", demo_agent_key=None, assistant_runner=None, workspaces=None,
               policy_drafter=None, trusted_proxy_hops=0):
    """users: {username: (user_id, password)}.

    workspaces: optional DemoWorkspaces. When given (hosted review mode) every login gets its own
    isolated TrustService and `svc` is ignored; all state below resolves per request."""
    app = FastAPI(title="TrustGate", docs_url=None, redoc_url=None)
    base_agent_key, base_runner = demo_agent_key, assistant_runner
    drafter = policy_drafter or ScriptedDrafter()
    current_ws = contextvars.ContextVar("trustgate_workspace", default=None)

    class _Live:
        """Forwards attribute access to the current request's workspace object."""
        def __init__(self, pick):
            self._pick = pick

        def __getattr__(self, name):
            return getattr(self._pick(), name)

    def _ws():
        ws = current_ws.get()
        if ws is None:
            raise AuthError("no review workspace for this request")
        return ws

    if workspaces is not None:
        svc = _Live(lambda: _ws().svc)
        auth = _Live(lambda: _ws().svc.auth)
    else:
        auth = svc.auth

    def agent_key_now():
        return _ws().agent_key if workspaces is not None else base_agent_key

    def runner_now():
        return _ws().assistant_runner if workspaces is not None else base_runner

    def adapter_label():
        return "PAYPAL SANDBOX" if paypal_mode == "sandbox" else "SIMULATED"

    def client_key(request):
        peer = request.client.host if request.client else None
        return client_ip(request.headers, peer, trusted_proxy_hops)

    BUSY_MESSAGE = ("Review demo is currently busy. Please try again in a few minutes. "
                    "No existing review sessions were removed.")

    def start_session(request, user_id):
        """Returns (token, ttl_seconds). In review mode each login is a fresh isolated workspace."""
        if workspaces is None:
            return auth.issue_session(user_id), auth.session_ttl_seconds
        ws = workspaces.create(client_key(request))
        current_ws.set(ws)
        return workspaces.issue_session(ws), ws.svc.auth.session_ttl_seconds

    def end_session(token):
        auth.revoke_session(token)
        if workspaces is not None and current_ws.get() is not None:
            workspaces.discard(current_ws.get())

    @app.middleware("http")
    async def bind_workspace(request: Request, call_next):
        if workspaces is not None:
            ws = None
            tok = request.cookies.get(COOKIE)
            if tok:
                ws = workspaces.by_token(tok)
            if ws is None:
                h = request.headers.get("authorization", "")
                if h.lower().startswith("bearer "):
                    ws = workspaces.by_agent_key(h[7:].strip())
            if ws is not None:
                workspaces.touch(ws)
            current_ws.set(ws)
        return await call_next(request)

    # CORS — allow the React dev server (port 5173) and same origin
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://localhost:8000",
                       "http://127.0.0.1:5173", "http://127.0.0.1:8000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def disable_private_response_caching(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith(("/api/", "/console", "/intents", "/approvals", "/audit")):
            response.headers["Cache-Control"] = "no-store, private, max-age=0"
        return response

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

    def page(title, body, active_nav, token, extra_head=""):
        stats = svc.stats()
        # append logout form link into the topbar via extra_head JS is messy —
        # instead we put a tiny logout link inside the sidebar user block
        logout_form = (f'<form method="post" action="/logout" id="logout-form">'
                       f'<input type="hidden" name="csrf" value="{csrf_for(token)}"></form>')
        body_with_logout = body + logout_form
        return layout(title, body_with_logout, active_nav=active_nav,
                      paypal_mode=paypal_mode, stats=stats, extra_head=extra_head)

    # ── Public routes ──────────────────────────────────────────────────────
    @app.get("/", response_class=HTMLResponse)
    def landing():
        return landing_page(svc.stats() if (workspaces is None or current_ws.get()) else {"captured": 0, "awaiting": 0, "blocked": 0})

    @app.get("/login", response_class=HTMLResponse)
    def login_form():
        return _login_page()

    @app.post("/login")
    async def login(request: Request):
        form = await request.form()
        entry = users.get(form.get("username") or "")
        if not entry or not hmac.compare_digest(entry[1], form.get("password") or ""):
          return _login_page("Wrong username or password.", status_code=401)
        try:
            session_token, ttl = start_session(request, entry[0])
        except RateLimited:
            return _login_page("Too many sign-ins. Wait a minute and try again.", status_code=429)
        except DemoBusy:
            return _login_page(BUSY_MESSAGE, status_code=503)
        resp = RedirectResponse("/console", status_code=303)
        resp.set_cookie(COOKIE, session_token,
                        httponly=True, samesite="strict", secure=cookie_secure,
                        max_age=ttl)
        return resp

    @app.post("/logout")
    async def logout(request: Request):
        token, user = human(request)
        resp = RedirectResponse("/login", status_code=303)
        form = await request.form()
        if user and csrf_ok(token, form.get("csrf")):
            end_session(token)
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
        try:
            token, ttl = start_session(request, entry[0])
        except RateLimited:
            return JSONResponse({"detail": "Too many sign-ins. Wait a minute and try again."}, status_code=429)
        except DemoBusy:
            return JSONResponse({"detail": BUSY_MESSAGE}, status_code=503, headers={"Retry-After": "120"})
        resp = JSONResponse({"ok": True, "csrf": csrf_for(token)})
        resp.set_cookie(COOKIE, token, httponly=True, samesite="lax",
                        secure=cookie_secure, max_age=ttl)
        return resp

    @app.post("/api/logout")
    async def api_logout(request: Request):
        token, user = human(request)
        if not user:
            return JSONResponse({"detail": "auth required"}, status_code=401)
        body = await request.json()
        if not csrf_ok(token, body.get("csrf")):
            return JSONResponse({"detail": "bad CSRF token"}, status_code=403)
        end_session(token)
        resp = JSONResponse({"ok": True})
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
            "adapter": adapter_label(),
            "review_workspace": workspaces is not None,
            "review_agent_key": agent_key_now() if workspaces is not None else None,
            "assistant_mode": runner_now().provider_name.lower() if runner_now() else "scripted",
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

    @app.get("/api/assistant/products")
    def assistant_products(request: Request, q: str = ""):
        _, user = human(request)
        if not user:
            return JSONResponse({"detail": "auth required"}, status_code=401)
        query = q.lower()
        fee_search = any(word in query for word in ("fee", "activation", "injected"))
        budget = None
        for marker in ("under", "below", "less than"):
            if marker in query:
                tail = query.split(marker, 1)[1]
                match = re.search(r"\$?\s*(\d+(?:\.\d+)?)", tail)
                if match:
                    budget = float(match.group(1))
                break
        refs = ("cpt-jnb-economy-180", "cpt-jnb-flex-320", "cpt-jnb-business-900",
                "cpt-jnb-eur-100", "activation-fee-3")
        products = []
        for product_ref in refs:
            product = svc.registry.product(product_ref)
            merchant = svc.registry.merchant(product.merchant_id) if product else None
            if not product or not merchant:
                continue
            if fee_search != (merchant.category != "travel"):
                continue
            if any(term in query for term in ("flight", "johannesburg", "cpt", "jnb")) and product.currency != "USD":
                continue
            if budget is not None and float(product.unit_amount) > budget:
                continue
            products.append({
                "merchant_reference": merchant.merchant_id,
                "merchant": merchant.name,
                "product_reference": product.product_id,
                "display_name": product.name,
                "display_amount": str(product.unit_amount),
                "currency": product.currency,
                "category": merchant.category,
            })
        return {"products": products}

    @app.get("/api/assistant/products/{product_reference}")
    def assistant_product_detail(product_reference: str, request: Request):
        _, user = human(request)
        if not user:
            return JSONResponse({"detail": "auth required"}, status_code=401)
        product = svc.registry.product(product_reference)
        if not product:
            return JSONResponse({"detail": "product not found"}, status_code=404)
        merchant = svc.registry.merchant(product.merchant_id)
        return {
            "merchant_reference": merchant.merchant_id,
            "merchant": merchant.name,
            "product_reference": product.product_id,
            "display_name": product.name,
            "display_amount": str(product.unit_amount),
            "currency": product.currency,
            "category": merchant.category,
            "registered_domain": merchant.domain,
        }

    @app.post("/api/assistant/propose")
    async def assistant_propose(body: AssistantProposalIn, request: Request):
        if request.headers.get("authorization"):
            return JSONResponse({"detail": "agent credentials are not accepted on the human demo surface"},
                                status_code=403)
        token, user = human(request)
        if not user:
            return JSONResponse({"detail": "auth required"}, status_code=401)
        if not csrf_ok(token, body.csrf):
            return JSONResponse({"detail": "bad CSRF token"}, status_code=403)
        if not agent_key_now():
            return JSONResponse({"detail": "demo agent is not configured"}, status_code=503)
        try:
            agent_user, _ = auth.agent(agent_key_now())
        except AuthError:
            return JSONResponse({"detail": "demo agent is not configured"}, status_code=503)
        if agent_user != user:
            return JSONResponse({"detail": "demo agent is not bound to this user"}, status_code=403)
        product = svc.registry.product(body.product_reference)
        if not product:
            return JSONResponse({"detail": "product not found"}, status_code=404)
        source_url = ("https://demo-airlines.test/injected-fee"
                      if product.product_id == "activation-fee-3"
                      else "https://demo-airlines.test/checkout")
        result = svc.propose_purchase(
            agent_key_now(),
            product.merchant_id,
            product.product_id,
            quantity=1,
            source_url=source_url,
            request_id="console-" + uuid.uuid4().hex,
        )
        result["approval_url"] = (f"/approvals/{result['intent_id']}"
                                  if result["state"] == "HELD_FOR_APPROVAL" else None)
        return jsonable_encoder(result)

    @app.post("/api/assistant/chat")
    def assistant_chat(body: AssistantChatIn, request: Request):
        token, user = human(request)
        if not user:
            return JSONResponse({"detail": "auth required"}, status_code=401)
        if not csrf_ok(token, body.csrf):
            return JSONResponse({"detail": "bad CSRF token"}, status_code=403)
        if not runner_now():
            return JSONResponse({"detail": "language model is not configured"}, status_code=503)
        if workspaces is not None and not workspaces.allow_chat(current_ws.get()):
            return JSONResponse({"detail": "review workspace chat limit reached; reset the workspace"}, status_code=429)
        try:
            agent_user, _ = auth.agent(agent_key_now())
        except AuthError:
            return JSONResponse({"detail": "demo agent is not configured"}, status_code=503)
        if agent_user != user:
            return JSONResponse({"detail": "demo agent is not bound to this user"}, status_code=403)
        try:
            return jsonable_encoder(runner_now().run(user, body.message))
        except Exception:
            return JSONResponse({"detail": "assistant request failed; no action was confirmed"}, status_code=502)

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

    @app.post("/api/policy/draft")
    async def api_policy_draft(request: Request):
        if request.headers.get("authorization"):
            return JSONResponse({"detail": "agent credentials cannot author policy"}, status_code=403)
        token, user = human(request)
        if not user:
            return JSONResponse({"detail": "auth required"}, status_code=401)
        body = await request.json()
        if not csrf_ok(token, body.get("csrf")):
            return JSONResponse({"detail": "bad CSRF token"}, status_code=403)
        text = body.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > 600:
            return JSONResponse({"detail": "describe your policy in 1-600 characters"}, status_code=422)
        if workspaces is not None and not workspaces.allow_chat(current_ws.get()):
            return JSONResponse({"detail": "review workspace AI limit reached; reset the workspace"}, status_code=429)
        policy = next((p for p in svc.policies.values() if p.user_id == user), None)
        if policy is None:
            return JSONResponse({"detail": "no policy to edit"}, status_code=404)
        try:
            fields = validate_draft(drafter.draft(text.strip(), svc.registry), svc.registry)
            draft_id, changes = svc.create_policy_draft(token, policy.policy_id, fields, text.strip(), drafter.name)
        except DraftError as exc:
            return JSONResponse({"detail": "draft rejected: " + str(exc)}, status_code=422)
        except ApprovalError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409)
        except Exception:
            return JSONResponse({"detail": "drafting failed; no policy was changed"}, status_code=502)
        return jsonable_encoder({"draft_id": draft_id, "source": drafter.name, "changes": changes,
                                 "draft": public_json(fields), "base_version": policy.version})

    @app.post("/api/policy/activate")
    async def api_policy_activate(request: Request):
        if request.headers.get("authorization"):
            return JSONResponse({"detail": "agent credentials cannot activate policy"}, status_code=403)
        token, user = human(request)
        if not user:
            return JSONResponse({"detail": "auth required"}, status_code=401)
        body = await request.json()
        if not csrf_ok(token, body.get("csrf")):
            return JSONResponse({"detail": "bad CSRF token"}, status_code=403)
        try:
            policy = svc.activate_policy_draft(token, str(body.get("draft_id") or ""))
        except (ApprovalError, DraftError) as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409)
        return {"ok": True, "version": policy.version, "status": policy.status}

    @app.post("/api/policy/resume")
    async def api_resume(request: Request):
        token, user = human(request)
        if not user:
            return JSONResponse({"detail": "auth required"}, status_code=401)
        body = await request.json()
        if not csrf_ok(token, body.get("csrf")):
            return JSONResponse({"detail": "bad CSRF token"}, status_code=403)
        try:
            for policy in list(svc.policies.values()):
                if policy.user_id == user and policy.status == "REVOKED":
                    svc.resume_policy(token, policy.policy_id)
        except ApprovalError as exc:
            return JSONResponse({"detail": str(exc)}, status_code=409)
        return {"ok": True}

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
    static_dir = pathlib.Path(__file__).parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
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
        except IdempotencyConflict as exc:
            return err(str(exc), 409)

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
            if workspaces is not None:
                owner = workspaces.find_intent(intent_id)
                if owner is None:
                    return err("not found", 404)
                current_ws.set(owner)
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

    def _reset_current(token):
        """Reset only this reviewer's workspace. Returns (new_token, error_response)."""
        if workspaces is None:
            return None, err("workspace reset is only available in the hosted review demo", 404)
        try:
            return workspaces.reset(current_ws.get()), None
        except RateLimited as exc:
            return None, err(str(exc), 429)

    @app.post("/api/demo/reset")
    async def api_demo_reset(request: Request):
        token, user = human(request)
        if not user:
            return err("auth required", 401)
        body = await request.json()
        if not csrf_ok(token, body.get("csrf")):
            return err("bad CSRF token", 403)
        new_token, error = _reset_current(token)
        if error:
            return error
        resp = JSONResponse({"ok": True, "csrf": csrf_for(new_token)})
        resp.set_cookie(COOKIE, new_token, httponly=True, samesite="lax", secure=cookie_secure,
                        max_age=current_ws.get().svc.auth.session_ttl_seconds)
        return resp

    @app.post("/demo/reset")
    async def demo_reset(request: Request):
        token, user = human(request)
        if not user:
            return err("human login required", 401)
        form = await request.form()
        if not csrf_ok(token, form.get("csrf")):
            return err("bad CSRF token", 403)
        new_token, error = _reset_current(token)
        if error:
            return error
        resp = RedirectResponse("/console", status_code=303)
        resp.set_cookie(COOKIE, new_token, httponly=True, samesite="strict", secure=cookie_secure,
                        max_age=current_ws.get().svc.auth.session_ttl_seconds)
        return resp

    @app.post("/policy/resume")
    async def resume(request: Request):
        token, user = human(request)
        if not user:
            return err("human login required", 401)
        form = await request.form()
        if not csrf_ok(token, form.get("csrf")):
            return err("bad CSRF token", 403)
        try:
            for policy in list(svc.policies.values()):
                if policy.user_id == user and policy.status == "REVOKED":
                    svc.resume_policy(token, policy.policy_id)
        except ApprovalError as exc:
            return err(str(exc), 409)
        return RedirectResponse("/console", status_code=303)

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
        policy = next((p for p in svc.policies.values()), None)
        paused = bool(policy and policy.status != "ACTIVE")
        pending = [v for v in all_intents
                   if v["state"] == "HELD_FOR_APPROVAL" and v["approval_status"] == "PENDING"
                   and not paused and policy and v["policy_version"] == policy.version]
        banner = ""
        if paused:
            stuck = sum(1 for v in all_intents if v["state"] == "HELD_FOR_APPROVAL")
            banner = f"""
<div style="background:#fef2f2;border:1px solid #fca5a5;border-radius:var(--radius-lg);
  padding:16px 20px;margin-bottom:20px">
  <div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;
    color:var(--bad);margin-bottom:6px">&#9888; Policy paused</div>
  <div style="font-weight:700;font-size:16px;margin-bottom:2px">Agent spending is disabled</div>
  <div style="color:var(--fg3);font-size:13px">No new purchases can be authorized.
    {stuck} pending request(s) cannot be approved. Resume spending to submit new requests.</div>
</div>"""
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
        reset_form = (
            f'<form method="post" action="/demo/reset" style="margin-top:8px" '
            f'onsubmit="return confirm(\'Reset this review workspace? This removes demo intents and '
            f'restores the three scripted scenarios.\')">'
            f'<input type="hidden" name="csrf" value="{csrf_for(token)}">'
            f'<button class="btn btn-sm">&#8634; Reset review workspace</button></form>'
            f'<p style="font-size:11px;color:var(--fg3);margin-top:6px">Review workspace: your revokes, '
            f'approvals and resets affect only this session.</p>'
        ) if workspaces is not None else ""
        policy_label = f"travel.v{policy.version}" if policy else "—"
        if drafter.name == "scripted":
          drafter_mode = "A scripted drafter (no model key configured)"
          drafter_label = "Scripted draft policy"
        elif getattr(drafter, "provider_name", "") == "Gemini":
          drafter_mode = "Gemini AI drafter"
          drafter_label = "Gemini-generated draft policy"
        else:
          drafter_mode = "Claude AI drafter"
          drafter_label = "AI-generated draft policy"
        authoring_html = (AUTHORING_PANEL
                  .replace("__MODE__", drafter_mode)
                  .replace("__LABEL__", drafter_label)
                          .replace("__CSRF__", csrf_for(token)))
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
    <div class="stat-label">Unsafe proposals blocked</div>
    <div class="stat-value" style="color:var(--bad)">{blocked:02d}</div>
    <div class="stat-sub">blocked before order</div>
  </div>
  <div class="stat-card">
    <div class="stat-label">{'Policy' if paused else 'Active policy'}</div>
    <div class="stat-value" style="font-size:18px;font-family:ui-monospace;{'color:var(--bad)' if paused else ''}">{esc(policy_label)}{' · PAUSED' if paused else ''}</div>
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
    {chip(verdict.replace(" ", "_"))}
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
            if paused:
                posture = (f'<div class="posture-badge mb-12" style="background:#fef2f2;border-color:#fca5a5">'
                           f'<div class="pb-label" style="color:var(--bad)">&#9888; POLICY PAUSED</div>'
                           f'<div class="pb-sub">Agent spending: DISABLED. Pending requests cannot be approved.</div></div>')
                card_title, kill_form = "Spending policy", (
                    f'<form method="post" action="/policy/resume">'
                    f'<input type="hidden" name="csrf" value="{csrf_for(token)}">'
                    f'<button class="btn btn-primary btn-sm">&#9654; Resume spending</button></form>')
            else:
                posture = ('<div class="posture-badge mb-12"><div class="pb-label">✓ Bounded delegation</div>'
                           '<div class="pb-sub">Policy rules the authority. AI only supplies the suggestion.</div></div>')
                card_title, kill_form = "Active policy", (
                    f'<form method="post" action="/policy/revoke">'
                    f'<input type="hidden" name="csrf" value="{csrf_for(token)}">'
                    f'<button class="btn btn-danger btn-sm">&#9888; Pause all agent spending (kill switch)</button></form>')
            policy_html = f"""
<div class="card" style="margin-top:0">
  <div class="row-between mb-12">
    <div class="card-title" style="margin:0">{card_title}</div>
    <span style="background:#dbeafe;color:#1e40af;font-size:10px;font-weight:700;
      padding:2px 8px;border-radius:5px;letter-spacing:.04em">v{policy.version}{' · PAUSED' if paused else ''}</span>
  </div>
  {posture}
  <div class="fact-grid" style="font-size:12px">
    <dt>Policy ID</dt><dd><code>{esc(policy.policy_id)}</code></dd>
    <dt>Approved merchant</dt><dd>{esc(ml)}</dd>
    <dt>Allowed category</dt><dd>{esc(cl)}</dd>
    <dt>Auto-approve up to</dt><dd>${esc(str(policy.auto_approve_up_to))}</dd>
    <dt>Max single purchase</dt><dd>${esc(str(policy.max_single_purchase))}</dd>
    <dt>Total budget</dt><dd>${esc(str(policy.max_total_spend))}</dd>
  </div>
  <div class="sep"></div>
  {kill_form}
  {reset_form}
</div>"""

        body = f"""
<div class="page-eyebrow">TrustGate / Live console</div>
<h1 class="page-headline">
  Let AI find it.<br>
  <span class="hl-acc">Let policy decide.</span>
</h1>
<p class="page-lead">TrustGate turns an AI recommendation into a verified purchase intent—and stops it before PayPal when policy does not authorize it.
Developers give agents one governed purchase tool instead of raw PayPal payment tools.</p>
{banner}
<div class="assistant-grid">
  <section class="assistant-panel">
    <div class="assistant-head">
      <div><div class="assistant-kicker">AI purchase assistant</div>
        <div class="assistant-subtitle">Search registry products, inspect trusted facts, then send a governed proposal.</div></div>
      <span class="demo-agent-tag">{runner_now().provider_name + ' tool agent' if runner_now() else 'Scripted demo agent'}</span>
    </div>
    <div class="agent-note">{'The model can only search products, inspect registry facts, and submit governed proposals. TrustGate still controls authorization.' if runner_now() else 'This walkthrough uses a scripted agent; no live language model is configured. Every proposal still passes through TrustGate policy.'}</div>
    <div class="agent-thread" id="agent-thread" aria-live="polite">
      <div class="agent-message user"><span>You</span><p>Book me a direct flight to Johannesburg under $500. Ask me above $250.</p></div>
      <div class="agent-message"><span>Agent</span><p>{'Ready to search the registered catalog. Any purchase proposal will pass through TrustGate policy.' if runner_now() else 'I found two registered Demo Airlines options. The $180 fare is within the automatic limit; the $320 fare needs your approval.'}</p></div>
    </div>
    <div id="agent-results" class="agent-results" aria-live="polite"><div class="agent-loading">Searching the product registry…</div></div>
    <form id="agent-request-form" class="agent-form">
      <label class="sr-only" for="agent-request">Purchase request</label>
      <input id="agent-request" name="request" maxlength="240" placeholder="Try: Find a direct flight under $500" autocomplete="off">
      <button class="btn btn-primary" type="submit">Search products</button>
    </form>
  </section>
  <aside class="governance-panel">
    <div class="assistant-kicker">TrustGate evaluation</div>
    <div class="governance-subtitle">The agent recommends. The middleware authorizes.</div>
    <ol class="governance-steps" id="governance-steps">
      <li><span>1</span><div><b>Agent proposal</b><small>Waiting for product selection</small></div></li>
      <li><span>2</span><div><b>Trusted facts</b><small>Resolved from the product registry</small></div></li>
      <li><span>3</span><div><b>Policy decision</b><small>Deterministic; amount and payee are not agent inputs</small></div></li>
      <li><span>4</span><div><b>Human approval</b><small>Required above ${esc(str(policy.auto_approve_up_to)) if policy else '250'}</small></div></li>
    </ol>
    <div class="decision-box" id="decision-box">
      <div class="assistant-kicker">Decision</div><strong id="decision-value">Waiting for proposal</strong>
      <p id="decision-reason">No payment has been attempted.</p>
      <p id="approval-value">Approval: not requested</p>
      <a id="approval-link" class="btn btn-primary" href="#" hidden>Review purchase</a>
    </div>
    <div class="payment-box">
      <div class="assistant-kicker">Payment execution</div><strong id="payment-state">NOT STARTED</strong>
      <p id="payment-detail">A payment begins only after policy authorizes it.</p>
    </div>
  </aside>
</div>
{stats_html}
<div class="split">
  <div>
    <div class="card-title">Live agent activity</div>
    {"".join(story_cards)}
  </div>
  <div>
    <div class="card-title">Active spending policy</div>
    {policy_html}
    {authoring_html}
  </div>
</div>"""
        assistant_script = f"""<style>
.assistant-grid{{display:grid;grid-template-columns:minmax(0,1.25fr) minmax(320px,.75fr);gap:14px;margin:0 0 24px;align-items:stretch}}
.assistant-panel,.governance-panel{{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius);padding:20px;min-width:0}}
.assistant-head{{display:flex;justify-content:space-between;align-items:flex-start;gap:12px}}
.assistant-kicker{{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:var(--fg3)}}
.assistant-subtitle,.governance-subtitle{{font-size:12px;color:var(--fg2);margin:5px 0 0}}
.demo-agent-tag{{font-size:10px;font-weight:700;text-transform:uppercase;color:var(--warn-dark);background:var(--warn-light);padding:5px 8px;border-radius:5px;white-space:nowrap}}
.agent-note{{margin-top:12px;padding:9px 11px;background:var(--surface2);border-left:2px solid var(--warn);color:var(--fg2);font-size:11px}}
.agent-thread{{display:grid;gap:9px;margin:14px 0 10px;max-height:170px;overflow:auto}}
.agent-message{{max-width:94%;background:var(--surface2);padding:9px 11px;border-radius:7px;font-size:12px}}
.agent-message.user{{justify-self:end;background:var(--acc-light)}}
.agent-message span{{display:block;font-size:10px;font-weight:700;color:var(--fg3);margin-bottom:3px}}
.agent-message p{{margin:0;color:var(--fg)}}
.agent-results{{display:grid;gap:7px;margin:12px 0}}
.agent-product{{display:flex;justify-content:space-between;align-items:center;gap:12px;border:1px solid var(--line);padding:10px 11px;border-radius:6px}}
.agent-product h3{{font-size:12px;margin:0 0 2px}}.agent-product p{{font-size:11px;color:var(--fg3);margin:0}}
.agent-product strong{{white-space:nowrap;font-size:13px}}
.agent-product-actions{{display:flex;align-items:center;gap:8px}}
.agent-form{{display:flex;gap:8px;align-items:center}}.agent-form input{{min-width:0}}
.agent-loading,.agent-empty{{padding:10px;color:var(--fg3);font-size:12px}}
.governance-steps{{list-style:none;margin:17px 0;padding:0;display:grid;gap:11px}}
.governance-steps li{{display:flex;align-items:flex-start;gap:10px}}
.governance-steps li>span{{display:grid;place-items:center;width:20px;height:20px;flex:0 0 20px;border-radius:50%;background:var(--acc-light);color:var(--acc-dark);font-size:10px;font-weight:700}}
.governance-steps b,.decision-box strong,.payment-box strong{{font-size:12px;display:block}}
.governance-steps small{{display:block;color:var(--fg3);font-size:11px;margin-top:2px}}
.decision-box,.payment-box{{border-top:1px solid var(--line);padding-top:13px;margin-top:13px}}
.decision-box strong,.payment-box strong{{margin-top:5px}}
.decision-box p,.payment-box p{{font-size:11px;color:var(--fg2);margin:4px 0 0}}
.decision-box[data-decision="ALLOW"] strong,.payment-box[data-state="CAPTURED"] strong{{color:var(--ok)}}
.decision-box[data-decision="APPROVAL_REQUIRED"] strong{{color:var(--warn)}}
.decision-box[data-decision="BLOCK"] strong,.payment-box[data-state="NOT REACHED"] strong{{color:var(--bad)}}
.sr-only{{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0}}
@media(max-width:900px){{.assistant-grid{{grid-template-columns:1fr}}}}
@media(max-width:600px){{.assistant-panel,.governance-panel{{padding:15px}}.assistant-head{{flex-direction:column}}.agent-form{{align-items:stretch;flex-direction:column}}.agent-form .btn{{justify-content:center}}.agent-product{{align-items:flex-start;flex-direction:column}}}}
</style>
<script>
document.addEventListener('DOMContentLoaded',()=>{{
  const csrf={json.dumps(csrf_for(token))};
  const thread=document.getElementById('agent-thread');
  const results=document.getElementById('agent-results');
  const form=document.getElementById('agent-request-form');
  const requestInput=document.getElementById('agent-request');
  const decisionBox=document.getElementById('decision-box');
  const approvalLink=document.getElementById('approval-link');
  const modelEnabled={json.dumps(bool(runner_now()))};
  let approvalPoll;
  const money=(amount,currency)=>new Intl.NumberFormat('en-US',{{style:'currency',currency}}).format(Number(amount));
  function message(role,text,isUser=false){{const wrap=document.createElement('div');wrap.className='agent-message'+(isUser?' user':'');const name=document.createElement('span');name.textContent=role;const body=document.createElement('p');body.textContent=text;wrap.append(name,body);thread.append(wrap);thread.scrollTop=thread.scrollHeight}}
  function button(label,action){{const el=document.createElement('button');el.type='button';el.className='btn btn-sm';el.textContent=label;el.addEventListener('click',action);return el}}
  async function getJSON(url,options={{}}){{const response=await fetch(url,{{credentials:'same-origin',...options}});const data=await response.json();if(!response.ok)throw new Error(data.detail||'Request failed');return data}}
  function watchApproval(intentId){{
    clearInterval(approvalPoll);
    approvalPoll=setInterval(async()=>{{
      try{{
        const updated=await getJSON('/api/intents/'+encodeURIComponent(intentId));
        if(updated.state==='HELD_FOR_APPROVAL')return;
        clearInterval(approvalPoll);
        document.getElementById('approval-value').textContent='Approval: '+updated.approval_status;
        const finalPayment=updated.state==='BLOCKED'?'NOT REACHED':updated.state;
        const paymentBox=document.querySelector('.payment-box');paymentBox.dataset.state=finalPayment;
        document.getElementById('payment-state').textContent=finalPayment;
        document.getElementById('payment-detail').textContent=updated.state==='CAPTURED'?'Order '+updated.order_id+' · Capture '+updated.capture_id:'No PayPal payment was captured.';
        approvalLink.hidden=true;
        if(updated.state==='CAPTURED')message('Agent','Approved and purchased. Capture ID: '+updated.capture_id);
        else message('Agent','The purchase was not approved. No PayPal payment was captured.');
      }}catch(error){{clearInterval(approvalPoll)}}
    }},2000);
  }}
  function showIntent(detail,approvalUrl){{
    decisionBox.dataset.decision=detail.decision;
    document.getElementById('decision-value').textContent=detail.decision.replaceAll('_',' ');
    document.getElementById('decision-reason').textContent=(detail.reasons||[]).map(code=>code.replaceAll('_',' ').toLowerCase()).join('; ')||'Policy authorized this proposal.';
    document.getElementById('approval-value').textContent='Approval: '+detail.approval_status;
    const paymentState=detail.state==='BLOCKED'?'NOT REACHED':detail.state==='HELD_FOR_APPROVAL'?'NOT STARTED':detail.state;
    const paymentBox=document.querySelector('.payment-box');paymentBox.dataset.state=paymentState;
    document.getElementById('payment-state').textContent=paymentState;
    const paymentDetail=detail.state==='BLOCKED'?'Blocked by policy. No PayPal order was created.':detail.state==='HELD_FOR_APPROVAL'?'Waiting for authenticated human approval. No PayPal order was created.':detail.order_id?'Order '+detail.order_id+' · Capture '+detail.capture_id:'Purchase execution finished.';
    document.getElementById('payment-detail').textContent=paymentDetail;
    if(approvalUrl){{approvalLink.href=approvalUrl;approvalLink.hidden=false;watchApproval(detail.intent_id)}}
  }}
  async function runModel(query){{
    message('Agent','Checking the request and available products…');
    try{{
      const data=await getJSON('/api/assistant/chat',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{message:query,csrf}})}});
      data.tool_calls.forEach(call=>message('Agent tool · '+call.name,call.ok?'Completed by the server-side agent.':'Tool rejected.'));
      if(data.intent)showIntent(data.intent,data.approval_url);
      message('Agent',data.answer);
    }}catch(error){{message('Assistant error',error.message)}}
  }}
  async function search(query){{
    results.replaceChildren(Object.assign(document.createElement('div'),{{className:'agent-loading',textContent:'Searching registered products…'}}));
    try{{const data=await getJSON('/api/assistant/products?q='+encodeURIComponent(query));results.replaceChildren();
      if(!data.products.length){{results.textContent='No registered products match that request.';results.className='agent-results agent-empty';return}}
      results.className='agent-results';
      data.products.forEach(product=>{{
        const row=document.createElement('article');row.className='agent-product';
        const details=document.createElement('div');const name=document.createElement('h3');name.textContent=product.display_name;const merchant=document.createElement('p');merchant.textContent=product.merchant+' · '+product.category;details.append(name,merchant);
        const actions=document.createElement('div');actions.className='agent-product-actions';const price=document.createElement('strong');price.textContent=money(product.display_amount,product.currency);
        const inspect=button('Inspect',async()=>{{
          inspect.disabled=true;inspect.textContent='Inspecting…';
          try{{const verified=await getJSON('/api/assistant/products/'+encodeURIComponent(product.product_reference));
            message('Agent tool · get_product_details',verified.display_name+' · '+money(verified.display_amount,verified.currency)+' · Payee: '+verified.merchant+'. Amount and payee are resolved by the registry.');
            const propose=button('Propose purchase',()=>submitProposal(verified,propose));actions.replaceChildren(price,propose);
          }}catch(error){{inspect.disabled=false;inspect.textContent='Inspect';message('Tool error',error.message)}}
        }});
        actions.append(price,inspect);row.append(details,actions);results.append(row);
      }});
    }}catch(error){{results.textContent=error.message;results.className='agent-results agent-empty'}}
  }}
  async function submitProposal(product,buttonEl){{
    clearInterval(approvalPoll);
    buttonEl.disabled=true;buttonEl.textContent='Sending proposal…';
    message('Agent tool · propose_purchase','Submitting only the registered product reference. TrustGate supplies the trusted amount and payee.');
    document.getElementById('decision-value').textContent='Evaluating proposal';
    document.getElementById('decision-reason').textContent='Resolving trusted facts and checking policy…';
    document.getElementById('payment-state').textContent='NOT STARTED';
    document.getElementById('payment-detail').textContent='Payment execution waits for the TrustGate decision.';
    try{{const result=await getJSON('/api/assistant/propose',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{product_reference:product.product_reference,csrf}})}});
      const detail=await getJSON('/api/intents/'+encodeURIComponent(result.intent_id));
      showIntent(detail,result.approval_url);
      const responseText=result.decision==='APPROVAL_REQUIRED'?'This purchase needs your approval before payment.':result.decision==='BLOCK'?'I stopped this proposal. No PayPal order was created.':detail.state==='CAPTURED'?'Policy authorized the purchase and payment was captured.':'The proposal was evaluated by TrustGate.';
      message('Agent',responseText);
      buttonEl.textContent='Proposal sent';
    }}catch(error){{buttonEl.disabled=false;buttonEl.textContent='Try proposal again';message('Tool error',error.message)}}
  }}
  form.addEventListener('submit',event=>{{event.preventDefault();const query=requestInput.value.trim()||'direct flight under $500';message('You',query,true);requestInput.value='';if(modelEnabled)runModel(query);else{{message('Agent tool · search_products','Searching the registered catalog. The local demo agent is scripted; it does not call a language model.');search(query)}}}});
  if(!modelEnabled)search('direct flight under $500');
}});
</script>"""
        return HTMLResponse(page("TrustGate / Live Console", body, "console", token,
                                 extra_head=assistant_script))

    # ── Page: Purchase Intents (/intents) ──────────────────────────────────
    @app.get("/intents/{intent_id}", response_class=HTMLResponse)
    def intent_page(intent_id: str, request: Request):
        return intent_detail(intent_id, request)

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
                        ("Adapter", esc(adapter_label())),
                        ("Order ID", f'<code>{esc(str(v["order_id"]))}</code>'),
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
                    ("Adapter", esc(adapter_label())),
                    ("Order ID", f'<code>{esc(str(v["order_id"]))}</code>'),
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
            extra = (
                f'<dl class="fact-grid"><dt style="color:var(--fg3)">State</dt><dd>{chip(state)}</dd>'
                f'<dt style="color:var(--fg3)">Decision</dt><dd>{chip(v["decision"])}</dd></dl>'
                f'{_flags_html(v["flags"])}'
            )

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
      {flow_html(v, simulated=(paypal_mode != 'sandbox'))}
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
        _pol = svc.policies.get(v["policy_id"])
        approvable = held and bool(_pol) and _pol.status == "ACTIVE" and _pol.version == v["policy_version"]

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
        if held and not approvable:
            c = csrf_for(token)
            facts_card += f"""
  <div class="sep"></div>
  <div style="background:#fef2f2;border:1px solid #fca5a5;border-radius:var(--radius);padding:12px 14px;margin-bottom:12px">
    <div style="font-weight:700;color:var(--bad);font-size:13px">&#9888; POLICY PAUSED</div>
    <div style="font-size:12px;color:var(--fg3)">This request can no longer be approved. Resume spending and submit a new request.</div>
  </div>
  <div class="row">
    <form method="post" action="/v1/approvals/{esc(intent_id)}">
      <input type="hidden" name="csrf" value="{c}">
      <input type="hidden" name="decision" value="DECLINE">
      <button class="btn btn-danger">Decline</button>
    </form>
    <button class="btn btn-primary" disabled style="opacity:.45;cursor:not-allowed">Approve purchase &#8594;</button>
  </div>"""
        elif held:
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
      <button class="btn btn-primary">Approve purchase →</button>
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
    <dt style="color:var(--fg3);font-size:12px">Adapter</dt><dd>{esc(adapter_label())}</dd>
    <dt style="color:var(--fg3);font-size:12px">Order ID</dt>
    <dd><code>{esc(str(v["order_id"]))}</code></dd>
    <dt style="color:var(--fg3);font-size:12px">Capture ID</dt>
    <dd><code>{esc(str(v["capture_id"]))}</code></dd>
  </dl>
</div>"""

        right_col = f"""
<div class="card">
  {flow_html(v, simulated=(paypal_mode != 'sandbox'))}
  {checks_html("Policy evaluation", v["checks"])}
  {checks_html("Approval bound to these verified facts", [(n,"pass") for n in v["binding_checks"]])}
</div>
<p class="mt-12 text-sm">
  <a href="/audit?selected={esc(intent_id)}">View audit timeline →</a>
</p>"""

        if state == "BLOCKED":
            status_badge = '<div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--bad);margin-bottom:14px">⚠ Blocked</div>'
            title = "Blocked"
            facts_card = f"""
<div class="card" style="border-color:#fca5a5;background:#fff5f5">
  {status_badge}
  <div class="amt-big" style="margin-bottom:4px;color:var(--bad)">{esc(f['amount'] + ' ' + f['currency']) if f else '—'}</div>
  <div class="amt-sub">{esc(f['product']) if f else '—'}</div>
  <dl class="fact-grid" style="row-gap:8px">
    <dt style="color:var(--fg3);font-size:12px">Merchant</dt><dd style="font-size:13px">{esc(f['merchant']) if f else '—'}</dd>
    <dt style="color:var(--fg3);font-size:12px">Reason</dt><dd style="font-size:13px">{esc(reason_text)}</dd>
    <dt style="color:var(--fg3);font-size:12px">State</dt><dd>{chip(state)}</dd>
  </dl>
  <div class="sep"></div>
  <div class="row"><span class="ic" style="display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;border-radius:50%;background:var(--bad-light);color:var(--bad);font-weight:700">&#10005;</span> <strong style="color:var(--bad);font-size:13px">Payment blocked before PayPal was called.</strong></div>
</div>"""
        elif held:
            status_badge = '<div style="font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.1em;color:var(--warn);margin-bottom:14px">Held for approval</div>'
            title = "Held for approval"
        elif captured:
            title = "Approved and captured"
        else:
            title = "Purchase"

        if state != "BLOCKED":
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
