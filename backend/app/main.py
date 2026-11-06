"""FastAPI application: REST API for runs, approvals, history and export."""
from __future__ import annotations

import csv
import io
import json
import re
import time
import uuid
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, demo, playbooks
from .agent import Spec
from .config import Settings, get_settings
from .db import Store
from .llm import LLMClient, resolve_llm
from .manager import RunManager
from .safety import DEFAULT_BLOCKED_DOMAINS, Policy

CLIENT_ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")


class LLMOverride(BaseModel):
    provider: Literal["gemini", "groq"] = "gemini"
    api_key: str = Field(max_length=300)
    model: str | None = Field(None, max_length=80)


class RunRequest(BaseModel):
    mode: Literal["agent", "playbook"]
    goal: str | None = Field(None, max_length=1000)
    start_url: str | None = Field(None, max_length=500)
    playbook_id: str | None = None
    params: dict = Field(default_factory=dict)
    allowed_domains: list[str] = Field(default_factory=list, max_length=10)
    max_steps: int | None = Field(None, ge=1, le=30)
    require_approval: bool | None = None
    llm: LLMOverride | None = None


class ApprovalBody(BaseModel):
    approve: bool


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    store = Store(settings.data_dir)
    store.fail_stale_runs()
    manager = RunManager(settings, store)
    hits: dict[str, deque] = defaultdict(deque)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await manager.start()
        yield
        await manager.shutdown()

    app = FastAPI(title="TaskPilot API", version=__version__, lifespan=lifespan)
    app.state.settings, app.state.store, app.state.manager = settings, store, manager
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])
    app.include_router(demo.router)

    # ------------------------------------------------------------------ auth
    def check_access(x_access_code: str | None) -> None:
        if settings.access_code and x_access_code != settings.access_code:
            raise HTTPException(401, "Invalid or missing access code")

    async def auth(x_client_id: str | None = Header(None), x_access_code: str | None = Header(None)) -> str:
        check_access(x_access_code)
        if not x_client_id or not CLIENT_ID_RE.match(x_client_id):
            raise HTTPException(400, "Missing or invalid X-Client-Id header")
        return x_client_id

    def owned_run(run_id: str, client_id: str) -> dict:
        run = store.get_run(run_id)
        if not run or run["client_id"] != client_id:
            raise HTTPException(404, "Run not found")
        return run

    def public_run(run: dict) -> dict:
        out = {k: v for k, v in run.items() if k != "client_id"}
        out["active"] = manager.is_active(run["id"])
        return out

    # ------------------------------------------------------------------ meta
    @app.get("/api/health")
    async def health():
        return {"ok": True, "version": __version__}

    @app.get("/api/config")
    async def config(x_access_code: str | None = Header(None)):
        # The access-code requirement itself is public so the UI knows to ask for it.
        authorised = not settings.access_code or x_access_code == settings.access_code
        llm = resolve_llm(settings)
        return {
            "version": __version__,
            "access_required": bool(settings.access_code),
            "authorised": authorised,
            "llm_configured": bool(llm),
            "llm_provider": llm.provider if llm else None,
            "llm_model": llm.model if llm else None,
            "max_steps": settings.max_steps,
            "max_runtime_s": settings.max_runtime_s,
            "approval_timeout_s": settings.approval_timeout_s,
            "require_approval_default": settings.require_approval_default,
            "blocked_domains": DEFAULT_BLOCKED_DOMAINS,
            "playbooks": playbooks.public_list(),
        }

    # ------------------------------------------------------------------ runs
    def rate_limit(request: Request) -> None:
        ip = (request.headers.get("x-forwarded-for", "").split(",")[0].strip()
              or (request.client.host if request.client else "?"))
        q, now = hits[ip], time.time()
        while q and now - q[0] > 3600:
            q.popleft()
        if len(q) >= settings.rate_limit_per_hour:
            raise HTTPException(429, f"Rate limit reached ({settings.rate_limit_per_hour} runs/hour). Please try later.")
        q.append(now)

    @app.post("/api/runs", status_code=201)
    async def create_run(body: RunRequest, request: Request, client_id: str = Depends(auth)):
        base = settings.public_base_url or str(request.base_url).rstrip("/")
        policy = Policy(
            allowed_domains=[d.strip() for d in body.allowed_domains if d.strip()],
            allow_private=settings.allow_private_hosts,
            require_approval=settings.require_approval_default if body.require_approval is None else body.require_approval,
        )
        max_steps = min(body.max_steps or settings.max_steps, 30)
        planner = None
        steps: list[dict] = []

        if body.mode == "playbook":
            if body.playbook_id not in playbooks.PLAYBOOKS:
                raise HTTPException(400, "Unknown playbook_id")
            steps = playbooks.build_steps(body.playbook_id, body.params, base)
            title = playbooks.PLAYBOOKS[body.playbook_id]["name"]
            goal = playbooks.PLAYBOOKS[body.playbook_id]["description"]
        else:
            goal = (body.goal or "").strip()
            if len(goal) < 5:
                raise HTTPException(400, "Describe the goal in at least a few words")
            cfg = resolve_llm(settings, body.llm.model_dump() if body.llm else None)
            if not cfg:
                raise HTTPException(400, "No LLM is configured. Add your own free Gemini/Groq API key in Settings, "
                                         "or use a playbook (no LLM needed).")
            planner = LLMClient(cfg)
            title = goal[:80]

        rate_limit(request)
        run_id = uuid.uuid4().hex
        params = {"start_url": body.start_url, "allowed_domains": policy.allowed_domains, "max_steps": max_steps,
                  "require_approval": policy.require_approval, "playbook_id": body.playbook_id, "params": body.params,
                  "llm": f"{planner.cfg.provider}:{planner.cfg.model}" if planner else None}
        run = store.create_run(run_id, client_id, body.mode, title, goal, params)
        spec = Spec(mode=body.mode, goal=goal, start_url=body.start_url, policy=policy,
                    max_steps=max_steps, playbook_steps=steps)
        manager.submit(run_id, spec, planner)
        return public_run(run)

    @app.get("/api/runs")
    async def list_runs(client_id: str = Depends(auth)):
        return [public_run(r) for r in store.list_runs(client_id)]

    @app.get("/api/runs/{run_id}")
    async def get_run(run_id: str, client_id: str = Depends(auth)):
        return public_run(owned_run(run_id, client_id))

    @app.get("/api/runs/{run_id}/steps")
    async def get_steps(run_id: str, after: int = 0, client_id: str = Depends(auth)):
        owned_run(run_id, client_id)
        return store.get_steps(run_id, after)

    @app.post("/api/runs/{run_id}/approve")
    async def approve(run_id: str, body: ApprovalBody, client_id: str = Depends(auth)):
        owned_run(run_id, client_id)
        if not manager.resolve_approval(run_id, body.approve):
            raise HTTPException(409, "No approval is pending for this run")
        return {"ok": True}

    @app.post("/api/runs/{run_id}/stop")
    async def stop(run_id: str, client_id: str = Depends(auth)):
        owned_run(run_id, client_id)
        return {"ok": manager.stop(run_id)}

    @app.delete("/api/runs/{run_id}", status_code=204)
    async def delete_run(run_id: str, client_id: str = Depends(auth)):
        owned_run(run_id, client_id)
        manager.stop(run_id)
        store.delete_run(run_id)
        return Response(status_code=204)

    @app.get("/api/runs/{run_id}/export")
    async def export(run_id: str, format: Literal["json", "csv"] = "json", client_id: str = Depends(auth)):
        run = owned_run(run_id, client_id)
        if format == "json":
            payload = {"run": public_run(run), "steps": store.get_steps(run_id)}
            return Response(json.dumps(payload, indent=2), media_type="application/json",
                            headers={"Content-Disposition": f'attachment; filename="run-{run_id[:8]}.json"'})
        buf = io.StringIO()
        w = csv.writer(buf)
        data = (run.get("result") or {}).get("data") or {}
        table = next((v for v in data.values() if isinstance(v, list) and v and isinstance(v[0], dict)), None)
        if table is None and isinstance(data.get("links"), dict):
            table = data["links"].get("results")
        if table:
            cols = list(table[0].keys())
            w.writerow(cols)
            for row in table:
                w.writerow([row.get(c, "") for c in cols])
        else:
            w.writerow(["key", "value"])
            for k, v in data.items():
                w.writerow([k, json.dumps(v) if not isinstance(v, str) else v])
            answer = (run.get("result") or {}).get("answer")
            if answer is not None:
                w.writerow(["answer", answer if isinstance(answer, str) else json.dumps(answer)])
        return Response(buf.getvalue(), media_type="text/csv",
                        headers={"Content-Disposition": f'attachment; filename="run-{run_id[:8]}.csv"'})

    @app.get("/api/shots/{run_id}/{name}")
    async def shot(run_id: str, name: str):
        # run ids are unguessable 128-bit tokens, so <img> tags can load screenshots without custom headers
        if not re.fullmatch(r"[0-9a-f]{32}", run_id) or not re.fullmatch(r"\d{1,4}\.jpg", name):
            raise HTTPException(404)
        path = store.shots_dir / run_id / name
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})

    # ------------------------------------------------------------------ optional: serve the built frontend
    if settings.static_dir:
        index = settings.static_dir / "index.html"
        if (settings.static_dir / "assets").is_dir():
            app.mount("/assets", StaticFiles(directory=settings.static_dir / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            if full_path.startswith(("api/", "demo/")):
                return JSONResponse({"detail": "Not found"}, status_code=404)
            candidate = settings.static_dir / full_path
            if full_path and candidate.is_file() and settings.static_dir in candidate.resolve().parents:
                return FileResponse(candidate)
            return FileResponse(index)
    else:
        @app.get("/", include_in_schema=False)
        async def root():
            return {"name": "TaskPilot API", "version": __version__, "docs": "/docs"}

    return app


app = create_app()
