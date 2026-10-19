"""Orchestrator: DAG data fetch -> policy -> (auto | human approval | block | escalate) -> verified action -> ledger."""
import asyncio
import json
import time
import uuid
from datetime import datetime

from app.core import ledger, verifier
from app.core.dag import Node, run_dag, DagError
from app.core.db import get_conn
from app.policy.engine import PolicyEngine, AUTO_APPROVE, HUMAN_APPROVAL, BLOCKED, ESCALATE
from app.tools import payment_tools as T

# Simulated network latency per external-system call (real Paytm/bank APIs are not used).
SIM_LATENCY = 0.3

APPROVALS_DDL = """
CREATE TABLE IF NOT EXISTS approvals (
    task_id     TEXT PRIMARY KEY,
    txn_id      TEXT,
    decision    TEXT,     -- JSON of the policy decision
    message     TEXT,
    status      TEXT NOT NULL,   -- PENDING | APPROVED | REJECTED
    reviewer    TEXT,
    reason      TEXT,
    created_at  TEXT,
    resolved_at TEXT
);
"""


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _new_task_id():
    return f"TASK-{uuid.uuid4().hex[:6].upper()}"


# ---------------- DAG definition ----------------

def build_refund_nodes(txn_id):
    def lat(fn):
        def wrapped(results):
            if SIM_LATENCY:
                time.sleep(SIM_LATENCY)
            return fn(results)
        return wrapped

    def get_txn(_):
        t = T.get_transaction(txn_id)
        if not t:
            raise ValueError("TXN_NOT_FOUND")
        return t

    def get_refunds(r):
        ids = [txn_id] + [m["txn_id"] for m in r["duplicates"]["matches"]]
        out = []
        for i in ids:
            out += T.get_refunds_for_txn(i)
        return out

    return [
        Node("txn", lat(get_txn)),
        Node("duplicates", lat(lambda r: T.find_duplicate_debits(txn_id)), ["txn"]),
        Node("merchant", lat(lambda r: T.get_merchant_profile(r["txn"]["merchant_id"])), ["txn"]),
        Node("tickets", lat(lambda r: T.get_ticket_history(r["txn"]["customer_id"], r["txn"]["merchant_id"])), ["txn"]),
        Node("refunds", lat(get_refunds), ["duplicates"]),
    ]


# ---------------- helpers ----------------

def _execute_refund(task_id, decision):
    d = decision["details"]
    target, amount = d["target_txn_id"], float(d["amount"])
    v = verifier.verified_action(
        task_id, "initiate_refund", {"txn_id": target, "amount": amount},
        snapshot_fn=lambda c: verifier.snapshot(c, "refunds", "txn_id", target),
        action_fn=lambda c: T.initiate_refund(target, amount, f"refund-{target}", conn=c),
        expect_fn=verifier.expect_new_row(txn_id=target, status="REFUND_INITIATED", amount=amount),
        key="refund_id",
    )
    if v["status"] != "VERIFIED_COMPLETED":
        ledger.append(task_id, "ESCALATED", "orchestrator",
                      {"reason": "action not verified: " + v["reason"]})
    return v


def _escalate(task_id, txn, decision):
    t = T.create_ticket(f"Human review needed for {txn['txn_id']} (rules: {', '.join(decision.rules)})",
                        customer_id=txn["customer_id"], merchant_id=txn["merchant_id"])
    ledger.append(task_id, "ESCALATED", "orchestrator",
                  {"to": "human_reviewer", "ticket": t["ticket"]["ticket_id"], "reasons": decision.reasons})


def _create_approval(task_id, txn_id, decision, message):
    conn = get_conn()
    try:
        conn.executescript(APPROVALS_DDL)
        conn.execute("INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?,?)",
                     (task_id, txn_id, json.dumps(decision.to_dict()), message,
                      "PENDING", None, None, _now(), None))
        conn.commit()
    finally:
        conn.close()


def list_pending_approvals():
    conn = get_conn()
    try:
        conn.executescript(APPROVALS_DDL)
        rows = conn.execute("SELECT * FROM approvals WHERE status='PENDING' ORDER BY created_at").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["decision"] = json.loads(d["decision"])
            out.append(d)
        return out
    finally:
        conn.close()


