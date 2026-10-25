"""Build a ready-to-use service for the demo, the evaluation harness and the tests."""
from .demo_data import make_policy
from .fake_paypal import FakePayPal
from .registry import demo_registry
from .service import AuthStore, TrustService


def build_service(pages, payments=None, policy=None):
    policy = policy or make_policy()
    auth = AuthStore()
    key = "agent_eval_key"
    auth.register_agent(key, policy.user_id, policy.policy_id)
    svc = TrustService(demo_registry(), {policy.policy_id: policy}, auth, payments or FakePayPal(), pages)
    return svc, key
