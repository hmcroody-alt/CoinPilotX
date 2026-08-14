# Sentinel Mission 5 — UNDX Financial Interface

Module: `services/sentinel/undx_interface.py`
(`financial_threat_context` surface). UNDX gets read-only advisory context
and ZERO money authority.

## The surface

`financial_threat_context` is registered in READ_SURFACES and dispatched
through the standard `read()` entry point. Given a valid financial entity
ref it returns:

- observed events, incidents, and reconciliation results (references
  only — TYPE:id, never raw instruments or internal identifiers),
- the latest unexpired risk assessment including its reasons, evidence
  refs, and **contradicting evidence** (Stage 18: exculpatory evidence
  must survive into the context),
- `signal_quality_note` carrying "ANOMALY != FRAUD" / "RISK != GUILT"
  framing — these notes classify at INTERNAL and survive redaction
  (tested),
- an explicit `may_not` list.

Invalid subject refs fail closed (tested).

## may_not — the contract in the payload

Every context includes FINANCIAL_MAY_NOT (14 entries), including:
move_funds, issue_refunds, freeze_wallets, confirm_fraud, assign_guilt —
plus the summary line "ZERO money authority". The consuming model is told,
in-band and on every read, what it cannot do and cannot conclude.

## Structural guarantees

- The interface exposes no money-moving function names (tested by surface
  scan of the interface module).
- Redaction (`classification.redact`) strips anything above INTERNAL;
  the Mission 5 operational-metadata allowlist admits closed-vocabulary
  states, amounts in cents (aggregated observations, not instruments),
  TYPE:id refs, and Sentinel's own deterministic explanations — nothing
  else. card_number/cvv-style fields redact even if they somehow appear.
- If UNDX (or anything else) tries to act on the context, the only
  "action" entry point is `financial_mutation_lock.attempt()`, which
  always refuses, records the attempt as evidence, and raises.

UNDX may summarize, prioritize, and recommend to the owner. It may not
judge, and it cannot touch money.
