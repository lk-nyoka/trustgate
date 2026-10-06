# TrustGate

**Trust infrastructure for autonomous commerce.**

> AI may recommend. Only policy may authorize.

TrustGate is a deterministic authorization middleware that sits between an AI agent and PayPal. The agent can only propose purchases; the middleware resolves trusted facts, evaluates policy, optionally requests human approval, and only then allows the PayPal adapter to proceed.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

---

## Quick start (recommended self-contained setup)

From the app root, not the outer project folder:

```powershell
cd "C:\Users\ASUS\Desktop\Coding Projects\Build or Die\Week 3\Hackathon\paypal\trust-middleware"

.\.venv\Scripts\Activate.ps1
python -m uvicorn trust_mw.demo_app:app --reload
```

Then open:

```text
http://127.0.0.1:8000/console
```

If activation is blocked, run without activation:

```powershell
cd "C:\Users\ASUS\Desktop\Coding Projects\Build or Die\Week 3\Hackathon\paypal\trust-middleware"
.\.venv\Scripts\python.exe -m uvicorn trust_mw.demo_app:app --reload
```

If `.venv` does not exist yet:

```powershell
cd "C:\Users\ASUS\Desktop\Coding Projects\Build or Die\Week 3\Hackathon\paypal\trust-middleware"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn trust_mw.demo_app:app --reload
```

This keeps the app self-contained in `trust-middleware/.venv`, which is the easiest path for judges and clean-clone verification.

### macOS/Linux

```bash
cd trust-middleware
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest -q
python -m uvicorn trust_mw.demo_app:app --reload
```

If PowerShell activation is blocked on Windows, use:

```powershell
.\.venv\Scripts\python.exe -m uvicorn trust_mw.demo_app:app --reload
```

Before opening the browser, verify the app from a separate terminal:

```powershell
cd "C:\Users\ASUS\Desktop\Coding Projects\Build or Die\Week 3\Hackathon\paypal\trust-middleware"
.\.venv\Scripts\python.exe -m pytest -q
```

Expected result:

```text
130 passed
```

Avoid running `live_check.py` just to open the frontend; it creates real PayPal Sandbox payments and should only be used when you intentionally want to verify the live payment integration.

---

## What it is

TrustGate is a developer control plane for agentic payments.

The live console uses a **scripted demo agent** when no model key is configured. Set `ANTHROPIC_API_KEY` in `.env` to enable live Anthropic tool calling. Both modes expose only `search_products`, `get_product_details`, and `propose_purchase`; only product references are submitted, while trusted price and payee details are resolved server-side. The model cannot approve, capture, or call PayPal directly.

To enable the live model, copy `.env.example` to `.env`, set `ANTHROPIC_API_KEY`, and restart Uvicorn. Optionally set `ANTHROPIC_MODEL`; the default is `claude-sonnet-5-5`. The startup banner reports whether the active mode is Anthropic or scripted.

When an AI agent proposes a purchase:

1. The agent submits only non-authoritative references, such as a merchant reference, product reference, quantity, and optional source context.
2. The middleware resolves the trusted merchant, payee, price, currency, category, and product details from its server-side registry.
3. A deterministic policy engine returns `ALLOW`, `APPROVAL_REQUIRED`, or `BLOCK`.
4. A deterministic page scanner records suspicious source-context signals such as hidden payment instructions.
5. If approval is required, an authenticated human reviews the exact server-derived transaction facts and approves or declines the request.
6. Only a permitted request or an approved held request reaches the PayPal adapter.
7. Every decision and payment transition is written to a hash-chained, tamper-evident audit log.

**Blocked requests never create a PayPal order.** No order ID. No capture ID.

---

## Problem

AI agents can propose payments. A prompt telling them to "ask first" is not an independently enforceable authorization boundary. TrustGate enforces the boundary in server-side code, outside the agent's reach.

---

## Demo flow

The judge-facing narrative is:

1. A pending $320 request appears in the console.
2. An authenticated human approves it.
3. The governed flow captures the payment.
4. A malicious $3 request is blocked before PayPal is called.
5. The audit trail shows the exact decision path.

---

## AI integration

When configured, the Anthropic assistant interprets the user's request and may call three tools: `search_products`, `get_product_details`, and `propose_purchase`. Without an API key, a scripted assistant demonstrates the same governed proposal path. In either mode, the scanner and deterministic policy run server-side, and authenticated human approval controls held payments.

---

## Security model

