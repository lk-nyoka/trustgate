"""Natural-language policy authoring: AI drafts, code validates, a human activates.

The model (or the scripted fallback) only PROPOSES a partial policy. Nothing it returns is applied:
`validate_draft` checks every field against the trusted registry and hard caps, the human reviews a
diff, and only a confirmed draft becomes a new policy version. Drafters never see credentials and
cannot activate, approve or pay.
"""
import re
from decimal import Decimal, InvalidOperation

MAX_SINGLE_CAP = Decimal("2000")
MAX_TOTAL_CAP = Decimal("10000")
CONTEXT_FLAGS = ("HIDDEN_PAYMENT_INSTRUCTION", "PAYMENT_INSTRUCTION_IN_PAGE",
                 "SOURCE_URL_OFF_DOMAIN", "PAYEE_DIFFERS_FROM_PAGE")
CONTEXT_ACTIONS = ("BLOCK", "REQUIRE_APPROVAL")  # context checks can only tighten a decision
MONEY_FIELDS = ("max_single_purchase", "auto_approve_up_to", "max_total_spend")
SET_FIELDS = ("merchant_allowlist", "category_allowlist", "currency_allowlist")
ALLOWED_KEYS = set(MONEY_FIELDS) | set(SET_FIELDS) | {"context_actions"}


class DraftError(ValueError):
    pass


def _money(value, name):
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise DraftError(f"{name} must be a number")
    if not d.is_finite() or d < 0:
        raise DraftError(f"{name} must be a non-negative number")
    return d.quantize(Decimal("0.01"))


def validate_draft(raw, registry):
    """Return a normalised partial policy (Decimals / frozensets) or raise DraftError."""
    if not isinstance(raw, dict) or not raw:
        raise DraftError("draft is empty")
    unknown = set(raw) - ALLOWED_KEYS
    if unknown:
        raise DraftError("unsupported fields: " + ", ".join(sorted(unknown)))
    out = {}
    universe = {"merchant_allowlist": registry.merchant_ids(),
                "category_allowlist": registry.categories(),
                "currency_allowlist": registry.currencies()}
    for name in SET_FIELDS:
        if name in raw:
            values = raw[name]
            if not isinstance(values, (list, tuple, set, frozenset)) or not values:
                raise DraftError(f"{name} must be a non-empty list")
            bad = [v for v in values if v not in universe[name]]
            if bad:
                raise DraftError(f"{name} contains values not in the trusted registry: {', '.join(map(str, bad))}")
            out[name] = frozenset(values)
    for name in MONEY_FIELDS:
        if name in raw:
            out[name] = _money(raw[name], name)
    if "max_single_purchase" in out and out["max_single_purchase"] > MAX_SINGLE_CAP:
        raise DraftError(f"max_single_purchase cannot exceed {MAX_SINGLE_CAP}")
    if "max_total_spend" in out and out["max_total_spend"] > MAX_TOTAL_CAP:
        raise DraftError(f"max_total_spend cannot exceed {MAX_TOTAL_CAP}")
    if "context_actions" in raw:
        ca = raw["context_actions"]
        if not isinstance(ca, dict):
            raise DraftError("context_actions must be an object")
        for flag, action in ca.items():
            if flag not in CONTEXT_FLAGS:
                raise DraftError(f"unknown context flag: {flag}")
            if action not in CONTEXT_ACTIONS:
                raise DraftError("context checks can only BLOCK or REQUIRE_APPROVAL")
        out["context_actions"] = tuple(sorted(ca.items()))
    return out


def apply_draft(policy, fields):
    """Resulting policy fields if `fields` were activated on top of `policy` (pure; validates)."""
    merged = {n: getattr(policy, n) for n in (*SET_FIELDS, *MONEY_FIELDS, "context_actions")}
    merged.update(fields)
    if merged["auto_approve_up_to"] > merged["max_single_purchase"]:
        raise DraftError("auto_approve_up_to cannot exceed max_single_purchase")
    if merged["max_single_purchase"] > merged["max_total_spend"]:
        raise DraftError("max_single_purchase cannot exceed max_total_spend")
    return merged


