"""Shared UI shell: dark sidebar layout, CSS, and helper renderers.

All page content is plain server-rendered HTML.  No external assets.
"""
import html as _html

esc = _html.escape

# ── Design tokens ────────────────────────────────────────────────────────────
CSS = """
:root{
  --sidebar:#0f1117;--sidebar-border:#1e2230;--sidebar-hover:#1a1f2e;--sidebar-active:#1e2640;
  --bg:#f4f5f7;--surface:#ffffff;--surface2:#f8f9fb;
  --fg:#0d1117;--fg2:#4b5563;--fg3:#9ca3af;
  --line:#e5e7eb;--line2:#f0f1f3;
  --acc:#2563eb;--acc-light:#dbeafe;--acc-dark:#1d4ed8;
  --ok:#059669;--ok-light:#d1fae5;--ok-dark:#065f46;
  --warn:#d97706;--warn-light:#fef3c7;--warn-dark:#92400e;
  --bad:#dc2626;--bad-light:#fee2e2;--bad-dark:#7f1d1d;
  --sidebar-fg:#e2e8f0;--sidebar-mut:#64748b;--sidebar-badge:#1e2a45;
  --radius:8px;--radius-lg:12px;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;font:14px/1.5 "Inter",system-ui,-apple-system,"Segoe UI",sans-serif;background:var(--bg);color:var(--fg)}
a{color:var(--acc);text-decoration:none}a:hover{text-decoration:underline}

/* ── Layout ──────────────────────────────────────────────────────────────── */
.shell{display:flex;height:100vh;overflow:hidden}
.sidebar{width:200px;flex-shrink:0;background:var(--sidebar);border-right:1px solid var(--sidebar-border);
  display:flex;flex-direction:column;overflow-y:auto}
.main{flex:1;display:flex;flex-direction:column;overflow:hidden}
.topbar{height:48px;flex-shrink:0;background:var(--surface);border-bottom:1px solid var(--line);
  display:flex;align-items:center;justify-content:space-between;padding:0 28px;font-size:12px;color:var(--fg3)}
.topbar .breadcrumb{display:flex;align-items:center;gap:8px;font-weight:500;color:var(--fg2);text-transform:uppercase;letter-spacing:.06em;font-size:11px}
.topbar .breadcrumb span{color:var(--fg3)}
.topbar .status-row{display:flex;align-items:center;gap:16px}
.live-dot{width:7px;height:7px;border-radius:50%;background:var(--ok);display:inline-block;
  animation:pulse-dot 2s infinite}
@keyframes pulse-dot{0%,100%{opacity:1}50%{opacity:.4}}
.content{flex:1;overflow-y:auto;padding:36px 40px}

/* ── Sidebar ─────────────────────────────────────────────────────────────── */
.sb-brand{padding:20px 16px 14px;border-bottom:1px solid var(--sidebar-border)}
.sb-brand .logo{display:flex;align-items:center;gap:10px;margin-bottom:4px}
.sb-brand .logo-icon{width:28px;height:28px;border-radius:7px;background:var(--acc);
  display:flex;align-items:center;justify-content:center;font-size:14px;font-weight:700;color:#fff;flex-shrink:0}
.sb-brand .logo-name{font-weight:700;color:var(--sidebar-fg);font-size:13px}
.sb-brand .logo-sub{font-size:10px;color:var(--sidebar-mut);text-transform:uppercase;letter-spacing:.08em;padding-left:38px}
.sb-section{padding:12px 10px 4px;font-size:10px;font-weight:600;color:var(--sidebar-mut);
  text-transform:uppercase;letter-spacing:.1em}
.sb-nav{list-style:none;padding:0 8px}
.sb-nav li a{display:flex;align-items:center;gap:10px;padding:8px 10px;border-radius:var(--radius);
  color:var(--sidebar-mut);font-size:13px;font-weight:500;transition:background .12s,color .12s;
  text-decoration:none}
.sb-nav li a:hover{background:var(--sidebar-hover);color:var(--sidebar-fg)}
.sb-nav li a.active{background:var(--sidebar-active);color:var(--sidebar-fg)}
.sb-nav .nav-icon{font-size:14px;width:18px;text-align:center;flex-shrink:0}
.sb-nav .nav-badge{margin-left:auto;background:var(--sidebar-badge);color:var(--sidebar-fg);
  font-size:10px;font-weight:700;padding:1px 7px;border-radius:99px;min-width:20px;text-align:center}
.sb-nav .nav-badge.warn{background:#78350f;color:#fde68a}
.sb-bottom{margin-top:auto;border-top:1px solid var(--sidebar-border);padding:12px 10px}
.paypal-mode{background:var(--sidebar-hover);border:1px solid var(--sidebar-border);border-radius:var(--radius);
  padding:10px 12px;margin-bottom:10px}
.paypal-mode .mode-label{font-size:10px;text-transform:uppercase;letter-spacing:.08em;color:var(--sidebar-mut);margin-bottom:4px}
.paypal-mode .mode-val{font-size:12px;font-weight:600;color:var(--sidebar-fg);display:flex;align-items:center;gap:6px}
.paypal-mode .mode-sub{font-size:10px;color:var(--sidebar-mut);margin-top:2px}
.user-block{display:flex;align-items:center;gap:10px;padding:8px 10px;border-radius:var(--radius)}
.user-avatar{width:28px;height:28px;border-radius:50%;background:var(--acc);display:flex;align-items:center;
  justify-content:center;font-size:12px;font-weight:700;color:#fff;flex-shrink:0}
.user-name{font-size:12px;font-weight:600;color:var(--sidebar-fg)}
.user-role{font-size:10px;color:var(--sidebar-mut)}
.logout-button{margin:2px 0 0 48px;padding:2px 0;border:0;background:none;color:var(--sidebar-mut);
  font:inherit;font-size:10px;cursor:pointer}
.logout-button:hover{color:var(--sidebar-fg)}

/* ── Page headers ────────────────────────────────────────────────────────── */
.page-eyebrow{font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.12em;
  color:var(--fg3);margin-bottom:10px;display:flex;align-items:center;gap:8px}
.page-eyebrow::before{content:"";display:inline-block;width:20px;height:2px;background:var(--acc)}
.page-headline{font-size:clamp(28px,4vw,44px);font-weight:800;line-height:1.1;letter-spacing:-.02em;
  margin-bottom:10px;color:var(--fg)}
.page-headline .hl-acc{color:var(--acc)}
.page-lead{color:var(--fg2);font-size:14px;max-width:600px;margin-bottom:28px}

/* ── Cards & surfaces ────────────────────────────────────────────────────── */
.card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius-lg);padding:20px}
.card+.card{margin-top:12px}
.card-title{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.08em;color:var(--fg3);margin-bottom:14px}
.card-section{font-size:11px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;color:var(--fg3);
  padding:10px 16px 6px;border-bottom:1px solid var(--line)}

/* ── Stats row ───────────────────────────────────────────────────────────── */
.stats-row{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:24px}
.stat-card{background:var(--surface);border:1px solid var(--line);border-radius:var(--radius-lg);padding:16px 20px}
.stat-label{font-size:10px;font-weight:600;text-transform:uppercase;letter-spacing:.1em;color:var(--fg3);margin-bottom:6px}
.stat-value{font-size:26px;font-weight:700;font-variant-numeric:tabular-nums;line-height:1;margin-bottom:4px}
.stat-sub{font-size:11px;color:var(--fg3)}

/* ── Chips / badges ──────────────────────────────────────────────────────── */
.chip{display:inline-flex;align-items:center;padding:2px 8px;border-radius:5px;
  font-size:11px;font-weight:700;letter-spacing:.04em;white-space:nowrap}
.chip-ALLOW,.chip-CAPTURED,.chip-APPROVED,.chip-NOT_REQUIRED,.chip-valid{background:var(--ok-light);color:var(--ok-dark)}
.chip-APPROVAL_REQUIRED,.chip-HELD_FOR_APPROVAL,.chip-PENDING,.chip-HELD{background:var(--warn-light);color:var(--warn-dark)}
.chip-BLOCK,.chip-BLOCKED,.chip-DECLINED,.chip-EXPIRED,.chip-REFUSED,.chip-invalid{background:var(--bad-light);color:var(--bad-dark)}
.chip-RECEIVED,.chip-VERIFIED,.chip-acc{background:var(--acc-light);color:var(--acc-dark)}

/* ── Table ───────────────────────────────────────────────────────────────── */
.tbl{width:100%;border-collapse:collapse}
.tbl th{font-size:10px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--fg3);
  padding:10px 12px;border-bottom:2px solid var(--line);text-align:left;background:var(--surface2)}
.tbl td{padding:11px 12px;border-bottom:1px solid var(--line2);font-size:13px;vertical-align:middle}
.tbl tr:last-child td{border-bottom:none}
.tbl tr.row-active{background:#eff6ff}
.tbl tr:hover td{background:var(--surface2)}

/* ── DL (fact grids) ─────────────────────────────────────────────────────── */
.fact-grid{display:grid;grid-template-columns:160px 1fr;gap:6px 16px;font-size:13px}
.fact-grid dt{color:var(--fg3);font-size:12px;padding-top:1px}
.fact-grid dd{font-variant-numeric:tabular-nums}

/* ── Buttons ─────────────────────────────────────────────────────────────── */
.btn{display:inline-flex;align-items:center;gap:6px;padding:8px 16px;border-radius:var(--radius);
  border:1px solid var(--line);background:var(--surface);color:var(--fg);font:inherit;
  font-size:13px;font-weight:500;cursor:pointer;text-decoration:none;transition:background .12s}
.btn:hover{background:var(--surface2)}
.btn-primary{background:var(--acc);border-color:var(--acc);color:#fff}
.btn-primary:hover{background:var(--acc-dark)}
.btn-danger{background:var(--bad-light);border-color:var(--bad);color:var(--bad-dark)}
.btn-danger:hover{background:#fecaca}
.btn-sm{padding:5px 12px;font-size:12px}
.btn-xs{padding:3px 9px;font-size:11px;border-radius:6px}

/* ── Flow diagram ────────────────────────────────────────────────────────── */
.flow{display:flex;align-items:center;gap:6px;margin:4px 0 18px}
.flow-node{flex:0 0 auto;min-width:110px;text-align:center;padding:10px 14px;
  border:1.5px solid var(--acc);border-radius:var(--radius);font-weight:600;font-size:12px;background:var(--surface)}
.flow-node small{display:block;font-weight:400;color:var(--fg3);font-size:11px}
.flow-node.ok{border-color:var(--ok);color:var(--ok-dark)}
.flow-node.warn{border-color:var(--warn);color:var(--warn-dark)}
.flow-node.bad{border-color:var(--bad);color:var(--bad-dark)}
.flow-node.dim{border-color:var(--line);color:var(--fg3)}
.flow-lane{flex:1;height:2px;background:var(--acc)}
.flow-lane.ok{background:var(--ok)}
.flow-lane.warn{background:repeating-linear-gradient(90deg,var(--warn) 0 6px,transparent 6px 12px)}
.flow-lane.off{background:var(--line)}

/* ── Check list (animated) ───────────────────────────────────────────────── */
@keyframes rise{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:none}}
.chk-list{list-style:none}
.chk-list li{display:flex;align-items:center;gap:10px;padding:7px 0;
  border-bottom:1px solid var(--line2);opacity:0;animation:rise .35s ease forwards;
  animation-delay:calc(var(--i)*.18s);font-size:13px}
.chk-list li:last-child{border-bottom:none}
.chk-ic{width:16px;height:16px;border-radius:50%;display:flex;align-items:center;justify-content:center;
  font-size:10px;font-weight:700;flex-shrink:0}
.chk-pass .chk-ic{background:var(--ok-light);color:var(--ok)}
.chk-fail .chk-ic{background:var(--bad-light);color:var(--bad)}
.chk-review .chk-ic{background:var(--warn-light);color:var(--warn)}

/* ── Timeline ────────────────────────────────────────────────────────────── */
.tl-list{list-style:none}
.tl-item{display:flex;gap:14px;padding:12px 0;border-bottom:1px solid var(--line2);
  opacity:0;animation:rise .3s ease forwards;animation-delay:calc(var(--i)*.08s)}
.tl-item:last-child{border-bottom:none}
.tl-dot{width:9px;height:9px;border-radius:50%;flex-shrink:0;margin-top:4px}
.tl-dot-ok{background:var(--ok)}.tl-dot-warn{background:var(--warn)}
.tl-dot-bad{background:var(--bad)}.tl-dot-acc{background:var(--acc)}.tl-dot-mut{background:var(--fg3)}
.tl-ts{font:12px ui-monospace,Menlo,Consolas,monospace;color:var(--fg3);white-space:nowrap;padding-top:1px;min-width:64px}
.tl-body{flex:1}
.tl-ev{font-weight:600;font-size:13px;margin-bottom:2px}
.tl-desc{color:var(--fg2);font-size:12px}

/* ── Split layout ────────────────────────────────────────────────────────── */
.split{display:grid;grid-template-columns:1fr 1fr;gap:16px;align-items:start}
.split-left{display:flex;flex-direction:column;gap:0}
.intent-row{padding:14px 16px;border-bottom:1px solid var(--line2);cursor:pointer;
  transition:background .1s;text-decoration:none;display:block}
.intent-row:hover{background:var(--surface2)}
.intent-row.selected{background:#eff6ff;border-left:3px solid var(--acc)}
.intent-row .ir-top{display:flex;justify-content:space-between;align-items:center;margin-bottom:3px}
.intent-row .ir-name{font-weight:600;font-size:13px}
.intent-row .ir-sub{font-size:11px;color:var(--fg3)}
.intent-row .ir-amt{font-weight:700;font-size:14px;font-variant-numeric:tabular-nums}

/* ── Posture badge ───────────────────────────────────────────────────────── */
.posture-badge{background:var(--ok-light);border:1px solid #a7f3d0;border-radius:var(--radius);
  padding:10px 14px;font-size:12px}
.posture-badge .pb-label{font-weight:700;color:var(--ok-dark);font-size:11px;text-transform:uppercase;
  letter-spacing:.08em;margin-bottom:3px;display:flex;align-items:center;gap:6px}
.posture-badge .pb-sub{color:var(--ok-dark);font-size:11px;opacity:.8}

/* ── Evidence block ──────────────────────────────────────────────────────── */
.evidence{background:var(--bad-light);border:1px solid #fca5a5;border-radius:var(--radius);
  padding:12px 14px;margin-top:8px}
.evidence-title{font-weight:700;color:var(--bad-dark);font-size:12px;margin-bottom:4px}
.evidence-body{color:var(--bad-dark);font-size:12px;opacity:.85}

/* ── Misc ────────────────────────────────────────────────────────────────── */
.amt-big{font-size:32px;font-weight:800;font-variant-numeric:tabular-nums;line-height:1}
.amt-sub{color:var(--fg3);font-size:12px;margin-top:3px;margin-bottom:18px}
.mono{font-family:ui-monospace,Menlo,Consolas,monospace}
.row{display:flex;align-items:center;gap:8px}
.row-between{display:flex;justify-content:space-between;align-items:center}
.text-mut{color:var(--fg3)}
.text-ok{color:var(--ok)}.text-warn{color:var(--warn)}.text-bad{color:var(--bad)}
.text-sm{font-size:12px}.text-xs{font-size:11px}
.mb-4{margin-bottom:4px}.mb-8{margin-bottom:8px}.mb-12{margin-bottom:12px}.mb-16{margin-bottom:16px}
.mb-24{margin-bottom:24px}.mt-12{margin-top:12px}.mt-16{margin-top:16px}.mt-24{margin-top:24px}
.sep{height:1px;background:var(--line);margin:16px 0}
code{font-family:ui-monospace,Menlo,Consolas,monospace;font-size:11px;background:var(--surface2);
  padding:1px 5px;border-radius:4px;border:1px solid var(--line)}
pre{background:var(--surface2);border:1px solid var(--line);border-radius:var(--radius);
  padding:14px;font-size:11px;font-family:ui-monospace,Menlo,Consolas,monospace;overflow-x:auto}
input{font:inherit;padding:8px 10px;border:1px solid var(--line);border-radius:var(--radius);
  background:var(--surface);color:var(--fg);width:100%}
@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}.chk-list li,.tl-item{opacity:1}}
"""


