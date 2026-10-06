"""Adapter tests against MOCKED PayPal responses (shaped from PayPal's docs and your spike output).

They prove our request building, retry, idempotency and read-back checks. They do not prove
PayPal's behaviour; that is what live_check.py is for.
"""
import pytest

from tests.conftest import Env
from trust_mw.paypal_adapter import PayPalAdapter, PaymentError

SPEC = {"intent": "CAPTURE", "payee_id": "paypal_sandbox_demo_airlines", "amount": "180.00",
        "currency": "USD", "reference_id": "pi_test1"}


class Resp:
    def __init__(self, status, data=None):
        self.status_code, self._data = status, data or {}
        self.ok = status < 400
        self.text = str(self._data)

    def json(self):
        return self._data


def order(amount="180.00", currency="USD", status="COMPLETED", ref="pi_test1",
          custom="paypal_sandbox_demo_airlines", merchant="MERCH123"):
    return {"id": "ORDER1", "status": status, "purchase_units": [{
        "reference_id": ref, "payee": {"merchant_id": merchant},
        "payments": {"captures": [{"id": "CAP1", "status": "COMPLETED", "custom_id": custom,
                                   "amount": {"currency_code": currency, "value": amount}}]}}]}


class FakeSession:
    def __init__(self, post_responses, get_response=None):
        self.post_responses = list(post_responses)
        self.get_response = get_response
        self.oauth_calls = 0
        self.requests = []

    def post(self, url, **kw):
        self.oauth_calls += 1
        return Resp(200, {"access_token": "tok", "expires_in": 3600})

    def request(self, method, url, headers=None, json=None, timeout=None):
        self.requests.append((method, url, headers, json))
        if method == "POST":
            return self.post_responses.pop(0)
        return self.get_response


def adapter(session, **kw):
    return PayPalAdapter("id", "secret", "VAULT_TOKEN_1", session=session, sleep=lambda s: None, **kw)


def test_success_builds_correct_request_and_reads_back():
    s = FakeSession([Resp(201, order())], Resp(200, order()))
    result = adapter(s).charge(SPEC, "exec:pi_test1")
    assert (result.status, result.amount, result.currency) == ("COMPLETED", 180, "USD")
    assert result.payee_id == "paypal_sandbox_demo_airlines" and result.capture_id == "CAP1"
    method, url, headers, body = s.requests[0]
    assert url.endswith("/v2/checkout/orders") and headers["PayPal-Request-Id"] == "exec:pi_test1"
    assert body["intent"] == "CAPTURE"
    assert body["payment_source"]["token"] == {"id": "VAULT_TOKEN_1", "type": "PAYMENT_METHOD_TOKEN"}
    assert body["purchase_units"][0]["amount"] == {"currency_code": "USD", "value": "180.00"}


def test_503_is_retried_with_the_same_idempotency_key():
    s = FakeSession([Resp(503), Resp(201, order())], Resp(200, order()))
    adapter(s).charge(SPEC, "exec:pi_test1")
    posts = [r for r in s.requests if r[0] == "POST"]
    assert len(posts) == 2 and {p[2]["PayPal-Request-Id"] for p in posts} == {"exec:pi_test1"}


def test_persistent_outage_fails_after_bounded_retries():
    s = FakeSession([Resp(503)] * 3)
    with pytest.raises(PaymentError, match="unavailable"):
        adapter(s).charge(SPEC, "k")
    assert len([r for r in s.requests if r[0] == "POST"]) == 3


def test_client_error_is_not_retried():
    s = FakeSession([Resp(422, {"name": "UNPROCESSABLE_ENTITY"})])
    with pytest.raises(PaymentError, match="422"):
        adapter(s).charge(SPEC, "k")
    assert len(s.requests) == 1


def test_order_not_completed_is_an_error():
    s = FakeSession([Resp(201, order(status="PAYER_ACTION_REQUIRED"))])
    with pytest.raises(PaymentError, match="not completed"):
        adapter(s).charge(SPEC, "k")


def test_reference_mismatch_is_an_error():
    s = FakeSession([Resp(201, order())], Resp(200, order(ref="someone_elses_order")))
    with pytest.raises(PaymentError, match="reference_id"):
        adapter(s).charge(SPEC, "k")


def test_access_token_is_cached_across_charges():
    s = FakeSession([Resp(201, order()), Resp(201, order())], Resp(200, order()))
    a = adapter(s)
    a.charge(SPEC, "k1")
    a.charge(SPEC, "k2")
    assert s.oauth_calls == 1


class EchoSession(FakeSession):
    """Answers like PayPal would for whatever intent the service sends (echoes reference_id)."""

    def __init__(self, **order_kwargs):
        super().__init__([])
        self.order_kwargs = order_kwargs
        self.last_ref = None

    def request(self, method, url, headers=None, json=None, timeout=None):
        self.requests.append((method, url, headers, json))
        if json:
            self.last_ref = json["purchase_units"][0]["reference_id"]
        return Resp(201 if method == "POST" else 200, order(ref=self.last_ref, **self.order_kwargs))


def test_service_marks_payment_mismatch_when_paypal_recorded_another_amount():
    e = Env(payments=adapter(EchoSession(amount="999.00")))
    assert e.propose("cpt-jnb-economy-180")["state"] == "PAYMENT_MISMATCH"


def test_unexpected_receiving_merchant_is_a_mismatch():
    e = Env(payments=adapter(EchoSession(merchant="ATTACKER"), expected_merchant_id="MERCH123"))
    assert e.propose("cpt-jnb-economy-180")["state"] == "PAYMENT_MISMATCH"


def test_expected_merchant_passes():
    e = Env(payments=adapter(EchoSession(merchant="MERCH123"), expected_merchant_id="MERCH123"))
    assert e.propose("cpt-jnb-economy-180")["state"] == "CAPTURED"


def test_service_end_to_end_with_adapter_captures():
    e = Env(payments=adapter(EchoSession()))
    r = e.propose("cpt-jnb-economy-180")
    assert (r["decision"], r["state"]) == ("ALLOW", "CAPTURED")


def test_inspect_order_reports_what_paypal_has_on_record_without_secrets():
    s = FakeSession([], Resp(200, order()))
    info = adapter(s).inspect_order("ORDER1")
    assert info["order_status"] == "COMPLETED" and info["capture_status"] == "COMPLETED"
    assert (info["amount"], info["currency"], info["capture_id"]) == ("180.00", "USD", "CAP1")
    assert "VAULT_TOKEN_1" not in str(info)