| Property | Mechanism |
|---|---|
| Agent cannot supply authoritative price/payee | Request schema accepts references only; trusted facts come from the server-side registry |
| Unexpected agent fields are rejected | Request model uses `extra="forbid"` |
| Agent cannot approve its own purchase | Agent bearer tokens are rejected at `/v1/approvals/` with HTTP 403 |
| Approval is bound to exact facts | Approval-time checks compare merchant, product, payee, amount, currency, policy version, and expiry |
| Blocked requests never reach PayPal | The PayPal adapter runs only after `ALLOW` or authenticated approval of a held intent |
| Decisions are tamper-evident | SHA-256 hash-chained audit log with `chain_valid` verification |
| Policy revocation is immediate | The kill switch pauses authorization and held requests fail approval checks |

---

## Architecture

```
AI Agent
   │  propose_purchase(merchant_reference, product_reference, quantity, source_context)
   ▼
Purchase Intent API          ← agent API key, rejects extra fields
   ▼
Trusted Fact Resolution      ← merchant registry, product catalogue, payee binding
   ▼
Deterministic Policy Engine  ← merchant allowlist, category, currency, limits, thresholds
   ▼
Context Safety Layer         ← DOM scanner: hidden JSON-LD, CSS-hidden text, phrase patterns
   ▼
Human Approval Service       ← session cookie + CSRF, agent bearer rejected with 403
   ▼
PayPal Execution Adapter     ← create order, capture, read-back verify
   ▼
PayPal Sandbox
   ▼
Audit Log                    ← SHA-256 hash-chained, append-only
```

---