def chip(value):
    safe = str(value).replace(" ", "_")
    return f'<span class="chip chip-{esc(safe)}">{esc(str(value))}</span>'


def flow_html(v):
    if v.get("order_id"):
        mid, mcls, lane, end, ecls = "Authorized", "ok", "ok", "Order captured", "ok"
    elif v.get("state") == "HELD_FOR_APPROVAL":
        mid, mcls, lane, end, ecls = "Held for approval", "warn", "warn", "Not reached yet", "dim"
    elif v.get("decision") == "BLOCK":
        mid, mcls, lane, end, ecls = "Blocked", "bad", "off", "Not reached", "dim"
    else:
        mid, mcls, lane, end, ecls = (v.get("state","—").replace("_"," ").title()), "bad", "off", "Not reached", "dim"
    return (f'<div class="flow">'
            f'<div class="flow-node">AI agent<small>proposed</small></div>'
            f'<div class="flow-lane ok"></div>'
            f'<div class="flow-node {mcls}">TrustGate<small>{esc(mid)}</small></div>'
            f'<div class="flow-lane {lane}"></div>'
            f'<div class="flow-node {ecls}">PayPal<small>{esc(end)}</small></div>'
            f'</div>')


def checks_html(title, items):
    if not items:
        return ""
    ICON = {"pass": "✓", "fail": "&#10005;", "review": "!"}

    def render_icon(state):
        icon = ICON.get(state, "?")
        if state == "fail":
            return f'<span class="ic">{icon}</span>'
        return icon

    lis = "".join(
        f'<li class="chk-{esc(s)}" style="--i:{i}">'
        f'<span class="chk-ic">{render_icon(s)}</span>{esc(n)}</li>'
        for i, (n, s) in enumerate(items)
    )
    return f'<div class="mb-8" style="font-size:11px;font-weight:700;text-transform:uppercase;letter-spacing:.08em;color:var(--fg3);margin-top:16px">{esc(title)}</div><ul class="chk-list">{lis}</ul>'


