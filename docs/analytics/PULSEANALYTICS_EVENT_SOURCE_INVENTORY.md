# PulseAnalytics — Stage 6 Event Source Inventory

**Date:** 2026-09-22
**Base:** `dd9d240e` (fresh `origin/main`)
**Method:** production Postgres row counts and column lists, joined against a call-site
scan of 2,332 Python files (`bot.py`, `services/`, `scripts/`, `tests/`, `models/`,
`migrations/`).
**Nothing in this stage deletes, migrates or alters any table.** This is a read-only census.

---

## 0. The headline

Production carries **111 event/audit tables holding 194,091 rows**. Of those:

- **3** are canonical (`commerce_discovery_*`) and hold **27 rows**.
- **24** are active-but-legacy and hold **174,259 rows** — 90% of everything.
- **62** hold zero rows (32 wired-but-never-fired, 20 declared-but-never-wired, 9
  duplicates, 1 canonical table whose surface has not emitted yet).
- **14** duplicate another table's job.
- **1** holds 3,833 rows that no code in the repository reads or writes.

Only **49 of the 111 are populated at all**, and across those there is **no shared
envelope**:

| dimension | conventions in use |
|---|---|
| metadata blob | `payload_json` (13), `metadata_json` (11), `metadata` (3), `details_json` (2), `request_meta_json` (2), `details`, `raw_json`, `payload_summary` — **8 names** |
| event name | `event_type` (22), `action` (17), `event_key` (2), `event_name` (1), `funnel_name`+`step_name` (1) — **5 conventions** |
| actor | `user_id` (23/49), `actor_user_id` (12/49) — and **26 of 49 name no user at all** |
| session | `session_id` — **5 of 49** |
| time | 3 populated sources have **no time column whatsoever** (`arena_events`, `payment_webhook_events`, `provider_webhook_events`) |

**Zero** populated sources carry `order_id`, `product_id`, `store_id` or `seller_id`.
`listing_id` and `seller_user_id` appear twice each, and both times inside
`commerce_discovery_*`.

That last row is the whole problem in one line: **outside the canonical family, production
has no event that can be joined to a seller or a product.**

---

## 1. The finding that most constrains PulseAnalytics

**The mission brief names `analytics_events` (31,830 rows) and `conversion_funnel_events`
(28,855 rows) as the foundation to build the commerce read layer on. Neither contains any
commerce signal.**

`analytics_events` breaks down as 54% `signup_started` (17,297 rows) originating from
**42 distinct users across 296 sessions** — legacy crypto-product website funnel traffic,
a large share of it plausibly crawler noise. It carries `page_url`, `ip_hash` and
`country`. It carries no listing, no seller, no order, no price. It is read, by
`retention_analytics.retention_summary()` (admin route `bot.py:17529`) and by
`analytics_summary()` (`bot.py:29928`), and both are admin dashboards about site traffic.

**So `analytics_events` cannot back the Stage 9 commerce aggregations.** Asking it for
seller revenue or funnel conversion would not return a wrong number; it would return an
empty one, which is worse, because an empty result reads as "this seller made no sales"
rather than "this table was never about sales."

The commerce event stream is `commerce_discovery_*`. It is live and correct and nearly
empty — 35 placements, 24 impressions, 3 engagements, 0 feedback — because the surfaces
that emit it only just shipped.

---

## 2. Verified duplication: one Stripe webhook, three tables

This is the clearest fragmentation evidence in the census, and it is confirmed against
production rather than inferred from names:

```
payment_webhook_events    47 rows
provider_webhook_events   47 rows
stripe_events             50 rows
join on provider_event_id:  overlap 47, payment-only 0, provider-only 0
stripe_events ∩ provider_webhook_events on event_id: 47
```

Every single payment webhook is written three times, into three schemas that disagree:
`payment_webhook_events` has `raw_json` and no received time; `provider_webhook_events`
has `payload_json`, `signature_verified`, `retry_count` and `received_at`; `stripe_events`
has `payload_summary` *and* `payload_json` *and* both `event_id` and `stripe_event_id`.

