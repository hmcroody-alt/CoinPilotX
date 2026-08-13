# PulseSoc advertising — current architecture

Written 2026-08-13, before any code in the Advertising Intelligence mission was
changed. It describes what exists, not what we wish existed. Every claim below
was read out of the source; where something is absent, the absence is stated
explicitly, because the absences are what the intelligence work has to fill.

---

## The finding that shapes everything else

**PulseSoc does not have one advertising system. It has two, and both are live.**

| | Legacy platform | Business OS platform |
|---|---|---|
| Tables | 31 × `pulse_ad_*` | 13 × `business_os_ad_*` |
| Services | `services/pulse_ads_*.py` (~12k lines) | `services/business_os/advertising/` (~9.7k lines, 23 modules) |
| Routes | `/api/pulse/ads/*` | `/api/business-os/advertising/*` |
| Schema created by | `bot.init_db()`, imperatively | `advertising/schema.py::ensure_schema`, idempotent |
| Feature gate | `pulse_ad_platform_settings` kill switch | `BUSINESS_OS_ADVERTISING` env flag |
| **Actually serves ads to users** | **Yes** | No |
| Has event dedup keys | No | Yes (`dedup_key` UNIQUE) |
| Has billing idempotency | Partial (`pulse_ad_idempotency`) | Yes (`idempotency_key` UNIQUE) |
| Money authority | `pulse_ad_wallets` balance column | canonical `ledger_entries` |

The legacy platform is the one users actually see. The Business OS platform is
the newer, better-designed one — built in numbered slices 1 through 7 — with
real dedup, real ledger authority, server-resolved click destinations, and
privacy-safe salted `subject_ref` viewer references instead of raw user ids. It
is architecturally the system we would want. It is not yet the system that runs.

The mission's rule is "do not create a second ads platform." There are already
two. **The operative rule for this work is therefore: do not create a third.**
The intelligence layer must be a subsystem that both platforms feed, owning no
campaigns, no advertisers, no creatives, no wallet, and no review queue.

---

## 1. Tables

### Legacy — `pulse_ad_*` (31 tables, created in `bot.init_db()`)

**Identity and account**
- `pulse_ad_accounts` — advertiser account, `status`, `verification_status`
- `pulse_ad_account_profiles` — business profile submitted for verification
- `pulse_ad_team_members` — delegated access
- `pulse_ad_billing_profiles` — billing identity

**Campaign structure**
- `pulse_ad_campaigns` — `status`, `budget_type`, `daily_budget_cents`,
  `lifetime_budget_cents`, `spent_cents`, `priority`, `start_at`, `end_at`
- `pulse_ad_adsets` — ad set layer with its own `status`
- `pulse_ad_creatives` — `creative_type`, `status`, `moderation_status`,
  `media_asset_id`, `destination_url`, `call_to_action`
- `pulse_ad_media_assets` — creative media with its own `moderation_status`
- `pulse_ad_campaign_placements` → `pulse_ad_placements` — placement allowlist,
  carrying `max_frequency`, `priority`, `supported_creative_types`
- `pulse_ad_targeting` — `country`, `language`, `device_type`,
  `premium_audience`, `contextual_category`, `audience_mode`,
  `saved_audience_ids_json`, `excluded_audience_ids_json`
- `pulse_ad_saved_audiences` — advertiser-defined audiences

**Delivery and measurement**
- `pulse_ad_impressions` — one row per impression
- `pulse_ad_clicks` — one row per click
- `pulse_ad_events` — generic event bag (`hide`, `report`, `save`, `dismiss`,
  `conversion`, video milestones, audio milestones, `error`)
- `pulse_ad_frequency_caps` — `(viewer_user_id | session_id, campaign_id,
  placement_key) → impressions_count`. **Lifetime counter. No time window.**

**Money**
- `pulse_ad_wallets` — mutable balance column
- `pulse_ad_wallet_transactions`, `pulse_ad_wallet_funding_sessions`
- `pulse_ad_billing_events`, `pulse_ad_invoices`, `pulse_ad_receipts`,
  `pulse_ad_refunds`
- `pulse_ad_idempotency` — idempotency keys for funding operations

**Governance**
- `pulse_ad_review_board`, `pulse_ad_moderation_queue`, `pulse_ad_policy_flags`,
  `pulse_ad_appeals`, `pulse_ad_audit_logs`, `pulse_ad_campaign_history`,
  `pulse_ad_notifications`, `pulse_ad_platform_settings` (kill switch)

