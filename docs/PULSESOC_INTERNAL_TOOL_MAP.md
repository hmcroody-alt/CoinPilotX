# PulseSoc Internal Tool Map

Deliverable for ADDENDUM §63 ("return a build-buy map before large implementation").
Nothing in this document has been built. It is the inventory and the recommendation.

Verified 2026-09-22 against the working tree and against production Postgres
(read-only, `DATABASE_PUBLIC_URL`). Every row is labelled with how it was checked.

---

> ## ⚠️ CORRECTION — 2026-09-22, after `git fetch origin`
>
> **The first draft of this document was written against a checkout that was 278
> commits behind `origin/main`.** Several "ABSENT" and "BUILD" rows below were
> wrong: the capability already exists upstream and is already live in production.
>
> Re-verified against `origin/main` and production:
>
> | Was called | Actually | Evidence |
> |---|---|---|
> | Seller metrics — "BUILD / EXTEND" | **EXISTS, LIVE** | `services/business_os/marketplace/seller_metrics.py`, 504 lines. Owns `is_live_listing` and `confirmed_order_state`. Route `api_pulse_marketplace_seller_metrics` at `bot.py:57828`. Prod `/api/pulse/marketplace/seller/metrics` → **401** (live, auth-gated). |
> | Commerce Discovery — "EXTEND `recommendations/engine.py` (475)" | **EXISTS, LIVE, far bigger** | `services/commerce_discovery/` — **4,445 lines across 14 modules**: `ranking` 624, `engine` 604, `config` 475, `events` 423, `exposure` 398, `pool` 394, `preferences` 290, `schema` 273, `metrics` 269, `eligibility` 230, `router` 158, `subject` 128, `promotion` 127. Prod `/api/pulse/commerce/discovery/feed` → **405** (POST-only ⇒ registered). |
> | Feed / Reels / Messenger commerce — "not wired" | **EXISTS** | `mobile-native/src/commerce/`: `CommerceFeedCard`, `ReelsCommerceChip`, `MessengerCommerceStrip`, `MarketplaceDiscoveryShelves`, plus tests for `attribution`, `consent`, `reelSlots`, `reelContext`, and `privateConversationsAreCommerceFree`. |
> | Exposure logging / user controls — "absent" | **EXISTS** | `commerce_discovery/exposure.py` (398) and `preferences.py` (290). |
>
> `seller_metrics.py` also documents, in its own header, the exact discrepancies a
> later mission brief asked to be investigated: Business OS showed **43 live
> listings** (28 were drafts; true figure **13**) and **32 orders** (13
> `checkout_created`, 9 `checkout_expired`, 7 `checkout_failed`, 2 bare `created`,
> 1 Stripe `failed`; true figure **0**). Both were fixed upstream.
>
> **Standing rule this cost us:** `git fetch origin` before any capability survey.
> `main` takes ~60 commits/day from parallel sessions, so a local tree is stale
> within hours and a filename survey against it reports "absent" for live systems.

---

## 0. Read this first: the inventory cannot be done by filename

`services/` holds 350 Python modules. **85 of them are under 60 lines**, and many of
those carry the most important-sounding names in the repo. They are pure functions that
take a dict and return a dict. They touch no database and no real data.

| Module | Lines | What it actually is |
|---|---|---|
| `services/wallet_service.py` | 5 | one-line delegation to `wallet_intel` |
| `services/scam_shield_service.py` | 5 | — |
| `services/alert_service.py` | 9 | — |
| `services/decentralized_trust_engine.py` | 10 | one function: `source_trust >= 30` |
| `services/stripe_service.py` | 11 | reports which env vars are set. No Stripe calls. |
| `services/marketplace_engine.py` | 12 | returns `{"status": "foundation_only"}` |
| `services/analytics_service.py` | 22 | one `INSERT`; **zero call sites** |
| `services/analytics_intelligence_engine.py` | 25 | hardcoded English coaching tips |
| `services/event_stream_analytics.py` | 23 | arithmetic on a dict you pass in |
| `services/pulse_search_engine.py` | 29 | in-memory keyword scorer over a list you pass in |

This matters for a build-vs-buy decision in both directions:

- **A name-based inventory reports EXISTS for things that are placeholders.** If we
  read `pulse_search_engine.py` as "search exists," we would skip the single highest-value
  build in the program.