A reader that picks one of these tables gets a defensible answer. A reader that unions
them triples the payment count. Neither failure announces itself.

---

## 3. Classification

Definitions used:

- **ACTIVE_CANONICAL** — written and read, with a designed envelope, and intended to survive.
- **ACTIVE_LEGACY** — written and read today, no shared envelope, no migration planned.
- **MIGRATION_CANDIDATE** — written, but **nothing reads it**. Data accumulating into a void.
- **DUPLICATE** — another table does the same job; this one is the loser.
- **SECURITY_ONLY** / **FINANCIAL_ONLY** — in scope for audit, out of scope for product analytics.
- **ORPHANED_DATA** — rows exist; no writer and no reader exist in the repository.
- **EMPTY** — a writer exists in code; zero rows have ever been written.
- **DEAD** — declared in DDL; no writer was ever wired.

### ACTIVE_CANONICAL — 3 source(s), 27 rows

| rows | table | time column | writers | readers | note |
|---:|---|---|---|---|---|
| 24 | `commerce_discovery_impression_events` | `created_at` | 0 | 4 | writer is dynamic — events.py:209 passes the table name positionally to _insert_idempotent, so a grep for "INSERT INTO <name>" finds nothing |
| 3 | `commerce_discovery_engagement_events` | `created_at` | 0 | 1 | same dynamic writer (events.py:293) |
| 0 | `commerce_discovery_feedback_events` | `created_at` | 0 | 0 | writer exists (events.py:361); 0 rows because no surface has emitted feedback yet |

### ACTIVE_LEGACY — 24 source(s), 174,259 rows

| rows | table | time column | writers | readers | note |
|---:|---|---|---|---|---|
| 64,839 | `alert_events` | `created_at` | 1 | 5 |  |
| 31,831 | `analytics_events` | `created_at` | 2 | 2 |  |
| 28,856 | `conversion_funnel_events` | `created_at` | 1 | 1 |  |
| 16,850 | `pulse_live_events` | `created_at` | 1 | 1 |  |
| 13,581 | `command_center_message_events` | `created_at` | 1 | 1 |  |
| 10,384 | `communication_call_events` | `created_at` | 1 | 1 |  |
| 4,972 | `notification_events` | `created_at` | 1 | 1 |  |
| 784 | `command_center_notification_events` | `created_at` | 1 | 1 |  |
| 420 | `pulse_live_audit_logs` | `created_at` | 1 | 1 |  |
| 410 | `pulse_ai_learning_events` | `created_at` | 1 | 1 |  |
| 368 | `pulse_ai_safety_events` | `created_at` | 1 | 1 |  |
| 328 | `pulse_ai_provider_events` | `created_at` | 1 | 2 |  |
| 119 | `trial_email_events` | `created_at` | 1 | 1 |  |
| 111 | `referral_events` | `created_at` | 1 | 1 |  |
| 75 | `pulse_ad_audit_logs` | `created_at` | 1 | 2 |  |
| 69 | `business_os_store_audit` | `created_at` | 1 | 1 |  |
| 53 | `telegram_debug_events` | `created_at` | 1 | 1 |  |
| 51 | `profile_audit_logs` | `created_at` | 1 | 2 |  |
| 47 | `provider_webhook_events` | `—` | 1 | 3 |  |
| 41 | `intelligence_events` | `created_at` | 1 | 1 |  |
| 40 | `arena_match_events` | `created_at` | 3 | 2 |  |
| 20 | `user_welcome_events` | `created_at` | 1 | 1 |  |
| 7 | `pulse_page_audit` | `created_at` | 1 | 1 |  |
| 3 | `music_takedown_audit` | `created_at` | 1 | 1 |  |

### MIGRATION_CANDIDATE — 11 source(s), 8,800 rows

