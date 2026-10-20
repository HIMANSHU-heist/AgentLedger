import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import db, orchestrator as orch
from tests.test_injection_heldout import HELD_OUT

orch.SIM_LATENCY = 0
caught = 0
for txn, msg in HELD_OUT:
    db.init_db(reset=True)
    r = asyncio.run(orch.process_refund_request(txn, msg))
    caught += r["status"] == "BLOCKED"
    print(f"{r['status']:18} {txn}  {msg[:60]}")
db.init_db(reset=True)
print(f"\nRegex tripwire caught {caught}/{len(HELD_OUT)}. No refund executed without approval (see tests).")