def _customer_message(status, disclosure, decision=None, verification=None):
    if status == "VERIFIED_COMPLETED":
        row = verification["diff"]["added"][0]
        body = (f"We confirmed the issue with your payment. Refund {row['refund_id']} of "
                f"\u20b9{row['amount']:.0f} has been initiated against transaction {row['txn_id']} "
                f"and verified in our system. When it reaches your account depends on your bank.")
    elif status == "AWAITING_APPROVAL":
        body = ("We found evidence of a problem with your payment. Because of the amount, a human "
                "supervisor must approve the refund. We will update you once it is reviewed.")
    elif status == "BLOCKED" and decision and "REF-002" in decision.rules:
        body = "A refund already exists for this transaction, so no new refund was created."
    elif status in ("BLOCKED", "ESCALATED"):
        body = "We could not process this automatically, so your request has been passed to a human reviewer."
    else:
        body = "We could not confirm the refund in our system. A human reviewer has been notified."
    return f"{disclosure}\n\n{body}"


def _result(task_id, status, disclosure, decision=None, dag=None, verification=None):
    return {"task_id": task_id, "status": status,
            "decision": decision.to_dict() if decision else None,
            "dag": {k: dag[k] for k in ("levels", "timings", "total_s", "parallel")} if dag else None,
            "verification": verification,
            "customer_message": _customer_message(status, disclosure, decision, verification)}


# ---------------- main workflow ----------------

async def process_refund_request(txn_id, message="", customer_requests_human=False, parallel=True):
    engine = PolicyEngine()
    task_id = _new_task_id()
    disclosure = engine.policy["disclosure"]["text"]
    ledger.append(task_id, "RECEIVED", "orchestrator",
                  {"txn_id": txn_id, "message": message, "ai_disclosure": disclosure})

    try:
        dag = await run_dag(build_refund_nodes(txn_id), parallel=parallel)
    except DagError as e:
        ledger.append(task_id, "ESCALATED", "orchestrator",
                      {"reason": f"data fetch failed at '{e.node}': {e.error}"})
        return _result(task_id, "ESCALATED", disclosure)

    ledger.append(task_id, "DAG_COMPLETED", "orchestrator",
                  {"levels": dag["levels"], "timings": dag["timings"],
                   "total_s": dag["total_s"], "parallel": parallel})
    res = dag["results"]

    decision = engine.decide_refund(
        txn=res["txn"], duplicates=res["duplicates"], existing_refunds=res["refunds"],
        untrusted_texts=[message], customer_requests_human=customer_requests_human)
    ledger.append(task_id, "POLICY_DECISION", "policy_engine", decision.to_dict())

    if decision.outcome == BLOCKED:
        ledger.append(task_id, "BLOCKED", "policy_engine",
                      {"rules": decision.rules, "reasons": decision.reasons})
        if decision.requires_human:
            _escalate(task_id, res["txn"], decision)
        return _result(task_id, "BLOCKED", disclosure, decision, dag)

    if decision.outcome == ESCALATE:
        _escalate(task_id, res["txn"], decision)
        return _result(task_id, "ESCALATED", disclosure, decision, dag)

    if decision.outcome == HUMAN_APPROVAL:
        _create_approval(task_id, txn_id, decision, message)
        ledger.append(task_id, "APPROVAL_REQUESTED", "policy_engine",
                      {"to": "supervisor", "reasons": decision.reasons, "details": decision.details})
        return _result(task_id, "AWAITING_APPROVAL", disclosure, decision, dag)

    # AUTO_APPROVE
    ledger.append(task_id, "AUTO_APPROVED", "policy_engine", {"rules": decision.rules})
    v = _execute_refund(task_id, decision.to_dict())
    return _result(task_id, v["status"], disclosure, decision, dag, v)


def resolve_approval(task_id, approve, reviewer, reason):
    """Supervisor action. Refund executes only after APPROVED, and is verified by state diff."""
    if not reason or not reason.strip():
        return {"status": "REASON_REQUIRED"}
    conn = get_conn()
    try:
        conn.executescript(APPROVALS_DDL)
        row = conn.execute("SELECT * FROM approvals WHERE task_id=?", (task_id,)).fetchone()
        if not row or row["status"] != "PENDING":
            return {"status": "NOT_PENDING"}
        decision = json.loads(row["decision"])
        conn.execute("UPDATE approvals SET status=?, reviewer=?, reason=?, resolved_at=? WHERE task_id=?",
                     ("APPROVED" if approve else "REJECTED", reviewer, reason, _now(), task_id))
        conn.commit()
    finally:
        conn.close()

    if not approve:
        ledger.append(task_id, "REJECTED", reviewer, {"reason": reason})
        return {"status": "REJECTED", "task_id": task_id}

    ledger.append(task_id, "APPROVED", reviewer, {"reason": reason})
    v = _execute_refund(task_id, decision)
    return {"status": v["status"], "task_id": task_id, "verification": v}


def process_refund_request_sync(*args, **kwargs):
    return asyncio.run(process_refund_request(*args, **kwargs))