def _show(v):
    if isinstance(v, frozenset):
        return sorted(v)
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, tuple):
        return {k: a for k, a in v}
    return v


def describe_changes(policy, fields):
    rows = []
    for name, new in fields.items():
        old = getattr(policy, name)
        if old != new:
            rows.append({"field": name, "from": _show(old), "to": _show(new)})
    return rows


def public_json(fields):
    return {k: _show(v) for k, v in fields.items()}


# ----------------------------------------------------------------------------- drafters
class ScriptedDrafter:
    """Deterministic fallback used when no model key is configured. Understands the demo phrasing."""
    name = "scripted"

    def draft(self, text, registry):
        raw = {}
        low = text.lower()
        for sentence in re.split(r"[.;\n]+", low):
            m = re.search(r"\$\s*(\d+(?:\.\d+)?)", sentence) or re.search(r"(\d+(?:\.\d+)?)\s*(?:usd|dollars)", sentence)
            if not m:
                continue
            amount = m.group(1)
            if re.search(r"ask me|before (?:spending|paying|buying|purchasing)|confirm with me|check with me|my approval", sentence):
                raw["auto_approve_up_to"] = amount
            elif re.search(r"total|budget|overall|in all", sentence):
                raw["max_total_spend"] = amount
            elif re.search(r"under|up to|at most|max|no more than|below|less than|limit", sentence):
                raw["max_single_purchase"] = amount
        cats = [c for c in registry.categories() if c in low or (c == "travel" and re.search(r"flight|airline|travel|trip", low))]
        if cats:
            raw["category_allowlist"] = sorted(set(cats))
            raw["merchant_allowlist"] = sorted(m for m in registry.merchant_ids()
                                               if registry.merchant(m).category in cats)
        if re.search(r"never pay|do not pay|don't pay|no unrelated|unrelated (?:fee|payment|charge)|activation fee|hidden (?:fee|instruction)", low):
            raw["context_actions"] = {"HIDDEN_PAYMENT_INSTRUCTION": "BLOCK",
                                      "PAYMENT_INSTRUCTION_IN_PAGE": "BLOCK"}
        if not raw:
            raise DraftError("could not find any policy rules in that text; "
                             "try e.g. 'Allow flights under $500. Ask me before spending more than $250.'")
        return raw


class LLMDrafter:
    """Asks a model to call one tool, `submit_policy_draft`. Its output is only a proposal."""
    name = "ai"
    provider_name = "Claude"

    def __init__(self, client, model):
        self.client, self.model = client, model

    def _tool(self, registry):
        money = {"type": "number", "minimum": 0}
        def enum_list(values):
            return {"type": "array", "items": {"type": "string", "enum": sorted(values)}, "minItems": 1}
        return {"name": "submit_policy_draft",
                "description": "Submit a DRAFT spending policy. Include only fields the user's text actually specifies. "
                               "A human reviews and activates it; you cannot activate, approve or pay.",
                "input_schema": {"type": "object", "additionalProperties": False, "properties": {
                    "merchant_allowlist": enum_list(registry.merchant_ids()),
                    "category_allowlist": enum_list(registry.categories()),
                    "currency_allowlist": enum_list(registry.currencies()),
                    "max_single_purchase": money, "auto_approve_up_to": money, "max_total_spend": money,
                    "context_actions": {"type": "object", "additionalProperties": {"type": "string", "enum": list(CONTEXT_ACTIONS)},
                                        "propertyNames": {"enum": list(CONTEXT_FLAGS)}}}}}

    def draft(self, text, registry):
        resp = self.client.messages.create(
            model=self.model, max_tokens=600,
            system=("You translate a user's plain-language spending wishes into a draft policy by calling "
                    "submit_policy_draft. You only propose; deterministic code validates and a human activates. "
                    "Never invent merchants, categories or limits the user did not state."),
            tools=[self._tool(registry)], tool_choice={"type": "tool", "name": "submit_policy_draft"},
            messages=[{"role": "user", "content": text}])
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use" and block.name == "submit_policy_draft":
                return dict(block.input)
        raise DraftError("the model did not return a draft")


