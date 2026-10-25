# TrustGate

**AI may recommend. Only policy may authorize.**

TrustGate sits between an AI agent and PayPal. The agent can only *propose* a purchase. TrustGate resolves the
real price and payee from its own registry, checks your spending policy with deterministic code, asks a human
to approve exceptions, and only then creates the PayPal order. Every decision is written to a hash-chained
audit log. Built for the *Build What's Next with PayPal and AI* hackathon.

## For judges: try it in 30 seconds

| What | Where |
|---|---|
| Interactive demo, no sign-in | `/try` on the hosted URL (see the Devpost testing instructions) |
| Why, how, evidence, limits | `/how` |
| Human console (queue, approvals, audit) | `/login` (credentials in the Devpost testing instructions) |
| Run it yourself, no PayPal account needed | `pip install -r requirements.txt` then `uvicorn trustgate.demo_app:app --port 8000` (offline mode, PayPal simulated) |

## Hackathon requirements, where each is met

| Requirement | Where |
|---|---|
| PayPal developer platform (Sandbox) is central | Orders v2 create/capture with a vaulted payment token, `PayPal-Request-Id` idempotency, and read-back verification of every order and capture: `trustgate/paypal_adapter.py`, `live_check.py`, `spikes/` |
| Meaningful AI use | (1) plain-English spending rules drafted by a model, validated by code, activated by a human: `trustgate/policy_compiler.py`; (2) an LLM shopping agent: `trustgate/agent.py`; (3) a red-team harness: `trustgate/redteam.py`. The model is never in the authorization path |
| Working demo | Hosted `/try`, or run locally as above. Not a mockup |
| Public repo with a detectable license | MIT, `LICENSE` at the repo root |
| Built during the submission period | Started in October 2026, after the 1 Oct submission start (the first Sandbox check ran on 6 Oct) |
| Demo video under 3 minutes, on YouTube | Linked on the Devpost page |
| Test accounts and keys | `/try` needs none. Offline mode needs none. Sandbox mode: `.env.example` |

