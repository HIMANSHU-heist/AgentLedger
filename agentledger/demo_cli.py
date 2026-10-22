import asyncio
import json

from app.core import db, ledger, orchestrator as orch
from app.core.dag import run_dag

db.init_db(reset=True)

print("=== 1. Sequential vs parallel data fetch (simulated %.1fs latency per system call) ===" % orch.SIM_LATENCY)
seq = asyncio.run(run_dag(orch.build_refund_nodes("TXN8822"), parallel=False))
par = asyncio.run(run_dag(orch.build_refund_nodes("TXN8822"), parallel=True))
print("levels    :", par["levels"])
print("sequential: %.2fs" % seq["total_s"])
print("parallel  : %.2fs  (speedup %.1fx)" % (par["total_s"], seq["total_s"] / par["total_s"]))

print("\n=== 2. Customer: 'I paid Rs 7500 twice' ===")
r = asyncio.run(orch.process_refund_request("TXN9401", "I paid Rs 7500 twice to the same merchant"))
print("status   :", r["status"])
print("decision :", r["decision"]["outcome"], r["decision"]["rules"], "-", r["decision"]["reasons"][0])
print("reply    :", r["customer_message"])
print("pending  :", [(p["task_id"], p["txn_id"]) for p in orch.list_pending_approvals()])

print("\n=== 3. Supervisor approves ===")
out = orch.resolve_approval(r["task_id"], True, "supervisor_01", "Duplicate debit confirmed")
print("status   :", out["status"])
print("before   :", out["verification"]["before"])
print("after    :", json.dumps(out["verification"]["after"], indent=1))

print("\n=== 4. Ledger ===")
for e in ledger.get_task_events(r["task_id"]):
    print(e["seq"], f"{e['event_type']:20}", f"{e['actor']:18}", e["hash"][:10])
print("chain    :", ledger.verify_chain())

print("\n=== 5. Prompt injection (TXN9301 memo) ===")
b = asyncio.run(orch.process_refund_request("TXN9301", "check my payment"))
print("status   :", b["status"], "|", b["decision"]["reasons"][0])

db.init_db(reset=True)
