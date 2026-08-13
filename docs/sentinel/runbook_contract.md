# Sentinel Runbook Contract (Stages 14–15)

Module: `services/sentinel/runbooks.py`. Table: `sentinel_runbook_executions`.

## What a runbook is

The ONLY way Sentinel automation acts. A `RunbookSpec` declares:

| field | contract |
|-------|----------|
| `name` | unique; screened against `FORBIDDEN_NAME_PATTERNS` |
| `description` | screened for arbitrary-execution language |
| `domain` | authority dimension the action lives in |
| `required_level` | `AuthorityLevel`; `OWNER_ONLY` is unconstructible as a runbook (SC3) |
| `budget` | `RiskBudget` — bounded or unconstructible (SC14) |
| `executor` | deterministic callable |
| `verifier` | independent check callable, run by a *different* actor (SC4) |

## Structurally forbidden

`ForbiddenRunbookError` at construction for names/descriptions matching:
`arbitrary_shell`, `arbitrary_sql`, `raw_shell`, `raw_sql`, `exec_shell`,
`shell_exec`, `run_sql`, `eval`, `sudo`. There is no generic "execute this
command" runbook and never will be — that would be a super key by another
name (SC11).

Shipped registry additionally must contain nothing matching `refund`,
`payout`, `rollback`, `transfer`, `ban_account` (SC6; regression-tested).

## Execution pipeline (`runbooks.execute`)

Every call walks, in order — any failure returns a `DENIED` dict (never an
exception to the caller) and the denial is recorded:

1. Registry lookup — unknown runbook → DENIED (SC15)
2. Kill-switch chain — master AND domain AND per-runbook must be ON (SC3)
3. Risk budget — `BudgetTracker.try_spend`; exhausted → DENIED (SC14)
4. Persist `RUNNING` row, run executor
5. Outcome: `EXECUTED_UNVERIFIED` (success is a *claim*) or `FAILED`
6. Evidence-chain record either way (SC5)

`EXECUTED_UNVERIFIED` only becomes `COMPLETED` through
`verification.verify_execution` by a different actor — see
`verification.md`.

## Shipped runbooks (V1 — deliberately boring)

| name | what it does | level |
|------|--------------|-------|
| `snapshot_provider_health` | reads provider capability table into a report | ACT_REVERSIBLE |
| `run_financial_invariants` | executes the read-only invariant suite | ACT_REVERSIBLE |
| `verify_evidence_chain` | recomputes the evidence hash chain | ACT_REVERSIBLE |

All three are read-only probes. V1 ships **zero** state-mutating runbooks;
the pipeline exists so that when one is proposed, every guard is already
load-bearing.
