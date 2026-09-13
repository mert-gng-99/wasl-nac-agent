"""Decision ledger.

Every case the agent handles is written here with the network answers that
produced it: which APIs were called, in what order, what each one said, what it
cost, and what the agent concluded. Three reasons this is a core component
rather than logging:

*   A held bank transfer, a blocked delivery, a flagged exam has to be
    explainable to the person on the receiving end, months later.
*   The judging criteria ask for orchestration that can be shown, and a
    replayable trail is how you show it.
*   Deciding not to call an API is itself a decision worth recording, so the
    ledger stores skipped checks alongside the ones that ran.

SQLite with a WAL journal, because a prototype that survives a restart is
worth more at a demo than one that does not.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS decisions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id      TEXT NOT NULL,
    subject      TEXT NOT NULL,
    kind         TEXT NOT NULL,
    level        TEXT NOT NULL,
    action       TEXT NOT NULL,
    rationale    TEXT NOT NULL,
    confidence   REAL NOT NULL DEFAULT 0,
    budget_spent REAL NOT NULL DEFAULT 0,
    budget_limit REAL NOT NULL DEFAULT 0,
    planner      TEXT NOT NULL DEFAULT 'policy',
    apis_used    TEXT NOT NULL DEFAULT '[]',
    evidence     TEXT NOT NULL DEFAULT '[]',
    skipped      TEXT NOT NULL DEFAULT '[]',
    steps        TEXT NOT NULL DEFAULT '[]',
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_decisions_case ON decisions(case_id);
CREATE INDEX IF NOT EXISTS idx_decisions_subject ON decisions(subject);
CREATE INDEX IF NOT EXISTS idx_decisions_created ON decisions(created_at DESC);

CREATE TABLE IF NOT EXISTS api_calls (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id      TEXT NOT NULL,
    api          TEXT NOT NULL,
    operation    TEXT NOT NULL,
    endpoint     TEXT NOT NULL,
    source       TEXT NOT NULL,
    status       INTEGER NOT NULL DEFAULT 200,
    latency_ms   INTEGER NOT NULL DEFAULT 0,
    cost_units   REAL NOT NULL DEFAULT 0,
    response     TEXT NOT NULL DEFAULT '{}',
    created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_calls_case ON api_calls(case_id);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class LedgerStats:
    decisions: int
    api_calls: int
    total_cost_units: float
    calls_per_decision: float
    by_level: Dict[str, int]
    by_api: Dict[str, int]


class DecisionLedger:
    def __init__(self, db_path: str = "data/ledger.db") -> None:
        self.db_path = db_path
        parent = os.path.dirname(os.path.abspath(db_path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)
            try:
                self._conn.execute("PRAGMA journal_mode=WAL")
            except sqlite3.DatabaseError:
                pass
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # -- writing -------------------------------------------------------------

    def record(self, decision: Any) -> int:
        """Persist an agent decision and each API call behind it."""
        payload = decision.to_dict() if hasattr(decision, "to_dict") else dict(decision)
        with self._lock:
            cursor = self._conn.execute(
                """
                INSERT INTO decisions (
                    case_id, subject, kind, level, action, rationale, confidence,
                    budget_spent, budget_limit, planner, apis_used, evidence,
                    skipped, steps, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    payload.get("case_id", ""),
                    payload.get("subject", ""),
                    payload.get("kind", ""),
                    payload.get("level", ""),
                    payload.get("action", ""),
                    payload.get("rationale", ""),
                    float(payload.get("confidence") or 0),
                    float(payload.get("budget_spent") or 0),
                    float(payload.get("budget_limit") or 0),
                    payload.get("planner", "policy"),
                    json.dumps(payload.get("apis_used", [])),
                    json.dumps(payload.get("evidence", []), default=str),
                    json.dumps(payload.get("skipped", []), default=str),
                    json.dumps(payload.get("steps", []), default=str),
                    payload.get("created_at") or _now_iso(),
                ),
            )
            for call in payload.get("evidence", []) or []:
                self._conn.execute(
                    """
                    INSERT INTO api_calls (
                        case_id, api, operation, endpoint, source, status,
                        latency_ms, cost_units, response, created_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        payload.get("case_id", ""),
                        call.get("api", ""),
                        call.get("operation", ""),
                        call.get("endpoint", ""),
                        call.get("source", ""),
                        int(call.get("status") or 200),
                        int(call.get("latency_ms") or 0),
                        float(call.get("cost_units") or 0),
                        json.dumps(call.get("data", {}), default=str),
                        call.get("at") or _now_iso(),
                    ),
                )
            self._conn.commit()
            return int(cursor.lastrowid or 0)

    # -- reading -------------------------------------------------------------

    def recent(self, limit: int = 50, subject: Optional[str] = None) -> List[Dict[str, Any]]:
        query = "SELECT * FROM decisions"
        params: List[Any] = []
        if subject:
            query += " WHERE subject = ?"
            params.append(subject)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [_row_to_decision(row) for row in rows]

    def get_case(self, case_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM decisions WHERE case_id = ? ORDER BY id DESC LIMIT 1",
                (case_id,),
            ).fetchone()
        return _row_to_decision(row) if row else None

    def stats(self) -> LedgerStats:
        with self._lock:
            decisions = self._conn.execute("SELECT COUNT(*) c FROM decisions").fetchone()["c"]
            calls = self._conn.execute("SELECT COUNT(*) c FROM api_calls").fetchone()["c"]
            cost = self._conn.execute(
                "SELECT COALESCE(SUM(cost_units),0) s FROM api_calls"
            ).fetchone()["s"]
            levels = self._conn.execute(
                "SELECT level, COUNT(*) c FROM decisions GROUP BY level"
            ).fetchall()
            apis = self._conn.execute(
                "SELECT api, COUNT(*) c FROM api_calls GROUP BY api ORDER BY c DESC"
            ).fetchall()
        return LedgerStats(
            decisions=int(decisions),
            api_calls=int(calls),
            total_cost_units=round(float(cost), 2),
            calls_per_decision=round(float(calls) / decisions, 2) if decisions else 0.0,
            by_level={row["level"]: int(row["c"]) for row in levels},
            by_api={row["api"]: int(row["c"]) for row in apis},
        )

    def reset(self) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM api_calls")
            self._conn.execute("DELETE FROM decisions")
            self._conn.commit()


def _row_to_decision(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "id": row["id"],
        "case_id": row["case_id"],
        "subject": row["subject"],
        "kind": row["kind"],
        "level": row["level"],
        "action": row["action"],
        "rationale": row["rationale"],
        "confidence": row["confidence"],
        "budget_spent": row["budget_spent"],
        "budget_limit": row["budget_limit"],
        "planner": row["planner"],
        "apis_used": json.loads(row["apis_used"] or "[]"),
        "evidence": json.loads(row["evidence"] or "[]"),
        "skipped": json.loads(row["skipped"] or "[]"),
        "steps": json.loads(row["steps"] or "[]"),
        "created_at": row["created_at"],
    }
