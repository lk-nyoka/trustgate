"""LLM agent against the RUNNING server (for the demo video).   (PowerShell)
  $env:ANTHROPIC_API_KEY="..."; $env:AGENT_API_KEY="agent_demo_key"
  .\venv\Scripts\python demo_llm_agent.py --page injected-fee --profile trusting
Pages: checkout (benign), injected-upgrade, injected-fee
"""
import argparse
import os
import sys
import uuid

import requests

from trustgate.agent import run_agent
from trustgate.demo_data import PAGES
from trustgate.llm import AnthropicClient, LLMError
from trustgate.registry import demo_registry

ap = argparse.ArgumentParser()
ap.add_argument("--goal", default="Book the economy flight from Cape Town to Johannesburg.")
ap.add_argument("--page", default="checkout", choices=["checkout", "injected-upgrade", "injected-fee"])
ap.add_argument("--profile", default="plain", choices=["plain", "trusting"])
args = ap.parse_args()
base = os.getenv("BASE_URL", "http://127.0.0.1:8000")
key = os.getenv("AGENT_API_KEY") or sys.exit("Set AGENT_API_KEY to the key the server printed.")
try:
    llm = AnthropicClient()
except LLMError as exc:
    sys.exit(str(exc))


def propose(merchant, product, qty, source_url):
    body = {"merchant_reference": merchant, "product_reference": product, "quantity": qty,
            "source_url": source_url, "request_id": uuid.uuid4().hex[:12]}
    r = requests.post(f"{base}/v1/purchase-intents", json=body, headers={"Authorization": f"Bearer {key}"}, timeout=30)
    r.raise_for_status()
    v = r.json()
    print(f"  proposed {merchant} / {product} -> {v['decision']} | {v['state']} | {', '.join(v['reason_codes'])}")
    return v


url = f"https://demo-airlines.test/{args.page}"
print(f"Goal: {args.goal}\nReading: {url} (profile: {args.profile}, model: {llm.model})")
run = run_agent(llm, args.goal, url, PAGES.get, propose, demo_registry(), args.profile)
print(f"Done in {run['steps']} steps; proposals: {len(run['proposals'])}; notes: {run['notes'] or 'none'}")
