"""Spike 1: create an order, approve it as the sandbox buyer, then capture it."""
from paypal_client import CURRENCY, approval_link, call, get_access_token, wait_for_approval

token = get_access_token()

order = call("POST", "/v2/checkout/orders", token, {
    "intent": "CAPTURE",
    "payment_source": {
        "paypal": {
            "experience_context": {
                "return_url": "https://example.com/return",
                "cancel_url": "https://example.com/cancel",
            }
        }
    },
    "purchase_units": [{
        "reference_id": "spike1",
        "amount": {"currency_code": CURRENCY, "value": "10.00"},
    }],
})
print("Order:", order["id"], "| status:", order["status"])

link = approval_link(order)
print("\nOpen this in a private window and log in with a SANDBOX PERSONAL account:")
print(link)
print("\nWaiting up to 3 minutes for you to approve in the browser...")
print("Order status:", wait_for_approval(order["id"], token))

captured = call("POST", f"/v2/checkout/orders/{order['id']}/capture", token)
print("Status after capture:", captured["status"])
capture = captured["purchase_units"][0]["payments"]["captures"][0]
print("Capture id:", capture["id"], "| amount:", capture["amount"])
