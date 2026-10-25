"""In-memory stand-in for the PayPal adapter, used in tests and the offline demo.

The real adapter would call PayPal Orders v2 (create + capture) and read the order
back. This fake only lets the service logic be tested without network access.
"""
import itertools
from decimal import Decimal

from .service import PaymentResult


class FakePayPal:
    def __init__(self, tamper_amount=None, fail=False):
        self.calls = []
        self._ids = itertools.count(1)
        self.tamper_amount = tamper_amount
        self.fail = fail
        self._seen = {}

    def charge(self, spec, idempotency_key):
        if idempotency_key in self._seen:
            return self._seen[idempotency_key]
        if self.fail:
            raise RuntimeError("simulated PayPal outage")
        self.calls.append(spec)
        amount = Decimal(self.tamper_amount) if self.tamper_amount else Decimal(spec["amount"])
        result = PaymentResult("COMPLETED", f"FAKE-ORDER-{next(self._ids)}", amount,
                               spec["currency"], spec["payee_id"],
                               f"FAKE-CAPTURE-{next(self._ids)}")
        self._seen[idempotency_key] = result
        return result