EVENT_LABELS = {
    "INTENT_RECEIVED":                 ("Purchase intent received from agent",       "acc"),
    "FACTS_RESOLVED":                  ("Trusted facts resolved from registry",      "acc"),
    "CONTEXT_SCANNED":                 ("Context evidence scanned",                  "acc"),
    "DECISION":                        ("Policy evaluated",                          "acc"),
    "HELD_FOR_APPROVAL":               ("Approval required — held for human review", "warn"),
    "APPROVED":                        ("Authenticated user approved",               "ok"),
    "DECLINED":                        ("User declined",                             "bad"),
    "PAYMENT_CAPTURED":                ("PayPal order created and captured",         "ok"),
    "PAYMENT_FAILED":                  ("Payment adapter failed",                    "bad"),
    "PAYMENT_MISMATCH":                ("PayPal returned mismatched data",           "bad"),
    "APPROVAL_EXPIRED":                ("Approval window expired",                   "bad"),
    "APPROVAL_REFUSED_POLICY_CHANGED": ("Approval refused — policy changed",         "bad"),
    "APPROVAL_REFUSED_FACTS_CHANGED":  ("Approval refused — facts changed",          "bad"),
    "POLICY_REVOKED":                  ("Policy revoked by user",                    "bad"),
    "POLICY_CONFIRMED":                ("Policy confirmed",                          "ok"),
}

