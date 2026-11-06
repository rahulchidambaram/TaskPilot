"""RunManager: schedules runs, owns the shared Playwright driver, approvals and cancellation."""
from __future__ import annotations

import asyncio
import logging
import time

from playwright.async_api import Playwright, async_playwright

from .agent import AgentFailure, RunSession, Spec, run_agent, run_playbook
from .browser import BrowserController
from .config import Settings
from .db import Store
from .llm import LLMError

log = logging.getLogger("taskpilot")


class RunManager:
    def __init__(self, settings: Settings, store: Store):
        self.settings = settings
        self.store = store
        self._pw_cm = None
        self.pw: Playwright | None = None
        self._sem = asyncio.Semaphore(settings.max_concurrent_runs)
        self._tasks: dict[str, asyncio.Task] = {}
        self._approvals: dict[str, asyncio.Future] = {}

    async def start(self) -> None:
        self._pw_cm = async_playwright()
        self.pw = await self._pw_cm.start()

    async def shutdown(self) -> None:
        for t in list(self._tasks.values()):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks.values(), return_exceptions=True)
        if self.pw:
            await self.pw.stop()

    # ------------------------------------------------------------------ public API
    def submit(self, run_id: str, spec: Spec, planner) -> None:
        task = asyncio.create_task(self._execute(run_id, spec, planner), name=f"run-{run_id}")
        self._tasks[run_id] = task
        task.add_done_callback(lambda _t, rid=run_id: self._tasks.pop(rid, None))

    def is_active(self, run_id: str) -> bool:
        return run_id in self._tasks

    def stop(self, run_id: str) -> bool:
        task = self._tasks.get(run_id)
        if not task:
            return False
        task.cancel()
        return True

    def resolve_approval(self, run_id: str, approve: bool) -> bool:
        fut = self._approvals.get(run_id)
        if fut and not fut.done():
            fut.set_result(approve)
            return True
        return False

    # ------------------------------------------------------------------ internals
    async def _request_approval(self, run_id: str, info: dict) -> bool:
        fut = asyncio.get_running_loop().create_future()
        self._approvals[run_id] = fut
        timeout = self.settings.approval_timeout_s
        self.store.update_run(run_id, status="awaiting_approval", pending={**info, "expires_at": time.time() + timeout})
        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            return False
        finally:
            self._approvals.pop(run_id, None)
            self.store.update_run(run_id, status="running", pending=None)

    async def _execute(self, run_id: str, spec: Spec, planner) -> None:
        store = self.store
        browser: BrowserController | None = None
        try:
            async with self._sem:
                store.update_run(run_id, status="running")
                browser = BrowserController(self.pw, self.settings, spec.policy)
                await browser.start()
                session = RunSession(run_id, store, browser, spec.policy, lambda info: self._request_approval(run_id, info))
                if spec.mode == "playbook":
                    coro = run_playbook(session, spec.playbook_steps)
                else:
                    coro = run_agent(session, planner, spec)
                result = await asyncio.wait_for(coro, timeout=self.settings.max_runtime_s)
                result["steps"] = session.n
                store.update_run(run_id, status="completed", result=result, error=None, pending=None)
        except asyncio.CancelledError:
            store.update_run(run_id, status="stopped", error="Stopped by you", pending=None)
        except asyncio.TimeoutError:
            store.update_run(run_id, status="failed", error=f"Run exceeded the {self.settings.max_runtime_s}s time limit", pending=None)
        except AgentFailure as e:
            store.update_run(run_id, status="failed", error=e.message, result={"data": e.data} if e.data else None, pending=None)
        except LLMError as e:
            store.update_run(run_id, status="failed", error=f"LLM error: {e}", pending=None)
        except Exception as e:  # noqa: BLE001
            log.exception("run %s crashed", run_id)
            store.update_run(run_id, status="failed", error=f"Unexpected error: {type(e).__name__}: {str(e)[:200]}", pending=None)
        finally:
            if browser:
                await browser.close()
            store.prune(self.settings.keep_runs)
