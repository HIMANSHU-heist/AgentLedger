import pytest
from app.core import db as db_mod, ledger, verifier
from app.tools import payment_tools as T


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "test.db")
    db_mod.init_db(reset=True)


def test_chain_valid_after_appends():
    for i in range(3):
        ledger.append("TASK-1", "ATTEMPTED", "agent", {"i": i})
    assert ledger.verify_chain() == {"ok": True, "checked": 3, "broken_at": None}


def test_update_is_blocked():
    ledger.append("TASK-1", "ATTEMPTED", "agent", {"a": 1})
    conn = db_mod.get_conn()
    with pytest.raises(Exception):
        conn.execute("UPDATE audit_ledger SET actor='hacker'")
    conn.close()


def test_tampering_is_detected():
    for i in range(3):
        ledger.append("TASK-1", "ATTEMPTED", "agent", {"i": i})
    conn = db_mod.get_conn()
    conn.execute("DROP TRIGGER audit_no_update")  # attacker removes the guard...
    conn.execute("UPDATE audit_ledger SET payload='{\"i\": 999}' WHERE seq=2")
    conn.commit()
    conn.close()
    r = ledger.verify_chain()
    assert not r["ok"] and r["broken_at"] == 2   # ...but the hash chain still catches it


def test_refund_verified_completed():
    res = verifier.verified_action(
        "TASK-2", "initiate_refund", {"txn_id": "TXN8822", "amount": 2499},
        snapshot_fn=lambda c: verifier.snapshot(c, "refunds", "txn_id", "TXN8822"),
        action_fn=lambda c: T.initiate_refund("TXN8822", 2499, "key-1", conn=c),
        expect_fn=verifier.expect_new_row(status="REFUND_INITIATED", amount=2499.0),
        key="refund_id",
    )
    assert res["status"] == "VERIFIED_COMPLETED"
    assert res["before"] == [] and len(res["after"]) == 1
    events = [e["event_type"] for e in ledger.get_task_events("TASK-2")]
    assert events == ["ATTEMPTED", "VERIFIED_COMPLETED"]
    assert ledger.verify_chain()["ok"]


def test_fake_success_is_caught():
    """Tool claims ok but DB did not change -> must be FAILED, not 'done'."""
    res = verifier.verified_action(
        "TASK-3", "initiate_refund", {"txn_id": "TXN8822"},
        snapshot_fn=lambda c: verifier.snapshot(c, "refunds", "txn_id", "TXN8822"),
        action_fn=lambda c: {"ok": True, "message": "Refund initiated!"},  # lies
        expect_fn=verifier.expect_new_row(status="REFUND_INITIATED"),
        key="refund_id",
    )
    assert res["status"] == "FAILED"


def test_tool_error_is_failed():
    res = verifier.verified_action(
        "TASK-4", "initiate_refund", {"txn_id": "TXN8822"},
        snapshot_fn=lambda c: verifier.snapshot(c, "refunds", "txn_id", "TXN8822"),
        action_fn=lambda c: T.initiate_refund("TXN8822", 9999, "k", conn=c),
        expect_fn=verifier.expect_new_row(status="REFUND_INITIATED"),
    )
    assert res["status"] == "FAILED" and "INVALID_AMOUNT" in res["reason"]
