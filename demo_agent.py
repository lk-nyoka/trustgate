"""Scripted stand-in for an AI agent: proposes purchases over HTTP using ONLY the agent API key.

  set AGENT_API_KEY=<key printed by the server>   (PowerShell: $env:AGENT_API_KEY="...")
  python demo_agent.py
"""
import os
import sys
import time
import uuid

import requests

BASE = os.getenv("BASE_URL", "http://127.0.0.1:8000")
KEY = os.getenv("AGENT_API_KEY") or sys.exit("Set AGENT_API_KEY to the key the server printed.")
HEAD = {"Authorization": f"Bearer {KEY}"}


def propose(label, product, merchant="merchant_demo_airlines", url="https://demo-airlines.test/checkout"):
    body = {"merchant_reference": merchant, "product_reference": product, "quantity": 1,
            "source_url": url, "request_id": uuid.uuid4().hex[:12]}
    r = requests.post(f"{BASE}/v1/purchase-intents", json=body, headers=HEAD, timeout=30)
    r.raise_for_status()
    v = r.json()
    print(f"\n=== {label}\ndecision: {v['decision']} | approval: {v['approval_status']} | payment: {v['state']}")
    print("reasons:", ", ".join(v["reason_codes"]))
    return v


propose("Scene 1: $180 flight (within delegated limits)", "cpt-jnb-economy-180")
held = propose("Scene 2: $320 flight (needs the human)", "cpt-jnb-flex-320")
print(f"\nOpen {BASE}/approvals/{held['intent_id']} and approve. Waiting up to 3 minutes...")
for _ in range(90):
    state = requests.get(f"{BASE}/v1/intents/{held['intent_id']}", headers=HEAD, timeout=30).json()
    if state["state"] != "HELD_FOR_APPROVAL":
        print("After human decision:", state["state"], "| approval:", state["approval_status"])
        break
    time.sleep(2)
propose("Scene 3: injected $3 fee to an unlisted merchant", "activation-fee-3",
        merchant="merchant_activation_services", url="https://demo-airlines.test/injected-fee")
r = requests.post(f"{BASE}/v1/approvals/{held['intent_id']}", data={"decision": "APPROVE"}, headers=HEAD, timeout=30)
print(f"\nAgent tries to approve its own purchase: HTTP {r.status_code} ({r.json()['detail']})")
