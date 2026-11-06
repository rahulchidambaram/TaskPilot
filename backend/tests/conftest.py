import dataclasses
import re
import socket
import threading
import time

import httpx
import pytest
import uvicorn

from app.config import get_settings
from app.main import create_app

import os

# Local sandbox uses system Chrome; in CI set CHROME_PATH="" to use Playwright's bundled Chromium.
CHROME = os.getenv("CHROME_PATH", "/usr/bin/google-chrome" if os.path.exists("/usr/bin/google-chrome") else "")
HEADERS = {"X-Client-Id": "test-client-0001"}


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Server:
    def __init__(self, tmp_path, **overrides):
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        base = dataclasses.replace(
            get_settings(), data_dir=tmp_path, chrome_path=CHROME, allow_private_hosts=True,
            public_base_url=self.url, max_runtime_s=90, approval_timeout_s=20,
        )
        self.settings = dataclasses.replace(base, **overrides)
        self.app = create_app(self.settings)
        self.server = uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning"))
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self):
        self.thread.start()
        for _ in range(100):
            if self.server.started:
                break
            time.sleep(0.1)
        assert self.server.started, "server did not start"
        self.client = httpx.Client(base_url=self.url, headers=HEADERS, timeout=30)
        return self

    def __exit__(self, *exc):
        self.client.close()
        self.server.should_exit = True
        self.thread.join(timeout=15)


@pytest.fixture(scope="module")
def srv(tmp_path_factory):
    with Server(tmp_path_factory.mktemp("data")) as s:
        yield s


def wait_for(client: httpx.Client, run_id: str, statuses: set[str], timeout: float = 60) -> dict:
    deadline = time.time() + timeout
    run = {}
    while time.time() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] in statuses:
            return run
        time.sleep(0.3)
    raise AssertionError(f"timeout waiting for {statuses}; last run state: {run}")


TERMINAL = {"completed", "failed", "stopped"}


# --------------------------------------------------------------------------- scripted fake LLM (no network)
def element_ids(user: str) -> list[tuple[int, str]]:
    return [(int(i), rest) for i, rest in re.findall(r"^\[(\d+)\] (.*)$", user, re.M)]


def find(user: str, needle: str) -> int:
    for i, rest in element_ids(user):
        if needle in rest:
            return i
    raise AssertionError(f"element containing {needle!r} not in prompt:\n{user[:1500]}")


class FakeLLM:
    """Stands in for Gemini/Groq. The scenario is chosen through the `model` field of the per-run LLM override."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.calls = 0

    async def next_action(self, system: str, user: str) -> dict:
        self.calls += 1
        return getattr(self, "s_" + self.cfg.model)(user)

    def s_search(self, user):
        if "1 product(s) shown" in user:
            return {"thought": "Only one product matches", "action": {"type": "done", "result": "Travel Mug costs $18.25"}}
        return {"thought": "Use the search box", "action": {"type": "type", "id": find(user, 'placeholder="e.g. mug"'), "text": "travel", "submit": True}}

    def s_guard(self, user):
        if self.calls == 1:
            return {"thought": "Enter card", "action": {"type": "type", "id": find(user, "name=card"), "text": "4111 1111 1111 1111"}}
        if self.calls == 2:
            return {"thought": "Pay", "action": {"type": "click", "id": find(user, '"Pay now"')}}
        return {"thought": "Blocked, so report back", "action": {"type": "done", "success": False, "result": "Payment was blocked by the safety policy"}}

    def s_approval(self, user):
        if self.calls == 1:
            return {"thought": "Name", "action": {"type": "type", "id": find(user, "name=name"), "text": "Priya"}}
        if self.calls == 2:
            return {"thought": "Message", "action": {"type": "type", "id": find(user, "name=message"), "text": "Hi there"}}
        if self.calls == 3:
            return {"thought": "Send", "action": {"type": "click", "id": find(user, '"Send message"')}}
        return {"thought": "Done", "action": {"type": "done", "result": "Message sent"}}

    def s_wait(self, user):
        return {"thought": "Waiting", "action": {"type": "wait", "seconds": 2}}

    def s_badjson(self, user):
        raise AssertionError("should not be called")


@pytest.fixture()
def fake_llm(monkeypatch):
    monkeypatch.setattr("app.main.LLMClient", FakeLLM)


def agent_body(goal: str, scenario: str, **extra) -> dict:
    return {"mode": "agent", "goal": goal, "llm": {"provider": "gemini", "api_key": "test-key", "model": scenario}, **extra}
