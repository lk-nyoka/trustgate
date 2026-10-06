"""LIVE sandbox check: runs the three scenes through the real PayPal adapter, then
independently re-reads each order from PayPal and checks the security claims.

Sandbox money only. Needs .env and .vault_token.json from spike 2 in this folder.
"""
import sys
from decimal import Decimal

from tests.conftest import Env
from trust_mw.paypal_adapter import PayPalAdapter

paypal = PayPalAdapter.from_env()
env = Env(payments=paypal)
checks = []


def check(label, ok):
    checks.append(ok)
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")


def payment_ids(intent_id):
    for e in env.svc.get_audit(intent_id):
        if e["event"] == "PAYMENT_CAPTURED":
            return e["data"]["order_id"], e["data"]["capture_id"]
    return None, None


def show(title, view):
    order_id, capture_id = payment_ids(view["intent_id"])
    print(f"\n=== {title}")
    print(f"policy decision: {view['decision']}")
    print(f"approval:        {view['approval_status']}")
    print(f"payment state:   {view['state']}")
    print(f"PayPal order:    {order_id}")
    print(f"capture id:      {capture_id}")
    return order_id


o1 = show("Scene 1: allowed purchase ($180, no human click)",
          env.propose("cpt-jnb-economy-180", url="https://demo-airlines.test/checkout"))
held = env.propose("cpt-jnb-flex-320", url="https://demo-airlines.test/checkout")
o2a = show("Scene 2a: held for approval ($320)", held)
check("held purchase created NO PayPal order", o2a is None)
o2b = show("Scene 2b: after authenticated human approval", env.svc.approve(env.user1, held["intent_id"]))
blocked = env.propose("activation-fee-3", merchant="merchant_activation_services",
                      url="https://demo-airlines.test/injected-fee")
o3 = show("Scene 3: injected $3 fee", blocked)
print("reasons:         ", ", ".join(blocked["reason_codes"]))
check("injected fee created NO PayPal order", o3 is None)

print("\n=== Independent PayPal verification (re-read from PayPal)")
for label, order_id, expected in (("Scene 1", o1, "180.00"), ("Scene 2b", o2b, "320.00")):
    info = paypal.inspect_order(order_id)
    print(f"{label}: {info}")
    check(f"{label}: order COMPLETED", info["order_status"] == "COMPLETED")
    check(f"{label}: capture COMPLETED", info["capture_status"] == "COMPLETED")
    check(f"{label}: captured {expected} USD",
          Decimal(info["amount"]) == Decimal(expected) and info["currency"] == "USD")

check("audit chain valid", env.svc.audit.verify())
print("\nALL CHECKS PASSED" if all(checks) else "\nSOME CHECKS FAILED")
sys.exit(0 if all(checks) else 1)
