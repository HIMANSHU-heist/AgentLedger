"""Deterministic policy engine. Rules live in policy.yaml, not in prompts."""
import re
from dataclasses import dataclass, field, asdict
from pathlib import Path

import yaml

from app.tools import payment_tools as T

POLICY_PATH = Path(__file__).with_name("policy.yaml")

AUTO_APPROVE = "AUTO_APPROVE"
HUMAN_APPROVAL = "HUMAN_APPROVAL_REQUIRED"
BLOCKED = "BLOCKED"
ESCALATE = "ESCALATE"


def load_policy(path=POLICY_PATH):
    with open(path) as f:
        return yaml.safe_load(f)


@dataclass
class Decision:
    outcome: str
    requires_human: bool
    rules: list = field(default_factory=list)
    reasons: list = field(default_factory=list)
    details: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


class PolicyEngine:
    def __init__(self, policy=None):
        self.policy = policy or load_policy()
        self._patterns = [re.compile(p, re.I) for p in self.policy["injection_defense"]["patterns"]]

    # ---------- injection defense ----------
    def scan_injection(self, texts):
        hits = []
        for t in texts:
            if not t:
                continue
            for p in self._patterns:
                if p.search(str(t)):
                    hits.append({"pattern": p.pattern, "text": str(t)[:120]})
        return hits

    # ---------- refunds ----------
    def decide_refund(self, txn, duplicates=None, existing_refunds=None, requested_amount=None,
                      untrusted_texts=(), customer_requests_human=False):
        rp, esc = self.policy["refund_policy"], self.policy["escalation"]
        duplicates = duplicates or {"found": False, "matches": []}
        existing_refunds = existing_refunds or []
        candidates = [txn] + list(duplicates.get("matches", []))

        # 1. Untrusted text (customer msg + memos of every involved txn) is scanned first.
        texts = list(untrusted_texts) + [c.get("memo") for c in candidates]
        hits = self.scan_injection(texts)
        if hits:
            return Decision(BLOCKED, True, ["INJ-001"],
                            ["Instruction-like content detected in untrusted field"], {"hits": hits})

        # 2. Customer asked for a human.
        if customer_requests_human and esc["on_customer_requests_human"]:
            return Decision(ESCALATE, True, ["ESC-001"], ["Customer requested a human reviewer"])

        # 3. Never double-refund.
        ids = {c["txn_id"] for c in candidates}
        active = [r for r in existing_refunds if r["txn_id"] in ids and r["status"] != "REJECTED"]
        if active:
            return Decision(BLOCKED, False, ["REF-002"],
                            ["A refund already exists for this transaction"], {"refund": active[0]})

        # 4. Evidence: duplicate debit, or debited-but-failed payment.
        target, evidence = None, None
        if duplicates.get("found"):
            extras = [c for c in candidates if c["credited"] == 0]
            if len(extras) == 1:
                target, evidence = extras[0], "DUPLICATE_DEBIT"
        elif txn["status"] == "FAILED" and txn["debited"] == 1 and txn["credited"] == 0:
            target, evidence = txn, "FAILED_PAYMENT_DEBITED"

        if target is None:
            if esc["on_ambiguous_evidence"]:
                return Decision(ESCALATE, True, ["ESC-002"],
                                ["No clear evidence of duplicate debit or failed payment"])
            return Decision(BLOCKED, False, ["REF-003"], ["No refund grounds"])

        # 5. Amount sanity.
        amount = requested_amount if requested_amount is not None else target["amount"]
        if amount <= 0 or amount > target["amount"]:
            return Decision(BLOCKED, False, ["REF-004"], ["Refund amount invalid for this transaction"])

        # 6. Threshold rule.
        details = {"target_txn_id": target["txn_id"], "amount": amount, "evidence": evidence}
        if amount <= rp["auto_approve_limit"]:
            return Decision(AUTO_APPROVE, False, ["REF-AUTO"],
                            [f"{evidence}; amount {amount} within auto limit {rp['auto_approve_limit']}"], details)
        return Decision(HUMAN_APPROVAL, True, ["REF-HUMAN"],
                        [f"{evidence}; amount {amount} above auto limit {rp['auto_approve_limit']}"], details)

    # ---------- loans ----------
    def decide_loan(self, merchant, untrusted_texts=()):
        lp = self.policy["loan_policy"]
        hits = self.scan_injection(untrusted_texts)
        if hits:
            return Decision(BLOCKED, True, ["INJ-001"],
                            ["Instruction-like content detected in untrusted field"], {"hits": hits})
        ps = lp["prescreen"]
        ok = (merchant["months_active"] >= ps["min_months_active"]
              and merchant["monthly_volume"] >= ps["min_monthly_volume"])
        rec = "RECOMMEND_FOR_REVIEW" if ok else "NOT_RECOMMENDED"
        # Hard rule: credit is NEVER auto-approved, whatever the pre-screen says.
        return Decision(HUMAN_APPROVAL, True, ["LOAN-001", "LOAN-002"],
                        [f"Pre-screen: {rec} (months_active={merchant['months_active']}, "
                         f"monthly_volume={merchant['monthly_volume']})",
                         "Final credit decision belongs to a human lending officer"],
                        {"recommendation": rec, "ai_disclosure": lp["disclose_ai_recommendation"]})


def build_context(txn_id, conn=None):
    """Gather facts the engine needs for a refund decision."""
    txn = T.get_transaction(txn_id, conn=conn)
    if not txn:
        return None
    dup = T.find_duplicate_debits(txn_id, conn=conn)
    refunds = T.get_refunds_for_txn(txn_id, conn=conn)
    for m in dup["matches"]:
        refunds += T.get_refunds_for_txn(m["txn_id"], conn=conn)
    return {"txn": txn, "duplicates": dup, "existing_refunds": refunds}
