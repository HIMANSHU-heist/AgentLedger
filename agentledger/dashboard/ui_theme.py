"""Look & feel only. No business logic."""
import html

import streamlit as st

from app.core import ledger

CSS = """
<style>
.block-container {padding-top: 1.4rem; max-width: 1150px;}
#MainMenu, footer {visibility: hidden;}
.al-hero {padding: 20px 24px; border-radius: 14px; margin-bottom: 12px;
  background: linear-gradient(135deg, rgba(108,140,255,.18), rgba(108,140,255,.04));
  border: 1px solid rgba(108,140,255,.28);}
.al-hero h1 {margin: 0; font-size: 1.8rem;}
.al-hero p {margin: 6px 0 0; opacity: .8; font-size: 1.02rem;}
.al-how {display: flex; gap: 10px; flex-wrap: wrap; margin: 0 0 14px;}
.al-how div {flex: 1; min-width: 200px; padding: 10px 14px; border-radius: 10px;
  background: rgba(255,255,255,.045); font-size: .9rem;}
.al-how b {display: block; margin-bottom: 2px;}
.al-card {border-radius: 12px; padding: 14px 18px; margin: 8px 0; border-left: 6px solid;
  background: rgba(255,255,255,.05);}
.al-flow {display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0 14px;}
.al-chip {padding: 4px 12px; border-radius: 999px; font-size: .78rem; border: 1px solid;}
.al-timeline {margin: 6px 0 12px;}
.al-step {position: relative; margin-left: 6px; padding: 6px 0 10px 18px;
  border-left: 2px solid rgba(255,255,255,.15);}
.al-dot {position: absolute; left: -7px; top: 9px; width: 12px; height: 12px;
  border-radius: 50%; background: #6C8CFF;}
.al-meta {opacity: .55; font-size: .78rem;}
</style>
"""

# status -> (plain-English label, colour)
PLAIN = {
    "VERIFIED_COMPLETED": ("Done and proven", "#2ECC71"),
    "AWAITING_APPROVAL": ("Waiting for a human to approve", "#F5B041"),
    "BLOCKED": ("Stopped for safety", "#E74C3C"),
    "ESCALATED": ("Handed to a human", "#5DADE2"),
    "REJECTED": ("Declined by the supervisor", "#E74C3C"),
    "FAILED": ("Could not be proven, so not counted as done", "#E74C3C"),
    "REASON_REQUIRED": ("Please type a reason first", "#F5B041"),
    "NOT_PENDING": ("Already handled", "#5DADE2"),
}

EVENT_TEXT = {
    "RECEIVED": "Request received",
    "DAG_COMPLETED": "Facts gathered from 5 systems at the same time",
    "POLICY_DECISION": "Company rules checked",
    "APPROVAL_REQUESTED": "Sent to a supervisor for approval",
    "AUTO_APPROVED": "Small amount, so approved automatically",
    "APPROVED": "Supervisor approved",
    "REJECTED": "Supervisor declined",
    "ATTEMPTED": "Refund attempted",
    "VERIFIED_COMPLETED": "Refund confirmed in the database (proof saved)",
    "FAILED": "The action did not change the data, so it was NOT counted as done",
    "BLOCKED": "Stopped by a safety rule",
    "ESCALATED": "Passed to a human reviewer",
}

REASON_TEXT = {
    "INJ-001": "The message or payment note contained instruction-like text, which can be an attempt "
               "to trick an AI. Nothing was executed.",
    "ESC-001": "The customer asked for a person, so a human takes over.",
    "ESC-002": "There is no clear proof of a double charge or a failed payment, so a human will review it. "
               "No refund was made.",
    "REF-002": "A refund already exists for this payment, so no second refund was created.",
    "REF-003": "No grounds for a refund were found.",
    "REF-004": "The refund amount is not valid for this payment.",
    "REF-HUMAN": "The problem is confirmed in the data, but the amount is above the auto-refund limit "
                 "(Rs 500), so a supervisor must approve first.",
    "REF-AUTO": "The problem is confirmed in the data and the amount is small enough to refund automatically.",
}


def apply():
    st.markdown(CSS, unsafe_allow_html=True)


def hero():
    st.markdown(
        '<div class="al-hero"><h1>AgentLedger</h1>'
        "<p>An AI helper for payment support. It never just says \u201crefund done\u201d: "
        "it checks the system and shows proof.</p></div>"
        '<div class="al-how">'
        "<div><b>1. Understand</b>Reads the customer message and finds the payment.</div>"
        "<div><b>2. Check the rules</b>Company rules decide: refund, ask a human, or stop.</div>"
        "<div><b>3. Act and prove</b>Compares the data before and after, and keeps a tamper-evident record.</div>"
        "</div>",
        unsafe_allow_html=True)


def explain(r):
    """One plain sentence for the status card."""
    if r["status"] == "FAILED":
        return PLAIN["FAILED"][0] + ". A human has been notified."
    d = r.get("decision")
    if not d:
        return "We could not find that transaction, so a human has been notified."
    for rule in d["rules"]:
        if rule in REASON_TEXT:
            return REASON_TEXT[rule]
    return d["reasons"][0]


def status_card(status, text):
    label, c = PLAIN.get(status, (status, "#5DADE2"))
    st.markdown(f'<div class="al-card" style="border-left-color:{c}">'
                f'<b style="color:{c};font-size:1.1rem">{html.escape(label)}</b><br>'
                f'{html.escape(text)}</div>', unsafe_allow_html=True)


def timeline(task_id):
    rows = []
    for e in ledger.get_task_events(task_id):
        text = EVENT_TEXT.get(e["event_type"], e["event_type"])
        who = e["actor"].replace("_", " ")
        rows.append(f'<div class="al-step"><span class="al-dot"></span><b>{html.escape(text)}</b><br>'
                    f'<span class="al-meta">{e["ts"][11:19]} \u00b7 {html.escape(who)}</span></div>')
    st.markdown('<div class="al-timeline">' + "".join(rows) + "</div>", unsafe_allow_html=True)


def _chip(label, color, done=True):
    style = (f"color:{color};border-color:{color};background:{color}22" if done
             else "color:#8A93A6;border-color:#3A4152")
    return f'<span class="al-chip" style="{style}">{"\u2713 " if done else ""}{label}</span>'


def stepper(task_id):
    """Progress bar built from the audit ledger itself."""
    seen = {e["event_type"] for e in ledger.get_task_events(task_id)}
    flow = [("Received", {"RECEIVED"}), ("Facts gathered", {"DAG_COMPLETED"}),
            ("Rules checked", {"POLICY_DECISION"}), ("Approved", {"AUTO_APPROVED", "APPROVED"}),
            ("Action taken", {"ATTEMPTED"}), ("Proven", {"VERIFIED_COMPLETED"})]
    chips = [_chip(label, "#2ECC71", bool(events & seen)) for label, events in flow]
    if "APPROVAL_REQUESTED" in seen and not ({"APPROVED", "REJECTED"} & seen):
        chips.append(_chip("Waiting for supervisor", "#F5B041"))
    for ev, label in (("BLOCKED", "Stopped"), ("ESCALATED", "Handed to human"), ("REJECTED", "Declined")):
        if ev in seen:
            chips.append(_chip(label, PLAIN[ev][1]))
    st.markdown('<div class="al-flow">' + "".join(chips) + "</div>", unsafe_allow_html=True)
