"""Per-session review workspaces for the hosted demo.

Every demo login gets its own fully independent TrustService: fresh active policy, the three
seeded scenarios, its own audit chain and its own kill-switch state. One reviewer revoking a
policy, approving a purchase or resetting cannot change what another reviewer sees.

Workspaces always use the in-memory fake payment adapter. They never hold PayPal credentials.
"""
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass, field

SEED_SCENARIOS = (
    # (merchant_ref, product_ref, source_url, request_id)
    ("merchant_demo_airlines", "cpt-jnb-economy-180", "https://demo-airlines.test/checkout", "seed-180"),
    ("merchant_demo_airlines", "cpt-jnb-flex-320", "https://demo-airlines.test/checkout", "seed-320"),
    ("merchant_activation_services", "activation-fee-3", "https://demo-airlines.test/injected-fee", "seed-fee"),
)


class RateLimited(Exception):
    pass


class _SlidingWindow:
    def __init__(self, limit, seconds, clock):
        self.limit, self.seconds, self.clock = limit, seconds, clock
        self._hits = {}

    def allow(self, key):
        now = self.clock()
        q = self._hits.setdefault(key, deque())
        while q and now - q[0] > self.seconds:
            q.popleft()
        if len(q) >= self.limit:
            return False
        q.append(now)
        if len(self._hits) > 5000:  # bound memory
            for k in [k for k, v in self._hits.items() if not v][:1000]:
                self._hits.pop(k, None)
        return True


@dataclass
class Workspace:
    workspace_id: str
    svc: object
    agent_key: str
    assistant_runner: object
    user_id: str
    created_at: float
    last_seen: float
    chat_count: int = 0
    resets: int = 0
    tokens: set = field(default_factory=set)


def seed_demo_intents(svc, agent_key):
    """Run the three canonical scenarios through the full policy + scanner + audit pipeline.
    The $320 flexible flight is left HELD_FOR_APPROVAL so a new reviewer has a live request."""
    for merchant_ref, product_ref, url, req_id in SEED_SCENARIOS:
        svc.propose_purchase(agent_key, merchant_ref, product_ref, 1, url, req_id)


def make_review_workspace_factory(assistant_factory=None, approval_expiry_minutes=60):
    """Return factory() -> (svc, agent_key, assistant_runner, user_id) for a brand-new workspace.

    assistant_factory(svc, agent_key) -> runner, or None for the scripted demo agent."""
    from .demo_data import DictPages, make_policy
    from .fake_paypal import FakePayPal
    from .registry import demo_registry
    from .service import AuthStore, TrustService

    def factory():
        policy = make_policy(approval_expiry_minutes=approval_expiry_minutes)
        auth = AuthStore()
        agent_key = "agent_" + secrets.token_urlsafe(16)
        auth.register_agent(agent_key, policy.user_id, policy.policy_id)
        svc = TrustService(demo_registry(), {policy.policy_id: policy}, auth, FakePayPal(), DictPages())
        seed_demo_intents(svc, agent_key)
        runner = assistant_factory(svc, agent_key) if assistant_factory else None
        return svc, agent_key, runner, policy.user_id
    return factory


class DemoWorkspaces:
    def __init__(self, factory, ttl_seconds=2 * 60 * 60, max_workspaces=200, clock=time.monotonic,
                 login_limit=(30, 60), reset_limit=(5, 60), max_chat_messages=40):
        self._factory = factory
        self.ttl_seconds = ttl_seconds
        self.max_workspaces = max_workspaces
        self.max_chat_messages = max_chat_messages
        self.clock = clock
        self._lock = threading.RLock()
        self._by_id, self._by_token, self._by_agent = {}, {}, {}
        self._login_rl = _SlidingWindow(*login_limit, clock)
        self._reset_rl = _SlidingWindow(*reset_limit, clock)

    def __len__(self):
        return len(self._by_id)

    # -- lifecycle -----------------------------------------------------------------
    def create(self, client_key="anon"):
        """New isolated workspace. Raises RateLimited when one client creates too many."""
        if not self._login_rl.allow(client_key):
            raise RateLimited("too many sign-ins; wait a minute")
        with self._lock:
            self._evict()
            svc, agent_key, runner, user_id = self._factory()
            now = self.clock()
            ws = Workspace("ws_" + secrets.token_urlsafe(12), svc, agent_key, runner, user_id, now, now)
            self._by_id[ws.workspace_id] = ws
            self._by_agent[agent_key] = ws.workspace_id
            return ws

    def issue_session(self, ws):
        with self._lock:
            token = ws.svc.auth.issue_session(ws.user_id)
            ws.tokens.add(token)
            self._by_token[token] = ws.workspace_id
            return token

    def reset(self, ws):
        """Rebuild this workspace's state from scratch; returns a fresh session token.
        Only this workspace changes. Raises RateLimited when reset too often."""
        if not self._reset_rl.allow(ws.workspace_id):
            raise RateLimited("too many resets; wait a minute")
        with self._lock:
            self._forget(ws)
            ws.svc, ws.agent_key, ws.assistant_runner, ws.user_id = self._factory()
            ws.chat_count, ws.resets = 0, ws.resets + 1
            self._by_id[ws.workspace_id] = ws
            self._by_agent[ws.agent_key] = ws.workspace_id
            return self.issue_session(ws)

    def discard(self, ws):
        with self._lock:
            self._forget(ws)

    def _forget(self, ws):
        self._by_id.pop(ws.workspace_id, None)
        for t in list(ws.tokens):
            self._by_token.pop(t, None)
        ws.tokens.clear()
        for k in [k for k, v in self._by_agent.items() if v == ws.workspace_id]:
            self._by_agent.pop(k, None)

    def _evict(self):
        now = self.clock()
        for ws in [w for w in self._by_id.values() if now - w.last_seen > self.ttl_seconds]:
            self._forget(ws)
        while len(self._by_id) >= self.max_workspaces:
            self._forget(min(self._by_id.values(), key=lambda w: w.last_seen))

    # -- lookup --------------------------------------------------------------------
    def by_token(self, token):
        with self._lock:
            return self._by_id.get(self._by_token.get(token))

    def by_agent_key(self, key):
        with self._lock:
            return self._by_id.get(self._by_agent.get(key))

    def find_intent(self, intent_id):
        with self._lock:
            for ws in self._by_id.values():
                if intent_id in ws.svc._intents:
                    return ws
        return None

    def touch(self, ws):
        ws.last_seen = self.clock()

    def allow_chat(self, ws):
        if ws.chat_count >= self.max_chat_messages:
            return False
        ws.chat_count += 1
        return True
