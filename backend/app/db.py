"""Tiny SQLite store for runs and steps (stdlib only)."""
from __future__ import annotations

import json
import shutil
import sqlite3
import threading
import time
from pathlib import Path


class Store:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.shots_dir = data_dir / "shots"
        self.shots_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(str(data_dir / "taskpilot.db"), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, client_id TEXT NOT NULL, mode TEXT NOT NULL, title TEXT NOT NULL,
                    goal TEXT, params TEXT, status TEXT NOT NULL, created_at REAL NOT NULL, updated_at REAL NOT NULL,
                    result TEXT, error TEXT, pending TEXT, step_count INTEGER DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS runs_client ON runs (client_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS steps (
                    run_id TEXT NOT NULL, n INTEGER NOT NULL, body TEXT NOT NULL, PRIMARY KEY (run_id, n)
                );
                """
            )

    # ------------------------------------------------------------------ helpers
    @staticmethod
    def _run(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        d = dict(row)
        for k in ("params", "result", "pending"):
            d[k] = json.loads(d[k]) if d.get(k) else None
        return d

    def create_run(self, run_id: str, client_id: str, mode: str, title: str, goal: str, params: dict) -> dict:
        now = time.time()
        with self._lock:
            self._db.execute(
                "INSERT INTO runs (id, client_id, mode, title, goal, params, status, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (run_id, client_id, mode, title, goal, json.dumps(params), "queued", now, now),
            )
            self._db.commit()
        return self.get_run(run_id)

    def update_run(self, run_id: str, **fields) -> None:
        if not fields:
            return
        for k in ("result", "pending"):
            if k in fields and fields[k] is not None:
                fields[k] = json.dumps(fields[k])
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        with self._lock:
            self._db.execute(f"UPDATE runs SET {cols} WHERE id=?", (*fields.values(), run_id))
            self._db.commit()

    def get_run(self, run_id: str) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        return self._run(row)

    def list_runs(self, client_id: str, limit: int = 50) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM runs WHERE client_id=? ORDER BY created_at DESC LIMIT ?", (client_id, limit)
            ).fetchall()
        return [self._run(r) for r in rows]

    def add_step(self, run_id: str, n: int, body: dict) -> None:
        with self._lock:
            self._db.execute("INSERT OR REPLACE INTO steps (run_id, n, body) VALUES (?,?,?)", (run_id, n, json.dumps(body)))
            self._db.execute("UPDATE runs SET step_count=?, updated_at=? WHERE id=?", (n, time.time(), run_id))
            self._db.commit()

    def get_steps(self, run_id: str, after: int = 0) -> list[dict]:
        with self._lock:
            rows = self._db.execute(
                "SELECT body FROM steps WHERE run_id=? AND n>? ORDER BY n", (run_id, after)
            ).fetchall()
        return [json.loads(r["body"]) for r in rows]

    def delete_run(self, run_id: str) -> None:
        with self._lock:
            self._db.execute("DELETE FROM steps WHERE run_id=?", (run_id,))
            self._db.execute("DELETE FROM runs WHERE id=?", (run_id,))
            self._db.commit()
        shutil.rmtree(self.shots_dir / run_id, ignore_errors=True)

    def prune(self, keep: int) -> None:
        with self._lock:
            rows = self._db.execute("SELECT id FROM runs ORDER BY created_at DESC LIMIT -1 OFFSET ?", (keep,)).fetchall()
        for r in rows:
            self.delete_run(r["id"])

    def fail_stale_runs(self) -> None:
        """On startup, anything still queued/running belonged to a previous process."""
        with self._lock:
            self._db.execute(
                "UPDATE runs SET status='failed', error='Server restarted while this run was in progress', pending=NULL, updated_at=? "
                "WHERE status IN ('queued','running','awaiting_approval')", (time.time(),))
            self._db.commit()

    def save_shot(self, run_id: str, n: int, data: bytes) -> str:
        folder = self.shots_dir / run_id
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{n}.jpg").write_bytes(data)
        return f"/api/shots/{run_id}/{n}.jpg"