- **It also reports ABSENT for things that do exist under another name.** Search, feature
  flags, and seller lifecycle all have real or stub modules that a keyword sweep missed
  or misread.

I ran two inventory agents over this repo and both produced errors of each kind. Their
findings are used below only where I re-verified them by hand. Treat any future
"capability X exists" claim about this repo as unproven until someone has read the file
and checked that it touches data.

### Corrections to the agent inventories

| Claim | Verdict | Evidence |
|---|---|---|
| "Search is absent" | **Misleading** | `services/pulse_search_engine.py` exists, is imported at `bot.py:389`. It is 29 lines and ranks a list passed in by the caller. No index, no store, no query planner. Absent *as a capability*; present *as a name*. |
| "Feature flags / experimentation absent" | **Misleading** | `services/feature_flag_engine.py` is 313 lines, but it is a **static hardcoded readiness catalog** (`FEATURE_DEFINITIONS`, states like `beta`/`owner-only`). It is not a runtime flag service and has no per-user targeting, no assignment, no exposure logging. Absent as a capability. |
| "Analytics pipeline absent" | **WRONG** | `analytics_events` holds **31,830 rows in production**. `conversion_funnel_events` holds 28,855. Ingest is `POST /api/track` at `bot.py:14733`. A pipeline exists and has years of data in it. |
| "Seller CRM / health absent" | **Partly wrong** | `services/seller_lifecycle.py` is 1,191 real lines — merchant application state machine, completeness + risk scoring, admin review queue, document handling, audit. That is onboarding, not CRM. The CRM/segmentation half is genuinely absent. |
| "Supplier health absent" | **Partly wrong** | `services/business_os/suppliers/` is **11,341 lines across 23 modules**, including `diagnostics.py`, `audit.py`, `quota.py`, `policy.py`, `vault.py`. A supplier adapter framework exists and is serious. |
| "Stripe is centralized in `payment_provider.py`" | **WRONG** | Four files import `stripe` directly: `payment_provider.py`, `stripe_webhook_verification.py`, `marketplace_reservation_reconciler.py`, and `bot.py`. Fan-out is small but the wrapper is not a chokepoint. |
| `services/business_os/events/` is the event pipeline | **WRONG — name collision** | It is a **ticketing** domain: events, ticket types, check-in. `business_os_event_tickets`, `business_os_events`. Nothing to do with analytics. |

---

## 1. External dependency audit

Per-dependency, in the §63 format. Fan-out counts are files under `services/` + `bot.py`
that reference the vendor.

### KEEP_EXTERNAL — locked by §61 or by the boundary rule

| Name | Purpose | Data sent out | Fan-out | Lock-in | Verdict |
|---|---|---|---|---|---|
| **Agora** | realtime RTC for calls + live ingest | audio/video streams, channel + uid | 15 files | High — native SDK in the app | **KEEP.** §61 hard lock. Global realtime media network; the boundary rule puts this outside. |
| **Mux** | live distribution, playback, VOD | video streams | 20 files | Medium — `services/mux_live_service.py` is a real wrapper | **KEEP.** §61 hard lock. Video CDN is explicitly named as not-ours-to-build. |
| **Stripe** | payments, Connect payouts | card data (never ours — that is the point), customer + payout metadata | 4 files | High by regulation, low by code | **KEEP.** §61 hard lock. Regulated rails. Card networks and money transmission are the canonical keep-external case. |
| **APNs / FCM** | push delivery | device tokens, notification payloads | 8 + 10 files | High — carrier-equivalent | **KEEP.** §61 hard lock. See §4 for the orchestration layer above it, which *is* ours. |
| **Cloudflare R2** | object storage | user media | 3 files (`boto3`) | **Low** — well contained in `media_storage.py` | **KEEP.** §61 names current storage. Also genuinely physical infra. Note the containment is good: 3 files. |

### BUILD_INTERNAL or HYBRID — worth moving

