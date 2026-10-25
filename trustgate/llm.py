"""Minimal, provider-agnostic LLM interface. Implement complete() to use another provider."""
import json
import os
import time

import requests


class LLMError(Exception):
    pass


def extract_json(text):
    """First JSON object in the text (models sometimes add prose or code fences)."""
    start = text.find("{")
    while start != -1:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except ValueError:
                        break
        start = text.find("{", start + 1)
    raise LLMError("no JSON object in model output")


class AnthropicClient:
    URL = "https://api.anthropic.com/v1/messages"

    def __init__(self, api_key=None, model=None, session=None):
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self.api_key:
            raise LLMError("Set ANTHROPIC_API_KEY (or implement another client with complete()).")
        self.model = model or os.getenv("LLM_MODEL", "claude-haiku-4-5-20251001")
        self._http = session or requests.Session()

    def complete(self, system, messages, max_tokens=500):
        body = {"model": self.model, "max_tokens": max_tokens, "system": system, "messages": messages}
        headers = {"x-api-key": self.api_key, "anthropic-version": "2023-06-01",
                   "content-type": "application/json"}
        for attempt in range(4):
            resp = self._http.post(self.URL, headers=headers, json=body, timeout=60)
            if resp.status_code in (429, 500, 502, 503, 529) and attempt < 3:
                time.sleep(2 ** attempt)
                continue
            if not resp.ok:
                raise LLMError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            return "".join(b.get("text", "") for b in resp.json()["content"] if b.get("type") == "text")
        raise LLMError("unreachable")


class OpenAICompatClient:
    """Any OpenAI-compatible /chat/completions endpoint (many free tiers offer one).
    LLM_BASE_URL (e.g. https://api.groq.com/openai/v1), LLM_API_KEY, LLM_MODEL."""

    def __init__(self, base_url=None, api_key=None, model=None, session=None):
        self.base = (base_url or os.environ.get("LLM_BASE_URL", "")).rstrip("/")
        self.api_key = api_key or os.environ.get("LLM_API_KEY")
        self.model = model or os.environ.get("LLM_MODEL")
        if not (self.base and self.api_key and self.model):
            raise LLMError("Set LLM_BASE_URL, LLM_API_KEY and LLM_MODEL.")
        self._http = session or requests.Session()

    def complete(self, system, messages, max_tokens=500):
        body = {"model": self.model, "max_tokens": max_tokens, "temperature": 0,
                "messages": [{"role": "system", "content": system}] + list(messages)}
        headers = {"Authorization": f"Bearer {self.api_key}", "content-type": "application/json"}
        for attempt in range(3):
            resp = self._http.post(self.base + "/chat/completions", headers=headers, json=body, timeout=45)
            if resp.status_code in (429, 500, 502, 503) and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            if not resp.ok:
                raise LLMError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            try:
                return resp.json()["choices"][0]["message"]["content"] or ""
            except (KeyError, IndexError, ValueError) as exc:
                raise LLMError("unexpected response shape") from exc
        raise LLMError("unreachable")


def llm_from_env():
    """The configured client, or None. Never raises: the product must work without a key."""
    try:
        if os.environ.get("LLM_BASE_URL") and os.environ.get("LLM_API_KEY"):
            return OpenAICompatClient()
        if os.environ.get("ANTHROPIC_API_KEY"):
            return AnthropicClient()
    except LLMError:
        pass
    return None


class FakeLLM:
    """Scripted stand-in for tests: script(system, messages) -> str."""

    def __init__(self, script):
        self.script = script
        self.calls = 0

    def complete(self, system, messages, max_tokens=500):
        self.calls += 1
        return self.script(system, messages)
