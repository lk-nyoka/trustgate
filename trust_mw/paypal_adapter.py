"""Real PayPal Orders v2 adapter (sandbox by default).

Charges the delegating user's vaulted PayPal account with a payment token, then reads
the order back and returns what PayPal actually recorded, so the service can verify
amount, currency and payee against the facts it evaluated.

The vault token is a credential. Only this adapter holds it; the agent never sees it.
All mock merchants pay into ONE sandbox business account, so the logical merchant
payee travels in `custom_id` and is verified on read-back (not enforced by PayPal).
"""
import json
import os
import time
from decimal import Decimal
from pathlib import Path

import requests

from .service import PaymentResult

SANDBOX_URL = "https://api-m.sandbox.paypal.com"


class PaymentError(Exception):
    pass


class PayPalAdapter:
    def __init__(self, client_id, client_secret, vault_token, base_url=SANDBOX_URL,
                 expected_merchant_id=None, session=None, sleep=time.sleep):
        self._client_id = client_id
        self._client_secret = client_secret
        self._vault_token = vault_token
        self._base_url = base_url
        self._expected_merchant_id = expected_merchant_id
        self._http = session or requests.Session()
        self._sleep = sleep
        self._access = None
        self._access_expiry = 0.0

    @classmethod
    def from_env(cls, token_file=".vault_token.json"):
        from dotenv import load_dotenv
        load_dotenv()
        return cls(os.environ["PAYPAL_CLIENT_ID"], os.environ["PAYPAL_CLIENT_SECRET"],
                   json.loads(Path(token_file).read_text())["vault_id"],
                   expected_merchant_id=os.getenv("PAYPAL_MERCHANT_ID") or None)

    def _access_token(self):
        if self._access and time.time() < self._access_expiry - 30:
            return self._access
        resp = self._http.post(f"{self._base_url}/v1/oauth2/token",
                               auth=(self._client_id, self._client_secret),
                               data={"grant_type": "client_credentials"}, timeout=30)
        if not resp.ok:
            raise PaymentError(f"PayPal auth failed: HTTP {resp.status_code}")
        data = resp.json()
        self._access = data["access_token"]
        self._access_expiry = time.time() + float(data.get("expires_in", 300))
        return self._access

    def _request(self, method, path, body=None, request_id=None, retries=2):
        headers = {"Authorization": f"Bearer {self._access_token()}",
                   "Content-Type": "application/json", "Prefer": "return=representation"}
        if request_id:
            headers["PayPal-Request-Id"] = request_id  # same key on retry => PayPal will not double-charge
        if method == "POST" and not request_id:
            retries = 0  # never retry a POST that PayPal cannot de-duplicate
        last = None
        for attempt in range(retries + 1):
            try:
                resp = self._http.request(method, f"{self._base_url}{path}", headers=headers,
                                          json=body, timeout=30)
            except requests.RequestException as exc:
                last = f"network error ({exc.__class__.__name__})"
            else:
                if resp.status_code < 500:
                    if not resp.ok:
                        raise PaymentError(f"PayPal HTTP {resp.status_code}: {resp.text[:300]}")
                    return resp.json()
                last = f"HTTP {resp.status_code}"
            self._sleep(0.5 * (attempt + 1))
        raise PaymentError(f"PayPal unavailable after retries ({last})")

    def charge(self, spec, idempotency_key):
        body = {
            "intent": "CAPTURE",
            "payment_source": {"token": {"id": self._vault_token, "type": "PAYMENT_METHOD_TOKEN"}},
            "purchase_units": [{
                "reference_id": spec["reference_id"],
                "custom_id": spec["payee_id"],
                "amount": {"currency_code": spec["currency"], "value": spec["amount"]},
            }],
        }
        order = self._request("POST", "/v2/checkout/orders", body, request_id=idempotency_key)
        if order.get("status") != "COMPLETED":
            raise PaymentError(f"order {order.get('id')} not completed: {order.get('status')}")
        verified = self._request("GET", f"/v2/checkout/orders/{order['id']}")
        return self._to_result(verified, spec)

    def _to_result(self, order, spec):
        unit = order["purchase_units"][0]
        if unit.get("reference_id") != spec["reference_id"]:
            raise PaymentError("order reference_id does not match the intent")
        captures = unit.get("payments", {}).get("captures", [])
        if len(captures) != 1 or captures[0].get("status") != "COMPLETED":
            raise PaymentError("expected exactly one COMPLETED capture")
        cap = captures[0]
        payee = cap.get("custom_id") or unit.get("custom_id")
        merchant_id = unit.get("payee", {}).get("merchant_id")
        if self._expected_merchant_id and merchant_id != self._expected_merchant_id:
            payee = f"UNEXPECTED_PAYEE:{merchant_id}"
        return PaymentResult(order["status"], order["id"], Decimal(cap["amount"]["value"]),
                             cap["amount"]["currency_code"], payee, cap.get("id"))

    def inspect_order(self, order_id):
        """Independent read of what PayPal has on record (no secrets, no token)."""
        order = self._request("GET", f"/v2/checkout/orders/{order_id}")
        unit = order["purchase_units"][0]
        caps = unit.get("payments", {}).get("captures", [])
        cap = caps[0] if caps else {}
        return {"order_id": order["id"], "order_status": order["status"],
                "capture_id": cap.get("id"), "capture_status": cap.get("status"),
                "amount": cap.get("amount", {}).get("value"),
                "currency": cap.get("amount", {}).get("currency_code"),
                "payment_source": sorted(order.get("payment_source", {})),
                "reference_id": unit.get("reference_id")}