| Name | Purpose | Cost model | Data sent out | Fan-out | Verdict |
|---|---|---|---|---|---|
| **Brevo** | transactional email + SMS | per-send | recipient email/phone, message body | 16 files, but wrapped in `email_service.py` / `sms_service.py` | **HYBRID.** Keep Brevo as the SMTP/SMS *rail*. The control layer above it (templating, suppression, send policy, audit) should be ours — see PulseNotify. Deliverability reputation is a regulated-adjacent asset; do not build an MTA. |
| **CoinGecko** | crypto price data | none (read-only) | **21 files** | **HYBRID, low priority.** Highest unwrapped fan-out after Telegram. Worth a single adapter for cache + rate-limit control, not worth replacing — it is specialized market data. Legacy subsystem; low strategic value. |
| **Telegram** | bot channel | messages | **35 files — highest fan-out in the repo, no wrapper** | **KEEP_EXTERNAL, but wrap.** It is a third-party network; we cannot build it. But 35 unwrapped call sites is the single worst coupling measured here. One adapter. |
| **OpenAI / Anthropic / Gemini / DeepSeek / Groq** | model inference | prompts, and whatever context we attach | 25 files, routed by `services/undx_router.py` | **HYBRID — already correct.** The router pattern is right: multi-provider, server-side keys, swappable. Add cost observability and provider contracts on top (§ PulseAI). Do not train foundation models. |

### DEFER / DEAD — remove rather than decide

| Name | Status |
|---|---|
| **SendGrid** | `SENDGRID_API_KEY` is declared at `.env.example:118`. **Zero imports anywhere in the repo.** Dead key held alive by the env-contract gate. |
| **Mailgun** | `MAILGUN_API_KEY` at `.env.example:119`. **Zero imports.** Same. |
| **Google Cloud Translation** | Named as an integration in `CLAUDE.md`. **Zero imports**, and not in `.env.example`. Stale doc claim. |

Recommended action: delete the two dead keys and correct `CLAUDE.md`. This is a
five-minute change that removes two fake vendor relationships from the security surface.

---

## 2. Capability inventory

### EXISTS — real, DB-backed, do not rebuild

Verified by line count plus a count of actual cursor/DB calls in the module.

| Capability | Module | Lines | DB calls |
|---|---|---|---|
| Recommendations engine | `services/business_os/recommendations/engine.py` | 475 | 7 |
| Attribution | `services/business_os/attribution/engine.py` | 421 | 8 |
| Payment reconciliation | `services/business_os/payments/reconciliation.py` | **1,141** | 11 |
| Notification orchestration | `services/notification_service.py` | **2,353** | 76 |
| Inventory | `services/business_os/marketplace/inventory.py` | 311 | 4 |
| Supplier adapter framework | `services/business_os/suppliers/` (23 modules) | **11,341** | many |
| Seller onboarding lifecycle | `services/seller_lifecycle.py` | 1,191 | many |
| Seller analytics | `services/business_os/insights/seller_analytics.py` | 604 | — |
| Analytics ingest | `bot.py:14733` `POST /api/track` | — | live |

**Caveat that applies to every `business_os` row above:** the flags are on in production
and the tables are empty. All `business_os_mkt_*` and `business_os_rec_*` audit/event
tables read **0 rows**. Only `business_os_store_audit` (69) and `business_os_ent_audit`
(54) have any data at all. So these modules are built, deployed, reachable — and serving
nothing. "It exists" and "it works in production" are different claims here, and the
second one is unproven for the whole canonical commerce domain.

### STUB — a name, not a capability

`pulse_search_engine` (29), `analytics_service` (22, zero call sites),
`analytics_intelligence_engine` (25), `event_stream_analytics` (23),
`decentralized_trust_engine` (10), `marketplace_engine` (12), `stripe_service` (11),
`wallet_service` (5), `scam_shield_service` (5), `alert_service` (9), `audit_service` (32),
and ~70 more under 60 lines.

These are not technical debt in the usual sense — they are **inventory hazard**. They make
the repo read as more complete than it is, to us and to any agent surveying it.

### ABSENT — verified by both filename sweep and content grep

Runtime feature flags / experimentation (assignment, exposure, readout) · unified
cross-entity search · seller CRM + segmentation · seller health scoring · product quality
scoring · review fraud detection · duplicate-listing detection · supplier health scoring ·
margin intelligence · demand + trend intelligence · creator↔product matching · price watch ·
restock alerts · collections · config service · load testing harness · schema health tooling.

---

## 3. The finding that should drive the program

Production has **111 event and audit tables. 49 of them are non-empty. 62 are empty.**

Non-empty, top of the distribution:

```
64,839  alert_events                 13,581  command_center_message_events
31,830  analytics_events             10,384  communication_call_events
28,855  conversion_funnel_events      8,428  pulse_music_events
16,850  pulse_live_events             4,972  notification_events
                                      4,047  security_events
```

