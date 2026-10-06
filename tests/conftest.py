from datetime import datetime, timedelta, timezone

import pytest

from trust_mw.fake_paypal import FakePayPal
from trust_mw.registry import demo_registry
from trust_mw.service import AuthStore, TrustService

from trust_mw.demo_data import (ATTACK_FEE, ATTACK_UPGRADE, BENIGN, PAGES, DictPages,  # noqa: F401
                                make_policy)


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, minutes):
        self.now += timedelta(minutes=minutes)


class Env:
    def __init__(self, policy=None, payments=None):
        self.clock = Clock()
        self.payments = payments or FakePayPal()
        self.auth = AuthStore()
        self.policy = policy or make_policy()
        self.auth.register_agent("agent_key_1", "user_1", self.policy.policy_id)
        self.user1 = self.auth.issue_session("user_1")
        self.user2 = self.auth.issue_session("user_2")
        self.svc = TrustService(demo_registry(), {self.policy.policy_id: self.policy}, self.auth,
                                self.payments, DictPages(), self.clock)
        self._n = 0

    def propose(self, product, merchant="merchant_demo_airlines", qty=1, url=None, rid=None):
        self._n += 1
        return self.svc.propose_purchase("agent_key_1", merchant, product, qty, url, rid or f"r{self._n}")


@pytest.fixture
def env():
    return Env()
