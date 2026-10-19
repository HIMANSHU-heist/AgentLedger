import pytest
from app.core import db as db_mod
from app.tools import payment_tools as T


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_mod, "DB_PATH", tmp_path / "test.db")
    db_mod.init_db(reset=True)


def test_duplicate_detected():
    r = T.find_duplicate_debits("TXN8822")
    assert r["found"] and r["matches"][0]["txn_id"] == "TXN8821"


def test_no_duplicate_for_normal_txn():
    assert not T.find_duplicate_debits("TXN9201")["found"]


def test_refund_is_idempotent():
    a = T.initiate_refund("TXN8822", 2499, "key-1")
    b = T.initiate_refund("TXN8822", 2499, "key-1")
    assert a["ok"] and not a["idempotent_replay"]
    assert b["ok"] and b["idempotent_replay"]
    assert len(T.get_refunds_for_txn("TXN8822")) == 1


def test_second_refund_with_new_key_blocked():
    T.initiate_refund("TXN8822", 2499, "key-1")
    r = T.initiate_refund("TXN8822", 2499, "key-2")
    assert not r["ok"] and r["error"] == "REFUND_ALREADY_EXISTS"


def test_key_reuse_on_other_txn_blocked():
    T.initiate_refund("TXN8822", 2499, "key-1")
    r = T.initiate_refund("TXN9001", 640, "key-1")
    assert r["error"] == "IDEMPOTENCY_KEY_REUSE"


def test_refund_amount_cannot_exceed_txn():
    assert T.initiate_refund("TXN8822", 9999, "k")["error"] == "INVALID_AMOUNT"


def test_dispute_not_duplicated():
    a = T.create_dispute("TXN9001", "Debited but merchant not credited")
    b = T.create_dispute("TXN9001", "Debited but merchant not credited")
    assert b["idempotent_replay"] and a["dispute"]["dispute_id"] == b["dispute"]["dispute_id"]