### Business OS — `business_os_ad_*` (`advertising/schema.py`)

- `business_os_ad_advertisers` — approval state, one row per user
- `business_os_ad_campaigns` — `objective`, `status`, `review_reason`
- `business_os_ad_campaign_funding` — funding state; **no balance stored**,
  balances derive from the ledger
- `business_os_ad_funding_ops` — append-only, `idempotency_key` UNIQUE
- `business_os_ad_campaign_operations` — operational state, deliberately
  separate from review status and funding status
- `business_os_ad_sets` — `placements_json`, `audience_json`, `version`
- `business_os_ad_creatives` — `version`, `supersedes_creative_id`, review state
- `business_os_ad_delivery_instances` — a server-authorized opportunity to show
  ONE creative version, carrying `impression_token`, `subject_ref` (salted hash,
  never a raw user id), and `eligibility_snapshot_json`
- `business_os_ad_impression_events` — `dedup_key` UNIQUE, `fraud_status`,
  `billing_eligible`, `billing_processed`
- `business_os_ad_click_events` — same, plus a click requires an accepted
  impression on the same delivery
- `business_os_ad_billing_events` — `idempotency_key` UNIQUE, `accrued_millicents`
- `business_os_ad_pricing_policy` — versioned, no hardcoded prices in code
- `business_os_ad_spend_accumulator` — sub-cent CPM carry + budget-exhaustion latch
- `business_os_ad_audit`

---

## 2. Service responsibilities

### Legacy
| Module | Owns |
|---|---|
| `pulse_ads_service.py` (2495) | accounts, verification, campaigns, creatives, review, **`select_ads` — the live delivery path**, impression/viewability/click/event recording, advertiser analytics |
| `pulse_ad_payments.py` (1854) | wallet, funding sessions, transactions, invoices, receipts, spending limits, auto top-up |
| `pulse_ads_os.py` (1940) | the "full campaign" composite create used by the mobile wizards |
| `pulse_advertiser_portal.py` (1220) | web advertiser portal surface |
| `pulse_ads_reporting.py` (839) | report generation |
| `pulse_ads_adsets.py` (698) | ad set CRUD |
| `pulse_ads_insights.py` (610) | insight cards + apply |
| `pulse_ads_worker_service.py` (616) | background campaign maintenance |
| `pulse_ads_audiences.py` (524) | saved audiences, lookalikes |
| `pulse_ads_library.py` (344) | public ad library |
| `ad_policy_engine.py` (73) | policy checks |
| `dashboard_ads_command_center.py` (730) | admin command centre |

**Overlap to be aware of:** `pulse_ads_os.py` and `pulse_ads_service.py` both
create campaigns; `pulse_ads_adsets.py` and `pulse_ads_os.py` both touch ad sets.
That overlap is pre-existing and out of scope here — noted so we do not add to it.

### Business OS
`api.py` (routes) · `service.py` (campaign lifecycle) · `ad_sets.py` ·
`creatives.py` · `eligibility.py` · `targeting.py` · `selection.py` ·
`delivery.py` · `delivery_common.py` · `frequency.py` · `events.py` ·
`pricing.py` · `billing.py` · `spend.py` · `funding.py` · `operations.py` ·
`reporting.py` · `admin.py` · `assistant.py` · `notifications.py` · `readiness.py`

Sibling packages that already exist and must be reused, not duplicated:
`business_os/attribution/` (829 lines, has an `engine.py`),
`business_os/events/` (979 lines), `business_os/performance/`,
`business_os/recommendations/`, `business_os/insights/`.

---

## 3. The delivery path (legacy — the one that runs)

Entry: `GET /api/pulse/ads/placements` → `api_pulse_ads_placements()`
(bot.py:16993) → `pulse_ads_service.select_ads()`
(services/pulse_ads_service.py:2122).

`select_ads` does, in order:

1. Kill-switch check — `platform_ads_enabled()`
2. Resolve candidate placements from context + device
3. **One SQL query** joining creatives → campaigns → accounts → placements →
   targeting → media, filtering on: placement active, campaign `active`, account
   `active`, account verified/approved, creative `approved` **and** moderation
   `approved`, media moderation approved, schedule window, device match, ad set
   active. Ordered by `placement_priority, campaign_priority, id`, capped at
   `limit × 8`.