BLOCK_LABELS = {
    "UNKNOWN_MERCHANT":            "Merchant is not recognised in the registry",
    "UNKNOWN_PRODUCT":             "Product is not recognised in the registry",
    "PAYEE_NOT_BOUND_TO_PRODUCT":  "Payee is not bound to this product",
    "INVALID_QUANTITY":            "Quantity is out of the allowed range",
    "POLICY_NOT_ACTIVE":           "The active policy is not currently active",
    "MERCHANT_NOT_IN_ALLOWLIST":   "Merchant is not included in your active policy",
    "CATEGORY_NOT_ALLOWED":        "Category is not permitted under this policy",
    "CURRENCY_NOT_ALLOWED":        "Currency is not in the allowed list",
    "EXCEEDS_MAX_SINGLE_PURCHASE": "Amount exceeds the single-purchase limit",
    "EXCEEDS_TOTAL_BUDGET":        "Amount would exceed the total spending budget",
    "HIDDEN_PAYMENT_INSTRUCTION":  "Hidden payment instruction detected in page metadata",
    "PAYMENT_INSTRUCTION_IN_PAGE": "Unexpected payment instruction found in page content",
    "SOURCE_URL_OFF_DOMAIN":       "Source URL does not match the merchant's registered domain",
    "PAYEE_DIFFERS_FROM_PAGE":     "Payee on the page differs from the payee in the purchase",
}


