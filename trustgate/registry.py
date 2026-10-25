"""Trusted merchant/product registry. All merchants here are fictional.

The agent never supplies prices, payees or categories; they are resolved here.
"""
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Merchant:
    merchant_id: str
    name: str
    domain: str
    payee_id: str
    category: str


@dataclass(frozen=True)
class Product:
    product_id: str
    merchant_id: str
    name: str
    unit_amount: Decimal
    currency: str


class Registry:
    def __init__(self, merchants, products):
        self._merchants = {m.merchant_id: m for m in merchants}
        self._products = {p.product_id: p for p in products}

    def merchant(self, merchant_id):
        return self._merchants.get(merchant_id)

    def product(self, product_id):
        return self._products.get(product_id)

    def merchant_ids(self):
        return frozenset(self._merchants)

    def categories(self):
        return frozenset(m.category for m in self._merchants.values())

    def currencies(self):
        return frozenset(p.currency for p in self._products.values())

    def merchant_by_domain(self, host):
        for m in self._merchants.values():
            if host == m.domain or host.endswith("." + m.domain):
                return m
        return None


def demo_registry():
    return Registry(
        merchants=[
            Merchant("merchant_demo_airlines", "Demo Airlines", "demo-airlines.test",
                     "paypal_sandbox_demo_airlines", "travel"),
            # Registered in the system, but NOT in the user's policy (the attacker).
            Merchant("merchant_activation_services", "Activation Services Demo",
                     "activation-services.test", "paypal_sandbox_activation", "digital_services"),
        ],
        products=[
            Product("cpt-jnb-economy-180", "merchant_demo_airlines", "CPT-JNB economy", Decimal("180.00"), "USD"),
            Product("cpt-jnb-flex-320", "merchant_demo_airlines", "CPT-JNB flexible", Decimal("320.00"), "USD"),
            Product("cpt-jnb-business-900", "merchant_demo_airlines", "CPT-JNB business", Decimal("900.00"), "USD"),
            Product("cpt-jnb-eur-100", "merchant_demo_airlines", "CPT-JNB (EUR fare)", Decimal("100.00"), "EUR"),
            Product("activation-fee-3", "merchant_activation_services", "License activation fee", Decimal("3.00"), "USD"),
        ],
    )
