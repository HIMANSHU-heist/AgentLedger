"""Append-only, hash-chained audit ledger (tamper-evident)."""
import hashlib
import json
from datetime import datetime

from app.core.db import get_conn

GENESIS = "0" * 64

DDL = """
CREATE TABLE IF NOT EXISTS audit_ledger (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    task_id    TEXT NOT NULL,
    event_type TEXT NOT NULL,   -- ATTEMPTED | BLOCKED | ESCALATED | APPROVED | VERIFIED_COMPLETED | FAILED
    actor      TEXT NOT NULL,
    payload    TEXT NOT NULL,   -- JSON
    prev_hash  TEXT NOT NULL,
    hash       TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_ledger
BEGIN SELECT RAISE(ABORT, 'audit_ledger is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_ledger
BEGIN SELECT RAISE(ABORT, 'audit_ledger is append-only'); END;
"""


def _hash(prev_hash, ts, task_id, event_type, actor, payload):
    blob = json.dumps([prev_hash, ts, task_id, event_type, actor, payload], sort_keys=True)
    return hashlib.sha256(blob.encode()).hexdigest()


def append(task_id, event_type, actor, payload, conn=None):
    own = conn is None
    c = conn or get_conn()
    try:
        c.executescript(DDL)
        row = c.execute("SELECT hash FROM audit_ledger ORDER BY seq DESC LIMIT 1").fetchone()
        prev = row["hash"] if row else GENESIS
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")
        payload_str = json.dumps(payload, sort_keys=True, default=str)
        h = _hash(prev, ts, task_id, event_type, actor, payload_str)
        c.execute(
            "INSERT INTO audit_ledger (ts, task_id, event_type, actor, payload, prev_hash, hash) "
            "VALUES (?,?,?,?,?,?,?)",
            (ts, task_id, event_type, actor, payload_str, prev, h),
        )
        c.commit()
        return h
    finally:
        if own:
            c.close()


def verify_chain(conn=None):
    own = conn is None
    c = conn or get_conn()
    try:
        c.executescript(DDL)
        prev = GENESIS
        n = 0
        for r in c.execute("SELECT * FROM audit_ledger ORDER BY seq"):
            expected = _hash(prev, r["ts"], r["task_id"], r["event_type"], r["actor"], r["payload"])
            if r["prev_hash"] != prev or r["hash"] != expected:
                return {"ok": False, "checked": n, "broken_at": r["seq"]}
            prev = r["hash"]
            n += 1
        return {"ok": True, "checked": n, "broken_at": None}
    finally:
        if own:
            c.close()


def get_task_events(task_id, conn=None):
    own = conn is None
    c = conn or get_conn()
    try:
        c.executescript(DDL)
        rows = c.execute("SELECT * FROM audit_ledger WHERE task_id=? ORDER BY seq", (task_id,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d["payload"])
            out.append(d)
        return out
    finally:
        if own:
            c.close()
