"""Spike 2 (go/no-go): vault a PayPal account once, then charge it with no buyer present.

Run it twice:
  1st run: buyer approves once and the payment token is vaulted and saved locally.
  2nd run: charges the saved token with NO buyer approval. This is the path an
           "ALLOW" decision in the middleware would use.
"""
import json
import sys
from pathlib import Path

from paypal_client import CURRENCY, approval_link, call, get_access_token, wait_for_approval

TOKEN_FILE = Path(".vault_token.json")  # gitignored: a vault token is a credential

token = get_access_token()

if not TOKEN_FILE.exists():
    # Step 1: create an order that asks PayPal to vault the buyer's account.
    order = call("POST", "/v2/checkout/orders", token, {
        "intent": "CAPTURE",
        "payment_source": {
            "paypal": {
                "experience_context": {
                    "return_url": "https://example.com/return",
                    "cancel_url": "https://example.com/cancel",
                },
                "attributes": {
                    "vault": {
                        "store_in_vault": "ON_SUCCESS",
                        "usage_type": "MERCHANT",
                        "customer_type": "CONSUMER",
                    }
                },
            }
        },
        "purchase_units": [{
            "reference_id": "spike2-vault",
            "amount": {"currency_code": CURRENCY, "value": "10.00"},
        }],
    })
    print("Order:", order["id"], "| status:", order["status"])
    print("\nOpen this in a private window and log in with a SANDBOX PERSONAL account:")
    print(approval_link(order))
    print("\nWaiting up to 3 minutes for you to approve in the browser...")
    print("Order status:", wait_for_approval(order["id"], token))

    # Step 2: capture, then read the vault token out of the response.
    captured = call("POST", f"/v2/checkout/orders/{order['id']}/capture", token)
    print("Status after capture:", captured["status"])
    vault = (
        captured.get("payment_source", {})
        .get("paypal", {})
        .get("attributes", {})
        .get("vault")
    )
    print("Vault block:", json.dumps(vault, indent=2))
    if not vault or not vault.get("id"):
        sys.exit(
            "No vault id returned. Possible causes: the merchant account is not "
            "enabled for vaulting / reference transactions, or the token arrives "
            "later via a webhook. Paste this output back for diagnosis."
        )
    TOKEN_FILE.write_text(json.dumps({"vault_id": vault["id"]}))
    print("Saved token locally. Run this script again to charge without the buyer.")
    sys.exit(0)

# Step 3: charge the saved token. No buyer approval is involved.
saved = json.loads(TOKEN_FILE.read_text())
order = call("POST", "/v2/checkout/orders", token, {
    "intent": "CAPTURE",
    "payment_source": {
        "token": {"id": saved["vault_id"], "type": "PAYMENT_METHOD_TOKEN"}
    },
    "purchase_units": [{
        "reference_id": "spike2-charge",
        "amount": {"currency_code": CURRENCY, "value": "29.99"},
    }],
})
print("Status on create:", order["status"])

if order["status"] != "COMPLETED":
    print("Not completed on create; attempting capture...")
    order = call("POST", f"/v2/checkout/orders/{order['id']}/capture", token)
    print("Status after capture:", order["status"])

if order["status"] == "COMPLETED":
    print("\nRESULT: GO - charged a saved PayPal account with no buyer present.")
else:
    print("\nRESULT: NO-GO - see the full response below.")
    print(json.dumps(order, indent=2))
