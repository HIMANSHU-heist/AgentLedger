import asyncio
import pytest
from app.core import db as db_mod, orchestrator as orch
from app.tools import payment_tools as T
from benchmark.cases import CASES

INJ = [c for c in CASES if c.get("injection")]


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr(orch, "SIM_LATENCY", 0)
    db_mod.init_db(reset=True)


def test_exactly_five_injection_cases():
    assert len(INJ) == 5


@pytest.mark.parametrize("case", INJ, ids=[f"case{c['id']}" for c in INJ])
def test_injection_blocked_and_no_refund(case):
    r = asyncio.run(orch.process_refund_request(case["txn"], case["msg"]))
    assert r["status"] == "BLOCKED"
    assert orch.list_pending_approvals() == []
    assert T.get_refunds_for_txn(case["txn"]) == []