## API reference

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/v1/purchase-intents` | Bearer API key | Agent proposes a purchase |
| `POST` | `/v1/approvals/{intent_id}` | Session + CSRF | Human approves or declines |
| `GET` | `/v1/intents/{intent_id}` | Delegated-user session or authorized admin key | Poll intent state |
| `GET` | `/v1/intents/{intent_id}/audit` | Session or admin key | Hash-chained audit trail |
| `POST` | `/api/login` | — | JSON login |
| `GET` | `/api/me` | Session | Current user + stats + policy |
| `GET` | `/api/intents` | Session | All intents for this user |

---

## PayPal integration

- **Environment:** PayPal Sandbox only; all merchants and payment scenarios are fictional.
- **Payment flow:** PayPal Orders v2 create-and-capture flow using the validated saved-payment-token path.
- **Credential boundary:** PayPal credentials and the vault token remain server-side and are never exposed to the agent or browser.
- **Read-back verification:** After capture, the adapter re-reads the PayPal order and confirms order/capture completion, amount, currency, and the internal merchant/payee binding represented in the Sandbox order (`custom_id`). All demo merchants share one Sandbox business account, so this prototype does **not** provide universal PayPal recipient-identity verification; the UI labels this check "Configured payee binding verified".
- **Blocked requests:** Blocked requests do not create a PayPal order.
- **Human approval:** PayPal execution occurs only after the middleware confirms the held intent is still valid and the authenticated user has approved it.

---

## Live checks

```bash
python live_check.py
```

> This command creates PayPal Sandbox payments and should only be run when Sandbox credentials and buyer-consent setup are available.

The normal verification command remains:

```bash
python -m pytest -q
```

---

## Test scenarios

| Scenario | Merchant | Amount | Decision | Outcome |
|---|---|---|---|---|
| Safe flight | Demo Airlines | $180 | ALLOW | Auto-captured |
| Flexible flight | Demo Airlines | $320 | APPROVAL_REQUIRED | Held; human approves → captured |
| Injected fee | Activation Services Demo | $3 | BLOCK | PayPal never called |

---

## Verified results

### Application and frontend verification

- **130 tests passed** with `python -m pytest -q`
- React production build succeeds with `npm run build` from `frontend/`
- Desktop and mobile layouts checked; reduced-motion behavior is supported
- Sign-in journey covers empty, invalid, valid, refresh, logout, back-cache protection, and eight-hour expiry
- `git diff --check` passes
- Approval mutation checks cover trusted price, payee, and policy-version changes
- Agent approval attempts are rejected
- Blocked and held intents do not invoke the PayPal adapter
- Audit-chain verification passes

### PayPal Sandbox verification

- **2 governed Sandbox payments completed in the prior live verification**
- Captured amounts independently verified as `$180.00 USD` and `$320.00 USD`
- Both PayPal orders and captures returned `COMPLETED`
- **1 held request created no PayPal order**
- **1 blocked injected-fee request created no PayPal order**
- Audit chain verified

> Sandbox payment results are historical integration evidence, separate from the current automated tests and local frontend build. The public Render demo uses `PAYPAL_MODE=fake`; it never submits payments to PayPal.

---

## Which UI should judges use?

The **server-rendered console** (`/console`, served by `trust_mw/api.py`) is the authoritative interface and the one deployed on Render. The hosted review experience uses the server-rendered console. The React app in `frontend/` is optional/experimental and is not required for the judge walkthrough; it is an optional client for the same JSON API (`npm install && npm run build`, then open `/app`); it is not part of the hosted deployment path.

## Review demo vs. PayPal Sandbox

| | Hosted review demo (`PAYPAL_MODE=fake`, default) | Local integration (`PAYPAL_MODE=sandbox`) |
|---|---|---|
| Payments | **Simulated** fake adapter, never contacts PayPal, no credentials loaded | Real PayPal Sandbox Orders v2 |
| State | **One isolated workspace per login**: fresh active policy, the $180 / $320 / $3 scenarios, own audit chain and kill switch | One shared in-memory service |
| UI banner | `REVIEW DEMO · Hosted mode · Simulated payments`, `Adapter: SIMULATED` | `PayPal Sandbox · Governed payments`, `Adapter: PAYPAL SANDBOX` |
| Reset | `Reset review workspace` button / `POST /api/demo/reset` (session + CSRF, rate-limited) restores only your workspace | n/a |

One reviewer revoking the policy, approving purchases or resetting never affects another reviewer. The sandbox path needs `.env` credentials and `.vault_token.json` and is run locally with `PAYPAL_MODE=sandbox`.

## Public review deployment

The repository is public at <https://github.com/lk-nyoka/trustgate>. GitHub Pages cannot run this FastAPI backend; use the included Render Blueprint to host the complete application:

1. Open <https://render.com/deploy?repo=https://github.com/lk-nyoka/trustgate> and sign in to Render.
2. Review the `trustgate-review` web service and deploy it.
3. Open the generated `*.onrender.com/console` URL.

Review login: username `demo`, password `TrustGateReview2026!`. This is a shared public demo account, the app runs the fake payment adapter, and in-memory state may reset when the free service sleeps or restarts. Do not put real credentials or payment data into this review instance.

---

## Demo access

After starting the app, open:

```text
http://127.0.0.1:8000/console
```

Use the seeded demo user shown in the application startup output or in the judge setup instructions.

The application is intentionally limited to fictional merchants and PayPal Sandbox data. Do not use real credentials or production payment details.

---

## Setup for judges

### Windows

```powershell
cd trust-middleware
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python -m pytest -q
```

### macOS/Linux

```bash
cd trust-middleware
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pytest -q
```

To launch the app after setup:

```bash
python -m uvicorn trust_mw.demo_app:app --reload
```

---

## Known limitations

- Requires **Python 3.12+** (pinned in `.python-version` and `render.yaml`)
- Budget is cumulative: captured spend plus live held (approval-pending) reservations. Held intents release their reservation when declined, expired or when the policy is revoked, and approval re-checks the remaining budget under a lock
- Idempotency keys are bound to a canonical payload hash; reusing a `request_id` with a different payload returns HTTP 409
- The scanner detects selected hidden-content and payment-instruction patterns (hidden JSON-LD, off-screen/CSS-hidden text, visible payment phrases). It is a defense-in-depth signal, not a general prompt-injection detector; HTML comments, `meta`/`alt` attributes and paraphrased instructions are not reliably detected
- The demo's hostile source context is simulated and supplied to the agent; it is not an agent browsing arbitrary pages
- Approval expiry: the policy default and the test suite use **10 minutes**; hosted review-demo workspaces deliberately seed **60 minutes** so reviewers have time to read the console. The 60-minute value is a demo convenience, not a production policy default
- Hosted review demo state is in memory: each login gets its own isolated workspace (idle workspaces are evicted after 2 hours, at most 200 live, sign-ins and resets are rate-limited), but everything is lost when the free Render service sleeps or restarts

- In-memory state — restart clears all intents
- Single process, single demo user
- Scanner is heuristic and not a general-purpose detector
- Cumulative budget counts captured purchases only
- Webhooks not implemented

---

## License

MIT — see [LICENSE](LICENSE)

---

*Built for the PayPal AI Hackathon 2026. All merchants are fictional. PayPal Sandbox only.*