| rows | table | time column | writers | readers | note |
|---:|---|---|---|---|---|
| 8,428 | `pulse_music_events` | `created_at` | 1 | 0 |  |
| 236 | `crypto_audit_logs` | `created_at` | 1 | 0 |  |
| 54 | `business_os_ent_audit` | `created_at` | 2 | 0 |  |
| 53 | `event_bus_events` | `created_at` | 1 | 0 |  |
| 19 | `pulse_translation_events` | `created_at` | 1 | 0 |  |
| 3 | `verification_audit_logs` | `created_at` | 1 | 0 |  |
| 2 | `business_os_seller_profile_audit` | `created_at` | 1 | 0 |  |
| 2 | `pulse_content_promotion_audit` | `created_at` | 1 | 0 |  |
| 1 | `arena_share_events` | `created_at` | 1 | 0 |  |
| 1 | `command_center_ai_events` | `created_at` | 1 | 0 |  |
| 1 | `pulse_premium_audit_logs` | `created_at` | 1 | 0 |  |

### DUPLICATE — 14 source(s), 218 rows

| rows | table | time column | writers | readers | note |
|---:|---|---|---|---|---|
| 75 | `command_center_security_events` | `created_at` | 1 | 1 | superseded by security_events |
| 75 | `user_security_events` | `created_at` | 1 | 1 | superseded by security_events |
| 47 | `payment_webhook_events` | `—` | 1 | 1 | superseded by provider_webhook_events |
| 15 | `live_events` | `created_at` | 1 | 1 | superseded by pulse_live_events |
| 6 | `arena_events` | `—` | 1 | 1 | superseded by arena_match_events |
| 0 | `audit_logs` | `created_at` | 0 | 0 | superseded by nothing — generic name, never wired |
| 0 | `dashboard_events` | `created_at` | 0 | 0 | superseded by analytics_events |
| 0 | `engagement_events` | `created_at` | 1 | 0 | superseded by commerce_discovery_engagement_events |
| 0 | `global_events` | `created_at` | 0 | 1 | superseded by event_bus_events |
| 0 | `marketplace_return_events` | `created_at` | 1 | 1 | superseded by business_os_mkt_return_events |
| 0 | `pulse_ad_billing_events` | `created_at` | 0 | 0 | superseded by business_os_ad_billing_events |
| 0 | `pulse_ad_events` | `created_at` | 1 | 3 | superseded by business_os_ad_impression_events |
| 0 | `security_login_events` | `created_at` | 0 | 1 | superseded by security_events |
| 0 | `seller_payout_events` | `created_at` | 1 | 1 | superseded by marketplace_payout_state_events |

### SECURITY_ONLY — 5 source(s), 6,904 rows

| rows | table | time column | writers | readers | note |
|---:|---|---|---|---|---|
| 4,047 | `security_events` | `created_at` | 2 | 3 |  |
| 1,349 | `auth_events` | `created_at` | 1 | 2 |  |
| 827 | `admin_audit_logs` | `created_at` | 2 | 3 |  |
| 620 | `private_audit_events` | `created_at` | 0 | 0 | writer via _schema.AUDIT_TABLE constant (private_office/audit.py:473), reader at :593 |
| 61 | `account_audit_logs` | `created_at` | 1 | 2 |  |

### FINANCIAL_ONLY — 1 source(s), 50 rows

| rows | table | time column | writers | readers | note |
|---:|---|---|---|---|---|
| 50 | `stripe_events` | `created_at` | 1 | 1 |  |

### ORPHANED_DATA — 1 source(s), 3,833 rows

| rows | table | time column | writers | readers | note |
|---:|---|---|---|---|---|
| 3,833 | `pulse_live_provider_events` | `created_at` | 0 | 0 | 3,833 rows; the ONLY reference in the entire repo is CREATE TABLE at bot.py:120563 — no writer, no reader |

### EMPTY — 32 source(s), 0 rows

