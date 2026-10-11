"""SQLite store for the change log and the approval queue."""

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS changes (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL,
    kind         TEXT NOT NULL,
    params       TEXT NOT NULL,
    summary      TEXT NOT NULL,
    reason       TEXT NOT NULL,
    before_state TEXT,
    after_state  TEXT,
    status       TEXT NOT NULL,      -- pending | applied | rejected | failed
    decided_at   TEXT,
    result       TEXT
);
CREATE INDEX IF NOT EXISTS idx_changes_status ON changes(status);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(config.DB_PATH) or ".", exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def _row(r: sqlite3.Row) -> dict[str, Any]:
    d = dict(r)
    for key in ("params", "before_state", "after_state", "result"):
        if d.get(key):
            try:
                d[key] = json.loads(d[key])
            except json.JSONDecodeError:
                pass
    return d


def record(
    kind: str,
    params: dict,
    summary: str,
    reason: str,
    before: Any,
    after: Any,
    status: str,
    result: Any = None,
) -> int:
    with _conn() as conn:
        cur = conn.execute(
            "INSERT INTO changes (created_at, kind, params, summary, reason, before_state,"
            " after_state, status, decided_at, result) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                _now(),
                kind,
                json.dumps(params),
                summary,
                reason,
                json.dumps(before),
                json.dumps(after),
                status,
                None if status == "pending" else _now(),
                json.dumps(result) if result is not None else None,
            ),
        )
        return int(cur.lastrowid)


def get(change_id: int) -> dict[str, Any] | None:
    with _conn() as conn:
        r = conn.execute("SELECT * FROM changes WHERE id = ?", (change_id,)).fetchone()
        return _row(r) if r else None


def set_status(change_id: int, status: str, result: Any = None) -> None:
    with _conn() as conn:
        conn.execute(
            "UPDATE changes SET status = ?, decided_at = ?, result = ? WHERE id = ?",
            (status, _now(), json.dumps(result) if result is not None else None, change_id),
        )


def list_changes(status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    with _conn() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM changes WHERE status = ? ORDER BY id DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM changes ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [_row(r) for r in rows]
