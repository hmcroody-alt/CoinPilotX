# Sentinel Mission 5 — Ad Wallet Integrity

Module: `services/sentinel/ad_wallet_integrity.py`, gated by
`SENTINEL_AD_WALLET_RISK_ENABLED` (chains on the master detection switch).
Cross-checks advertising spend against wallet ledgers and reports what it
finds — exactly as found.

## Checks

`check(...)` compares expected vs. observed figures per wallet/campaign and
returns one of:

- **MATCH** — figures agree (a 1-cent tolerance absorbs rounding; tested).
- **MISMATCH** — figures disagree beyond tolerance. Both sides are
  preserved verbatim in the result; with the ad-wallet switch on, an
  idempotent AD_WALLET_INTEGRITY_ANOMALY incident opens. Switch off →
  finding recorded, no incident.
- **PARTIAL** — some figures comparable, some missing; labeled as such.
- **UNKNOWN** — insufficient data; reported as unknown, never coerced to
  MATCH.
- **STALE** — inputs too old to trust; stale data can never produce MATCH
  (adversarially tested).

Unknown source names raise instead of being absorbed.

## Advertiser assessment

`assess_advertiser(...)` is advisory only: it aggregates wallet findings
into a prioritization view for the owner. ADVERTISING_FINANCIAL_ANOMALY
findings follow the same evidence rules as every other incident type.

## What Sentinel never does here

It never adjusts a wallet balance, re-bills an advertiser, changes a fee,
or pauses a campaign. `modify_balance`, `adjust_balance`, `change_fee`,
`set_fee`, and `charge_payment_method` are FORBIDDEN_CAPABILITIES —
structurally absent (surface-scanned), refused with evidence on any
attempt. A mismatch is escalated to the owner with both numbers intact;
repair is a human decision made outside Sentinel.