| rows | table | time column | writers | readers | note |
|---:|---|---|---|---|---|
| 0 | `account_health_events` | `created_at` | 1 | 1 |  |
| 0 | `account_system_events` | `created_at` | 1 | 1 |  |
| 0 | `ads_intel_events` | `created_at` | 1 | 7 |  |
| 0 | `ai_action_audit_logs` | `created_at` | 1 | 1 |  |
| 0 | `arena_victory_events` | `created_at` | 1 | 0 |  |
| 0 | `business_os_ad_audit` | `created_at` | 2 | 1 |  |
| 0 | `business_os_ad_billing_events` | `created_at` | 1 | 5 |  |
| 0 | `business_os_ad_click_events` | `created_at` | 1 | 1 |  |
| 0 | `business_os_ad_impression_events` | `created_at` | 1 | 4 |  |
| 0 | `business_os_business_audit` | `created_at` | 1 | 1 |  |
| 0 | `business_os_crypto_alert_events` | `created_at` | 1 | 0 |  |
| 0 | `business_os_event_audit` | `created_at` | 1 | 0 |  |
| 0 | `business_os_event_ticket_types` | `created_at` | 1 | 2 |  |
| 0 | `business_os_event_tickets` | `created_at` | 1 | 2 |  |
| 0 | `business_os_events` | `created_at` | 1 | 4 |  |
| 0 | `business_os_mkt_audit` | `created_at` | 3 | 2 |  |
| 0 | `business_os_mkt_offer_events` | `created_at` | 1 | 1 |  |
| 0 | `business_os_mkt_order_events` | `created_at` | 1 | 1 |  |
| 0 | `business_os_mkt_return_events` | `created_at` | 1 | 1 |  |
| 0 | `capability_audit_results` | `created_at` | 1 | 0 |  |
| 0 | `marketplace_ip_case_events` | `created_at` | 1 | 0 |  |
| 0 | `marketplace_order_fulfillment_events` | `created_at` | 1 | 1 |  |
| 0 | `marketplace_payout_state_events` | `created_at` | 1 | 1 |  |
| 0 | `payment_audit_logs` | `created_at` | 1 | 1 |  |
| 0 | `progress_events` | `created_at` | 1 | 2 |  |
| 0 | `pulse_ad_wallet_events` | `created_at` | 1 | 1 |  |
| 0 | `pulse_chat_recovery_events` | `created_at` | 1 | 1 |  |
| 0 | `pulse_region_preference_events` | `created_at` | 1 | 0 |  |
| 0 | `reward_events` | `created_at` | 1 | 2 |  |
| 0 | `sentinel_events` | `occurred_at` | 1 | 13 |  |
| 0 | `sentinel_external_data_audit` | `—` | 1 | 1 |  |
| 0 | `usage_events` | `created_at` | 1 | 0 |  |

### DEAD — 20 source(s), 0 rows

| rows | table | time column | writers | readers | note |
|---:|---|---|---|---|---|
| 0 | `ai_observability_events` | `created_at` | 0 | 1 |  |
| 0 | `arena_world_events` | `created_at` | 0 | 1 |  |
| 0 | `backend_management_audit_events` | `created_at` | 0 | 1 |  |
| 0 | `business_os_attr_audit` | `created_at` | 0 | 0 |  |
| 0 | `business_os_creator_audit` | `created_at` | 0 | 0 |  |
| 0 | `business_os_crypto_audit` | `created_at` | 0 | 0 |  |
| 0 | `business_os_l10n_audit` | `created_at` | 0 | 0 |  |
| 0 | `business_os_merchant_audit` | `created_at` | 0 | 0 |  |
| 0 | `business_os_perf_audit` | `created_at` | 0 | 0 |  |
| 0 | `business_os_rec_audit` | `created_at` | 0 | 0 |  |
| 0 | `business_os_undx_audit` | `created_at` | 0 | 0 |  |
| 0 | `comm_v2_moderation_events` | `created_at` | 0 | 0 |  |
| 0 | `creator_revenue_events` | `created_at` | 0 | 0 |  |
| 0 | `dashboard_audit_logs` | `created_at` | 0 | 0 |  |
| 0 | `monetization_events` | `created_at` | 0 | 1 |  |
| 0 | `private_domain_events` | `created_at` | 0 | 0 |  |
| 0 | `pulse_ai_verification_events` | `created_at` | 0 | 0 |  |
| 0 | `pulse_payment_events` | `created_at` | 0 | 0 |  |
| 0 | `pulse_reel_retention_events` | `created_at` | 0 | 0 |  |
| 0 | `user_trust_events` | `created_at` | 0 | 0 |  |

