"""Offline demo of the three scenes, using the fake PayPal adapter (no network)."""
from tests.conftest import Env


def show(title, view, env):
    print(f"\n=== {title}")
    print(f"policy decision: {view['decision']} | approval: {view['approval_status']} | payment state: {view['state']}")
    print(f"reasons:  {', '.join(view['reason_codes'])}")
    if view["trusted_purchase"]:
        t = view["trusted_purchase"]
        print(f"trusted:  {t['merchant']} | {t['product']} | {t['amount']} {t['currency']} | payee {t['payee_id']}")


env = Env()
r1 = env.propose("cpt-jnb-economy-180", url="https://demo-airlines.test/checkout")
show("Scene 1: allowed purchase", r1, env)

r2 = env.propose("cpt-jnb-flex-320", url="https://demo-airlines.test/checkout")
show("Scene 2a: above threshold, held", r2, env)
r2 = env.svc.approve(env.user1, r2["intent_id"])
show("Scene 2b: after authenticated human approval", r2, env)

r3 = env.propose("activation-fee-3", merchant="merchant_activation_services",
                 url="https://demo-airlines.test/injected-fee")
show("Scene 3: injected $3 fee to an unlisted merchant", r3, env)

print(f"\nPayments executed: {len(env.payments.calls)} (expected 2). Audit chain valid: {env.svc.audit.verify()}")
