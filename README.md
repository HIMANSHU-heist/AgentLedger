# 🧾 AgentLedger: Actions with Proof

**An AI teammate for payment operations that doesn't just say a refund happened. It proves it.**

Most support bots stop at "I have initiated your refund." In payments that is not enough: was the refund actually created, was the right transaction refunded, did a human approve a large amount, and can anyone audit it later? AgentLedger separates what the AI **attempted** from what is **verified in system state**.

> All payment systems here are **simulated** (SQLite). No real Paytm, bank or NPCI APIs are used.

## What it does

A customer writes: *"I paid ₹2,499 twice on TXN8821."*

1. **Understand**: intent label (LLM optional) and transaction ID (always by regex, never by LLM).
2. **Fetch in parallel**: transaction, duplicate-debit check, merchant profile, ticket history, existing refunds, run as a DAG (Kahn topological levels, `asyncio`).
3. **Decide by policy-as-code**: rules live in [`policy.yaml`](agentledger/app/policy/policy.yaml), not in prompts.
4. **Route**: auto-approve small cases, require **human approval** above the limit, **block** instruction-like text, **escalate** unclear evidence or "I want a human".
5. **Act** through idempotent tools (same key never creates a second refund).
6. **Verify by state diff**: snapshot before, snapshot after. Only a real new row gives `VERIFIED_COMPLETED`. A tool that says "ok" but changed nothing is marked `FAILED` and escalated.
7. **Record** every step in an append-only, **hash-chained audit ledger**.

```mermaid
flowchart TD
    U["Customer message"] --> I["Intent + transaction ID"]
    I --> D["DAG: parallel data fetch"]
    D --> P["Policy engine (YAML rules)"]
    P -->|"small and clear evidence"| A
    P -->|"above limit"| H["Human approval (reason required)"]
    P -->|"injection, no evidence, wants human"| B["Blocked or escalated to human"]
    H -->|"approved"| A["Execute idempotent tool"]
    H -->|"rejected"| R["Rejected, no action"]
    A --> V["State-diff verifier"]
    V -->|"DB changed as expected"| OK["VERIFIED_COMPLETED"]
    V -->|"no change"| F["FAILED, escalate"]
    P -.-> L[("Hash-chained audit ledger")]
    H -.-> L
    V -.-> L
```

## Benchmark (measured, with caveats)

30 hand-written cases x 3 runs, two scenarios, two systems (360 runs). Run it yourself: `python benchmark/run_benchmark.py`.

| Scenario | System | Success | Unsafe actions | False completions |
|---|---|---:|---:|---:|
| clean | naive baseline | 53.3% | 66.7% | 0.0% |
| clean | **AgentLedger** | **100%** | **0%** | **0%** |
| flaky refund tool | naive baseline | 53.3% | 60.0% | 16.7% |
| flaky refund tool | **AgentLedger** | 93.3% | **0%** | **0%** |

*Unsafe action* = refund without evidence, wrong transaction, refund before a required approval, or more than one refund. *False completion* = the system claims a refund is done but the database has no such refund.

**Read this before trusting the numbers:**
- The baseline is a **naive keyword bot** (same tools, no policy, no verification). It is **not** an LLM agent.
- Cases and rules were written by us, so this is a **regression suite**, not an independent evaluation.
- Refund-tool faults are **simulated** (20% of calls silently do nothing but return ok).
- The LLM runs in **mock mode** in the benchmark. It measures orchestration, policy and verification, not LLM quality.
- The clean scenario is deterministic, so repeated runs are identical.
- In the flaky scenario AgentLedger's 6 failed runs (of 90) are silent tool failures that the verifier **caught and escalated**. There is no auto-retry yet, so those cases are not completed.

## Prompt-injection defense

Customer messages, merchant notes and transaction memos are treated as **untrusted data**.

- A regex layer is a **tripwire**: it blocks the 5 injection cases in our suite, but on **5 held-out phrasings written afterwards it caught only 1 of 5** (`python benchmark/heldout_report.py`).
- The real defense is **structural**: a refund needs database evidence (duplicate debit or failed payment) plus the amount threshold plus human approval above the limit. Message text cannot create evidence, change the amount, or approve itself. A test asserts that none of the held-out phrasings result in an executed refund.
- The LLM only labels intent and polishes wording. Numbers and IDs in polished replies are checked and the original is used if any fact changes.

## Run it

Tested on Python 3.14 (GitHub Codespaces).

```bash
git clone https://github.com/HIMANSHU-heist/AgentLedger.git
cd AgentLedger/agentledger
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# optional: live LLM wording and intent labels via Groq (the app also runs without it)
echo "GROQ_API_KEY=your_key_here" > .env

python -m pytest tests -q                  # full test suite
python demo_cli.py                         # end-to-end demo in the terminal
python benchmark/run_benchmark.py          # baseline vs AgentLedger
streamlit run dashboard/app.py             # dashboard
```

Dashboard tabs: customer chat with a live progress stepper built from the ledger, supervisor approval console, audit ledger with chain verification, sequential vs parallel DAG timing, benchmark chart.

## Code map

| Idea | Where |
|---|---|
| Simulated payment systems and seed cases | `agentledger/app/core/db.py` |
| Idempotent tools (refund, dispute, ticket) | `agentledger/app/tools/payment_tools.py` |
| Append-only, hash-chained ledger | `agentledger/app/core/ledger.py` |
| State-diff verification | `agentledger/app/core/verifier.py` |
| Policy-as-code and engine | `agentledger/app/policy/` |
| DAG runner and orchestrator | `agentledger/app/core/dag.py`, `orchestrator.py` |
| LLM layer (Groq, model tiers, cache, mock fallback) | `agentledger/app/llm/` |
| Dashboard | `agentledger/dashboard/` |
| Benchmark and injection tests | `agentledger/benchmark/`, `agentledger/tests/` |

## Design principles

Human override, explainable decisions, AI disclosure in every reply, an audit trail and contestability ("ask for a human") are inspired by the principles in RBI's FREE-AI framework. This is a design inspiration, **not a compliance claim**.

## What we are not claiming

- No real Paytm, bank or NPCI integration; no control over real UPI dispute outcomes.
- No autonomous loan approval. The loan pre-screen in the policy engine always routes to a human and is tested, but it is **not wired into the UI yet**.
- The benchmark is hand-written and partly simulated; it is not proof of production reliability.
- The hash chain is **tamper-evident, not tamper-proof**: someone with full database write access could rebuild it without an external anchor.
- Simulated latency (0.3 s per call) is used to show the DAG benefit; it is not real API speed.
- Guaranteed refunds, 100% accuracy and full autonomy are not claimed.

## Not built yet

Settlement-delay flow, loan flow in the UI, UPI dispute state machine, automatic retry and circuit breaker, conformal-prediction escalation and shadow-mode "earned autonomy".
