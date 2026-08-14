# Sentinel Mission 5 — Financial Invariants

Module: `services/sentinel/financial_invariants.py`. Fifteen named,
individually-evaluable transaction-integrity invariants (FIN-001 … FIN-015).
Each evaluation returns VIOLATED, HOLDS, or NOT_EVALUATED — an invariant
that can't be checked says so honestly instead of guessing.

## Invariant catalogue

| ID | Invariant (informal) |
|---|---|
| FIN-001 | A refund must reference an existing captured payment and not exceed it |
| FIN-002 | Total refunds per payment must not exceed the captured amount |
| FIN-003 | A payout must reconcile against settled, available balance |
| FIN-004 | Order state transitions must follow the legal lifecycle |
| FIN-005 | Fee computation must match the platform fee schedule (proposed-standard flag honored) |
| FIN-006 | Every settlement line must map to a known internal transaction |
| FIN-007 | Ledger debits and credits must balance per transaction group |
| FIN-008 | Client-submitted payment claims must never be treated as canonical |
| FIN-009 | A provider event may produce at most one economic effect |
| FIN-010 | Webhook-derived state requires a valid provider signature |
| FIN-011 | Ad spend must not exceed wallet balance plus approved credit |
| FIN-012 | Currency must be consistent within a transaction group |
| FIN-013 | Amounts must be non-negative integers in cents |
| FIN-014 | Every economic effect must trace to an authoritative source event |
| FIN-015 | A confirmed loss requires evidence refs (no evidence → cannot confirm) |

(Authoritative definitions and the exact fact vocabulary live in the module's
`INVARIANTS` mapping; tests exercise VIOLATED/HOLDS/NOT_EVALUATED for all 15.)

## Escalation

`escalate(...)` on a VIOLATED result opens an owner-action incident
(FINANCIAL_LEDGER_MISMATCH family) with the subject_ref embedded in
detail_json. Escalation is idempotent (incident_key dedupe) and is a no-op
on HOLDS. Escalation is the ceiling of Sentinel's authority: the numbers
stay exactly as found; nothing is repaired, reversed, or adjusted.

## Honesty properties (tested)

- Missing facts → NOT_EVALUATED, never HOLDS by default.
- FIN-008 fires VIOLATED when a client claim is presented as canonical —
  this is the structural backstop for the client-authority principle.
- FIN-015 blocks "confirmed loss" claims without evidence: exposure
  recording of a CONFIRMED amount without an evidence ref raises.
