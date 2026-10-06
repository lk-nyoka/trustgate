"""Policy schema and the deterministic evaluator.

Context signals (page scanner, LLM) can only add friction. They can never turn a
hard-policy denial into an allow. That is enforced structurally: the final decision
is the stricter of the hard-policy result and the context result.
"""
from dataclasses import dataclass
from decimal import Decimal

SEVERITY = {"ALLOW": 0, "APPROVAL_REQUIRED": 1, "BLOCK": 2}
CONTEXT_ACTIONS = {"REQUIRE_APPROVAL": "APPROVAL_REQUIRED", "BLOCK": "BLOCK"}  # no "IGNORE"
DEFAULT_CONTEXT_ACTION = "REQUIRE_APPROVAL"


@dataclass(frozen=True)
class Policy:
    policy_id: str
    version: int
    user_id: str
    status: str  # DRAFT | ACTIVE | REVOKED
    merchant_allowlist: frozenset
    category_allowlist: frozenset
    currency_allowlist: frozenset
    max_single_purchase: Decimal
    max_total_spend: Decimal
    auto_approve_up_to: Decimal
    approval_expiry_minutes: int = 10
    context_actions: tuple = ()  # ((flag, "REQUIRE_APPROVAL" | "BLOCK"), ...)

    def __post_init__(self):
        if self.status not in {"DRAFT", "ACTIVE", "REVOKED"}:
            raise ValueError(f"bad status {self.status}")
        if min(self.max_single_purchase, self.max_total_spend, self.auto_approve_up_to) < 0:
            raise ValueError("limits must be non-negative")
        if self.auto_approve_up_to > self.max_single_purchase:
            raise ValueError("auto_approve_up_to cannot exceed max_single_purchase")
        for _, action in self.context_actions:
            if action not in CONTEXT_ACTIONS:
                raise ValueError("context checks can only tighten decisions")

    def context_action(self, flag):
        return dict(self.context_actions).get(flag, DEFAULT_CONTEXT_ACTION)


@dataclass(frozen=True)
class TrustedFacts:
    merchant_id: str
    merchant_name: str
    payee_id: str
    category: str
    product_id: str
    product_name: str
    unit_amount: Decimal
    quantity: int
    amount: Decimal
    currency: str


@dataclass(frozen=True)
class Decision:
    decision: str
    reasons: tuple
    hard_result: str
    context_result: str
    checks: tuple = ()  # ((label, "pass" | "fail" | "review"), ...) exactly as evaluated


def evaluate(policy, facts, spent_so_far, flags):
    ok = {
        "active": policy.status == "ACTIVE",
        "merchant": facts.merchant_id in policy.merchant_allowlist,
        "category": facts.category in policy.category_allowlist,
        "currency": facts.currency in policy.currency_allowlist,
        "single": facts.amount <= policy.max_single_purchase,
        "budget": spent_so_far + facts.amount <= policy.max_total_spend,
    }
    codes = {"active": "POLICY_NOT_ACTIVE", "merchant": "MERCHANT_NOT_IN_POLICY",
             "category": "CATEGORY_NOT_IN_POLICY", "currency": "CURRENCY_NOT_IN_POLICY",
             "single": "EXCEEDS_MAX_SINGLE_PURCHASE", "budget": "EXCEEDS_TOTAL_BUDGET"}
    hard_reasons = [codes[k] for k, passed in ok.items() if not passed]
    auto_ok = facts.amount <= policy.auto_approve_up_to

    if hard_reasons:
        hard = "BLOCK"
    elif not auto_ok:
        hard, hard_reasons = "APPROVAL_REQUIRED", ["AMOUNT_EXCEEDS_AUTO_APPROVAL_LIMIT"]
    else:
        hard, hard_reasons = "ALLOW", ["WITHIN_DELEGATED_LIMITS"]

    context, context_reasons = "ALLOW", []
    for flag in flags:
        result = CONTEXT_ACTIONS[policy.context_action(flag.flag)]
        if SEVERITY[result] > SEVERITY[context]:
            context = result
        context_reasons.append(f"CONTEXT_{flag.flag}")

    final = hard if SEVERITY[hard] >= SEVERITY[context] else context
    if final != "ALLOW" and hard == "ALLOW":
        hard_reasons = []
    reasons = tuple(dict.fromkeys(hard_reasons + context_reasons))

    def mark(passed):
        return "pass" if passed else "fail"

    checks = (
        ("Spending policy is active", mark(ok["active"])),
        ("Merchant is on the allowlist", mark(ok["merchant"])),
        ("Category is allowed", mark(ok["category"])),
        ("Currency is allowed", mark(ok["currency"])),
        ("Within the per-purchase limit", mark(ok["single"])),
        ("Within the total budget", mark(ok["budget"])),
        ("Below the automatic-approval threshold", "pass" if auto_ok else "review"),
        ("Page context has no payment instructions" if not flags else "Page context raised flags",
         "pass" if not flags else "review"),
    )
    return Decision(final, reasons, hard, context, checks)
