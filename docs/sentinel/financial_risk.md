# Sentinel Mission 5 — Financial Risk Scoring

Module: `services/sentinel/financial_risk.py`. RISK != GUILT — every output
is an advisory prioritization signal, never a verdict.

## Design

- **Multi-factor, weighted, explainable.** Factor weights sum to exactly 1.0
  (tested). Every assessment carries deterministic `reasons` and
  `evidence_refs` — an unexplainable score is a bug.
- **Evidence floor for HIGH_RISK.** An entity cannot reach HIGH_RISK from a
  single signal or without reasons/evidence refs, in any entity role
  (buyer, seller, advertiser, wallet holder). Framing attempts using one
  planted signal are structurally impossible (tested per role).
- **Contradicting evidence reduces risk** and must survive redaction
  (Stage 18): a context that hid exculpatory evidence would be dishonest.
- **Shared-infrastructure damping.** Shared device/network factors are
  damped ×0.3 — DEVICE/NETWORK SHARING != FRAUD. Family homes, offices,
  CGNAT, and QA rigs stay NOT_ESTABLISHED.
- **External fusion caps** (Mission 4 alignment): external-intelligence-only
  scores cap at 0.6; with internal corroboration (≥3 corroborating internal
  signals) the cap lifts to 0.8. A provider verdict alone can never produce
  HIGH_RISK.
- **Velocity is bounded input.** 25 events in a window maps to signal 1.0
  with the explicit note "HIGH VOLUME != FRAUD"; window sizes are bounded —
  a 365-day window is rejected (baseline-poisoning defense).

## Decay and expiry

- Assessments decay over time: at +71h a recorded score reads back < 0.1.
- Every row has a mandatory expiry; TTL is bounded. After expiry
  (`latest()` at +73h) the answer is `None` → surfaced as trust_state
  UNKNOWN.
- **No permanent labels**: at +31 days nothing remains. Unknown is reported
  as unknown — never as safe, never as risky.

## Read surface

- `financial_risk.latest(entity_ref)` — latest unexpired decayed assessment
  or None.
- `financial_risk.active_high_risk()` — count feeding observability.
- Admin API: `GET /api/admin/sentinel/financial/risk/<entity_ref>`
  (read-only; invalid refs → 400; absent/expired → honest UNKNOWN).

## What a score cannot do

A score cannot hold funds, delay a payout, freeze a wallet, or trigger any
enforcement. There is no code path from an assessment to a mutation —
`financial_mutation_lock.verify_module_surface()` proves the capabilities
don't exist in the package.
