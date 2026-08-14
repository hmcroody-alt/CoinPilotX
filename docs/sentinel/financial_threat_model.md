# Sentinel Mission 5 — Financial Threat Model

Status: V1 foundation, detection OFF by default. Read-only. Sentinel has ZERO
money-movement authority — structurally enforced, not policy-enforced
(`services/sentinel/financial_mutation_lock.py`).

## Scope

Financial fraud and transaction-integrity threats against PulseSoc:
payments (Stripe), marketplace orders, refunds, payouts to sellers,
advertising wallets, and the ledgers that reconcile them.

## Absolute principles (constitution)

- **ANOMALY != FRAUD** — an unusual number is a question, not an accusation.
- **RISK != GUILT** — a score prioritizes review; it never convicts.
- **RELATIONSHIP != COLLUSION** — knowing someone is not conspiring with them.
- **DEVICE/NETWORK SHARING != FRAUD** — families, offices, CGNAT, QA rigs.
- **REFUND != ABUSE** — item-not-received, platform errors, seller fault,
  goodwill, and CS-approved refunds are legitimate by definition.
- **HIGH VOLUME != FRAUD** — velocity is a signal input, never a verdict.
- **EXTERNAL PROVIDER VERDICT != CANONICAL GUILT** — provider output is
  evidence; fused external-only scores cap at 0.6 (see financial_risk.md).
- **CLIENT CLAIM != AUTHORITY** — client-submitted "payment succeeded" carries
  confidence ≤ 0.1 and trust grade UNKNOWN; it can never become canonical.

## Threat catalogue

| Threat | Incident type(s) | Detection surface |
|---|---|---|
| Financial account takeover | FINANCIAL_ACCOUNT_TAKEOVER_SUSPECTED | FAT sequence chains (identity events → payout/payment changes) |
| Payment abuse (card testing, stolen instruments) | PAYMENT_ABUSE_SUSPECTED | payment event velocity + decline patterns |
| Refund abuse (cycling, serial full refunds) | REFUND_ABUSE_SUSPECTED | refund pattern analysis with legitimate-reason exclusions |
| Payout abuse (drain after ATO, mule payouts) | PAYOUT_ABUSE_SUSPECTED | payout threat signals; ≥2 independent signals to flag |
| Marketplace abuse (fake orders, collusion) | MARKETPLACE_ABUSE_SUSPECTED | coordination analysis requiring ≥3 accounts, ≥2 dimensions, anomaly |
| Coordinated financial abuse | COORDINATED_FINANCIAL_ABUSE | multi-entity correlation with shared-infrastructure damping |
| Ad wallet integrity anomaly | AD_WALLET_INTEGRITY_ANOMALY, ADVERTISING_FINANCIAL_ANOMALY | spend vs. ledger cross-checks, 1-cent tolerance |
| Ledger mismatch | FINANCIAL_LEDGER_MISMATCH | reconciliation engine; mismatches recorded, never repaired |
| Webhook replay / duplicate effect | FINANCIAL_WEBHOOK_REPLAY, DUPLICATE_ECONOMIC_EFFECT_RISK | delivery-count tracking, signature validation |
| Provider inconsistency | FINANCIAL_PROVIDER_INCONSISTENCY | signature failures, contradictory provider states |

All 12 types are SUSPECTED/ANOMALY/RISK/MISMATCH framings. There is no
FRAUD_CONFIRMED, GUILTY, or FRAUDSTER type anywhere in the vocabulary
(asserted by tests).

## Adversarial model — attacks Sentinel itself must survive

The test suite (`tests/sentinel/test_mission5_financial.py`, TestAdversarial)
proves these attempts fail:

1. **Framing an innocent user** — a single planted signal, in any entity
   role, can never produce HIGH_RISK; high risk requires the evidence floor
   (multiple reasons + evidence refs).
2. **Baseline poisoning** — analysis windows are bounded; a 365-day window is
   rejected rather than diluting baselines.
3. **Client authority escalation** — client "payment succeeded" claims are
   locked to confidence 0.1 / trust UNKNOWN, and invariant FIN-008 fires
   VIOLATED if a client claim is ever treated as canonical.
4. **Replay amplification** — event ingest is idempotent; five duplicate
   observations produce one row.
5. **Provider-verdict laundering** — external verdicts fuse capped at 0.6
   without internal corroboration, and any attempt to act on one
   (`attempt("hold_funds")`) raises `FinancialMutationForbidden`.
6. **Permanent labeling** — risk assessments expire (TTL-bounded); after
   expiry the honest answer is UNKNOWN, not a remembered stain.
7. **Payload smuggling** — card/bank fields (card_number, cvv,
   routing_number, …) are rejected at ingest by
   `financial_entities.assert_payload_safe` and redacted by classification if
   they ever appear downstream.

## What Sentinel MAY do

Observe, correlate, score (with decay + expiry), explain deterministically,
open SUSPECTED incidents, estimate exposure (classes never summed),
recommend, and escalate to the owner.

## What Sentinel MAY NOT do — ever

Move, hold, reverse, refund, pay out, freeze, rebalance, re-fee, or re-route
money; suspend sellers; ban buyers. These capabilities do not exist in the
package (surface-scanned by `verify_module_surface()`), the only entry point
(`financial_mutation_lock.attempt`) always refuses and records the attempt as
evidence, and `SENTINEL_FINANCIAL_AUTOMATION_ENABLED=1` changes nothing.
