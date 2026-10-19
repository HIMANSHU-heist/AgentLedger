"""State-diff verification: an action is VERIFIED_COMPLETED only if the DB changed as expected."""
import json

from app.core import ledger
from app.core.db import get_conn


def snapshot(conn, table, where_col, value):
    rows = conn.execute(f"SELECT * FROM {table} WHERE {where_col}=?", (value,)).fetchall()
    return [dict(r) for r in rows]


def diff_states(before, after, key=None):
    canon = lambda r: json.dumps(r, sort_keys=True, default=str)
    b, a = {canon(r) for r in before}, {canon(r) for r in after}
    added = [json.loads(x) for x in a - b]
    removed = [json.loads(x) for x in b - a]
    changed = []
    if key:
        bmap = {r[key]: r for r in before}
        amap = {r[key]: r for r in after}
        for k in bmap.keys() & amap.keys():
            fields = {f: {"before": bmap[k][f], "after": amap[k][f]}
                      for f in bmap[k] if bmap[k][f] != amap[k].get(f)}
            if fields:
                changed.append({key: k, "fields": fields})
        added = [r for r in added if r.get(key) not in bmap]
        removed = [r for r in removed if r.get(key) not in amap]
    return {"added": added, "removed": removed, "changed": changed}


def expect_new_row(**fields):
    """Expectation: a NEW row appeared whose fields match."""
    def check(before, after, diff):
        for r in diff["added"]:
            if all(r.get(k) == v for k, v in fields.items()):
                return True, f"new row matches {fields}"
        return False, f"expected new row {fields} not found in state diff"
    return check


def verified_action(task_id, action_name, params, snapshot_fn, action_fn, expect_fn,
                    key=None, actor="workflow_agent"):
    conn = get_conn()
    try:
        before = snapshot_fn(conn)
        ledger.append(task_id, "ATTEMPTED", actor,
                      {"action": action_name, "params": params, "state_before": before}, conn=conn)
        result, error = None, None
        try:
            result = action_fn(conn)
            conn.commit()
        except Exception as e:  # tool crashed
            conn.rollback()
            error = str(e)

        after = snapshot_fn(conn)
        d = diff_states(before, after, key)

        if error:
            ok, reason = False, f"tool exception: {error}"
        elif isinstance(result, dict) and result.get("ok") is False:
            ok, reason = False, f"tool returned error: {result.get('error')}"
        else:
            ok, reason = expect_fn(before, after, d)

        status = "VERIFIED_COMPLETED" if ok else "FAILED"
        ledger.append(task_id, status, "verification_agent",
                      {"action": action_name, "state_after": after, "diff": d,
                       "reason": reason, "tool_result": result}, conn=conn)
        return {"status": status, "reason": reason, "diff": d, "before": before,
                "after": after, "tool_result": result}
    finally:
        conn.close()
