"""Thin, dependency-light LLM clients (Gemini + Groq) that return a parsed JSON object."""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass

import httpx

from .config import Settings


class LLMError(Exception):
    pass


@dataclass
class LLMConfig:
    provider: str
    api_key: str
    model: str


def resolve_llm(settings: Settings, override: dict | None = None) -> LLMConfig | None:
    """Pick the LLM config: a per-run (bring-your-own-key) override wins over the server's env config."""
    if override and override.get("api_key"):
        provider = (override.get("provider") or "gemini").lower()
        default_model = settings.gemini_model if provider == "gemini" else settings.groq_model
        return LLMConfig(provider, override["api_key"].strip(), (override.get("model") or default_model).strip())
    if settings.llm_provider == "groq" and settings.groq_api_key:
        return LLMConfig("groq", settings.groq_api_key, settings.groq_model)
    if settings.llm_provider == "gemini" and settings.gemini_api_key:
        return LLMConfig("gemini", settings.gemini_api_key, settings.gemini_model)
    # fall back to whichever key exists
    if settings.gemini_api_key:
        return LLMConfig("gemini", settings.gemini_api_key, settings.gemini_model)
    if settings.groq_api_key:
        return LLMConfig("groq", settings.groq_api_key, settings.groq_model)
    return None


def parse_json(text: str) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise LLMError("Model reply was not valid JSON")
        try:
            obj = json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            raise LLMError("Model reply was not valid JSON")
    if not isinstance(obj, dict):
        raise LLMError("Model reply was not a JSON object")
    return obj


class LLMClient:
    """Calls the configured provider. `next_action(system, user)` -> dict."""

    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg

    async def next_action(self, system: str, user: str) -> dict:
        if self.cfg.provider == "gemini":
            text = await self._gemini(system, user)
        elif self.cfg.provider == "groq":
            text = await self._groq(system, user)
        else:
            raise LLMError(f"Unknown LLM provider '{self.cfg.provider}'")
        return parse_json(text)

    async def _post(self, url: str, headers: dict, body: dict) -> dict:
        delay = 2.0
        async with httpx.AsyncClient(timeout=60) as client:
            for attempt in range(4):
                try:
                    r = await client.post(url, headers=headers, json=body)
                except httpx.HTTPError as e:
                    if attempt == 3:
                        raise LLMError(f"Could not reach the LLM provider: {type(e).__name__}")
                    await asyncio.sleep(delay)
                    delay *= 2
                    continue
                if r.status_code == 429 or r.status_code >= 500:
                    if attempt == 3:
                        raise LLMError(f"LLM provider is rate-limiting or unavailable (HTTP {r.status_code}). Try again shortly.")
                    wait = r.headers.get("retry-after")
                    await asyncio.sleep(min(float(wait), 20) if wait and wait.replace(".", "").isdigit() else delay)
                    delay *= 2
                    continue
                if r.status_code in (400, 401, 403, 404):
                    detail = ""
                    try:
                        detail = r.json().get("error", {}).get("message", "")[:200]
                    except Exception:
                        pass
                    raise LLMError(f"LLM provider rejected the request (HTTP {r.status_code}): {detail or 'check API key and model name'}")
                r.raise_for_status()
                return r.json()
        raise LLMError("LLM request failed")

    async def _gemini(self, system: str, user: str) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.cfg.model}:generateContent"
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.2, "responseMimeType": "application/json", "maxOutputTokens": 1024},
        }
        data = await self._post(url, {"x-goog-api-key": self.cfg.api_key}, body)
        try:
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(p.get("text", "") for p in parts)
        except (KeyError, IndexError):
            raise LLMError("Gemini returned no content (possibly blocked by its safety filter)")

    async def _groq(self, system: str, user: str) -> str:
        body = {
            "model": self.cfg.model,
            "temperature": 0.2,
            "max_tokens": 1024,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        data = await self._post("https://api.groq.com/openai/v1/chat/completions",
                                {"Authorization": f"Bearer {self.cfg.api_key}"}, body)
        try:
            return data["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError):
            raise LLMError("Groq returned no content")
