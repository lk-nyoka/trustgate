# PayPal sandbox spikes

Two throwaway scripts that answer one question before you build: **can the
middleware charge a user's PayPal account with no buyer present after a one-time
approval?** (PayPal's vault / saved payment token flow.)

## Setup

1. In the PayPal Developer Dashboard, open **Apps & Credentials**, switch to
   **Sandbox**, and open the app attached to your sandbox **business** account.
   Copy its Client ID and Secret.
2. `cp .env.example .env` and fill in the two values.
3. `python -m venv venv && source venv/bin/activate && pip install -r requirements.txt`

Never paste the secret into chat, and never commit `.env`. The hackathon repo
must be public.

## Run

```bash
python spike1_order.py   # create -> approve as sandbox buyer -> capture
python spike2_vault.py   # 1st run: approve once and vault; 2nd run: charge with no buyer
```

Approve in a private browser window, logged in as a sandbox **personal**
account (not the business one).

## If it fails

- Currency or feature errors with ZA sandbox accounts: set `PAYPAL_CURRENCY`,
  or create a US business + personal sandbox account and a new app under the US
  business account, then retry. Judges will run your project with their own
  sandbox credentials, so a US-account build is the more portable one.
- "No vault id returned": the business account may not be enabled for vaulting
  or reference transactions, or the token may arrive later by webhook.

## What to report back

Paste the console output, including any `PayPal-Debug-Id` and error JSON. Redact
the vault token and never include the client secret.

`RESULT: GO` from spike 2 means the "ALLOW without a human click" path is viable.
`RESULT: NO-GO` means fall back to routing every purchase through PayPal's
buyer-approval redirect.
