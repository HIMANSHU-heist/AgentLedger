"""Simulated payment-ops tools. Deterministic, least-privilege, idempotent where it matters."""
from contextlib import contextmanager
from datetime import datetime, timedelta

from app.core.db import get_conn

TS_FMT = "%Y-%m-%d %H:%M:%S"


def _now() -> str:
    return datetime.now().strftime(TS_FMT)


@contextmanager
def _conn(conn=None):
    own = conn is None
    c = conn or get_conn()
    try:
        yield c
        if own:
            c.commit()
    finally:
        if own:
            c.close()


# ---------- READ TOOLS ----------

def get_transaction(txn_id, conn=None):
    with _conn(conn) as c:
        r = c.execute("SELECT * FROM transactions WHERE txn_id=?", (txn_id,)).fetchone()
        return dict(r) if r else None


def get_merchant_profile(merchant_id, conn=None):
    with _conn(conn) as c:
        r = c.execute("SELECT * FROM merchants WHERE merchant_id=?", (merchant_id,)).fetchone()
        return dict(r) if r else None


def get_settlements(merchant_id, conn=None):
    with _conn(conn) as c:
        rows = c.execute(
            "SELECT * FROM settlements WHERE merchant_id=? ORDER BY due_date DESC", (merchant_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_ticket_history(customer_id=None, merchant_id=None, conn=None):
    with _conn(conn) as c:
        rows = c.execute(
            "SELECT * FROM tickets WHERE customer_id=? OR merchant_id=? ORDER BY created_at DESC",
            (customer_id, merchant_id),
        ).fetchall()
        return [dict(r) for r in rows]


def get_refunds_for_txn(txn_id, conn=None):
    with _conn(conn) as c:
        rows = c.execute("SELECT * FROM refunds WHERE txn_id=?", (txn_id,)).fetchall()
        return [dict(r) for r in rows]


def find_duplicate_debits(txn_id, window_minutes=5, conn=None):
    """Same customer + merchant + amount within a time window = suspected duplicate debit."""
    with _conn(conn) as c:
        base = c.execute("SELECT * FROM transactions WHERE txn_id=?", (txn_id,)).fetchone()
        if not base:
            return {"found": False, "error": "TXN_NOT_FOUND", "matches": []}
        t0 = datetime.strptime(base["created_at"], TS_FMT)
        lo = (t0 - timedelta(minutes=window_minutes)).strftime(TS_FMT)
        hi = (t0 + timedelta(minutes=window_minutes)).strftime(TS_FMT)
        rows = c.execute(
            """SELECT * FROM transactions
               WHERE customer_id=? AND merchant_id=? AND amount=? AND debited=1
                 AND txn_id != ? AND created_at BETWEEN ? AND ?""",
            (base["customer_id"], base["merchant_id"], base["amount"], txn_id, lo, hi),
        ).fetchall()
        matches = [dict(r) for r in rows]
        return {"found": len(matches) > 0, "base_txn": dict(base), "matches": matches}


# ---------- WRITE TOOLS ----------

def initiate_refund(txn_id, amount, idempotency_key, conn=None):
    with _conn(conn) as c:
        existing = c.execute(
            "SELECT * FROM refunds WHERE idempotency_key=?", (idempotency_key,)
        ).fetchone()
        if existing:
            if existing["txn_id"] != txn_id:
                return {"ok": False, "error": "IDEMPOTENCY_KEY_REUSE"}
            return {"ok": True, "idempotent_replay": True, "refund": dict(existing)}

        txn = c.execute("SELECT * FROM transactions WHERE txn_id=?", (txn_id,)).fetchone()
        if not txn:
            return {"ok": False, "error": "TXN_NOT_FOUND"}
        if not txn["debited"]:
            return {"ok": False, "error": "NOT_DEBITED"}
        if amount <= 0 or amount > txn["amount"]:
            return {"ok": False, "error": "INVALID_AMOUNT"}

        active = c.execute(
            "SELECT * FROM refunds WHERE txn_id=? AND status != 'REJECTED'", (txn_id,)
        ).fetchone()
        if active:
            return {"ok": False, "error": "REFUND_ALREADY_EXISTS", "refund": dict(active)}

        n = c.execute("SELECT COUNT(*) FROM refunds").fetchone()[0] + 1
        refund = {
            "refund_id": f"RF{n:04d}",
            "txn_id": txn_id,
            "amount": amount,
            "status": "REFUND_INITIATED",
            "idempotency_key": idempotency_key,
            "created_at": _now(),
        }
        c.execute(
            "INSERT INTO refunds VALUES (:refund_id,:txn_id,:amount,:status,:idempotency_key,:created_at)",
            refund,
        )
        return {"ok": True, "idempotent_replay": False, "refund": refund}


def create_dispute(txn_id, reason, conn=None):
    with _conn(conn) as c:
        txn = c.execute("SELECT * FROM transactions WHERE txn_id=?", (txn_id,)).fetchone()
        if not txn:
            return {"ok": False, "error": "TXN_NOT_FOUND"}
        existing = c.execute(
            "SELECT * FROM disputes WHERE txn_id=? AND status IN ('RAISED','UNDER_REVIEW')", (txn_id,)
        ).fetchone()
        if existing:
            return {"ok": True, "idempotent_replay": True, "dispute": dict(existing)}
        n = c.execute("SELECT COUNT(*) FROM disputes").fetchone()[0] + 1
        d = {"dispute_id": f"DSP{n:04d}", "txn_id": txn_id, "reason": reason,
             "status": "RAISED", "created_at": _now()}
        c.execute("INSERT INTO disputes VALUES (:dispute_id,:txn_id,:reason,:status,:created_at)", d)
        return {"ok": True, "idempotent_replay": False, "dispute": d}


def create_ticket(summary, customer_id=None, merchant_id=None, conn=None):
    with _conn(conn) as c:
        n = c.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] + 1
        t = {"ticket_id": f"TKT{n:03d}", "customer_id": customer_id, "merchant_id": merchant_id,
             "summary": summary, "status": "OPEN", "created_at": _now()}
        c.execute("INSERT INTO tickets VALUES (:ticket_id,:customer_id,:merchant_id,:summary,:status,:created_at)", t)
        return {"ok": True, "ticket": t}
