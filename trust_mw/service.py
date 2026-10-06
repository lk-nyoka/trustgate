"""Trust middleware core: intents, decisions, approvals, execution, audit.

Principals are separate: an agent API key can only call propose_purchase. Approval,
policy confirmation and revocation need a human session token. The agent never
supplies prices, payees, categories, user ids or approvals.
"""
import hashlib
import json
import threading
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from urllib.parse import urlparse

from .policy import TrustedFacts, evaluate
from .scanner import ContextFlag, scan_page


class AuthError(Exception):
    pass


class ApprovalError(Exception):
    pass


class IdempotencyConflict(Exception):
    """The same request_id was reused with a different canonical payload."""


@dataclass(frozen=True)
class PaymentResult:
    status: str
    order_id: str
    amount: Decimal
    currency: str
    payee_id: str
    capture_id: str = None


class AuthStore:
    def __init__(self, session_ttl_seconds=8 * 60 * 60, clock=None):
        self._agents = {}    # api key -> (user_id, policy_id)
        self._sessions = {}  # session token -> (user_id, expires_at)
        self.session_ttl_seconds = session_ttl_seconds
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def register_agent(self, api_key, user_id, policy_id):
        self._agents[api_key] = (user_id, policy_id)

    def issue_session(self, user_id):
        token = "sess_" + uuid.uuid4().hex
        expires_at = self.clock() + timedelta(seconds=self.session_ttl_seconds)
        self._sessions[token] = (user_id, expires_at)
        return token

    def agent(self, api_key):
        if api_key not in self._agents:
            raise AuthError("invalid agent key")
        return self._agents[api_key]

    def session_user(self, token):
        session = self._sessions.get(token)
        if session is None:
            raise AuthError("not an authenticated human session")
        user_id, expires_at = session
        if self.clock() >= expires_at:
            self._sessions.pop(token, None)
            raise AuthError("human session expired")
        return user_id

    def revoke_session(self, token):
        self._sessions.pop(token, None)


class AuditLog:
    """Append-only, hash-chained so tampering is detectable."""

    def __init__(self, clock):
        self._clock = clock
        self.events = []

    def append(self, intent_id, event, data):
        prev = self.events[-1]["hash"] if self.events else "0" * 64
        body = {"seq": len(self.events), "ts": self._clock().isoformat(), "intent_id": intent_id,
                "event": event, "data": data, "prev": prev}
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
        self.events.append({**body, "hash": digest})

    def for_intent(self, intent_id):
        return [e for e in self.events if e["intent_id"] == intent_id]

    def verify(self):
        prev = "0" * 64
        for e in self.events:
            body = {k: v for k, v in e.items() if k != "hash"}
            digest = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
            if e["prev"] != prev or e["hash"] != digest:
                return False
            prev = e["hash"]
        return True


