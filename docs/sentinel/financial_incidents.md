# Sentinel Mission 5 — Financial Incidents

Modules: `services/sentinel/incidents.py` (+12 Mission 5 types),
`financial_exposure.py`, `api.py` (read routes).

## The 12 financial incident types

FINANCIAL_ACCOUNT_TAKEOVER_SUSPECTED, PAYMENT_ABUSE_SUSPECTED,
REFUND_ABUSE_SUSPECTED, PAYOUT_ABUSE_SUSPECTED,
MARKETPLACE_ABUSE_SUSPECTED, COORDINATED_FINANCIAL_ABUSE,
AD_WALLET_INTEGRITY_ANOMALY, ADVERTISING_FINANCIAL_ANOMALY,
FINANCIAL_LEDGER_MISMATCH, FINANCIAL_WEBHOOK_REPLAY,
DUPLICATE_ECONOMIC_EFFECT_RISK, FINANCIAL_PROVIDER_INCONSISTENCY.

Naming is deliberate: SUSPECTED means suspected, ANOMALY means anomaly,
MISMATCH means mismatch. No type asserts guilt; tests scan the vocabulary
for FRAUD_CONFIRMED / GUILTY / FRAUDSTER and require their absence.

## Mechanics

- Idempotent by `incident_key` (`dedupe_key(*components)` →
  `"inc_" + sha256[:24]`). Re-detection updates, never duplicates.
- `sentinel_incidents` has no subject_ref column; Mission 5 openers embed
  `"subject_ref"` inside detail_json, and lookups filter on it.
- High-severity findings must carry evidence refs — a high-risk incident
  without evidence is a DO-NOT-PASS condition, enforced in tests.
- Incidents marked `owner_action_required=1` feed the
  `owner_financial_review_required` counter in the owner summary. The owner
  decides; Sentinel only surfaces.

## Exposure estimation (financial_exposure.py)

Exposure classes: CONFIRMED (requires evidence ref — FIN-015), POTENTIAL,
DISPUTED, UNKNOWN. Classes are **never summed into one number** — a
confirmed 200¢ and a potential 750¢ stay separate, because adding a fact to
a guess produces a lie. UNKNOWN items are counted, not priced. Totals take
the latest record per incident (no double-counting across revisions;
tested: 3 revisions of 200¢ ≠ 1200¢). Negative amounts and unknown classes
raise.

## Read API (admin-gated, read-only)

- `GET /api/admin/sentinel/financial/summary` — counts, exposure totals,
  switch states, authority note.
- `GET /api/admin/sentinel/financial/incidents[?type=]` — open incidents
  restricted to the 12 types; unknown type → 400 with known_types.
- `GET /api/admin/sentinel/financial/transactions/<subject_ref>` — full
  observed context (references only).
- `GET /api/admin/sentinel/financial/reconciliation` — recent results +
  status counts.
- `GET /api/admin/sentinel/financial/risk/<entity_ref>` — latest assessment
  or honest UNKNOWN.

There is no POST/PUT/DELETE/PATCH anywhere in the contract (source-scanned
by tests). Enforcement actions are not HTTP verbs.