4. Per-row filters in Python: `_matches_targeting`, `_passes_audience_targeting`,
   `_compatible_creative`, `_campaign_budget_available`, `_frequency_allowed`
5. Score, sort, dedupe by campaign, mint a signed `delivery_token`, sanitize

**The entire ranking function is line 2217:**

```python
item["_score"] = (placement_priority * 100) + campaign_priority + rotation_hash - recent_penalty
```

where `rotation_hash` is `sha256(hour : session : creative : placement) % 20` and
`recent_penalty` is a flat 50 if this campaign was already seen at this placement.

So today's ranking is: **static priority, plus a stable pseudo-random rotation,
minus a recency penalty.** There is no relevance, no user affinity, no creative
performance, no objective awareness, no pacing, no fatigue, and no quality term.

**Eligibility, by contrast, is genuinely strong.** Verification, moderation on
both creative and media, schedule, budget, frequency, audience, and opt-out are
all enforced before an ad can be considered. This is the part to keep.

### What the delivery path does not do
- It does not record that an opportunity occurred.
- It does not record a delivery decision.
- **It returns `[]` with no reason.** When no ad is served, nothing anywhere
  says whether that was "no eligible campaign", "all budget exhausted",
  "frequency capped", or "kill switch on". This is the single biggest
  diagnostic gap in the system.

---

## 4. The finance path

`record_impression` / `record_click` (pulse_ads_service.py:2278, :2353) verify
the signed delivery token, re-assert the served creative against the database,
then write to `pulse_ad_impressions` / `pulse_ad_clicks`.

Spend is accrued against `pulse_ad_campaigns.spent_cents` and the wallet in
`pulse_ad_payments.py`. `_campaign_budget_available` (:1885) reads
`spent_cents` against `daily_budget_cents` / `lifetime_budget_cents`.

The Business OS platform is stricter: `billing.py` writes
`business_os_ad_billing_events` with a UNIQUE `idempotency_key`, carries
sub-cent remainders in `business_os_ad_spend_accumulator`, and posts whole cents
to the canonical ledger. Money is never stored as a mutable balance.

**Neither path bills from an analytics aggregate**, which is the invariant the
mission cares about, and it currently holds. Nothing in this mission may weaken it.

---

## 5. Existing event tracking

| Event | Legacy route | Recorded to | Dedup |
|---|---|---|---|
| impression | `POST /api/pulse/ads/impression` | `pulse_ad_impressions` | delivery-token replay check only |
| viewability | `POST /api/pulse/ads/viewability` | impression row update | — |
| click | `POST /api/pulse/ads/click` | `pulse_ad_clicks` | — |
| hide/report/save/video milestones | `POST /api/pulse/ads/event` | `pulse_ad_events` | none |

All four are **single-event POSTs**. There is no batch ingest endpoint. Writes
are CSRF-checked (or mobile Bearer) and rate-limited (240/240/80/120 per minute).

A viewability concept **does** exist and is better than expected: the client
accumulates dwell time and only reports after ≥1000 ms at ≥72 % visible
(`SponsoredAdCard.tsx`, `HomeScreen.tsx`). What is missing is that viewability
is stored as an attribute of the impression rather than as a first-class event
with `percent_visible` and `foreground_state`.

**Conversions:** `conversion` exists as a client-supplied `event_type` string in
`pulse_ad_events`. There is no server-side derivation of a purchase from the
canonical order/payment backend, and no attribution join. This is the mission's
"client events are not authoritative for money" gap, and it is real.

---

## 6. Client (mobile-native)

- `src/api/ads.ts` — `fetchSponsoredAds`, `recordAdImpression`,
  `recordAdViewability`, `recordAdClick`, `recordAdEvent`
- `src/api/adsOs.ts` — campaign creation contract
- `src/components/SponsoredAdCard.tsx` — renders the ad, owns the dwell timer,
  owns the ⋯ menu with **Hide this ad** and **Report ad** (already shipping)
- `src/feed/injectAds.ts` — pure interleave, one ad per 4 posts, lead-in of 4
- `src/screens/PromoteContentWizardScreen.tsx` — 6-step Post Ads wizard
- `src/screens/AdsCampaignWizardScreen.tsx` — 7-step Marketplace/full wizard
- `src/core/eventSync.ts` — server→client invalidation polling, **not** an
  analytics pipeline

**There is no client-side event queue.** Every ad event is an individual
fire-and-forget POST with no batching, no persistence, and no retry. An event
emitted while offline is lost. This is the client-side gap.