Every subsystem that ever needed to record something created its own table. There is no
shared event envelope, no shared schema, no shared consumer. Answering "what did this
seller's buyers do before they churned" means joining across a dozen tables that do not
agree on what a user id, a timestamp, or a session is.

This is precisely the sprawl §59 warns against, and it already happened. **The highest-value
internal tool is therefore not a new capability — it is a unification.** PulseAnalytics
should be a read/ingest layer over the events we already have, not a greenfield pipeline.
We have 31,830 rows of real behaviour sitting in `analytics_events` that nothing currently
reads.

---

## 4. The map

Priority reflects the mission's ordering: security, data integrity, payment/order
correctness first; growth and polish last. Complexity is engineer-weeks, rough.

### Tier 1 — do these first

| Tool | Purpose | Foundation today | External dep | Build? | Complexity | Strategic value |
|---|---|---|---|---|---|---|
| **PulseCommerce (cutover)** | make the canonical domain actually serve traffic | `business_os/` is fully built; every table is empty | none | **Already built — needs backfill + cutover, not code** | M | Highest. Everything commerce-shaped is blocked behind this. |
| **PulseAnalytics** | one event envelope over the 111 existing tables | 31,830 real rows in `analytics_events`; ingest route live | none | **BUILD** | L | Unblocks seller metrics, recs, trust, experimentation. Every Tier 2 tool reads from it. |
| **PulseExperiments** | runtime flags + assignment + exposure | `feature_flag_engine.py` is a static catalog, not this | none | **BUILD** | M | Required before any ranking or pricing change can be measured. Also the safe-rollout mechanism for the cutover above. |
| **Order/payment integrity** | reconciliation actually running | `payments/reconciliation.py` is 1,141 lines and real | Stripe (keep) | **Wire up, don't rebuild** | S | Mission priority 3. |
| **PulseTrust** | seller/buyer risk, review fraud, dupe detection | `user_trust_engine.py` is 94 lines; `decentralized_trust_engine.py` is 10 | none | **BUILD** | L | Mission priority 1–2. Currently near-zero. |

### Tier 2 — after PulseAnalytics exists

| Tool | Purpose | Foundation | Build? | Complexity |
|---|---|---|---|---|
| **PulseSearch** | real index over products, sellers, posts | 29-line stub | **BUILD** — Postgres FTS first, not a new datastore | L |
| **PulseMerchant** | seller metrics, health, CRM, segmentation | `seller_analytics.py` (604) + `seller_lifecycle.py` (1,191) | **EXTEND** | M |
| **PulseDiscovery** | recs + product-content + creator matching | `recommendations/engine.py` (475) | **EXTEND** | M |
| **PulseNotify** | control layer above APNs/FCM/Brevo | `notification_service.py` (2,353) | **EXTEND**, keep rails external | S |
| **PulseSuppliers** | supplier health + margin on the existing framework | 11,341 lines already | **EXTEND** | S |

### Tier 3 — defer

Price watch, restock alerts, collections, comparison, cart recovery, shipping estimate,
load testing, simulator, profiler. All real wants; none of them are blocked on anything,
and all of them are cheaper after Tier 1 lands.

### Explicitly not built (§61 + boundary rule)

Card network, bank, money transmission, video CDN, realtime media network, carrier/push
transport, object storage infrastructure, foundation model training.

---

## 5. Security posture for anything new (§57)

Every tool above inherits one non-negotiable list: authn, authz, rate limiting, input
validation, audit trail, secret redaction, tenant/seller/user isolation, tests. Two
repo-specific gates will block a new route regardless:

- `test_new_routes_must_declare_their_auth` is default-deny. A new route fails the
  protection suite until it declares its auth.
- `test_environment_contract.py` fails the whole suite on any undeclared `os.getenv`.

And one repo-specific hazard: schema changes are hand-rolled in `bot.init_db()` with no
migration framework, so every new table must be idempotent and must not take a lock that
blocks boot.

---

## 6. Recommendation

Do not start any Tier 2 or Tier 3 tool yet.

The program's stated goal is to own the social-commerce intelligence stack. The blocker is
not missing capability — it is that the capability we already built is serving zero rows,
and the behavioural data we already collect is spread across 111 tables that nothing
reads. Building PulseSearch or PulseDiscovery on top of that would add a twelfth thing
that reads from nowhere.

Tier 1, in order: land the commerce cutover, unify the event layer, add runtime flags so
the first two can be rolled out safely.
