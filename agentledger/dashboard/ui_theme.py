"""Look & feel only. No business logic."""
import streamlit as st

from app.core import ledger

CSS = """
<style>
.block-container {padding-top: 1.6rem; max-width: 1200px;}
#MainMenu, footer {visibility: hidden;}
.al-hero {padding: 18px 22px; border-radius: 14px; margin-bottom: 14px;
  background: linear-gradient(135deg, rgba(108,140,255,.20), rgba(108,140,255,.04));
  border: 1px solid rgba(108,140,255,.28);}
.al-hero h1 {margin: 0; font-size: 1.9rem; letter-spacing: .3px;}
.al-hero p {margin: 4px 0 0; opacity: .75;}
.al-badge {display: inline-block; padding: 2px 11px; margin: 10px 6px 0 0; border-radius: 999px;
  font-size: .74rem; border: 1px solid rgba(255,255,255,.2); opacity: .92;}
.al-card {border-radius: 12px; padding: 12px 16px; margin: 8px 0; border-left: 5px solid;
  background: rgba(255,255,255,.045);}
.al-card b {letter-spacing: .4px;}
.al-flow {display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0 14px;}
.al-chip {padding: 4px 12px; border-radius: 999px; font-size: .78rem; border: 1px solid;}
</style>
"""

COLORS = {"VERIFIED_COMPLETED": "#2ECC71", "AWAITING_APPROVAL": "#F5B041",
          "BLOCKED": "#E74C3C", "ESCALATED": "#5DADE2", "REJECTED": "#E74C3C"}


def apply():
    st.markdown(CSS, unsafe_allow_html=True)


def hero():
    st.markdown(
        '<div class="al-hero"><h1>🧾 AgentLedger</h1>'
        '<p>Actions with Proof: AI that doesn\'t just complete tasks, it proves them.</p>'
        '<span class="al-badge">Policy-as-code</span>'
        '<span class="al-badge">Human approval gate</span>'
        '<span class="al-badge">State-diff verified</span>'
        '<span class="al-badge">Hash-chained audit</span></div>',
        unsafe_allow_html=True)


def status_card(status, text):
    c = COLORS.get(status, "#5DADE2")
    st.markdown(f'<div class="al-card" style="border-left-color:{c}"><b style="color:{c}">{status}</b>'
                f'<br>{text}</div>', unsafe_allow_html=True)


def _chip(label, color, done=True):
    style = (f"color:{color};border-color:{color};background:{color}22" if done
             else "color:#8A93A6;border-color:#3A4152")
    mark = "✓ " if done else ""
    return f'<span class="al-chip" style="{style}">{mark}{label}</span>'


def stepper(task_id):
    """Live progress, built from the audit ledger itself."""
    seen = {e["event_type"] for e in ledger.get_task_events(task_id)}
    flow = [("Received", {"RECEIVED"}), ("DAG", {"DAG_COMPLETED"}), ("Policy", {"POLICY_DECISION"}),
            ("Approval", {"AUTO_APPROVED", "APPROVED"}), ("Action", {"ATTEMPTED"}),
            ("Verified", {"VERIFIED_COMPLETED"})]
    chips = [_chip(label, "#2ECC71", bool(events & seen)) for label, events in flow]
    if "APPROVAL_REQUESTED" in seen and not ({"APPROVED", "REJECTED"} & seen):
        chips.append(_chip("Awaiting supervisor", "#F5B041"))
    for ev, label in (("BLOCKED", "Blocked"), ("ESCALATED", "Escalated to human"), ("REJECTED", "Rejected")):
        if ev in seen:
            chips.append(_chip(label, COLORS[ev]))
    st.markdown('<div class="al-flow">' + "".join(chips) + "</div>", unsafe_allow_html=True)
