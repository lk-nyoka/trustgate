"""Plain-English text for reason codes and audit events (shared by the console and the playground)."""

AGENT_CANNOT_APPROVE = "agent credentials cannot approve purchases"

REASON_TEXT = {
    "WITHIN_DELEGATED_LIMITS": "Within the limits you delegated",
    "AMOUNT_EXCEEDS_AUTO_APPROVAL_LIMIT": "Above the automatic-approval threshold, so a human must decide",
    "MERCHANT_NOT_IN_POLICY": "Merchant is not on your policy's allowlist",
    "CATEGORY_NOT_IN_POLICY": "Category is not allowed by your policy",
    "UNKNOWN_MERCHANT": "Merchant is not in the trusted registry",
    "UNKNOWN_PRODUCT": "Product is not in the trusted registry",
    "PAYEE_NOT_BOUND_TO_PRODUCT": "Payee is not the registered payee for this product",
    "CURRENCY_NOT_IN_POLICY": "Currency is not allowed by your policy",
    "EXCEEDS_MAX_SINGLE_PURCHASE": "Above the per-purchase limit",
    "EXCEEDS_TOTAL_BUDGET": "Would exceed the total budget",
    "POLICY_NOT_ACTIVE": "Spending policy is revoked or inactive",
    "POLICY_REVOKED_OR_CHANGED": "Spending policy was revoked or changed",
    "TRUSTED_FACTS_CHANGED": "Trusted facts changed since the decision",
    "INVALID_QUANTITY": "Quantity is not valid",
    "NOT_DELEGATING_USER": "This agent is not delegated by that user",
    "NOT_POLICY_OWNER": "That user does not own this policy",
    "SOURCE_URL_OFF_DOMAIN": "Source page is not on the merchant's registered domain",
    "CONTEXT_HIDDEN_PAYMENT_INSTRUCTION": "The page the agent read contained a hidden payment instruction",
    "CONTEXT_PAYMENT_INSTRUCTION_IN_PAGE": "The page the agent read contained a payment instruction",
    "CONTEXT_PAYEE_DIFFERS_FROM_PAGE": "The payee differs from the merchant whose page the agent was on",
}
EVENT_TEXT = {
    "INTENT_RECEIVED": "Purchase intent received from the agent (claims treated as untrusted)",
    "FACTS_RESOLVED": "Trusted facts resolved from the server registry",
    "CONTEXT_SCANNED": "Page context scanned",
    "DECISION": "Policy decision made",
    "HELD_FOR_APPROVAL": "Held for human approval",
    "APPROVED": "Approved by the signed-in human",
    "DECLINED": "Declined by the signed-in human",
    "APPROVAL_EXPIRED": "Approval window expired",
    "APPROVAL_REFUSED_POLICY_CHANGED": "Approval refused: policy changed since evaluation",
    "APPROVAL_REFUSED_FACTS_CHANGED": "Approval refused: trusted facts changed since evaluation",
    "PAYMENT_CAPTURED": "PayPal order created and captured; amount, currency and payee verified",
    "PAYMENT_FAILED": "Payment attempt failed",
    "PAYMENT_MISMATCH": "PayPal result did not match the evaluated facts; not marked captured",
    "POLICY_CONFIRMED": "Spending policy confirmed by the user",
    "POLICY_REVOKED": "Spending policy revoked by the user",
    "POLICY_REPLACED": "New spending policy version activated by the user",
}


def reason_text(code):
    return REASON_TEXT.get(code, code.replace("_", " ").capitalize())