class TrustService:
    def __init__(self, registry, policies, auth, payments, pages=None, clock=None):
        self.registry = registry
        self.policies = dict(policies)
        self.auth = auth
        self.payments = payments
        self.pages = pages
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.audit = AuditLog(self.clock)
        self._intents = {}
        self._by_request = {}  # idempotency key -> (intent_id, canonical request hash)
        self._lock = threading.RLock()  # serialises propose/approve/decline/policy changes

    # ---- human-only policy lifecycle -------------------------------------
    def confirm_policy(self, session_token, policy_id):
        with self._lock:
            return self._confirm_policy(session_token, policy_id)

    def _confirm_policy(self, session_token, policy_id):
        user = self.auth.session_user(session_token)
        policy = self.policies[policy_id]
        if policy.user_id != user:
            raise ApprovalError("NOT_POLICY_OWNER")
        self.policies[policy_id] = replace(policy, status="ACTIVE")
        self.audit.append(None, "POLICY_CONFIRMED", {"policy_id": policy_id, "by": user})

    def revoke_policy(self, session_token, policy_id):
        with self._lock:
            return self._revoke_policy(session_token, policy_id)

    def _revoke_policy(self, session_token, policy_id):
        user = self.auth.session_user(session_token)
        policy = self.policies[policy_id]
        if policy.user_id != user:
            raise ApprovalError("NOT_POLICY_OWNER")
        self.policies[policy_id] = replace(policy, status="REVOKED")
        self.audit.append(None, "POLICY_REVOKED", {"policy_id": policy_id, "by": user})

    def resume_policy(self, session_token, policy_id):
        with self._lock:
            user = self.auth.session_user(session_token)
            policy = self.policies[policy_id]
            if policy.user_id != user:
                raise ApprovalError("NOT_POLICY_OWNER")
            if policy.status != "REVOKED":
                raise ApprovalError("POLICY_NOT_PAUSED")
            # New version: requests held before the pause stay unapprovable and must be re-proposed.
            self.policies[policy_id] = replace(policy, status="ACTIVE", version=policy.version + 1)
            self.audit.append(None, "POLICY_RESUMED", {"policy_id": policy_id, "by": user,
                                                       "version": policy.version + 1})

    # ---- agent surface ---------------------------------------------------
    @staticmethod
    def request_hash(merchant_reference, product_reference, quantity, source_url):
        """Canonical hash of the operation an idempotency key is bound to."""
        canonical = {"merchant_reference": merchant_reference,
                     "product_reference": product_reference,
                     "quantity": quantity,
                     "source_context_hash": hashlib.sha256((source_url or "").encode()).hexdigest()}
        return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":"),
                                         default=str).encode()).hexdigest()

    def propose_purchase(self, agent_key, merchant_reference, product_reference, quantity,
                         source_url, request_id):
        with self._lock:
            return self._propose_purchase(agent_key, merchant_reference, product_reference,
                                          quantity, source_url, request_id)

    def _propose_purchase(self, agent_key, merchant_reference, product_reference, quantity,
                          source_url, request_id):
        user_id, policy_id = self.auth.agent(agent_key)
        idem = f"{agent_key}:{request_id}"
        req_hash = self.request_hash(merchant_reference, product_reference, quantity, source_url)
        if idem in self._by_request:
            existing_id, existing_hash = self._by_request[idem]
            if existing_hash != req_hash:
                raise IdempotencyConflict("IDEMPOTENCY_KEY_REUSED_WITH_DIFFERENT_PAYLOAD")
            return self._agent_view(self._intents[existing_id])

        intent_id = "pi_" + uuid.uuid4().hex[:12]
        policy = self.policies[policy_id]
        rec = {"intent_id": intent_id, "user_id": user_id, "policy_id": policy_id,
               "policy_version": policy.version, "state": "RECEIVED", "decision": None,
               "reasons": [], "facts": None, "flags": [], "approver": None, "payment": None,
               "expires_at": None, "checks": [], "binding_checks": []}
        self._intents[intent_id] = rec
        self._by_request[idem] = (intent_id, req_hash)
        self.audit.append(intent_id, "INTENT_RECEIVED", {"agent_claimed_untrusted": {
            "merchant_reference": merchant_reference, "product_reference": product_reference,
            "quantity": quantity, "source_url": source_url}})

        facts, resolve_reasons = self._resolve(merchant_reference, product_reference, quantity)
        if facts is None:
            return self._finish_block(rec, resolve_reasons)
        rec["facts"] = facts
        self.audit.append(intent_id, "FACTS_RESOLVED", self._facts_dict(facts))

        flags = self._scan(facts, source_url)
        rec["flags"] = flags
        self.audit.append(intent_id, "CONTEXT_SCANNED", [f.__dict__ for f in flags])

        decision = evaluate(policy, facts, self._committed(policy_id), flags)
        rec["decision"], rec["reasons"] = decision.decision, list(decision.reasons)
        rec["checks"] = list(decision.checks)
        self.audit.append(intent_id, "DECISION", {"decision": decision.decision,
                                                  "reasons": list(decision.reasons),
                                                  "policy_version": policy.version,
                                                  "checks": [list(c) for c in decision.checks]})
        if decision.decision == "BLOCK":
            rec["state"] = "BLOCKED"
        elif decision.decision == "APPROVAL_REQUIRED":
            rec["state"] = "HELD_FOR_APPROVAL"
            rec["expires_at"] = self.clock() + timedelta(minutes=policy.approval_expiry_minutes)
            rec["reserved"] = facts.amount  # held intents reserve budget until captured/declined/expired/revoked
            self.audit.append(intent_id, "HELD_FOR_APPROVAL", {"expires_at": rec["expires_at"].isoformat(),
                                                               "reserved_amount": str(facts.amount)})
        else:
            self._execute(rec)
        return self._agent_view(rec)

    # ---- human-only approval surface ---------------------------------------
    def approve(self, session_token, intent_id):
        with self._lock:
            return self._approve(session_token, intent_id)

    def _approve(self, session_token, intent_id):
        rec = self._human_checked(session_token, intent_id)
        policy = self.policies[rec["policy_id"]]
        if self.clock() > rec["expires_at"]:
            rec["state"] = "EXPIRED"
            self.audit.append(intent_id, "APPROVAL_EXPIRED", {})
            raise ApprovalError("EXPIRED")
        if policy.status != "ACTIVE" or policy.version != rec["policy_version"]:
            rec["state"] = "REVOKED"
            self.audit.append(intent_id, "APPROVAL_REFUSED_POLICY_CHANGED", {})
            raise ApprovalError("POLICY_REVOKED_OR_CHANGED")
        fresh, _ = self._resolve(rec["facts"].merchant_id, rec["facts"].product_id, rec["facts"].quantity)
        if fresh != rec["facts"]:
            rec["state"] = "BLOCKED"
            self.audit.append(intent_id, "APPROVAL_REFUSED_FACTS_CHANGED", {})
            raise ApprovalError("TRUSTED_FACTS_CHANGED")
        # Atomic (under self._lock) budget re-check: captured spend plus every OTHER live reservation.
        policy_now = self.policies[rec["policy_id"]]
        available = (policy_now.max_total_spend - self._captured(rec["policy_id"])
                     - self._reserved(rec["policy_id"], exclude=intent_id))
        if rec["facts"].amount > available:
            rec["state"] = "BLOCKED"
            rec["reserved"] = Decimal("0")
            self.audit.append(intent_id, "APPROVAL_REFUSED_BUDGET_EXCEEDED",
                              {"amount": str(rec["facts"].amount), "available": str(available)})
            raise ApprovalError("CUMULATIVE_BUDGET_EXCEEDED")
        old = rec["facts"]
        pairs = [("Merchant verified", fresh.merchant_id == old.merchant_id),
                 ("Product verified", fresh.product_id == old.product_id),
                 ("Configured payee binding verified", fresh.payee_id == old.payee_id),
                 ("Amount verified", fresh.amount == old.amount),
                 ("Currency verified", fresh.currency == old.currency),
                 ("Policy version verified", policy.version == rec["policy_version"]),
                 ("Approval window valid", self.clock() <= rec["expires_at"])]
        rec["binding_checks"] = [name for name, passed in pairs if passed]  # all pass if we got here
        rec["approver"] = self.auth.session_user(session_token)
        rec["state"] = "APPROVED"
        rec["reserved"] = Decimal("0")  # reservation converts to captured spend when _execute succeeds
        self.audit.append(intent_id, "APPROVED", {"by": rec["approver"],
                                                  "binding_checks": rec["binding_checks"]})
        self._execute(rec)
        return self._agent_view(rec)

    def decline(self, session_token, intent_id):
        with self._lock:
            return self._decline(session_token, intent_id)

    def _decline(self, session_token, intent_id):
        rec = self._human_checked(session_token, intent_id)
        rec["state"] = "DECLINED"
        rec["reserved"] = Decimal("0")
        self.audit.append(intent_id, "DECLINED", {"by": self.auth.session_user(session_token)})
        return self._agent_view(rec)

    def human_view(self, rec):
        f, pay = rec["facts"], rec["payment"]
        return {"intent_id": rec["intent_id"], "state": rec["state"], "decision": rec["decision"],
                "approval_status": self._approval_status(rec), "reasons": list(rec["reasons"]),
                "facts": self._facts_dict(f) if f else None, "quantity": f.quantity if f else None,
                "flags": [fl.__dict__ for fl in rec["flags"]], "expires_at": rec["expires_at"],
                "checks": rec["checks"], "binding_checks": rec["binding_checks"],
                "policy_id": rec["policy_id"], "policy_version": rec["policy_version"],
                "auto_approve_up_to": str(self.policies[rec["policy_id"]].auto_approve_up_to),
                "order_id": pay.order_id if pay else None, "capture_id": pay.capture_id if pay else None}

    def stats(self):
        out = {"captured": 0, "awaiting": 0, "blocked": 0}
        for r in self._intents.values():
            key = {"CAPTURED": "captured", "HELD_FOR_APPROVAL": "awaiting", "BLOCKED": "blocked"}.get(r["state"])
            if key:
                out[key] += 1
        return out

    def intents_for_user(self, user_id):
        return [self.human_view(r) for r in reversed(list(self._intents.values())) if r["user_id"] == user_id]

    def intent_for_user(self, user_id, intent_id):
        rec = self._intents.get(intent_id)
        if rec is None or rec["user_id"] != user_id:
            raise ApprovalError("UNKNOWN_INTENT")
        return self.human_view(rec)

    def intent_for_agent(self, agent_key, intent_id):
        user_id, _ = self.auth.agent(agent_key)
        rec = self._intents.get(intent_id)
        if rec is None or rec["user_id"] != user_id:
            raise ApprovalError("UNKNOWN_INTENT")
        return self._agent_view(rec)

    def get_audit(self, intent_id):
        return self.audit.for_intent(intent_id)

    # ---- internals -------------------------------------------------------
    def _human_checked(self, session_token, intent_id):
        user = self.auth.session_user(session_token)  # agent keys fail here
        rec = self._intents.get(intent_id)
        if rec is None:
            raise ApprovalError("UNKNOWN_INTENT")
        if rec["user_id"] != user:
            raise ApprovalError("NOT_DELEGATING_USER")
        if rec["state"] != "HELD_FOR_APPROVAL":
            raise ApprovalError(f"STATE_{rec['state']}")
        return rec

    def _resolve(self, merchant_reference, product_reference, quantity):
        merchant = self.registry.merchant(merchant_reference)
        if merchant is None:
            return None, ["UNKNOWN_MERCHANT"]
        product = self.registry.product(product_reference)
        if product is None:
            return None, ["UNKNOWN_PRODUCT"]
        if product.merchant_id != merchant.merchant_id:
            return None, ["PAYEE_NOT_BOUND_TO_PRODUCT"]
        if isinstance(quantity, bool) or not isinstance(quantity, int) or not 1 <= quantity <= 10:
            return None, ["INVALID_QUANTITY"]
        return TrustedFacts(merchant.merchant_id, merchant.name, merchant.payee_id, merchant.category,
                            product.product_id, product.name, product.unit_amount, quantity,
                            product.unit_amount * quantity, product.currency), []

    def _scan(self, facts, source_url):
        if not source_url or self.pages is None:
            return []
        host = urlparse(source_url).hostname or ""
        page_merchant = self.registry.merchant_by_domain(host)
        if page_merchant is None:  # never fetch pages from unregistered domains
            return [ContextFlag("url_check", "SOURCE_URL_OFF_DOMAIN", "agent_claim", source_url[:200])]
        html = self.pages.fetch(source_url)  # the middleware fetches; the agent does not report page content
        flags = scan_page(html) if html else []
        if page_merchant.merchant_id != facts.merchant_id:
            flags.append(ContextFlag("url_check", "PAYEE_DIFFERS_FROM_PAGE", "agent_claim",
                                     f"page: {page_merchant.name}; payment proposed to: {facts.merchant_name}"))
        return flags

    def _captured(self, policy_id):
        return sum((r["facts"].amount for r in self._intents.values()
                    if r["policy_id"] == policy_id and r["state"] == "CAPTURED"), Decimal("0"))

    def _reserved(self, policy_id, exclude=None):
        """Budget held by live approval-pending intents. Expired, declined, revoked-policy or
        stale-version holds reserve nothing."""
        policy = self.policies[policy_id]
        now = self.clock()
        return sum((r["facts"].amount for r in self._intents.values()
                    if r["policy_id"] == policy_id and r["intent_id"] != exclude
                    and r["state"] == "HELD_FOR_APPROVAL" and r["expires_at"] >= now
                    and policy.status == "ACTIVE" and r["policy_version"] == policy.version),
                   Decimal("0"))

    def _committed(self, policy_id):
        return self._captured(policy_id) + self._reserved(policy_id)

    def _execute(self, rec):
        facts = rec["facts"]
        spec = {"intent": "CAPTURE", "payee_id": facts.payee_id, "amount": str(facts.amount),
                "currency": facts.currency, "reference_id": rec["intent_id"]}
        try:
            result = self.payments.charge(spec, idempotency_key="exec:" + rec["intent_id"])
        except Exception as exc:  # adapter failure
            rec["state"] = "PAYMENT_FAILED"
            self.audit.append(rec["intent_id"], "PAYMENT_FAILED", {"error": str(exc)})
            return
        if (result.amount, result.currency, result.payee_id) != (facts.amount, facts.currency, facts.payee_id):
            rec["state"] = "PAYMENT_MISMATCH"
            self.audit.append(rec["intent_id"], "PAYMENT_MISMATCH", {
                "expected": [str(facts.amount), facts.currency, facts.payee_id],
                "got": [str(result.amount), result.currency, result.payee_id]})
            return
        rec["state"] = "CAPTURED"
        rec["payment"] = result
        self.audit.append(rec["intent_id"], "PAYMENT_CAPTURED", {"order_id": result.order_id,
                                                         "capture_id": result.capture_id})

    def _finish_block(self, rec, reasons):
        rec["state"], rec["decision"], rec["reasons"] = "BLOCKED", "BLOCK", list(reasons)
        self.audit.append(rec["intent_id"], "DECISION", {"decision": "BLOCK", "reasons": list(reasons)})
        return self._agent_view(rec)

    @staticmethod
    def _facts_dict(f):
        return {"merchant": f.merchant_name, "payee_id": f.payee_id, "category": f.category,
                "product": f.product_name, "amount": str(f.amount), "currency": f.currency}

    @staticmethod
    def _approval_status(rec):
        if rec["decision"] != "APPROVAL_REQUIRED":
            return "NOT_REQUIRED" if rec["decision"] == "ALLOW" else "NOT_APPLICABLE"
        if rec["approver"]:
            return "APPROVED"
        return {"HELD_FOR_APPROVAL": "PENDING", "DECLINED": "DECLINED", "EXPIRED": "EXPIRED",
                "REVOKED": "REFUSED", "BLOCKED": "REFUSED"}.get(rec["state"], "NONE")

    def _agent_view(self, rec):
        """What the agent sees: no approval tokens, no payment credentials."""
        facts = rec["facts"]
        return {"intent_id": rec["intent_id"], "state": rec["state"], "decision": rec["decision"],
                "approval_status": self._approval_status(rec), "reason_codes": list(rec["reasons"]),
                "trusted_purchase": self._facts_dict(facts) if facts else None}
