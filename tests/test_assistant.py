from types import SimpleNamespace

from fastapi.testclient import TestClient

from tests.conftest import Env
from trust_mw.api import create_app
from trust_mw.assistant import AssistantRunner, TOOLS


def block(kind, **values):
    return SimpleNamespace(type=kind, **values)


class FakeMessages:
    def __init__(self):
        self.responses = [
            [block("tool_use", id="search-1", name="search_products", input={
                "query": "direct flight under $500", "category": "travel",
            })],
            [block("tool_use", id="details-1", name="get_product_details", input={
                "product_reference": "cpt-jnb-flex-320",
            })],
            [block("tool_use", id="proposal-1", name="propose_purchase", input={
                "product_reference": "cpt-jnb-flex-320", "quantity": 1,
            })],
            [block("text", text="The $320 flight needs your approval before payment.")],
        ]
        self.tools_seen = []

    def create(self, **request):
        self.tools_seen.append(request["tools"])
        return SimpleNamespace(content=self.responses.pop(0))


def test_live_assistant_uses_only_governed_tools_and_returns_pending_approval():
    env = Env()
    model = SimpleNamespace(messages=FakeMessages())
    runner = AssistantRunner(model, env.svc, "agent_key_1", "test-model")
    app = create_app(env.svc, {"alice": ("user_1", "pw1"), "bob": ("user_2", "pw2")},
                     csrf_secret="test-secret", demo_agent_key="agent_key_1",
                     assistant_runner=runner)
    client = TestClient(app, follow_redirects=False)

    assert client.post("/api/assistant/chat", json={"message": "Book a flight", "csrf": "x"}).status_code == 401
    client.post("/login", data={"username": "alice", "password": "pw1"})
    csrf = client.get("/api/me").json()["csrf"]
    response = client.post("/api/assistant/chat", json={
        "message": "Book a direct flight under $500 and ask me above $250.",
        "csrf": csrf,
    })

    assert response.status_code == 200
    result = response.json()
    assert result["answer"] == "The $320 flight needs your approval before payment."
    assert [call["name"] for call in result["tool_calls"]] == [
        "search_products", "get_product_details", "propose_purchase"]
    assert result["intent"]["state"] == "HELD_FOR_APPROVAL"
    assert result["intent"]["facts"]["amount"] == "320.00"
    assert result["approval_url"] == f"/approvals/{result['intent']['intent_id']}"
    assert env.payments.calls == []
    assert all([tool["name"] for tool in exposed] == [
        "search_products", "get_product_details", "propose_purchase"]
        for exposed in model.messages.tools_seen)
    assert [tool["name"] for tool in TOOLS] == [
        "search_products", "get_product_details", "propose_purchase"]