**There is no "Why am I seeing this ad?" surface.**

---

## 7. Classification

| Component | Call | Why |
|---|---|---|
| `pulse_ad_*` identity, campaign, creative, review, wallet tables | **KEEP** | live production data; not ours to move |
| `business_os_ad_*` tables | **KEEP** | the better model; the intelligence layer should mirror its conventions |
| `select_ads` eligibility filters | **KEEP** | genuinely strong and policy-correct |
| `select_ads` scoring (line 2217) | **EXTEND** | replace with an explainable deterministic score, behind a flag, shadow first |
| No-fill / opportunity / decision recording | **BUILD** | does not exist at all; blocks every diagnostic |
| `pulse_ad_impressions` / `_clicks` / `_events` | **NORMALIZE** | keep writing them; project them into a canonical event fabric |
| `pulse_ad_frequency_caps` | **EXTEND** | lifetime counter → add session/day/week windows alongside |
| Client single-event POSTs | **EXTEND** | keep the endpoints; add a batched, persisted, retrying queue in front |
| Viewability-as-attribute | **NORMALIZE** | promote to a first-class event with percent + duration |
| `conversion` as a client string | **MIGRATE** | must derive from the canonical order/payment backend |
| Interest graph, context signals, creative performance, pacing, invalid traffic, diagnostics, recommendations, ML readiness | **BUILD** | none of it exists |
| A third ads platform | **FORBIDDEN** | two is already one too many |

Nothing is marked DEPRECATE. Nothing is deleted by this mission.

---

## 8. Where the intelligence layer goes

A new Business OS subsystem, `services/business_os/ads_intelligence/`, following
the established `ensure_schema()` convention and registered in
`services/business_os/schema_bootstrap.py::_ENSURES`.

It owns: the canonical event fabric, delivery decisions and no-fill reasons, the
interest graph, performance aggregates, the explainable ranker, pacing state,
windowed frequency, invalid-traffic classification, diagnostics, and
recommendations.

It owns none of: advertisers, campaigns, ad sets, creatives, audiences, wallets,
ledgers, pricing, billing, review queues, or admin approval. It reads those from
whichever platform is canonical for them and never writes them.

It must never be on the critical path of a Feed or Reels render in a way that
can fail the render: every intelligence call degrades to the current behaviour.

### Attribution is reused, not rebuilt

`services/business_os/attribution/` already implements the whole of what an ads
attribution phase would need: append-only `business_os_attr_touchpoints` and
`business_os_attr_conversions`, a rebuildable `business_os_attr_credits`
projection, `impression`/`click` touch types, a `campaign_ref`, configurable
`lookback_days`, four named models, remainder-safe integer-cent splitting, and a
`campaign_report(model)`. Both logs are idempotent on `(source, external_ref)`.

Ads intelligence therefore forwards into it rather than storing attribution:
`source='ads_intel'` and `external_ref` = the event's `dedup_key`, which makes
the forward idempotent for free and lets a replay be a no-op at both layers. A
7-day (168h) click lookback matches `CLICK_ATTRIBUTION_WINDOW_HOURS`.

There is deliberately no `ads_intel_attribution*` table. Building one would have
been a second attribution store for the same facts — the exact duplication the
architecture rule exists to prevent.

`business_os_rec_*` is *not* reused for campaign advice: that subsystem is a
user→item recommender driven by implicit feedback, whereas advertiser
diagnostics are findings about a campaign that must carry a human-readable
reason. Same word, different shape — hence `ads_intel_diagnostics`.

### Two production bugs found while tracing this path

Both were the same root cause: the canonical `users` table is keyed by
`user_id` (`bot.init_db()`), not `id`.

1. `advertising/delivery.py` `_advertiser_identity()` selected
   `FROM users WHERE id = ?`. On PostgreSQL that raises `UndefinedColumn`, and
   the surrounding `try/except` swallowed it — so every served ad rendered a
   blank "Sponsored by" line rather than failing loudly.
2. `advertising/creatives.py` `_INTERNAL_DEST` mapped `"profile"` to
   `("users", "id")`. `_verify_internal_destination` fail-safes to rejection
   when the probe raises, so a profile-destination creative could never be
   created, and an existing one could never pass delivery eligibility.

Both were invisible to the test suite because the advertising tests seeded their
own `users` table keyed on `id` — a shape production does not have. The tests
now seed `user_id`, so they exercise the real schema.