**AI tools and models used.** Policy drafting and the shopping agent call an LLM through a small provider-agnostic
client (`trustgate/llm.py`): any OpenAI-compatible endpoint (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`; many have free
tiers) or Anthropic (`ANTHROPIC_API_KEY`). Without a key the product still works: presets replace AI drafting. Deployed on Render.

## What is verified, and what is not

Verified: 129 automated tests; a live Sandbox check on 6 Oct 2026 that completed a $180 payment (allowed) and a
$320 payment (after human approval), re-read both from PayPal and confirmed amounts and currencies, and confirmed no
PayPal order exists for the held and blocked requests. The AI policy drafting was tested against a scripted model and a
local OpenAI-compatible stub, not yet against a hosted model. The red-team harness has not been run on a hosted model and we publish no
detection rates. The page scanner is a heuristic; the policy, not the scanner, is the guarantee.

---

Deterministic authorization between an AI agent and a payment rail. This is the
part of the project that needs no PayPal network access, so it runs and is tested
locally. The PayPal adapter is a stand-in (`trustgate/fake_paypal.py`).

## Run

```bash
pip install -r requirements-dev.txt
python -m pytest -q    # 129 tests
python demo.py         # the three demo scenes, offline
```

## What is tested

- Allow, approval-required and block decisions; merchant, category, currency,
  single-purchase and cumulative-budget limits; unknown merchant/product fail closed
- Approval separation: agent keys cannot approve, confirm or revoke; another user
  cannot approve; approvals expire, cannot be reused, and stop on policy revocation
- Idempotent retries (no double charge); order spec built only from trusted facts;
  captured amount, currency and payee verified against the evaluated facts
- Context can only tighten (500 randomized cases); scanner true and false positives
- Hash-chained audit log with tamper detection

## Real PayPal adapter

`trustgate/paypal_adapter.py` charges the user's vaulted PayPal account with a payment
token (created once by `spikes/spike2_vault.py`), then reads the order back and
returns what PayPal recorded. The service verifies amount, currency and payee against
the facts it evaluated before marking anything captured.

- POST retries reuse the same `PayPal-Request-Id`, so PayPal de-duplicates them
- The adapter's own checks: order status, exactly one completed capture, matching reference
- All mock merchants pay into ONE sandbox business account. The logical merchant payee
  travels in `custom_id` and is verified on read-back; PayPal itself does not enforce it.
  Set `PAYPAL_MERCHANT_ID` to also verify the receiving PayPal merchant.

Live check (sandbox money only): copy `.env` and `.vault_token.json` from `spikes/`
into this folder, `pip install -r requirements.txt`, then `python live_check.py`. It runs the
three scenes, re-reads each order from PayPal, and prints PASS/FAIL for: held purchase created
no order, blocked purchase created no order, orders and captures COMPLETED, captured amounts
and currency, and the audit chain. It exits non-zero if any check fails.

A live run on 2026-10-06 produced two completed sandbox payments ($180 allowed, $320 after
human approval) and no order for the held or blocked requests. Consent was given once in
`spike2_vault.py` (vaulting); the allowed payment then needed no buyer interaction.

The adapter tests use MOCKED responses shaped from PayPal's docs and spike output. They
prove our request building, retries, idempotency and read-back checks, not PayPal's behaviour.

## Run the demo server (landing page, console, approval pages, HTTP API)

Use a virtual environment (on Windows, installing into the global Python can fail while writing
launcher .exe files and leave packages missing):

```powershell
python -m venv venv
.\venv\Scripts\python -m pip install -r requirements.txt
$env:PAYPAL_MODE="sandbox"      # or "fake" to run offline; sandbox needs .env + .vault_token.json
$env:AGENT_API_KEY="agent_demo_key"; $env:DEMO_PASSWORD="choose-a-password"
.\venv\Scripts\python -m uvicorn trustgate.demo_app:app --port 8000
```

Open http://127.0.0.1:8000/ for the landing page, then **Launch demo** and sign in as `demo`.
In a second terminal run the scripted agent:

```powershell
$env:AGENT_API_KEY="agent_demo_key"; .\venv\Scripts\python demo_agent.py
```

It proposes the $180 flight (allowed), the $320 flight (held; approve it in the browser),
and the injected $3 fee (blocked), then tries to approve its own purchase and gets a 403.

Pages: `/` landing (canvas motion background, 3D perspective floor and tilt, request-flow animation,
live counts from the server), `/console` request queue, `/approvals/{id}` decision and receipt,
`/intents/{id}` audit trail. The check lists and flow diagrams on the decision pages are rendered
from what the server actually evaluated and verified (policy checks, approval-binding checks,
whether a PayPal order exists); they are not decorative. Motion respects `prefers-reduced-motion`.
All colors are CSS variables at the top of `trustgate/api.py` and `trustgate/landing.py`.

Two identities: the agent uses a bearer API key and can only `POST /v1/purchase-intents` and
poll its own intents; the human uses a login session (HttpOnly, SameSite=Strict cookie, CSRF
token on every state-changing form) and can approve, decline, revoke the policy and read audit
trails. The agent request body rejects any field beyond merchant/product references, quantity,
source URL and request id. Untrusted page text shown in the UI is HTML-escaped.

## Interactive demo for judges (no sign-in)

`/try` lets a visitor play the owner of an AI agent: run the allowed, held, poisoned-page and over-limit
scenarios, approve or decline as the human, try to cheat (agent self-approval, tampered payee, replayed
request, kill switch) and open the audit trail. Each visitor gets an isolated session; per-visitor and
global request caps apply (`PLAYGROUND_MAX_RUNS`, `PLAYGROUND=0` disables it). `/how` explains why and how it
was built, what is verified and the limits. Set `REPO_URL` to show a Source link.

## Production deployment

`APP_ENV=production` makes the server fail closed at startup unless `AGENT_API_KEY` (>=24 chars),
`DEMO_PASSWORD` (>=12), `ADMIN_KEY` and `CSRF_SECRET` are set; it never prints credentials, forces
Secure cookies and HSTS, and trusts `X-Forwarded-For` for client IPs (set it only behind a proxy).

| Variable | Purpose |
|---|---|
| `PAYPAL_MODE` | `fake` (offline) or `sandbox` |
| `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `PAYPAL_VAULT_ID` | sandbox credentials; the vault id may instead come from `.vault_token.json` |
| `AUDIT_PATH` | JSONL file for the hash-chained audit log; reloaded and re-verified on start (refuses to start if tampered) |
| `RATE_LIMIT_PER_MIN`, `LOGIN_LIMIT_PER_MIN` | per-IP limits (defaults 120 and 10); `/healthz` is exempt |

Included: per-IP rate limiting (stricter on `POST /login`), CSP and other security headers,
JSON request logs with a request id (no headers, cookies, bodies or query strings),
`/healthz` (liveness) and `/readyz` (audit chain valid), non-root Docker image, `render.yaml`,
GitHub Actions CI. Run: `docker build -t trustgate . && docker run -p 8000:8000 --env-file .env trustgate`.
Put the audit file on a persistent disk (the Dockerfile uses `/data`); on Render's free tier the disk is ephemeral.

Remaining prototype limits, stated plainly: purchase intents, approvals and sessions are held in
memory (a restart drops pending approvals; the audit history survives), rate limits are per
process, there is one demo user, and PayPal webhooks are not implemented. Moving intents to a
database is the first step before real use.

## What is NOT tested here

- Any real PayPal call from the build environment (it cannot reach PayPal). The spikes
  proved create, approve, capture and vault charging live; `live_check.py` exercises the
  adapter. Webhooks are not implemented.
- AI policy drafting against a hosted model (tested with a scripted model and a local stub), and any LLM context evaluator
- Persistence of intents and approvals (only the audit log is persisted), multi-user accounts

## Known limits

- The scanner is a heuristic on 4 benign pages and 3 attack pages, not a measured detector
- Cumulative budget counts captured purchases only (not held ones)
- Budget totals assume a single currency
