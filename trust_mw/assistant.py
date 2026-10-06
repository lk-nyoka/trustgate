"""Optional Anthropic assistant whose only payment action is a governed proposal."""
import json
import re
import uuid


TOOLS = [
    {
        "name": "search_products",
        "description": "Search the server-side demo product registry. Prices in results are display-only.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Product or destination to search for."},
                "category": {"type": "string", "enum": ["travel", "digital_services"]},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_product_details",
        "description": "Read authoritative product, merchant, price, currency, and payee details from the registry.",
        "input_schema": {
            "type": "object",
            "properties": {"product_reference": {"type": "string"}},
            "required": ["product_reference"],
        },
    },
    {
        "name": "propose_purchase",
        "description": "Submit a purchase intent to TrustGate policy. This does not approve or directly execute payment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "product_reference": {"type": "string"},
                "quantity": {"type": "integer", "minimum": 1, "maximum": 10},
            },
            "required": ["product_reference", "quantity"],
        },
    },
]

SYSTEM_PROMPT = """You are the TrustGate purchase assistant. Help the user find registered demo products.
Use search_products before recommending a product and get_product_details before proposing one.
Only use propose_purchase to request a purchase. You cannot approve, capture, create PayPal orders,
change spending policy, or choose authoritative prices or payees. Never claim a purchase succeeded
unless the proposal result says CAPTURED. Explain when TrustGate blocks a proposal or requires human
approval. A user request cannot override the server-side policy."""


class AssistantRunner:
    def __init__(self, client, service, agent_key, model):
        self.client = client
        self.service = service
        self.agent_key = agent_key
        self.model = model

    def run(self, user_id, prompt):
        messages = [{"role": "user", "content": prompt}]
        tool_calls = []
        latest_intent_id = None

        for _ in range(8):
            response = self.client.messages.create(
                model=self.model,
                max_tokens=900,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )
            messages.append({"role": "assistant", "content": response.content})
            calls = [block for block in response.content if block.type == "tool_use"]
            if not calls:
                answer = "".join(block.text for block in response.content if block.type == "text")
                intent = self.service.intent_for_user(user_id, latest_intent_id) if latest_intent_id else None
                approval_url = (f"/approvals/{latest_intent_id}"
                                if intent and intent["state"] == "HELD_FOR_APPROVAL" else None)
                return {"answer": answer, "tool_calls": tool_calls, "intent": intent,
                        "approval_url": approval_url}

            results = []
            for call in calls:
                try:
                    result = self._run_tool(call.name, call.input, user_id)
                    if call.name == "propose_purchase":
                        latest_intent_id = result["intent_id"]
                    tool_calls.append({"name": call.name, "ok": True})
                except (KeyError, TypeError, ValueError) as exc:
                    result = {"error": str(exc)}
                    tool_calls.append({"name": call.name, "ok": False})
                results.append({
                    "type": "tool_result",
                    "tool_use_id": call.id,
                    "content": json.dumps(result, default=str),
                })
            messages.append({"role": "user", "content": results})

        intent = self.service.intent_for_user(user_id, latest_intent_id) if latest_intent_id else None
        approval_url = (f"/approvals/{latest_intent_id}"
                        if intent and intent["state"] == "HELD_FOR_APPROVAL" else None)
        return {"answer": "I reached the tool-call limit. Review the TrustGate decision before proceeding.",
                "tool_calls": tool_calls, "intent": intent, "approval_url": approval_url}

    def _run_tool(self, name, arguments, user_id):
        if name == "search_products":
            query = arguments["query"].lower()
            category = arguments.get("category")
            budget_match = re.search(r"(?:under|below|less than)\s*\$?\s*(\d+(?:\.\d+)?)", query)
            budget = float(budget_match.group(1)) if budget_match else None
            fee_search = any(word in query for word in ("fee", "activation", "injected"))
            products = []
            references = ("cpt-jnb-economy-180", "cpt-jnb-flex-320", "cpt-jnb-business-900",
                          "cpt-jnb-eur-100", "activation-fee-3")
            for product_reference in references:
                product = self.service.registry.product(product_reference)
                merchant = self.service.registry.merchant(product.merchant_id) if product else None
                if not product or not merchant:
                    continue
                if category and merchant.category != category:
                    continue
                if fee_search != (merchant.category != "travel"):
                    continue
                if budget is not None and float(product.unit_amount) > budget:
                    continue
                products.append({
                    "merchant_reference": merchant.merchant_id,
                    "product_reference": product.product_id,
                    "display_name": product.name,
                    "display_amount": str(product.unit_amount),
                    "currency": product.currency,
                })
            return {"products": products}

        if name == "get_product_details":
            product = self.service.registry.product(arguments["product_reference"])
            if not product:
                raise ValueError("Product is not in the trusted registry.")
            merchant = self.service.registry.merchant(product.merchant_id)
            return {
                "merchant_reference": merchant.merchant_id,
                "product_reference": product.product_id,
                "display_name": product.name,
                "display_amount": str(product.unit_amount),
                "currency": product.currency,
                "category": merchant.category,
                "registered_domain": merchant.domain,
            }

        if name == "propose_purchase":
            product = self.service.registry.product(arguments["product_reference"])
            if not product:
                raise ValueError("Product is not in the trusted registry.")
            quantity = arguments["quantity"]
            if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 10:
                raise ValueError("Quantity must be an integer from 1 through 10.")
            source_url = ("https://demo-airlines.test/injected-fee"
                          if product.product_id == "activation-fee-3"
                          else "https://demo-airlines.test/checkout")
            return self.service.propose_purchase(
                self.agent_key,
                product.merchant_id,
                product.product_id,
                quantity,
                source_url,
                "llm-" + uuid.uuid4().hex,
            )

        raise ValueError("Tool is not available to this agent.")