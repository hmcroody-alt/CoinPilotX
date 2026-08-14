# Sentinel Mission 5 — Refund Abuse Detection

Module: `services/sentinel/financial_detections.py` (refund pattern
analysis), gated by `SENTINEL_REFUND_RISK_ENABLED` (chains on the master
detection switch). REFUND != ABUSE is the governing principle.

## Legitimate refunds score zero — by construction

The following refund reasons are excluded from abuse signals entirely
(tested: they count 0 toward any pattern):

- `cs_approved` — customer service approved it; 8 of them score 0.0.
- `partial_refund` — partial resolutions; 6 of them score 0.0.
- `item_not_received` — the platform failed to deliver.
- `platform_error` — our bug, their refund.
- `seller_fault` — seller misrepresentation or failure.
- `goodwill` — deliberate business decisions.

A user who is repeatedly refunded because sellers fail them is a victim of
bad sellers, not a refund abuser.

## What does register

Abusive full-refund cycling — repeated buy → consume → full-refund loops
without legitimate reason codes — produces a signal > 0.5 (tested). Even
then the outcome is REFUND_ABUSE_SUSPECTED: a review queue entry with
evidence, not a judgment and not an action.

## Bounds

- Analysis windows are bounded; oversized windows are rejected
  (baseline-poisoning defense — a year of history cannot be used to dilute
  or manufacture a pattern).
- Detection off → no analysis, no residual scoring.

## Authority ceiling

Sentinel cannot issue, execute, deny, or reverse a refund
(`issue_refund` / `execute_refund` are FORBIDDEN_CAPABILITIES; any attempt
raises `FinancialMutationForbidden` and is recorded as evidence). The
refund pipeline is untouched; Sentinel only reads its outcomes.