def reason_label(code):
    return BLOCK_LABELS.get(code, code.replace("_", " ").title())


def fmt_expiry(seconds):
    if seconds is None or seconds <= 0:
        return "Expired"
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def layout(title, body, active_nav="", paypal_mode="fake", stats=None, extra_head=""):
    """Full dark-sidebar shell."""
    stats = stats or {}

    def nav_item(icon, label, href, key, badge=None, badge_warn=False):
        cls = ' class="active"' if key == active_nav else ""
        badge_html = ""
        if badge is not None:
            w = " warn" if badge_warn else ""
            badge_html = f'<span class="nav-badge{w}">{badge}</span>'
        return (f'<li><a href="{href}"{cls}>'
                f'<span class="nav-icon">{icon}</span>{esc(label)}{badge_html}</a></li>')

    total = stats.get("captured", 0) + stats.get("awaiting", 0) + stats.get("blocked", 0)
    pending = stats.get("awaiting", 0)

    mode_dot = "●" if paypal_mode == "sandbox" else "○"
    mode_label = "PayPal Sandbox" if paypal_mode == "sandbox" else "REVIEW DEMO"
    mode_sub = "Governed payments" if paypal_mode == "sandbox" else "Hosted mode \u00b7 Simulated payments"

    sidebar = f"""
<aside class="sidebar">
  <div class="sb-brand">
    <div class="logo">
      <div class="logo-icon">T</div>
      <div class="logo-name">TrustGate</div>
    </div>
    <div class="logo-sub">Autonomous commerce</div>
  </div>
  <div class="sb-section">Control plane</div>
  <ul class="sb-nav">
    {nav_item("▦", "Live console", "/console", "console",
              badge=total if total else None)}
    {nav_item("→", "Purchase intents", "/intents", "intents",
              badge=total if total else None)}
    {nav_item("≡", "Audit trail", "/audit", "audit",
              badge=total if total else None)}
    {nav_item("</>", "API surface", "/api-surface", "api")}
  </ul>
  <div class="sb-bottom">
    <div class="paypal-mode">
      <div class="mode-label">Environment</div>
      <div class="mode-val"><span class="live-dot" style="{'background:var(--ok)' if paypal_mode=='sandbox' else 'background:var(--fg3);animation:none'}"></span>{esc(mode_label)}</div>
      <div class="mode-sub">{esc(mode_sub)}</div>
    </div>
    <div class="user-block">
      <div class="user-avatar">A</div>
      <div>
        <div class="user-name">Alex Morgan</div>
        <div class="user-role">Policy owner</div>
      </div>
    </div>
    <button class="logout-button" type="submit" form="logout-form">Sign out</button>
  </div>
</aside>"""

    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · TrustGate</title>
<style>{CSS}</style>{extra_head}
</head><body>
<div class="shell">
  {sidebar}
  <div class="main">
    <div class="topbar">
      <div class="breadcrumb">
        <span>TrustGate</span>
        <span>/</span>
        <span style="color:var(--fg)">{esc(title.upper())}</span>
      </div>
      <div class="status-row">
        <span><span class="live-dot"></span> Event stream synced</span>
        <span style="color:var(--line)">·</span>
        <span>{total} request{"s" if total!=1 else ""} this session</span>
      </div>
    </div>
    <div class="content">
      {body}
    </div>
  </div>
</div>
</body></html>"""
