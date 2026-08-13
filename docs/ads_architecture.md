# Advertising Intelligence — architecture

This describes the layer added on top of PulseSoc's existing advertising system.
It does not describe an advertising system, because we did not build one; the
advertiser model, campaign engine, wallet, ledger, review pipeline and delivery
path all predate this work and are unchanged in their responsibilities.

For what already existed and how each piece was classified, read
[`advertising_current_architecture.md`](advertising_current_architecture.md)
first. This document assumes it.

## The rule that shaped everything

**There is exactly one advertising platform.** Every temptation to add a second
one was refused, and the refusals are the interesting part of the design:

| Temptation | What we did instead |
| --- | --- |
| A new advertiser/campaign model with better fields | Read the canonical tables; store only derived intelligence beside them |
| A new delivery engine that ranks better | `select_ads` still decides; ranking is offered to it, not substituted for it |
| A new wallet so pacing can stop spend | Pacing throttles *admission*; the existing ledger remains the only thing that moves money |
| A new admin app for the new dashboards | New routes on the existing `/admin/business-os` surface |
| A UNDX ads backend | UNDX reads the same endpoints an operator does |

The practical test applied to every module: *if this file were deleted, would
the advertising system still charge correctly, deliver correctly and pay out
correctly?* If the answer was ever "no", the module was in the wrong package.
This is why account guardrails live in `services/business_os/advertising/`
(stopping delivery stops charging — that is money authority) while everything
else lives in `services/business_os/ads_intelligence/` (measurement and advice,
structurally unable to act).

## Shape

```
                      client + server ad events
                                 │
                    events.py ── privacy.py ── taxonomy.py
                    validate, repair, classify, hash subjects
                                 │
                        ads_intel_events  (the fabric)
                                 │
     ┌───────────────┬───────────┴────────┬──────────────────┐
     │               │                    │                  │
 interest.py     context.py         performance.py    invalid_traffic.py
 who, decayed    where, permitted   how creatives do  what wasn't a human
     │               │                    │                  │
     └───────────────┴────────┬───────────┴──────────────────┘
                              │
                         ranking.py          pacing.py / frequency.py
                    explainable score          how much, how often
                              │                        │
                              └────────┬───────────────┘
                                       │
                          advertising.service.select_ads
                              (canonical, decides)
                                       │
                       decisions.py records what happened
                                       │
                        ads_intel_delivery_decisions
                                       │
        ┌──────────────┬───────────────┼──────────────┬─────────────┐
   transparency   diagnostics    recommendations    health    ml_readiness
   "why this ad"  "why dark"     "what to change"  "is it ok" "can we train"
```

Everything below `ads_intel_events` is derived and rebuildable. Nothing below it
is a source of truth about money.

## Modules

| Module | Answers | Notes |
| --- | --- | --- |
| `taxonomy.py` | What are the legal names and thresholds? | Closed vocabularies. A name not in here is rejected at ingest, not coerced. |
| `privacy.py` | Who is this, and what may we use it for? | Salted subject refs; purpose gates; forbidden sources. |
| `events.py` | Did this event really happen, and cleanly? | Validation, dedupe, quality grading. |
| `decisions.py` | What did delivery actually do, and why not? | The only writer of delivery decisions. |
| `interest.py` | What is this subject interested in? | Time-decayed, closed categories, reach floors. |
| `context.py` | Is an ad allowed here, and does it fit? | Refuses private and sensitive surfaces outright. |
| `performance.py` | How is this creative doing? | Rates only above minimum denominators; fatigue states. |
| `ranking.py` | Which candidate, and why that one? | Deterministic, weighted, explainable. No model. |
| `pacing.py` | Is this campaign spending at the right rate? | Throttles admission, never money. |
| `frequency.py` | Have we shown this person enough? | Windowed caps plus ad-load limits. |
| `attribution.py` | Did this click cause that conversion? | Last click only. View-through unsupported, on purpose. |
| `invalid_traffic.py` | Was that a human? | Screened before billing, never after. |
| `transparency.py` | Why am I seeing this ad? | Reads the recorded decision; never reconstructs one. |
| `diagnostics.py` | Why is my campaign dark? | Cause with an owner attached. |
| `recommendations.py` | What should I change? | Rules with evidence. Capped at "recommend". |
| `health.py` | Is anything wrong right now? | Anomalies. Always `requires_human`. |
| `ml_readiness.py` | Could we train on this yet? | Eight gates. Currently: no. |
| `api.py` | The HTTP shape | Thin. Derives identity server-side. |

## HTTP surface

Six routes, all registered in `bot.py` against the existing app.

| Route | Who | Purpose |
| --- | --- | --- |
| `POST /api/business-os/ads-intel/events` | client | Event ingest (allowlisted fields only) |
| `GET /api/business-os/ads-intel/campaigns/<id>/delivery` | advertiser | Diagnosis + recommendations |
| `GET /api/business-os/ads-intel/why/<decision_id>` | viewer | Why this ad |
| `GET /api/business-os/ads-intel/my-interests` | viewer | Interest disclosure |
| `GET /admin/business-os/ads-intel/delivery-health` | operator | No-fill breakdown |
| `GET /admin/business-os/ads-intel/status` | operator | Layer status |

Note what is *absent*: there is no endpoint that changes a bid, a budget, a
campaign state or a balance. The intelligence layer has no write path to
anything an advertiser is charged for. That is not an oversight to be fixed
later — see [`ads_safety_and_rollout.md`](ads_safety_and_rollout.md).

## Storage

Tables are created by `ads_intelligence/schema.py::ensure_schema`, registered in
`services/business_os/schema_bootstrap.py`, idempotent, and additive only. The
module never imports `bot.py`.

- `ads_intel_events` — the fabric. Graded, deduped, versioned.
- `ads_intel_delivery_decisions` — one row per opportunity, fill or no-fill.
- `ads_intel_interest_affinity` — derived, per subject/category/window.
- `ads_intel_creative_daily` / `ads_intel_campaign_daily` — rebuildable rollups.
- `ads_intel_attribution` — last-click conversions.
- `business_os_ad_account_guardrails` — *not* in this package; see above.

Every derived table can be dropped and rebuilt from the fabric. That is the
property that makes it safe to change how a metric is computed.

## Versioning

`taxonomy.py` carries `EVENT_SCHEMA_VERSION`, `PROCESSING_VERSION`,
`RANKING_VERSION`, `ATTRIBUTION_VERSION`, `FEATURE_VERSION`,
`RECOMMENDATION_VERSION` and `FRAUD_RULE_VERSION`, and rows are stamped with
them. This exists so that a question like "did the rules change halfway through
this window?" is answerable by a query rather than by memory — `ml_readiness`
depends on exactly that, and would otherwise silently train on labels that mean
two different things.
