# Sentinel Mission 5 — Financial Event Model

Modules: `services/sentinel/financial_sources.py`,
`financial_entities.py`, `financial_events.py`.

## Sources (financial_sources.py)

40 registered financial signal sources across 8 source classes. Each class
carries a hard confidence ceiling — an event can never claim more confidence
than its source class allows:

| Source class | Ceiling | Examples |
|---|---|---|
| AUTHORITATIVE | 1.0 | Stripe webhook (signature-verified), internal ledger |
| PLATFORM_INTERNAL | 0.9 | order lifecycle, payout scheduler |
| PLATFORM_DERIVED | 0.8 | derived aggregates, computed balances |
| PARTNER | 0.7 | partner settlement files |
| EXTERNAL_INTELLIGENCE | 0.6 | fraud-intel providers (Mission 4 fusion cap applies) |
| HEURISTIC | 0.5 | pattern detectors |
| USER_REPORTED | 0.4 | user disputes, reports |
| CLIENT_REPORTED | 0.3 | anything the client asserts about money |

Ceilings are strictly ordered (tested). Only AUTHORITATIVE sources may be
canonical. `payment_verifications` (client-side "payment succeeded") is
CLIENT_REPORTED with trust grade UNKNOWN.

Effective confidence = `min(source-class ceiling, trust-grade ceiling)`.
A client payment claim resolves to `min(0.3, 0.1) = 0.1` — locked in both
directions (tested bidirectionally).

## Entities (financial_entities.py)

16 entity types (ORDER, PAYMENT, REFUND, PAYOUT, SELLER, BUYER, WALLET,
AD_ACCOUNT, CAMPAIGN, LEDGER, SETTLEMENT, DISPUTE, INSTRUMENT_REF,
PROVIDER_EVENT, INVOICE, TRANSFER). Canonical reference form is `TYPE:id`;
ids containing colons are rejected. `is_valid_ref` gates every API and
context entry point.

`assert_payload_safe` rejects any payload containing payment-instrument
fields (card_number, pan, cvv, cvc, routing_number, account_number, iban,
…) at ingest — including inside nested dicts and lists. Sentinel stores
aggregated observations and TYPE:id references, never instruments.

## Events (financial_events.py)

- `observe(...)` is the single ingest path. Idempotent by dedupe key:
  re-observing the same event N times produces exactly one row (tested at
  5×).
- Confidence assignment is structural: authoritative signature-verified
  events get 1.0; client claims get 0.1; everything else is capped by class
  and trust grade.
- Emergency kill switch (`SENTINEL_EMERGENCY_KILL_SWITCH`) stops ingest.
- `correlation_keys` always include the subject_ref so the sequences engine
  can correlate financial events with identity events (subject_id join —
  `SELLER:901` correlates with identity events for user `901`).
- Events are append-only observations. Nothing in this module (or any
  sentinel module) can mutate a balance, order, or payment.

## Kill-switch chain

`SENTINEL_FINANCIAL_DETECTION_ENABLED` (default OFF) gates all detection.
Subdomain switches (`SENTINEL_MARKETPLACE_RISK_ENABLED`,
`SENTINEL_PAYOUT_RISK_ENABLED`, `SENTINEL_REFUND_RISK_ENABLED`,
`SENTINEL_AD_WALLET_RISK_ENABLED`) chain on the master.
`SENTINEL_EMERGENCY_KILL_SWITCH` kills everything.
`SENTINEL_FINANCIAL_AUTOMATION_ENABLED` is deliberately ignored —
`financial_automation_enabled()` returns False unconditionally.