---

## 4. What this means for Stages 7–9

**Stage 7 (canonical envelope) is already built, and not by me.**
`services/commerce_discovery/schema.py` defines exactly the envelope the mission
specifies — `event_id`, `subject_ref`, `surface`, `slot`, `listing_id`, `seller_user_id`,
`session_id`, `reason_code`, `ranking_version`, `event_at`, `request_meta_json` — plus a
`dedup_key UNIQUE` idempotency guarantee the mission did not ask for.
`services/commerce_discovery/events.py` defines the funnel vocabulary Stage 9 specifies
(`click → product_view → save → add_to_cart → checkout_started → purchase`), and it
deliberately refuses to infer missing steps, because "a purchase without a recorded click
is a real thing … and inventing the click would make attribution a fiction."

Defining a second envelope would create the sprawl §59 forbids, and would compete with a
design that is already more careful than the brief.

**PulseAnalytics should therefore adopt this envelope, not replace it.** The genuine gaps
the census exposes are narrower than the brief assumed, and there are two:

1. **`metrics.py` has no route.** `services/commerce_discovery/metrics.py` computes
   repetition and sequence statistics over the canonical events, and
   `grep -n "metrics" services/commerce_discovery_routes.py` returns nothing. The
   aggregation layer exists and is unreachable. This is a read-layer gap, which is exactly
   the gap the mission diagnosed — just one directory to the left of where it pointed.

2. **The 24 ACTIVE_LEGACY sources have no shared read layer.** But most of them have no
   analytic consumer either, and building adapters for all 24 would be sprawl. The honest
   scope is the subset with a real question attached to it.

**Stage 9's instruction to "integrate rather than replace" seller metrics applies with
more force than written.** `services/business_os/marketplace/seller_metrics.py` is the
authority on `is_live_listing` and `confirmed_order_state`, and it exists precisely
because earlier counts were wrong in this codebase: Business OS reported 43 live listings
when 28 were unpublishable drafts (true figure 13), and 32 orders when not one was paid
(true figure 0). Any aggregation PulseAnalytics performs over listings or orders must call
that module rather than re-deriving the predicate.

---

## 5. Explicitly not recommended

- **Do not build a new ingest path.** 194,091 rows already exist; the missing half is reads.
- **Do not union the webhook tables.** See §2 — it triples the payment count.
- **Do not delete anything on the strength of this document.** The EMPTY and DEAD
  classifications are evidence for a later decision, not a licence. 62 empty tables is the
  normal state of this repo, so "this table is empty" is weak evidence that any feature is
  broken.
- **Do not treat `pulse_live_provider_events` as safe to drop without an owner.** It is
  orphaned in code, but it holds 3,833 real rows and something wrote them.

---

## 6. Method notes and one caveat

The call-site scan classifies a mention by the SQL keyword preceding it. **It undercounts
writers that pass the table name as a variable.** Three `commerce_discovery_*` tables were
initially misfiled as DEAD for exactly this reason — `events.py` calls
`_insert_idempotent(cur, "commerce_discovery_impression_events", …)` positionally, so no
`INSERT INTO commerce_discovery_impression_events` string exists anywhere. The same held
for `private_audit_events`, written through `_schema.AUDIT_TABLE`.

Every table in this document that shows rows but zero writers was checked by hand. Only
one survived that check as genuinely unreferenced: `pulse_live_provider_events`, whose
sole appearance in the entire repository is its own `CREATE TABLE` at `bot.py:120563`.

Raw data: `/tmp/event_inventory.json` (census), `/tmp/event_final.json` (classified).