class GeminiPolicyDrafter(LLMDrafter):
    """Gemini Interactions adapter; deterministic server validation remains authoritative."""
    name = "gemini"
    provider_name = "Gemini"

    def _tool(self, registry):
        def enum_list(values):
            return {"type": "array", "items": {"type": "string", "enum": sorted(values)}}

        fields = {
            "merchant_allowlist": enum_list(registry.merchant_ids()),
            "category_allowlist": enum_list(registry.categories()),
            "currency_allowlist": enum_list(registry.currencies()),
            "max_single_purchase": {"type": "number"},
            "auto_approve_up_to": {"type": "number"},
            "max_total_spend": {"type": "number"},
            "context_actions": {
                "type": "object",
                "properties": {
                    flag: {"type": "string", "enum": list(CONTEXT_ACTIONS)}
                    for flag in CONTEXT_FLAGS
                },
            },
        }
        return {
            "type": "function",
            "name": "submit_policy_draft",
            "description": (
                "Submit a partial DRAFT spending policy based only on the user's request. "
                "A human reviews and activates it; never set status or activation fields."
            ),
            "parameters": {"type": "object", "properties": fields},
        }

    def draft(self, text, registry):
        response = self.client.interactions.create(
            model=self.model,
            input=text,
            store=False,
            system_instruction=(
                "Translate the user's spending wishes into a partial draft policy by calling "
                "submit_policy_draft. Include only specified fields. Never invent registry "
                "values or loosen hard blocks. The server validates the draft and a human "
                "must activate it."
            ),
            tools=[self._tool(registry)],
            generation_config={"tool_choice": "any"},
        )
        for step in response.steps:
            if step.type == "function_call" and step.name == "submit_policy_draft":
                return dict(step.arguments)
        raise DraftError("the model did not return a draft")


class GeminiPolicyDrafter(LLMDrafter):
    """Gemini Interactions adapter; strict server validation remains authoritative."""
    name = "gemini"
    provider_name = "Gemini"

    def _tool(self, registry):
        def enum_list(values):
            return {"type": "array", "items": {"type": "string", "enum": sorted(values)}}

        fields = {
            "merchant_allowlist": enum_list(registry.merchant_ids()),
            "category_allowlist": enum_list(registry.categories()),
            "currency_allowlist": enum_list(registry.currencies()),
            "max_single_purchase": {"type": "number"},
            "auto_approve_up_to": {"type": "number"},
            "max_total_spend": {"type": "number"},
            "context_actions": {
                "type": "object",
                "properties": {
                    flag: {"type": "string", "enum": list(CONTEXT_ACTIONS)}
                    for flag in CONTEXT_FLAGS
                },
            },
        }
        return {
            "type": "function",
            "name": "submit_policy_draft",
            "description": (
                "Submit a partial DRAFT spending policy based only on the user's request. "
                "A human reviews and activates it; never set status or activation fields."
            ),
            "parameters": {"type": "object", "properties": fields},
        }

    def draft(self, text, registry):
        response = self.client.interactions.create(
            model=self.model,
            input=text,
            store=False,
            system_instruction=(
                "Translate the user's spending wishes into a partial draft policy by calling "
                "submit_policy_draft. Include only specified fields. Never invent registry "
                "values or loosen hard blocks. The server validates the draft and a human "
                "must activate it."
            ),
            tools=[self._tool(registry)],
            generation_config={"tool_choice": "any"},
        )
        for step in response.steps:
            if step.type == "function_call" and step.name == "submit_policy_draft":
                return dict(step.arguments)
        raise DraftError("the model did not return a draft")
