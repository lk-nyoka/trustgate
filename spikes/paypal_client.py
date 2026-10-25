"""Minimal PayPal REST helper for sandbox spikes.

Sandbox only. Credentials come from environment variables (.env), never from code.
"""
import os
import sys
import time
import uuid

import requests
from dotenv import load_dotenv

load_dotenv()

BASE_URL = "https://api-m.sandbox.paypal.com"  # sandbox endpoint
CURRENCY = os.getenv("PAYPAL_CURRENCY", "USD")


def _require(name):
    value = os.getenv(name)
    if not value:
        sys.exit(f"Missing {name}. Copy .env.example to .env and fill it in.")
    return value


def _check(resp):
    if resp.ok:
        return
    print(f"HTTP {resp.status_code} from {resp.request.method} {resp.request.url}")
    print(f"PayPal-Debug-Id: {resp.headers.get('PayPal-Debug-Id')}")
    print(resp.text)
    sys.exit(1)


def get_access_token():
    resp = requests.post(
        f"{BASE_URL}/v1/oauth2/token",
        auth=(_require("PAYPAL_CLIENT_ID"), _require("PAYPAL_CLIENT_SECRET")),
        data={"grant_type": "client_credentials"},
        timeout=30,
    )
    _check(resp)
    return resp.json()["access_token"]


def call(method, path, token, body=None):
    """Call a PayPal REST endpoint with a fresh idempotency key."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
        "PayPal-Request-Id": str(uuid.uuid4()),
    }
    resp = requests.request(
        method, f"{BASE_URL}{path}", headers=headers, json=body, timeout=30
    )
    _check(resp)
    return resp.json()


def approval_link(order):
    """Return the buyer-approval URL from an order response, if any."""
    for link in order.get("links", []):
        if link.get("rel") in ("payer-action", "approve"):
            return link["href"]
    return None


def wait_for_approval(order_id, token, timeout=180, interval=3):
    """Poll the order until the buyer has approved it. Tolerates temporary PayPal 5xx errors."""
    deadline = time.time() + timeout
    status = None
    while time.time() < deadline:
        try:
            resp = requests.get(
                f"{BASE_URL}/v2/checkout/orders/{order_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=30,
            )
            if resp.status_code >= 500:
                status = f"temporary PayPal error {resp.status_code}"
            elif resp.ok:
                status = resp.json()["status"]
                if status in ("APPROVED", "COMPLETED"):
                    return status
            else:
                _check(resp)
        except requests.RequestException as exc:
            status = f"network error ({exc.__class__.__name__})"
        time.sleep(interval)
    sys.exit(
        f"Order is still {status} after {timeout}s: the buyer approval was not "
        "completed in the browser. Re-run and finish the login + approve step."
    )
