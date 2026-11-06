"""Unit tests that need no browser or network."""
import asyncio
import dataclasses

import pytest

from app.config import get_settings
from app.llm import LLMClient, LLMConfig, LLMError, parse_json, resolve_llm
from app.safety import Policy, assess_action, check_url, looks_sensitive, validate_action


def run(coro):
    return asyncio.run(coro)


# ---- URL policy
@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://localhost/admin", "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.5/", "http://192.168.1.1/", "http://[::1]/", "http://2130706433/", "http://foo.internal/",
    "https://example.com:8443/", "ftp://example.com/", "file:///etc/passwd", "javascript:alert(1)",
    "https://user:pw@example.com/", "https://paypal.com/", "https://www.paypal.com/checkout", "https://accounts.google.com/",
])
def test_url_blocked(url):
    ok, why = run(check_url(url, Policy()))
    assert not ok and why


def test_url_allowed_public_literal():
    ok, _ = run(check_url("https://8.8.8.8/", Policy()))
    assert ok


def test_url_allowlist_only_applies_to_navigation():
    p = Policy(allowed_domains=["example.com"], allow_private=True)
    assert run(check_url("https://example.com/x", p))[0]
    assert run(check_url("https://sub.example.com/x", p))[0]
    assert not run(check_url("https://evil.com/", p))[0]
    assert run(check_url("https://cdn.evil.com/lib.js", p, navigation=False))[0]  # subresource


def test_private_allowed_in_dev_mode():
    assert run(check_url("http://127.0.0.1:8000/demo/", Policy(allow_private=True)))[0]


# ---- action risk
def el(**kw):
    base = {"tag": "button", "type": "submit", "text": "", "label": "", "name": "", "placeholder": "", "aria": "",
            "autocomplete": "", "href": "", "form_method": None, "form_has_password": False}
    base.update(kw)
    return base


@pytest.mark.parametrize("text", ["Pay now", "Buy now", "Place your order", "Delete account", "Withdraw funds", "Donate", "Confirm payment"])
def test_click_blocked(text):
    assert assess_action({"type": "click", "id": 1}, el(text=text)).level == "blocked"


@pytest.mark.parametrize("text", ["Send message", "Add to cart", "Sign up", "Submit", "Proceed to checkout", "Subscribe"])
def test_click_needs_approval(text):
    assert assess_action({"type": "click", "id": 1}, el(text=text)).level == "medium"


@pytest.mark.parametrize("text", ["Search", "Next page", "Read more", "Home"])
def test_click_low(text):
    assert assess_action({"type": "click", "id": 1}, el(text=text, type="button")).level == "low"


def test_post_form_submit_is_medium_but_get_search_is_low():
    assert assess_action({"type": "click", "id": 1}, el(text="Go", form_method="post")).level == "medium"
    assert assess_action({"type": "click", "id": 1}, el(text="Go", form_method="get")).level == "low"


def test_auth_form_submit_blocked():
    assert assess_action({"type": "click", "id": 1}, el(text="Continue", form_has_password=True)).level == "blocked"


def test_typing_secrets_blocked():
    f = lambda **k: el(**{"tag": "input", "type": "text", **k})  # noqa: E731
    assert assess_action({"type": "type", "id": 1, "text": "x"}, f(type="password")).level == "blocked"
    assert assess_action({"type": "type", "id": 1, "text": "x"}, f(name="cvv")).level == "blocked"
    assert assess_action({"type": "type", "id": 1, "text": "x"}, f(autocomplete="cc-number")).level == "blocked"
    assert assess_action({"type": "type", "id": 1, "text": "4111 1111 1111 1111"}, f(name="q")).level == "blocked"
    assert assess_action({"type": "type", "id": 1, "text": "123-45-6789"}, f(name="q")).level == "blocked"
    assert assess_action({"type": "type", "id": 1, "text": "laptops"}, f(name="q")).level == "low"


def test_luhn():
    assert looks_sensitive("4242 4242 4242 4242")
    assert not looks_sensitive("1234 5678 9012 3456")
    assert not looks_sensitive("order 12345")


def test_validate_action():
    assert validate_action({"type": "click", "id": 3}, allow_selector=False) is None
    assert validate_action({"type": "click", "selector": "#x"}, allow_selector=False)
    assert validate_action({"type": "click", "selector": "#x"}, allow_selector=True) is None
    assert validate_action({"type": "type", "id": 1}, allow_selector=False)
    assert validate_action({"type": "rm_rf"}, allow_selector=False)
    assert validate_action({"type": "press", "key": "F12"}, allow_selector=False)
    assert validate_action({"type": "goto"}, allow_selector=False)


# ---- LLM helpers
def test_parse_json_variants():
    assert parse_json('{"a":1}') == {"a": 1}
    assert parse_json('```json\n{"a":1}\n```') == {"a": 1}
    assert parse_json('Sure! {"a": {"b": 2}} hope that helps') == {"a": {"b": 2}}
    for bad in ("", "no json", "[1,2]", "{bad"):
        with pytest.raises(LLMError):
            parse_json(bad)


def test_resolve_llm_priority():
    s = dataclasses.replace(get_settings(), llm_provider="gemini", gemini_api_key="", groq_api_key="")
    assert resolve_llm(s) is None
    assert resolve_llm(s, {"provider": "groq", "api_key": "k"}).provider == "groq"
    s2 = dataclasses.replace(s, llm_provider="groq", groq_api_key="g", gemini_api_key="m")
    assert resolve_llm(s2).provider == "groq"
    assert resolve_llm(s2, {"provider": "gemini", "api_key": "mine", "model": "x"}).model == "x"


def test_provider_request_shapes(monkeypatch):
    seen = {}

    async def fake_post(self, url, headers, body):
        seen["url"], seen["headers"], seen["body"] = url, headers, body
        if "googleapis" in url:
            return {"candidates": [{"content": {"parts": [{"text": '{"action":{"type":"wait"}}'}]}}]}
        return {"choices": [{"message": {"content": '{"action":{"type":"wait"}}'}}]}

    monkeypatch.setattr(LLMClient, "_post", fake_post)
    out = run(LLMClient(LLMConfig("gemini", "K", "gemini-x")).next_action("sys", "usr"))
    assert out["action"]["type"] == "wait"
    assert seen["url"].endswith("/models/gemini-x:generateContent") and seen["headers"] == {"x-goog-api-key": "K"}
    assert seen["body"]["generationConfig"]["responseMimeType"] == "application/json"
    out = run(LLMClient(LLMConfig("groq", "G", "llama-x")).next_action("sys", "usr"))
    assert seen["url"] == "https://api.groq.com/openai/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer G" and seen["body"]["model"] == "llama-x"
