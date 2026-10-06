# TrustGate

**Trust infrastructure for autonomous commerce.**

> AI may recommend. Only policy may authorize.

TrustGate is a deterministic authorization middleware that sits between an AI agent and PayPal. The agent can only *propose* purchases — the middleware resolves trusted facts, evaluates policy, optionally requests human approval, and only then calls PayPal.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

---

## What it is

A **developer control plane for agentic payments**. When an AI agent proposes a purchase:

1. The agent submits only references (merchant ID, product ID, quantity)
2. The middleware resolves the real merchant, payee, price, and category from its registry
3. A deterministic policy engine evaluates 8 checks and returns `ALLOW`, `APPROVAL_REQUIRED`, or `BLOCK`
4. A DOM scanner checks the source page for hidden payment instructions
5. If `APPROVAL_REQUIRED`, a human reviews server-derived facts in the browser and approves or declines
6. Only after `ALLOW` or human `APPROVE` does the PayPal adapter create an order and capture payment
7. Every decision is written to a hash-chained, tamper-detectable audit log

**Blocked requests never create a PayPal order.** No order ID. No capture ID.

---

## Problem

AI agents can propose payments. A prompt telling them to "ask first" is not an independently enforceable authorization boundary. TrustGate enforces the boundary in server-side code, outside the agent's reach.

---

## Demo

### Hosted
> Coming soon — see local run below

### Quick local run
```powershell
git clone https://github.com/lk-nyoka/trustgate.git
cd trustgate

# Python backend (fake PayPal, no credentials needed)
pip install -r requirements.txt
python -m uvicorn trust_mw.demo_app:app --port 8000

# React frontend (separate terminal)
cd frontend
npm install
npm run dev
```

Open **http://localhost:5173** — log in with `demo` / (password printed in backend console).

### With PayPal Sandbox
```powershell
cp .env.example .env
# Fill in PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET
# Copy .vault_token.json from a spike2_vault.py run
$env:PAYPAL_MODE="sandbox"
python -m uvicorn trust_mw.demo_app:app --port 8000
```

---

## Architecture

```
AI Agent
   │  propose_purchase(merchant_ref, product_ref, quantity, source_url)
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

## Security model

| Property | Mechanism |
|---|---|
| Agent cannot supply price/payee | `extra="forbid"` on the request model; registry resolves all facts |
| Agent cannot approve its own purchase | Bearer tokens rejected at `/v1/approvals/` with HTTP 403 |
| Approval bound to exact facts | 7-item binding check at approval time; any change → block |
| Blocked requests never reach PayPal | PayPal adapter only called after ALLOW or human APPROVE |
| Tamper-evident decisions | SHA-256 hash-chained audit log; `chain_valid` on every audit read |
| Policy revocation | Instant kill switch; any held requests refuse approval after revocation |

---

## API reference

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/v1/purchase-intents` | Bearer API key | Agent proposes a purchase |
| `POST` | `/v1/approvals/{intent_id}` | Session + CSRF | Human approves or declines |
| `GET` | `/v1/intents/{intent_id}` | Bearer or session | Poll intent state |
| `GET` | `/v1/intents/{intent_id}/audit` | Session or admin key | Hash-chained audit trail |
| `POST` | `/api/login` | — | JSON login (React frontend) |
| `GET` | `/api/me` | Session | Current user + stats + policy |
| `GET` | `/api/intents` | Session | All intents for this user |

---

## PayPal integration

- **Orders v2** — create + capture via saved payment token (vault flow)
- **Vault token** — established once via `spike2_vault.py`, held server-side, never exposed to the agent
- **Idempotency** — same `PayPal-Request-Id` on retries; PayPal deduplicates
- **Read-back verification** — after capture, the adapter GETs the order and verifies amount, currency, and payee independently
- **Sandbox** — all payments go to a fictional sandbox merchant; no real money

---

## AI integration

- **Agent** — submits purchase intents via a scoped API key; receives only policy decisions and intent state
- **Context scanner** — deterministic DOM scanner detects hidden payment instructions injected into pages the agent browsed
- **Policy engine** — deterministic Python function; not an LLM decision

The final authorization decision is deliberately independent of the AI. The agent cannot override it.

---

## Demo credentials

| | Value |
|---|---|
| Demo login | `demo` / *(printed in backend console at startup)* |
| API key | *(printed in backend console at startup)* |
| PayPal mode | `fake` (offline) or `sandbox` (needs `.env`) |

---

## Test scenarios

| Scenario | Merchant | Amount | Decision | Outcome |
|---|---|---|---|---|
| Safe flight | Demo Airlines | $180 | ALLOW | Auto-captured, no human needed |
| Flexible flight | Demo Airlines | $320 | APPROVAL_REQUIRED | Held; human approves → captured |
| Injected fee | Activation Services Demo | $3 | BLOCK | PayPal never called |

---

## Verified results

- **56 tests passed** (pytest)
- **2 PayPal Sandbox payments** captured and independently verified via read-back
- **1 injected fee blocked** before PayPal order creation
- **Audit chain verified** — SHA-256 hash chain intact

---

## Setup for judges

```powershell
git clone https://github.com/lk-nyoka/trustgate.git
cd trustgate
pip install -r requirements.txt

# Offline demo (no PayPal credentials needed)
python -m uvicorn trust_mw.demo_app:app --port 8000
# Login: demo / <see console output>

# Run tests
python -m pytest -q
```

For the React UI:
```powershell
cd frontend
npm install
npm run dev   # http://localhost:5173
```

---

## Known limitations

- In-memory state — restart clears all intents
- Single process, single demo user
- Scanner is heuristic on 7 test pages; not a trained detector
- Cumulative budget counts captured purchases only
- Webhooks not implemented
- LLM policy compiler and LLM context evaluator require an API key (not tested here)

---

## License

MIT — see [LICENSE](LICENSE)

---

*Built for the PayPal AI Hackathon 2026. All merchants are fictional. PayPal Sandbox only.*
