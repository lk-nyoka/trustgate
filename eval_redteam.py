"""Run the red-team evaluation with a real LLM.   (PowerShell)
  $env:ANTHROPIC_API_KEY="..."; .\venv\Scripts\python eval_redteam.py --profile both --trials 1
Writes evaluation/results.json. Payments are simulated; no sandbox money is used.
"""
import argparse
import sys

from trustgate.llm import AnthropicClient, LLMError
from trustgate.redteam import print_summary, run_evaluation

ap = argparse.ArgumentParser()
ap.add_argument("--profile", default="both", choices=["plain", "trusting", "both"])
ap.add_argument("--trials", type=int, default=1)
ap.add_argument("--limit", type=int, help="only the first N adversarial pages (quick test)")
ap.add_argument("--out", default="evaluation/results.json")
args = ap.parse_args()
try:
    llm = AnthropicClient()
except LLMError as exc:
    sys.exit(str(exc))
profiles = ("plain", "trusting") if args.profile == "both" else (args.profile,)
result = run_evaluation(llm, profiles, args.trials, args.limit, model=llm.model, out_path=args.out,
                        progress=lambda *a: print(" ", *a))
print_summary(result)
print(f"\nSaved {args.out}")
