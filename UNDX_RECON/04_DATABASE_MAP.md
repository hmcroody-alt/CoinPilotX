# UNDX RECON — Stage 4: DATABASE KNOWLEDGE MAP

_Generated 2026-08-26 from the live SQLite database and the source tree. Read-only recon; no application file was modified._

---

## 1. Methodology and totals

### 1.1 Sources of truth (in priority order)

| # | Source | Path | What it gave us |
|---|---|---|---|
| 1 | **Live SQLite database** | `coinpilotx.db` (123,265,024 bytes, mtime 2026-08-23 23:30) | Ground truth: every table, every real column and its declared type, every index, row counts. Opened read-only via `sqlite3.connect("file:...?mode=ro", uri=True)` and introspected with `sqlite_master`, `PRAGMA table_info`, `PRAGMA foreign_key_list`, `PRAGMA index_list`, `PRAGMA index_info`. |
| 2 | **`bot.py`** | repo root, ~111k lines | The imperative schema. 514 distinct `CREATE TABLE` names, 363 `CREATE INDEX`, plus 117 call sites of `add_column_if_missing` / `add_columns_if_missing` that evolve tables after creation. |
| 3 | **`services/*.py`, `migrations/*.sql`, `models/`** | | A further 335 distinct table names. `services/db.py` holds `AUTO_PK_TABLES`. `models/` contains only `live_session.py` — it is not the schema. |

There is **no migration framework**. Schema is created imperatively inside `bot.init_db()` and by scattered `CREATE TABLE IF NOT EXISTS` in service modules, and is then evolved additively by `add_columns_if_missing`. Consequence for reading this map: **a `CREATE TABLE` statement in `bot.py` is a historical baseline, not the current shape of the table.** The live database is authoritative and, in every table sampled, is a strict superset of the code text.

### 1.2 Headline totals

| Metric | Value |
|---|---|
| Tables in the **live** db | **776** (775 application tables + `sqlite_sequence`) |
| Tables that contain at least one row | **384** (49.5%) — the other 391 are empty |
| Distinct table names appearing in **code** | **813** (`bot.py` 514 + services/migrations 335, deduped) |
| Indexes in the live db | **1,072** |
| …of which UNIQUE | **449** |
| Views | **0** |
| Triggers | **0** |
| **Foreign keys, across all 776 tables** | **0** |
| Tables with **no index at all** | **198** |
| Tables with **no UNIQUE constraint at all** | **385** |
| Tables with **no PRIMARY KEY at all** | **15** |
| Tables carrying an ownership column | **467** |
| …of which have **no index led by that ownership column** | **237** (127 already hold rows) |

### 1.3 Code ↔ live divergence

**In code but NOT in the live database — 57 real names** (4 of the 61 raw matches — `if`, `nor`, `on`, `or` — are regex artifacts from dynamic SQL string-building and should be ignored):

`account_strike_appeals`, `ads_intel_campaign_daily`, `ads_intel_campaign_pacing`, `ads_intel_creative_daily`, `ads_intel_delivery_decisions`, `ads_intel_diagnostics`, `ads_intel_events`, `ads_intel_frequency_windows`, `ads_intel_ingest_batches`, `ads_intel_interest_affinity`, `ads_intel_signal_policy`, `alert_rule_symbol_state`, `business_os_ad_account_guardrails`, `connect_account_state`, `marketplace_cart_checkout_keys`, `marketplace_cart_items`, `marketplace_commercial_refunds`, `marketplace_commercial_settlements`, `marketplace_digital_files`, `marketplace_inventory_reservations`, `marketplace_ip_case_events`, `marketplace_ip_cases`, `marketplace_offers`, `marketplace_orders`, `marketplace_payout_state_events`, `marketplace_reconciliation_runs`, `marketplace_return_events`, `marketplace_returns`, `marketplace_seller_compliance`, `marketplace_seller_terms_acceptances`, `progress_events`, `progress_milestone_awards`, `progress_missions`, `progress_posting_days`, `progress_referral_qualifications`, `progress_reward_cycles`, `pulse_ad_adsets`, `pulse_ad_appeals`, `pulse_ad_attributions`, `pulse_ad_daily_aggregates`, `pulse_ad_idempotency`, `pulse_ad_jobs`, `pulse_ad_saved_audiences`, `pulse_ad_wallet_events`, `pulse_credit_ledger`, `pulse_generated_media`, `pulse_media_upload_sessions`, `pulse_page_audit`, `pulse_page_follows`, `pulse_page_links`, `pulse_page_members`, `pulse_pages`, `reconciliation_runs`, `referral_deferred_claims`, `reward_events`, `seller_payout_events`, `seller_payout_requests`

Three clusters stand out, because they are entire *product surfaces* that exist in code and not in this database:

* **`marketplace_orders` and the whole commercial-marketplace family** (cart, offers, returns, refunds, settlements, reservations, IP cases, digital files, reconciliation). The live db has `marketplace_listings` and `marketplace_sellers` but **no `marketplace_orders`** — there is only a stub named `marketplace_orders_placeholder` (0 rows, 4 columns). Real order state lives in `business_os_mkt_orders`, which is also empty. **There is currently no populated order table anywhere in this database.**
* **`pulse_pages` and the entire `pulse_page_*` family** (members, follows, links, audit). "Pages" as an ownership context does not exist here; group-like ownership is carried by `pulse_groups` (410 rows) instead.
* **`pulse_ad_adsets` / `ads_intel_*`** — the ad-set tier and the whole ads-intelligence layer are code-only. The live ad-set analogue is `business_os_ad_sets` (0 rows).

**In the live database but NOT created anywhere in code — 23 real names:**

The 20 `comm_v2_*` tables (`comm_v2_attachments`, `comm_v2_blocks`, `comm_v2_channels`, `comm_v2_communities`, `comm_v2_conversation_items`, `comm_v2_conversation_settings`, `comm_v2_conversations`, `comm_v2_live_streams`, `comm_v2_message_deletions`, `comm_v2_message_reactions`, `comm_v2_messages`, `comm_v2_moderation_events`, `comm_v2_participants`, `comm_v2_pinned_messages`, `comm_v2_presence`, `comm_v2_read_receipts`, `comm_v2_reports`, `comm_v2_typing`, `comm_v2_user_settings`), plus `seller_application_assignments`, `seller_application_notes`, `seller_application_status_history`, and `business_os_confirmation_grants`.

The `comm_v2_*` set is **not** dead: `comm_v2_conversations` holds 219 rows, `comm_v2_messages` 1,411, `comm_v2_participants` 458. It is a live second-generation messaging engine whose DDL is not in the searched tree — meaning **a fresh deploy would not recreate it**, and any consumer reasoning only from `bot.py` would not know it exists.

### 1.4 A correction to the project notes

`CLAUDE.md` states `AUTO_PK_TABLES` lives in `bot.py` with ~170 tables. It is actually in **`services/db.py:143` with 354 entries**. Values are only `"id"` or `"user_id"` (`"users": "user_id"`). It is consumed at `services/db.py:658-659`, which appends `RETURNING <pk>` to INSERTs for listed tables so Postgres and SQLite return the new id the same way. **13 of its entries name tables that do not exist in this database** — `marketplace_digital_files`, `pulse_ad_appeals`, `pulse_ad_daily_aggregates`, `pulse_ad_idempotency`, `pulse_ad_jobs`, `pulse_ad_saved_audiences`, `pulse_ad_wallet_events`, `pulse_page_audit`, `pulse_page_follows`, `pulse_page_links`, `pulse_page_members`, `pulse_pages`, `referral_deferred_claims` — so an INSERT against any of them fails on missing table, not on the RETURNING clause.

### 1.5 How ownership and permission columns were detected

Section 3 tags every column. The rules are mechanical and reproducible, applied to the live column names:

* **`[OWNER]`** — exact match on: `user_id`, `owner_id`, `owner_user_id`, `seller_id`, `buyer_id`, `author_id`, `creator_id`, `page_id`, `actor_user_id`, `advertiser_id`, `account_id`, `merchant_id`, `host_id`, `sender_id`, `recipient_id`, `customer_id`, `created_by`, `created_by_user_id`, `uploader_id`, `publisher_id`, `initiator_id`, `from_user_id`, `to_user_id`, `target_user_id`, `member_user_id`, `student_id`, `teacher_id`, `profile_user_id`, `admin_user_id`, `telegram_user_id`, `requester_id`, `reporter_id`, `assignee_id`, `assigned_to`.
* **`[PERM]`** — token match on: `is_public`, `public`, `visibility`, `privacy`, `private`, `is_private`, `role`, `status`, `is_admin`, `admin`, `tier`, `entitlement`, `permission`, `scope`, `access`, `enabled`, `allowed`, `banned`, `blocked`, `muted`, `hidden`, `deleted_at`, `archived`, `approved`, `verified`, `state`, `plan`, `premium`, `pro_active`, `level`, `moderation`.

Two caveats a consumer must hold onto. First, some `[OWNER]` columns are **not** tenancy keys — `actor_user_id` on an audit row is *who did it*, and `viewer_user_id` on an ad impression is the person who was shown the ad, not the person who may read the row; several tables carry two or three party columns and only one of them is the read-authorisation key. Second, some `[PERM]` hits are semantically neutral (`processing_status`, `transcoding_status`, `stream_health`); they are tagged because a generic filter cannot tell them apart, and being over-inclusive is the safe direction here.

---

## 2. Tables by domain

775 live application tables, classified by name prefix and column shape. Row counts in parentheses are from the live db — an empty table is either a future feature, a Postgres-only production surface, or dead. Where a domain's *canonical* table is missing entirely, that is called out in Section 1.3.

### Identity, accounts & auth — 32 tables (13 non-empty, 11,504 rows total)

`account_recovery_tokens`(0), `active_sessions`(0), `auth_events`(460), `email_verification_tokens`(34), `email_verifications`(0), `failed_login_controls`(0), `failed_login_safe_list`(0), `mobile_security_sessions`(9152), `password_reset_tokens`(19), `password_resets`(0), `presence_last_seen`(5), `presence_privacy_settings`(0), `presence_sessions`(32), `privacy_preferences`(0), `profile_audit_logs`(0), `pulse_region_preference_events`(0), `pulse_region_preferences`(0), `pulse_translation_preferences`(0), `security_devices`(0), `security_login_events`(0), `sessions`(44), `sms_verification_codes`(0), `telegram_link_codes`(325), `user_activity`(2), `user_onboarding_progress`(0), `user_presence`(54), `user_recovery_codes`(0), `user_settings`(15), `user_trusted_devices`(0), `user_verifications`(0), `user_welcome_events`(5), `users`(1357)

### Social graph — 13 tables (12 non-empty, 23 rows total)

`arena_blocks`(2), `arena_follows`(1), `arena_friendships`(2), `blocked_users`(2), `comm_v2_blocks`(1), `founder_memberships`(1), `founder_wall_entries`(1), `pulse_follows`(3), `pulse_friend_requests`(2), `pulse_friends`(2), `pulse_friendships`(4), `pulse_muted_users`(0), `pulse_user_mutes`(2)

### Content & engagement — 53 tables (37 non-empty, 41,166 rows total)

`pulse_ai_posts`(38), `pulse_audio_tracks`(33947), `pulse_camera_captures`(140), `pulse_camera_effects`(10), `pulse_camera_previews`(51), `pulse_comment_reactions`(0), `pulse_comments`(38), `pulse_content_music`(41), `pulse_content_preferences`(17), `pulse_content_promotion_audit`(17), `pulse_content_promotions`(17), `pulse_content_sentiment`(0), `pulse_content_translations`(0), `pulse_filters`(14), `pulse_identity_effects`(7), `pulse_jobs`(2278), `pulse_media_assets`(647), `pulse_music_events`(46), `pulse_music_reports`(0), `pulse_post_attempts`(125), `pulse_post_hides`(0), `pulse_post_saves`(2), `pulse_post_views`(114), `pulse_posts`(1140), `pulse_profile_themes`(0), `pulse_reactions`(10), `pulse_reel_audio`(10), `pulse_reel_retention_events`(0), `pulse_reel_sound_saves`(13), `pulse_reels`(130), `pulse_saved_collections`(5), `pulse_saved_items`(3), `pulse_saved_sounds`(12), `pulse_status`(965), `pulse_status_live`(0), `pulse_status_media`(490), `pulse_status_music`(65), `pulse_status_reactions`(148), `pulse_status_replies`(127), `pulse_status_shares`(16), `pulse_status_views`(268), `pulse_statuses`(1), `pulse_stories`(0), `pulse_story_reactions`(0), `pulse_story_views`(0), `pulse_trending_sounds`(7), `pulse_video_categories`(0), `pulse_video_comments`(0), `pulse_video_reactions`(0), `pulse_video_views`(81), `pulse_videos`(126), `pulsesoc_content_campaigns`(0), `pulsesoc_content_planner_items`(0)

### Messaging & chat — 46 tables (31 non-empty, 15,171 rows total)

`chat_media_uploads`(1499), `chat_memory`(0), `chat_reports`(0), `comm_v2_attachments`(80), `comm_v2_channels`(45), `comm_v2_communities`(45), `comm_v2_conversation_items`(0), `comm_v2_conversation_settings`(1), `comm_v2_conversations`(219), `comm_v2_live_streams`(0), `comm_v2_message_deletions`(9), `comm_v2_message_reactions`(83), `comm_v2_messages`(1411), `comm_v2_moderation_events`(2), `comm_v2_participants`(458), `comm_v2_pinned_messages`(0), `comm_v2_presence`(13), `comm_v2_read_receipts`(251), `comm_v2_reports`(1), `comm_v2_typing`(12), `comm_v2_user_settings`(2), `communication_call_device_sessions`(0), `communication_call_events`(0), `communication_call_participants`(0), `communication_call_quality_reports`(0), `communication_calls`(0), `conversation_members`(10), `conversations`(5), `message_attachments`(2), `message_read_receipts`(0), `private_messages`(57), `pulse_chat_health_traces`(5644), `pulse_chat_recovery_events`(21), `pulse_chat_room_members`(206), `pulse_chat_room_messages`(1194), `pulse_chat_rooms`(8), `pulse_conversation_participants`(1243), `pulse_conversation_typing`(6), `pulse_conversations`(494), `pulse_message_reactions`(14), `pulse_message_receipts`(0), `pulse_message_reports`(0), `pulse_message_threads`(28), `pulse_messages`(2108), `pulse_room_members`(0), `pulse_room_messages`(0)

### Live / streaming / calls — 24 tables (17 non-empty, 12,643 rows total)

`live_events`(11), `live_ops_plans`(10), `livestream_access`(128), `livestream_eligibility`(127), `pulse_live_archive_shares`(110), `pulse_live_audio_profiles`(0), `pulse_live_audit_logs`(958), `pulse_live_chat`(370), `pulse_live_classes`(0), `pulse_live_clips`(0), `pulse_live_destinations`(345), `pulse_live_events`(8814), `pulse_live_guest_requests`(101), `pulse_live_guests`(41), `pulse_live_moderation`(0), `pulse_live_provider_events`(0), `pulse_live_reactions`(64), `pulse_live_reports`(0), `pulse_live_restream_targets`(469), `pulse_live_scene_presets`(0), `pulse_live_sessions`(793), `pulse_live_streams`(122), `pulse_live_viewers`(175), `pulse_live_webrtc_signals`(5)

### Groups, communities, pages & spaces — 15 tables (14 non-empty, 1,712 rows total)

`pulse_group_action_logs`(39), `pulse_group_bans`(0), `pulse_group_comment_reports`(4), `pulse_group_creation_attempts`(410), `pulse_group_invites`(1), `pulse_group_members`(410), `pulse_group_post_comments`(6), `pulse_group_post_media`(5), `pulse_group_post_reactions`(3), `pulse_group_post_reports`(4), `pulse_group_posts`(10), `pulse_group_reports`(1), `pulse_group_roles`(407), `pulse_groups`(410), `pulse_space_members`(2)

### Marketplace, orders, sellers, payouts & escrow — 49 tables (6 non-empty, 32 rows total)

`business_os_mkt_audit`(0), `business_os_mkt_disputes`(0), `business_os_mkt_inventory_adjustments`(0), `business_os_mkt_listing_drafts`(0), `business_os_mkt_offer_events`(0), `business_os_mkt_offer_reservations`(0), `business_os_mkt_offers`(0), `business_os_mkt_order_events`(0), `business_os_mkt_order_items`(0), `business_os_mkt_orders`(0), `business_os_mkt_products`(0), `business_os_mkt_refunds`(0), `business_os_mkt_return_events`(0), `business_os_mkt_returns`(0), `business_os_mkt_reviews`(0), `business_os_mkt_seller_ratings`(0), `business_os_mkt_sellers`(0), `business_os_seller_profile`(0), `business_os_seller_profile_addresses`(0), `business_os_seller_profile_audit`(0), `business_os_seller_profile_hours`(0), `business_os_seller_profile_hours_overrides`(0), `business_os_seller_profile_links`(0), `business_os_store_audit`(0), `business_os_store_collection_products`(0), `business_os_store_collections`(0), `business_os_store_products`(0), `business_os_store_return_policy`(0), `business_os_store_shipping_profiles`(0), `business_os_store_storefront`(0), `business_os_store_storefront_versions`(0), `escrow_holds`(2), `marketplace_buyer_interest`(0), `marketplace_listings`(21), `marketplace_merchant_applications`(2), `marketplace_merchant_documents`(3), `marketplace_orders_placeholder`(0), `marketplace_product_media`(0), `marketplace_reports`(0), `marketplace_saved_products`(0), `marketplace_sellers`(2), `pulsesoc_seller_products`(0), `pulsesoc_seller_stores`(0), `seller_application_assignments`(0), `seller_application_notes`(0), `seller_application_status_history`(0), `seller_payout_accounts`(0), `seller_payouts`(0), `seller_transactions`(2)

### Payments, Stripe, subscriptions & entitlements — 48 tables (28 non-empty, 548 rows total)

`checkout_attempts`(2), `creator_balances`(2), `creator_ledger_entries`(4), `creator_payouts`(0), `creator_payouts_placeholder`(0), `creator_revenue_events`(0), `creator_tax_profiles`(0), `creator_transactions`(2), `creator_wallets`(3), `dashboard_entitlements`(0), `fee_ledger`(2), `financial_incidents`(4), `ledger_balances`(0), `ledger_entries`(0), `ledger_transactions`(0), `payment_audit_logs`(0), `payment_email_logs`(68), `payment_records`(16), `payment_verifications`(12), `payment_webhook_events`(6), `payout_failures`(0), `payout_history`(0), `payout_queue`(2), `platform_fee_rules`(3), `platform_payouts`(0), `platform_wallets`(1), `premium_badges`(1), `premium_entitlements`(179), `promo_codes`(0), `provider_webhook_events`(0), `pulse_payment_events`(0), `pulse_premium_audit_logs`(0), `pulse_premium_entitlements`(13), `pulse_premium_feature_flags`(7), `pulse_premium_profiles`(0), `pulse_subscriptions`(0), `revenue_breakdown`(2), `settlement_batches`(1), `stripe_events`(20), `subscription_plans`(3), `subscriptions`(160), `transaction_history`(0), `transactions`(12), `treasury_transactions`(2), `unmatched_payments`(7), `usage_events`(0), `user_entitlements`(13), `user_subscriptions`(1)

### Ads platform — 63 tables (16 non-empty, 2,174 rows total)

`ad_campaigns`(0), `ad_clicks`(0), `ad_creatives`(0), `ad_images`(0), `ad_impressions`(0), `ad_placements`(0), `ad_reports`(0), `ad_revenue`(0), `ad_reviews`(0), `ad_targeting`(0), `ad_videos`(0), `ads`(0), `advertisers`(0), `brand_deals`(0), `business_os_ad_advertisers`(0), `business_os_ad_audit`(0), `business_os_ad_billing_events`(0), `business_os_ad_campaign_funding`(0), `business_os_ad_campaign_operations`(0), `business_os_ad_campaigns`(0), `business_os_ad_click_events`(0), `business_os_ad_creatives`(0), `business_os_ad_delivery_instances`(0), `business_os_ad_funding_ops`(0), `business_os_ad_impression_events`(0), `business_os_ad_pricing_policy`(0), `business_os_ad_sets`(0), `business_os_ad_spend_accumulator`(0), `business_os_attr_audit`(0), `business_os_attr_conversions`(0), `business_os_attr_credits`(0), `business_os_attr_touchpoints`(0), `monetization_events`(0), `pulse_ad_account_profiles`(0), `pulse_ad_accounts`(531), `pulse_ad_audit_logs`(9), `pulse_ad_billing_events`(0), `pulse_ad_billing_profiles`(531), `pulse_ad_campaign_history`(0), `pulse_ad_campaign_placements`(5), `pulse_ad_campaigns`(1), `pulse_ad_clicks`(5), `pulse_ad_creatives`(2), `pulse_ad_events`(5), `pulse_ad_frequency_caps`(1), `pulse_ad_impressions`(5), `pulse_ad_invoices`(0), `pulse_ad_media_assets`(1), `pulse_ad_moderation_queue`(2), `pulse_ad_notifications`(0), `pulse_ad_placements`(12), `pulse_ad_platform_settings`(0), `pulse_ad_policy_flags`(0), `pulse_ad_receipts`(0), `pulse_ad_refunds`(0), `pulse_ad_review_board`(2), `pulse_ad_targeting`(0), `pulse_ad_team_members`(0), `pulse_ad_wallet_funding_sessions`(0), `pulse_ad_wallet_transactions`(531), `pulse_ad_wallets`(531), `sponsor_slots`(0), `sponsorships`(0)

### Crypto (legacy CoinPilotX subsystem) — 49 tables (11 non-empty, 95 rows total)

`alerts_history`(0), `business_os_crypto_alert_events`(0), `business_os_crypto_alerts`(0), `business_os_crypto_audit`(0), `business_os_crypto_holdings`(0), `business_os_crypto_lots`(0), `business_os_crypto_transactions`(0), `connected_wallets`(0), `crypto_ai_queries`(1), `crypto_alerts`(4), `crypto_audit_logs`(5), `crypto_favorite_assets`(0), `crypto_news_cache`(15), `crypto_recent_assets`(0), `crypto_watchlist_assets`(0), `crypto_watchlists`(0), `day_signal_results`(0), `last_prices`(0), `last_signals`(0), `manual_portfolio`(6), `market_observations`(0), `paper_portfolio`(8), `paper_simulator_trades`(0), `paper_simulator_wallets`(2), `portfolio_advice_history`(0), `portfolio_items`(0), `portfolio_snapshots`(40), `prediction_markets`(0), `prediction_watches`(0), `price_history`(0), `risk_scores`(0), `saved_wallets`(0), `scam_alerts`(0), `scam_reports`(0), `scam_scans`(4), `scam_shield_scans`(4), `simulator_accounts`(0), `simulator_ai_coaching_logs`(0), `simulator_lessons`(0), `simulator_orders`(0), `simulator_progress`(0), `simulator_trades`(0), `simulator_watchlists`(0), `wallet_risk_checks`(0), `watch_rules`(0), `watchlist_items`(0), `watchlists`(6), `whale_alerts`(0), `whale_intelligence`(0)

### Notifications, push, email & SMS — 36 tables (22 non-empty, 66,626 rows total)

`alert_delivery_jobs`(0), `alert_events`(10), `alert_rules`(30), `alert_worker_heartbeat`(1), `brevo_contact_sync_logs`(122), `daily_briefs`(0), `delivery_logs`(0), `email_logs`(405), `expo_push_tickets`(1), `failed_email_queue`(1236), `intelligence_delivery_jobs`(500), `intelligence_delivery_log`(500), `notification_delivery_jobs`(2186), `notification_delivery_logs`(900), `notification_device_tokens`(0), `notification_events`(1343), `notification_failures`(0), `notification_jobs`(0), `notification_logs`(24), `notification_preferences`(43682), `notification_schedules`(0), `notifications`(1365), `provider_health`(0), `pulse_notification_deliveries`(7951), `pulse_notification_devices`(6), `pulse_notification_preferences`(0), `pulse_notifications`(4150), `push_delivery_jobs`(2200), `push_subscriptions`(6), `sms_delivery_logs`(0), `telegram_debug_events`(0), `telegram_delivery_logs`(0), `telegram_notifications`(0), `user_alert_rules`(0), `user_alerts`(2), `user_device_tokens`(6)

### Security, audit, moderation & trust — 52 tables (19 non-empty, 15,259 rows total)

`account_audit_logs`(1), `account_health_events`(1), `account_restrictions`(0), `account_strikes`(0), `account_system_events`(0), `account_warnings`(0), `moderation_cases`(1), `pulse_account_data_requests`(0), `pulse_badges`(22), `pulse_privileges`(21), `pulse_reports`(6), `pulse_user_badges`(88), `pulse_user_privileges`(84), `reputation_ledger`(0), `security_events`(99), `security_reports`(2), `sentinel_dependency_inventory`(0), `sentinel_detection_exclusions`(0), `sentinel_edges`(10), `sentinel_enrichment_requests`(0), `sentinel_events`(0), `sentinel_evidence`(0), `sentinel_external_data_audit`(0), `sentinel_external_observations`(0), `sentinel_external_providers`(0), `sentinel_financial_exposure`(0), `sentinel_financial_reconciliations`(0), `sentinel_financial_risk`(0), `sentinel_health_snapshots`(0), `sentinel_identity_risk`(0), `sentinel_incident_transitions`(0), `sentinel_incidents`(0), `sentinel_metrics`(0), `sentinel_provider_capabilities`(0), `sentinel_provider_circuits`(0), `sentinel_runbook_executions`(0), `sentinel_sequence_firings`(0), `sentinel_vulnerability_findings`(0), `support_notes`(2), `support_ticket_messages`(2), `support_tickets`(3), `user_privilege_profiles`(133), `user_privilege_snapshots`(0), `user_reputation_scores`(0), `user_trust_events`(0), `user_trust_profiles`(130), `user_trust_score`(0), `verification_appeals`(0), `verification_audit_logs`(6), `verification_badges`(0), `verification_documents`(4), `verification_requests`(14644)

### Admin, roles & permissions — 22 tables (15 non-empty, 719 rows total)

`admin_activity_logs`(27), `admin_approvals`(0), `admin_audit_logs`(64), `admin_permissions`(40), `admin_role_permissions`(131), `admin_roles`(25), `admin_session_logs`(2), `admin_task_comments`(0), `admin_tasks`(4), `admin_user_actions`(11), `admin_user_notes`(2), `admin_user_roles`(0), `admin_users`(34), `backend_feature_registry`(151), `backend_management_audit_events`(0), `department_members`(0), `departments`(24), `employees`(0), `enterprise_leads`(0), `permissions`(40), `role_permissions`(139), `roles`(25)

### Dashboard — 19 tables (3 non-empty, 39 rows total)

`command_center_ai_events`(0), `command_center_message_events`(2), `command_center_notification_events`(2), `command_center_security_events`(0), `creator_dashboard_metrics`(0), `dashboard_audit_logs`(0), `dashboard_categories`(0), `dashboard_events`(0), `dashboard_modules`(0), `dashboard_permissions`(0), `dashboard_recommendations`(0), `dashboard_usage`(0), `dashboard_visibility`(0), `dashboard_widget_access_rules`(0), `dashboard_widgets`(0), `pulsesoc_dashboard_preferences`(0), `user_dashboard_metrics`(0), `user_dashboard_preferences`(35), `user_dashboard_widget_state`(0)

### Arena (gaming) — 65 tables (54 non-empty, 612 rows total)

`arena_academy_paths`(6), `arena_academy_progress`(0), `arena_ai_bosses`(7), `arena_ai_governors`(5), `arena_badges`(13), `arena_boss_attempts`(2), `arena_chat_messages`(18), `arena_chat_threads`(10), `arena_companions`(0), `arena_crowd_reactions`(0), `arena_emotes`(2), `arena_events`(7), `arena_faction_members`(0), `arena_factions`(6), `arena_friend_challenges`(7), `arena_highlights`(3), `arena_leaderboards`(0), `arena_legacy`(0), `arena_live_matches`(37), `arena_match_chat`(1), `arena_match_events`(44), `arena_match_participants`(40), `arena_matches`(37), `arena_message_requests`(14), `arena_mission_attempts`(1), `arena_missions`(64), `arena_os_activity`(13), `arena_play_sessions`(34), `arena_playbook_comments`(0), `arena_playbook_votes`(0), `arena_playbooks`(2), `arena_player_stories`(2), `arena_presence`(3), `arena_profiles`(67), `arena_psychology_scores`(1), `arena_quest_progress`(1), `arena_quests`(5), `arena_replays`(3), `arena_reports`(2), `arena_reputation`(1), `arena_rivalries`(1), `arena_roast_lines`(7), `arena_roast_participants`(13), `arena_room_messages`(1), `arena_rooms`(11), `arena_seasons`(4), `arena_share_events`(3), `arena_spectators`(2), `arena_team_members`(1), `arena_teams`(1), `arena_tournament_entries`(1), `arena_tournaments`(3), `arena_trade_positions`(1), `arena_trades`(1), `arena_user_badges`(5), `arena_user_preferences`(12), `arena_victory_events`(3), `arena_world_events`(0), `arena_world_history`(0), `arena_world_state`(64), `roast_matches`(5), `roast_messages`(10), `roast_reactions`(2), `roast_rooms`(0), `roast_votes`(3)

### Education & courses — 25 tables (10 non-empty, 177 rows total)

`education_ai_tutor_logs`(0), `education_badges`(0), `education_categories`(9), `education_lesson_views`(1), `education_lessons`(16), `education_progress`(0), `education_quiz_questions`(48), `education_quizzes`(16), `education_sections`(80), `education_user_progress`(0), `pulse_courses`(0), `pulse_lesson_media`(0), `pulse_lessons`(0), `pulse_quiz_questions`(0), `pulse_quizzes`(0), `pulse_student_enrollments`(0), `pulse_teacher_applications`(1), `pulse_teacher_documents`(0), `pulse_teacher_profiles`(1), `pulse_teacher_reviews`(0), `teacher_applications`(3), `teacher_earnings_placeholder`(0), `teacher_lessons`(0), `teacher_profiles`(2), `user_education_preferences`(0)

### Growth, referral, progress & rewards — 24 tables (18 non-empty, 9,559 rows total)

`leads`(0), `pulse_growth_accounts`(531), `pulse_growth_ai_sessions`(531), `pulse_growth_analytics_containers`(531), `pulse_growth_api_keys`(531), `pulse_growth_audience_models`(531), `pulse_growth_audience_profiles`(531), `pulse_growth_billing_profiles`(531), `pulse_growth_ledger`(531), `pulse_growth_preferences`(531), `pulse_growth_promotion_history`(531), `pulse_growth_provisioning_log`(1565), `pulse_growth_risk_profiles`(531), `pulse_growth_scores`(531), `pulse_growth_trust_links`(531), `pulse_growth_wallets`(531), `pulse_growth_workspaces`(531), `pulsesoc_premium_exploration`(0), `pulsesoc_user_goals`(0), `referral_conversions`(0), `referral_events`(0), `referral_invites`(1), `referral_rewards`(0), `user_streaks`(28)

### AI / UNDX memory & logs — 73 tables (38 non-empty, 7,987 rows total)

`ai_action_audit_logs`(0), `ai_action_requests`(0), `ai_action_results`(0), `ai_agents`(0), `ai_analyses`(0), `ai_chat_history`(0), `ai_context_summaries`(0), `ai_conversations`(5), `ai_feedback`(1), `ai_memory_cards`(0), `ai_messages`(10), `ai_observability_events`(0), `ai_recommendations`(1), `business_os_undx_action_receipts`(0), `business_os_undx_action_requests`(0), `business_os_undx_audit`(0), `business_os_undx_confirmations`(0), `business_os_undx_decisions`(0), `business_os_undx_emergency_stops`(0), `business_os_undx_permissions`(0), `business_os_undx_policies`(0), `business_os_undx_tool_registry`(0), `command_history`(36), `global_intelligence_edges`(0), `global_intelligence_nodes`(0), `global_intelligence_signals`(0), `global_intelligence_snapshots`(1161), `intelligence_alert_cadence`(1), `intelligence_collector_runs`(65), `intelligence_digest_jobs`(0), `intelligence_events`(27), `intelligence_feedback`(0), `intelligence_forecasts`(0), `intelligence_sources`(25), `intelligence_streams`(10), `pulse_ai_capability_registry`(97), `pulse_ai_client_contexts`(1), `pulse_ai_confirmations`(10), `pulse_ai_conversation_context_permissions`(7), `pulse_ai_conversations`(7), `pulse_ai_delegated_policies`(0), `pulse_ai_engagement`(0), `pulse_ai_feature_registry`(62), `pulse_ai_feedback`(0), `pulse_ai_knowledge_edges`(0), `pulse_ai_knowledge_items`(89), `pulse_ai_learning_events`(80), `pulse_ai_memory`(38), `pulse_ai_memory_provenance`(11), `pulse_ai_messages`(138), `pulse_ai_missions`(0), `pulse_ai_provider_events`(1), `pulse_ai_rotation_state`(1), `pulse_ai_safety_events`(66), `pulse_ai_safety_reviews`(0), `pulse_ai_schedules`(76), `pulse_ai_search_sessions`(0), `pulse_ai_skill_registry`(12), `pulse_ai_task_nodes`(0), `pulse_ai_tool_operations`(44), `pulse_ai_tool_registry`(97), `pulse_ai_topics`(0), `pulse_ai_truth_facts`(0), `pulse_ai_user_memory`(11), `pulse_ai_verification_events`(0), `pulse_ai_web_search_logs`(29), `pulse_creator_growth_profiles`(531), `pulse_daily_mentor_conversations`(44), `pulse_daily_mentor_messages`(176), `saved_command_results`(1), `saved_insights`(1), `user_ai_interactions`(15), `user_intelligence_streams`(5000)

### Analytics & telemetry — 20 tables (12 non-empty, 89,600 rows total)

`analytics_events`(3159), `background_jobs`(0), `capability_audit_results`(11880), `conversion_funnel_events`(2942), `engagement_events`(0), `event_bus_events`(0), `feature_flags`(15), `global_events`(0), `i18n_missing_translations`(10), `performance_traces`(1490), `product_health_checks`(0), `pulse_creator_analytics`(0), `pulse_creator_audience_segments`(0), `pulse_creator_energy_snapshots`(0), `pulse_online_sessions`(875), `reliability_snapshots`(792), `system_health_snapshots`(1160), `visitor_logs`(61731), `visitor_sessions`(5544), `worker_heartbeats`(2)

### Business OS (other) — 40 tables (4 non-empty, 40 rows total)

`business_os_business`(0), `business_os_business_audit`(0), `business_os_business_locations`(0), `business_os_business_members`(0), `business_os_business_policies`(0), `business_os_commerce_thread_links`(0), `business_os_confirmation_grants`(0), `business_os_creator_audit`(0), `business_os_creator_contributions`(0), `business_os_creator_offerings`(0), `business_os_creator_supporters`(0), `business_os_ent_audit`(0), `business_os_ent_catalog`(24), `business_os_ent_grants`(0), `business_os_ent_plans`(9), `business_os_ent_products`(6), `business_os_ent_provider_subs`(0), `business_os_ent_usage`(0), `business_os_event_audit`(0), `business_os_event_ticket_types`(0), `business_os_event_tickets`(0), `business_os_events`(1), `business_os_l10n_audit`(0), `business_os_l10n_locales`(0), `business_os_l10n_resolutions`(0), `business_os_l10n_strings`(0), `business_os_merchant_audit`(0), `business_os_merchant_proposals`(0), `business_os_merchant_rules`(0), `business_os_merchant_signals`(0), `business_os_perf_audit`(0), `business_os_perf_samples`(0), `business_os_perf_summaries`(0), `business_os_perf_targets`(0), `business_os_rec_audit`(0), `business_os_rec_interactions`(0), `business_os_rec_items`(0), `business_os_rec_recommendations`(0), `business_os_verification_checks`(0), `business_os_verification_runs`(0)

### Unclassified — 7 tables (4 non-empty, 174 rows total)

`audit_logs`(0), `creator_profiles`(1), `pulse_translation_events`(0), `trial_email_events`(168), `user_portfolio_settings`(0), `user_security_events`(4), `user_watch_items`(1)

---

## 3. Key tables — full column lists with ownership and permission call-outs

163 tables covering every domain in Section 2, including all of the high-traffic ones. Every column name and type below was read from `PRAGMA table_info` on the live database — **nothing here is inferred or invented**. `[OWNER]` and `[PERM]` tags follow the rules in Section 1.5. `BLANK` as a type means the column was declared with no type (SQLite allows this; it gets BLOB affinity).

Each entry also reports **Code vs live** — how many columns `bot.py`'s `CREATE TABLE` declares versus how many the live table actually has. A large gap means the table has been evolved by `add_columns_if_missing` and the source DDL should not be trusted as a description of the table.

#### `users`
*domain: identity_auth · rows: 1,357 · columns: 106 · PK: `user_id`*

- **OWNERSHIP:** `user_id`, `telegram_user_id`
- **PERMISSION/VISIBILITY:** `alerts_enabled`, `is_pro`, `subscription_plan`, `subscription_status`, `email_verified`, `plan`, `account_status`, `trial_status`, `deleted_at`, `suspended_reason`, `pro_active`, `last_payment_status`, `payment_status`, `phone_verified`, `sms_verified_at`, `auto_signals_enabled`, `profile_visibility`, `verified_badge`, `premium_status`, `is_super_user`, `trust_level`, `two_factor_enabled`, `access_enabled`, `login_enabled`, `hidden_from_discovery`
- **Code vs live:** bot.py `CREATE TABLE` declares 16 columns, live db has 106 (live is a strict superset; +90 added by `add_columns_if_missing`)
- **Indexes:** `idx_users_roast_call_sign_slug`(roast_call_sign_slug)

- **All columns:** `user_id INTEGER` [OWNER] · `username TEXT` · `display_name TEXT` · `email TEXT` · `signup_time TEXT` · `onboarding_complete INTEGER` · `alerts_enabled INTEGER` [PERM] · `is_pro INTEGER` [PERM] · `subscription_plan TEXT` [PERM] · `subscription_status TEXT` [PERM] · `subscription_started_at TEXT` · `subscription_expires_at TEXT` · `risk_profile TEXT` · `preferred_exchange_goal TEXT` · `stripe_customer_id TEXT` · `stripe_session_id TEXT` · `last_payment_type TEXT` · `full_name TEXT` · `password_hash TEXT` · `phone TEXT` · `country TEXT` · `telegram_user_id INTEGER` [OWNER] · `telegram_username TEXT` · `telegram_chat_id INTEGER` · `email_verified INTEGER` [PERM] · `email_opt_in INTEGER` · `sms_opt_in INTEGER` · `plan TEXT` [PERM] · `created_at TEXT` · `updated_at TEXT` · `last_login_at TEXT` · `last_seen_at TEXT` · `referral_code TEXT` · `referred_by TEXT` · `trial_start_date TEXT` · `trial_end_date TEXT` · `trial_used INTEGER` · `stripe_subscription_id TEXT` · `pro_expires_at TEXT` · `usage_ai_count INTEGER` · `usage_reset_at TEXT` · `account_status TEXT` [PERM] · `trial_status TEXT` [PERM] · `marketing_email_opt_in INTEGER` · `notification_email_opt_in INTEGER` · `security_email_opt_in INTEGER` · `payment_receipt_opt_in INTEGER` · `deleted_at TEXT` [PERM] · `restricted_reason TEXT` · `suspended_reason TEXT` [PERM] · `pro_active INTEGER` [PERM] · `pro_started_at TEXT` · `payment_provider TEXT` · `provider_customer_id TEXT` · `provider_subscription_id TEXT` · `last_payment_status TEXT` [PERM] · `payment_status TEXT` [PERM] · `payment_amount REAL` · `payment_currency TEXT` · `latest_stripe_event TEXT` · `latest_payment_at TEXT` · `phone_number TEXT` · `phone_verified INTEGER` [PERM] · `sms_verified_at TEXT` [PERM] · `roast_call_sign TEXT` · `roast_call_sign_slug TEXT` · `roast_call_sign_updated_at TEXT` · `auto_signals_enabled INTEGER` [PERM] · `auto_signals_mode TEXT` · `auto_signals_started_at TEXT` · `auto_signals_last_checked_at TEXT` · `auto_signals_paused_at TEXT` · `auto_signals_stopped_at TEXT` · `avatar_url TEXT` · `avatar_thumbnail_url TEXT` · `banner_url TEXT` · `bio TEXT` · `social_links_json TEXT` · `expertise_tags_json TEXT` · `profile_visibility TEXT` [PERM] · `verified_badge INTEGER` [PERM] · `premium_status TEXT` [PERM] · `premium_expires_at TEXT` · `lifetime_premium INTEGER` · `premium_glow_manual_grant INTEGER` · `premium_mark_type TEXT` · `premium_mark_override INTEGER` · `cover_url TEXT` · `cover_position TEXT` · `cover_filter TEXT` · `avatar_filter TEXT` · `is_super_user INTEGER` [PERM] · `date_of_birth TEXT` · `age_confirmed INTEGER` · `security_score INTEGER` · `trust_level TEXT` [PERM] · `two_factor_enabled INTEGER` [PERM] · `recovery_email TEXT` · `recovery_phone TEXT` · `profile_completed_at TEXT` · `last_security_review_at TEXT` · `access_enabled INTEGER` [PERM] · `login_enabled INTEGER` [PERM] · `preferred_language TEXT` · `pulse_id TEXT` · `hidden_from_discovery INTEGER` [PERM]

#### `sessions`
*domain: identity_auth · rows: 44 · columns: 10 · PK: `id`*

- **OWNERSHIP:** _none — table is not user-scoped; any row-level filter must come from a join_
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 10 columns, live db has 10
- **Indexes:** `sqlite_autoindex_sessions_1`(session_id)

- **All columns:** `id INTEGER` · `session_id TEXT` · `first_seen_at TEXT` · `last_seen_at TEXT` · `user_agent TEXT` · `referrer TEXT` · `landing_page TEXT` · `utm_source TEXT` · `utm_medium TEXT` · `utm_campaign TEXT`

#### `active_sessions`
*domain: identity_auth · rows: 0 · columns: 9 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** no `CREATE TABLE` for this name in `bot.py` (created in `services/` or pre-existing)
- **Indexes:** _none_

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `session_hash TEXT` · `device_label TEXT` · `ip_hash TEXT` · `user_agent_hash TEXT` · `revoked_at TEXT` · `last_seen_at TEXT` · `created_at TEXT`

#### `user_settings`
*domain: identity_auth · rows: 15 · columns: 5 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 5 columns, live db has 5
- **Indexes:** `idx_user_settings_setting_key`(setting_key), `idx_user_settings_user_id`(user_id), `sqlite_autoindex_user_settings_1`(user_id,setting_key)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `setting_key TEXT` · `setting_value TEXT` · `updated_at TEXT`

#### `user_presence`
*domain: identity_auth · rows: 54 · columns: 7 · PK: `user_id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `status`, `last_active_at`
- **Code vs live:** bot.py `CREATE TABLE` declares 7 columns, live db has 7
- **Indexes:** `idx_user_presence_status`(status,updated_at), `idx_user_presence_user_id`(user_id)

- **All columns:** `user_id INTEGER` [OWNER] · `status TEXT` [PERM] · `last_seen_at TEXT` · `last_active_at TEXT` [PERM] · `source TEXT` · `device_label TEXT` · `updated_at TEXT`

#### `presence_privacy_settings`
*domain: identity_auth · rows: 0 · columns: 4 · PK: `user_id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** no `CREATE TABLE` for this name in `bot.py` (created in `services/` or pre-existing)
- **Indexes:** _none_

- **All columns:** `user_id INTEGER` [OWNER] · `hide_last_seen INTEGER` · `invisible_mode INTEGER` · `updated_at TEXT`

#### `privacy_preferences`
*domain: identity_auth · rows: 0 · columns: 6 · PK: `user_id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `public_profile`, `creator_visibility`
- **Code vs live:** bot.py `CREATE TABLE` declares 6 columns, live db has 6
- **Indexes:** _none_

- **All columns:** `user_id INTEGER` [OWNER] · `analytics_opt_out INTEGER` · `personalized_ads_opt_out INTEGER` · `public_profile INTEGER` [PERM] · `creator_visibility INTEGER` [PERM] · `updated_at TEXT`

#### `user_entitlements`
*domain: payments_billing · rows: 13 · columns: 10 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `entitlement_key`, `status`
- **Code vs live:** no `CREATE TABLE` for this name in `bot.py` (created in `services/` or pre-existing)
- **Indexes:** `sqlite_autoindex_user_entitlements_1`(user_id,entitlement_key)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `entitlement_key TEXT` [PERM] · `status TEXT` [PERM] · `source TEXT` · `starts_at TEXT` · `expires_at TEXT` · `metadata_json TEXT` · `created_at TEXT` · `updated_at TEXT`

#### `user_subscriptions`
*domain: payments_billing · rows: 1 · columns: 23 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `plan_key`, `status`, `locked_price_cents`, `provider_status`
- **Code vs live:** no `CREATE TABLE` for this name in `bot.py` (created in `services/` or pre-existing)
- **Indexes:** `sqlite_autoindex_user_subscriptions_1`(user_id,plan_key)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `plan_key TEXT` [PERM] · `provider TEXT` · `provider_subscription_id TEXT` · `status TEXT` [PERM] · `locked_price_cents INTEGER` [PERM] · `currency TEXT` · `started_at TEXT` · `expires_at TEXT` · `metadata_json TEXT` · `created_at TEXT` · `updated_at TEXT` · `stripe_customer_id TEXT` · `stripe_subscription_id TEXT` · `stripe_checkout_session_id TEXT` · `stripe_price_id TEXT` · `stripe_product_id TEXT` · `provider_status TEXT` [PERM] · `current_period_start TEXT` · `current_period_end TEXT` · `cancel_at_period_end INTEGER` · `canceled_at TEXT`

#### `subscriptions`
*domain: payments_billing · rows: 160 · columns: 18 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `plan`, `status`, `plan_key`
- **Code vs live:** bot.py `CREATE TABLE` declares 13 columns, live db has 18 (live is a strict superset; +5 added by `add_columns_if_missing`)
- **Indexes:** _none_

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `plan TEXT` [PERM] · `status TEXT` [PERM] · `payment_type TEXT` · `stripe_customer_id TEXT` · `stripe_subscription_id TEXT` · `trial_start_date TEXT` · `trial_end_date TEXT` · `current_period_end TEXT` · `pro_expires_at TEXT` · `created_at TEXT` · `updated_at TEXT` · `plan_key TEXT` [PERM] · `provider TEXT` · `provider_subscription_id TEXT` · `current_period_start TEXT` · `cancel_at_period_end INTEGER`

#### `premium_entitlements`
*domain: payments_billing · rows: 179 · columns: 10 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `entitlement_key`, `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 10 columns, live db has 10
- **Indexes:** `idx_premium_entitlements_user_key`(user_id,entitlement_key,status)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `entitlement_key TEXT` [PERM] · `status TEXT` [PERM] · `source TEXT` · `starts_at TEXT` · `ends_at TEXT` · `metadata_json TEXT` · `created_at TEXT` · `updated_at TEXT`

#### `pulse_premium_entitlements`
*domain: payments_billing · rows: 13 · columns: 10 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `entitlement_key`, `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 10 columns, live db has 10
- **Indexes:** `idx_pulse_premium_entitlements_user`(user_id,status), `sqlite_autoindex_pulse_premium_entitlements_1`(user_id,entitlement_key)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `entitlement_key TEXT` [PERM] · `source TEXT` · `status TEXT` [PERM] · `granted_by INTEGER` · `starts_at TEXT` · `expires_at TEXT` · `created_at TEXT` · `updated_at TEXT`

#### `pulse_subscriptions`
*domain: payments_billing · rows: 0 · columns: 10 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `plan_key`, `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 10 columns, live db has 10
- **Indexes:** _none_

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `plan_key TEXT` [PERM] · `status TEXT` [PERM] · `provider TEXT` · `provider_subscription_id TEXT` · `started_at TEXT` · `expires_at TEXT` · `created_at TEXT` · `updated_at TEXT`

#### `subscription_plans`
*domain: payments_billing · rows: 3 · columns: 12 · PK: `id`*

- **OWNERSHIP:** _none — table is not user-scoped; any row-level filter must come from a join_
- **PERMISSION/VISIBILITY:** `plan_key`, `status`
- **Code vs live:** no `CREATE TABLE` for this name in `bot.py` (created in `services/` or pre-existing)
- **Indexes:** `sqlite_autoindex_subscription_plans_1`(plan_key)

- **All columns:** `id INTEGER` · `plan_key TEXT` [PERM] · `name TEXT` · `price_cents INTEGER` · `regular_price_cents INTEGER` · `currency TEXT` · `billing_interval TEXT` · `status TEXT` [PERM] · `description TEXT` · `metadata_json TEXT` · `created_at TEXT` · `updated_at TEXT`

#### `pulse_follows`
*domain: social_graph · rows: 3 · columns: 4 · PK: `follower_user_id`, `followed_user_id`*

- **OWNERSHIP:** `follower_user_id`, `followed_user_id`
- **PERMISSION/VISIBILITY:** `followed_public_player_id`
- **Code vs live:** bot.py `CREATE TABLE` declares 4 columns, live db has 4
- **Indexes:** `idx_pulse_follows_follower`(follower_user_id), `sqlite_autoindex_pulse_follows_1`(follower_user_id,followed_user_id)

- **All columns:** `follower_user_id INTEGER` [OWNER] · `followed_user_id INTEGER` [OWNER] · `followed_public_player_id TEXT` [PERM] · `created_at TEXT`

#### `pulse_friends`
*domain: social_graph · rows: 2 · columns: 5 · PK: `user_id`, `friend_user_id`*

- **OWNERSHIP:** `user_id`, `friend_user_id`
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 5 columns, live db has 5
- **Indexes:** `idx_pulse_friends_user`(user_id,status), `sqlite_autoindex_pulse_friends_1`(user_id,friend_user_id)

- **All columns:** `user_id INTEGER` [OWNER] · `friend_user_id INTEGER` [OWNER] · `status TEXT` [PERM] · `created_at TEXT` · `updated_at TEXT`

#### `pulse_friendships`
*domain: social_graph · rows: 4 · columns: 3 · PK: `user_id`, `friend_user_id`*

- **OWNERSHIP:** `user_id`, `friend_user_id`
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 3 columns, live db has 3
- **Indexes:** `sqlite_autoindex_pulse_friendships_1`(user_id,friend_user_id)

- **All columns:** `user_id INTEGER` [OWNER] · `friend_user_id INTEGER` [OWNER] · `created_at TEXT`

#### `pulse_friend_requests`
*domain: social_graph · rows: 2 · columns: 8 · PK: `id`*

- **OWNERSHIP:** `requester_user_id`, `receiver_user_id`, `recipient_user_id`
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 8 columns, live db has 8
- **Indexes:** `idx_pulse_friend_requests_recipient`(recipient_user_id,status), `idx_pulse_friend_requests_receiver`(receiver_user_id,status), `sqlite_autoindex_pulse_friend_requests_1`(requester_user_id,receiver_user_id)

- **All columns:** `id INTEGER` · `requester_user_id INTEGER` [OWNER] · `receiver_user_id INTEGER` [OWNER] · `status TEXT` [PERM] · `created_at TEXT` · `updated_at TEXT` · `recipient_user_id INTEGER` [OWNER] · `responded_at TEXT`

#### `blocked_users`
*domain: social_graph · rows: 2 · columns: 5 · PK: `id`*

- **OWNERSHIP:** `blocker_user_id`, `blocked_user_id`
- **PERMISSION/VISIBILITY:** `blocked_user_id`
- **Code vs live:** bot.py `CREATE TABLE` declares 5 columns, live db has 5
- **Indexes:** `sqlite_autoindex_blocked_users_1`(blocker_user_id,blocked_user_id)

- **All columns:** `id INTEGER` · `blocker_user_id INTEGER` [OWNER] · `blocked_user_id INTEGER` [OWNER] · `reason TEXT` · `created_at TEXT`

#### `pulse_muted_users`
*domain: social_graph · rows: 0 · columns: 5 · PK: `id`*

- **OWNERSHIP:** `muter_user_id`, `muted_user_id`
- **PERMISSION/VISIBILITY:** `muted_user_id`, `scope`
- **Code vs live:** no `CREATE TABLE` for this name in `bot.py` (created in `services/` or pre-existing)
- **Indexes:** `idx_pulse_muted_users_muter`(muter_user_id,created_at), `sqlite_autoindex_pulse_muted_users_1`(muter_user_id,muted_user_id)

- **All columns:** `id INTEGER` · `muter_user_id INTEGER` [OWNER] · `muted_user_id INTEGER` [OWNER] · `scope TEXT` [PERM] · `created_at TEXT`

#### `pulse_user_mutes`
*domain: social_graph · rows: 2 · columns: 7 · PK: `id`*

- **OWNERSHIP:** `user_id`, `muted_user_id`
- **PERMISSION/VISIBILITY:** `muted_user_id`, `muted_until`
- **Code vs live:** no `CREATE TABLE` for this name in `bot.py` (created in `services/` or pre-existing)
- **Indexes:** `sqlite_autoindex_pulse_user_mutes_1`(user_id,muted_user_id)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `muted_user_id INTEGER` [OWNER] · `reason TEXT` · `muted_until TEXT` [PERM] · `created_at TEXT` · `updated_at TEXT`

#### `pulse_posts`
*domain: content · rows: 1,140 · columns: 29 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `public_player_id`, `visibility`, `moderation_status`, `deleted_at`, `status`, `live_status`
- **Code vs live:** bot.py `CREATE TABLE` declares 18 columns, live db has 29 (live is a strict superset; +11 added by `add_columns_if_missing`)
- **Indexes:** `idx_pulse_posts_reels_feed`(post_type,visibility,moderation_status,status,created_at,id), `idx_pulse_posts_feed_author`(user_id,visibility,moderation_status,status,created_at), `idx_pulse_posts_mobile_feed`(visibility,moderation_status,status,created_at,id), `idx_pulse_posts_status_created`(status,created_at), `idx_pulse_posts_created_at`(created_at), `idx_pulse_posts_type_created`(post_type,created_at), `idx_pulse_posts_public_player`(public_player_id,created_at), `idx_pulse_posts_user_created`(user_id,created_at), `idx_pulse_posts_feed`(visibility,moderation_status,engagement_score,created_at)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `public_player_id TEXT` [PERM] · `post_type TEXT` · `body TEXT` · `media_ids_json TEXT` · `title TEXT` · `tags_json TEXT` · `visibility TEXT` [PERM] · `moderation_status TEXT` [PERM] · `ai_summary TEXT` · `ai_tags_json TEXT` · `sentiment TEXT` · `risk_score INTEGER` · `engagement_score REAL` · `created_at TEXT` · `updated_at TEXT` · `deleted_at TEXT` [PERM] · `pinned_at TEXT` · `pinned_by INTEGER` · `repost_of_post_id INTEGER` · `edited_at TEXT` · `status TEXT` [PERM] · `live_session_id INTEGER` · `live_status TEXT` [PERM] · `live_viewer_count INTEGER` · `playback_url TEXT` · `preview_url TEXT` · `replay_url TEXT`

#### `pulse_reels`
*domain: content · rows: 130 · columns: 34 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `status`, `editor_state_json`, `processing_status`, `transcoding_status`, `moderation_status`
- **Code vs live:** bot.py `CREATE TABLE` declares 18 columns, live db has 34 (live is a strict superset; +16 added by `add_columns_if_missing`)
- **Indexes:** `idx_pulse_reels_user_created`(user_id,created_at), `idx_pulse_reels_status_score_created`(status,reel_score,created_at,id), `idx_pulse_reels_post_status`(post_id,status), `idx_pulse_reels_trust_energy`(safety_score,educational_value,reel_score), `idx_pulse_reels_status_created`(status,created_at), `idx_pulse_reels_category_status`(category,status,created_at), `idx_pulse_reels_user`(user_id,created_at), `idx_pulse_reels_status_score`(status,reel_score,created_at), `sqlite_autoindex_pulse_reels_1`(post_id)

- **All columns:** `id INTEGER` · `post_id INTEGER` · `user_id INTEGER` [OWNER] · `category TEXT` · `caption TEXT` · `video_url TEXT` · `poster_url TEXT` · `ai_tags_json TEXT` · `watch_duration_ms INTEGER` · `completion_rate REAL` · `replay_count INTEGER` · `share_count INTEGER` · `safety_score INTEGER` · `educational_value INTEGER` · `reel_score INTEGER` · `status TEXT` [PERM] · `created_at TEXT` · `updated_at TEXT` · `audio_track_id INTEGER` · `sound_title TEXT` · `sound_start_seconds REAL` · `sound_end_seconds REAL` · `editor_state_json TEXT` [PERM] · `thumbnail_frame_seconds REAL` · `processing_status TEXT` [PERM] · `transcoding_status TEXT` [PERM] · `moderation_status TEXT` [PERM] · `mux_asset_created_at TEXT` · `mux_ready_at TEXT` · `webhook_received_at TEXT` · `db_ready_update_at TEXT` · `pinned_at TEXT` · `comments_disabled INTEGER` · `reactions_disabled INTEGER`

#### `pulse_videos`
*domain: content · rows: 126 · columns: 30 · PK: `id`*

- **OWNERSHIP:** `owner_user_id`
- **PERMISSION/VISIBILITY:** `mux_status`, `processing_status`, `visibility`, `status`, `moderation_status`
- **Code vs live:** bot.py `CREATE TABLE` declares 24 columns, live db has 30 (live is a strict superset; +6 added by `add_columns_if_missing`)
- **Indexes:** `idx_pulse_videos_visibility_status`(visibility,status,created_at), `idx_pulse_videos_owner_created`(owner_user_id,created_at), `idx_pulse_videos_source`(source_type,source_id), `sqlite_autoindex_pulse_videos_1`(source_type,source_id)

- **All columns:** `id INTEGER` · `owner_user_id INTEGER` [OWNER] · `source_type TEXT` · `source_id TEXT` · `media_id INTEGER` · `title TEXT` · `description TEXT` · `thumbnail_url TEXT` · `media_url TEXT` · `playback_url TEXT` · `mux_asset_id TEXT` · `mux_playback_id TEXT` · `mux_status TEXT` [PERM] · `processing_status TEXT` [PERM] · `duration_seconds REAL` · `width INTEGER` · `height INTEGER` · `orientation TEXT` · `visibility TEXT` [PERM] · `status TEXT` [PERM] · `view_count INTEGER` · `created_at TEXT` · `updated_at TEXT` · `tags TEXT` · `category TEXT` · `mux_asset_created_at TEXT` · `mux_ready_at TEXT` · `webhook_received_at TEXT` · `db_ready_update_at TEXT` · `moderation_status TEXT` [PERM]

#### `pulse_comments`
*domain: content · rows: 38 · columns: 11 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `moderation_status`, `deleted_at`
- **Code vs live:** bot.py `CREATE TABLE` declares 9 columns, live db has 11 (live is a strict superset; +2 added by `add_columns_if_missing`)
- **Indexes:** `idx_pulse_comments_post_visible_created`(post_id,deleted_at,moderation_status,created_at), `idx_pulse_comments_post_created`(post_id,created_at)

- **All columns:** `id INTEGER` · `post_id INTEGER` · `user_id INTEGER` [OWNER] · `parent_comment_id INTEGER` · `body TEXT` · `media_ids_json TEXT` · `moderation_status TEXT` [PERM] · `created_at TEXT` · `deleted_at TEXT` [PERM] · `updated_at TEXT` · `edited_at TEXT`

#### `pulse_reactions`
*domain: content · rows: 10 · columns: 5 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 5 columns, live db has 5
- **Indexes:** `idx_pulse_reactions_user_post`(user_id,post_id), `idx_pulse_reactions_post`(post_id), `sqlite_autoindex_pulse_reactions_1`(post_id,user_id)

- **All columns:** `id INTEGER` · `post_id INTEGER` · `user_id INTEGER` [OWNER] · `reaction_type TEXT` · `created_at TEXT`

#### `pulse_comment_reactions`
*domain: content · rows: 0 · columns: 5 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 5 columns, live db has 5
- **Indexes:** `sqlite_autoindex_pulse_comment_reactions_1`(comment_id,user_id)

- **All columns:** `id INTEGER` · `comment_id INTEGER` · `user_id INTEGER` [OWNER] · `reaction_type TEXT` · `created_at TEXT`

#### `pulse_post_saves`
*domain: content · rows: 2 · columns: 5 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 5 columns, live db has 5
- **Indexes:** `sqlite_autoindex_pulse_post_saves_1`(post_id,user_id)

- **All columns:** `id INTEGER` · `post_id INTEGER` · `user_id INTEGER` [OWNER] · `collection_name TEXT` · `created_at TEXT`

#### `pulse_post_views`
*domain: content · rows: 114 · columns: 6 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 6 columns, live db has 6
- **Indexes:** `idx_pulse_views_post`(post_id,viewed_at)

- **All columns:** `id INTEGER` · `post_id INTEGER` · `user_id INTEGER` [OWNER] · `visitor_id TEXT` · `viewed_at TEXT` · `dwell_ms INTEGER`

#### `pulse_post_hides`
*domain: content · rows: 0 · columns: 6 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** no `CREATE TABLE` for this name in `bot.py` (created in `services/` or pre-existing)
- **Indexes:** `sqlite_autoindex_pulse_post_hides_1`(user_id,post_id)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `post_id INTEGER` · `reason TEXT` · `created_at TEXT` · `updated_at TEXT`

#### `pulse_statuses`
*domain: content · rows: 1 · columns: 12 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `status_type`, `visibility`, `deleted_at`
- **Code vs live:** bot.py `CREATE TABLE` declares 12 columns, live db has 12
- **Indexes:** _none_

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `status_type TEXT` [PERM] · `body TEXT` · `media_ids_json TEXT` · `visibility TEXT` [PERM] · `music_track_id INTEGER` · `live_stream_id INTEGER` · `ai_context_json TEXT` · `created_at TEXT` · `expires_at TEXT` · `deleted_at TEXT` [PERM]

#### `pulse_status_views`
*domain: content · rows: 268 · columns: 8 · PK: `id`*

- **OWNERSHIP:** `viewer_user_id`
- **PERMISSION/VISIBILITY:** `status_id`
- **Code vs live:** bot.py `CREATE TABLE` declares 8 columns, live db has 8
- **Indexes:** `sqlite_autoindex_pulse_status_views_1`(status_id,viewer_user_id)

- **All columns:** `id INTEGER` · `status_id INTEGER` [PERM] · `viewer_user_id INTEGER` [OWNER] · `viewed_at TEXT` · `completed_at TEXT` · `completion_ratio REAL` · `watch_ms INTEGER` · `source TEXT`

#### `pulse_stories`
*domain: content · rows: 0 · columns: 8 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `visibility`, `deleted_at`
- **Code vs live:** bot.py `CREATE TABLE` declares 8 columns, live db has 8
- **Indexes:** _none_

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `media_ids_json TEXT` · `body TEXT` · `visibility TEXT` [PERM] · `created_at TEXT` · `expires_at TEXT` · `deleted_at TEXT` [PERM]

#### `pulse_story_views`
*domain: content · rows: 0 · columns: 4 · PK: `id`*

- **OWNERSHIP:** `viewer_user_id`
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 4 columns, live db has 4
- **Indexes:** _none_

- **All columns:** `id INTEGER` · `story_id INTEGER` · `viewer_user_id INTEGER` [OWNER] · `viewed_at TEXT`

#### `pulse_saved_items`
*domain: content · rows: 3 · columns: 13 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 13 columns, live db has 13
- **Indexes:** `sqlite_autoindex_pulse_saved_items_1`(user_id,content_type,content_id)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `collection_id INTEGER` · `content_type TEXT` · `content_id TEXT` · `title TEXT` · `preview_text TEXT` · `thumbnail_url TEXT` · `media_url TEXT` · `source_url TEXT` · `metadata_json TEXT` · `created_at TEXT` · `updated_at TEXT`

#### `pulse_saved_collections`
*domain: content · rows: 5 · columns: 8 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `is_default`
- **Code vs live:** bot.py `CREATE TABLE` declares 8 columns, live db has 8
- **Indexes:** `sqlite_autoindex_pulse_saved_collections_1`(user_id,slug)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `name TEXT` · `slug TEXT` · `description TEXT` · `is_default INTEGER` [PERM] · `created_at TEXT` · `updated_at TEXT`

#### `pulse_reports`
*domain: security_trust_moderation · rows: 6 · columns: 9 · PK: `id`*

- **OWNERSHIP:** `reporter_user_id`
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 7 columns, live db has 9 (live is a strict superset; +2 added by `add_columns_if_missing`)
- **Indexes:** `idx_pulse_reports_status`(status,created_at)

- **All columns:** `id INTEGER` · `reporter_user_id INTEGER` [OWNER] · `target_type TEXT` · `target_id INTEGER` · `reason TEXT` · `status TEXT` [PERM] · `created_at TEXT` · `details TEXT` · `updated_at TEXT`

#### `pulse_media_assets`
*domain: content · rows: 647 · columns: 20 · PK: `id`*

- **OWNERSHIP:** `owner_user_id`
- **PERMISSION/VISIBILITY:** `public_url`, `processing_status`, `mux_status`
- **Code vs live:** bot.py `CREATE TABLE` declares 16 columns, live db has 20 (live is a strict superset; +4 added by `add_columns_if_missing`)
- **Indexes:** _none_

- **All columns:** `id INTEGER` · `media_id INTEGER` · `owner_user_id INTEGER` [OWNER] · `storage_provider TEXT` · `storage_key TEXT` · `public_url TEXT` [PERM] · `thumbnail_url TEXT` · `poster_url TEXT` · `media_type TEXT` · `mime_type TEXT` · `width INTEGER` · `height INTEGER` · `aspect_ratio REAL` · `processing_status TEXT` [PERM] · `created_at TEXT` · `updated_at TEXT` · `mux_asset_id TEXT` · `mux_playback_id TEXT` · `mux_status TEXT` [PERM] · `playback_url TEXT`

#### `pulse_conversations`
*domain: messaging · rows: 494 · columns: 23 · PK: `id`*

- **OWNERSHIP:** `created_by_user_id`, `group_id`, `owner_user_id`, `business_id`
- **PERMISSION/VISIBILITY:** `is_public`, `status`, `privacy`, `deleted_at`
- **Code vs live:** bot.py `CREATE TABLE` declares 6 columns, live db has 23 (live is a strict superset; +17 added by `add_columns_if_missing`)
- **Indexes:** `idx_pulse_conv_business`(business_id), `idx_pulse_conversations_type_updated`(conversation_type,updated_at), `idx_pulse_conversations_updated`(updated_at), `idx_pulse_conversations_type_activity`(conversation_type,last_activity_at), `idx_pulse_conversations_group`(group_id,conversation_type)

- **All columns:** `id INTEGER` · `conversation_type TEXT` · `created_by_user_id INTEGER` [OWNER] · `created_at TEXT` · `updated_at TEXT` · `last_message_at TEXT` · `group_id INTEGER` [OWNER] · `title TEXT` · `avatar_url TEXT` · `owner_user_id INTEGER` [OWNER] · `is_public INTEGER` [PERM] · `participant_limit INTEGER` · `last_activity_at TEXT` · `status TEXT` [PERM] · `linked_group_id INTEGER` · `linked_space_id TEXT` · `linked_live_id INTEGER` · `description TEXT` · `privacy TEXT` [PERM] · `member_count INTEGER` · `deleted_at TEXT` [PERM] · `business_id TEXT` [OWNER] · `comm_v2_conversation_id INTEGER`

#### `pulse_conversation_participants`
*domain: messaging · rows: 1,243 · columns: 16 · PK: `id`*

- **OWNERSHIP:** `conversation_id`, `user_id`
- **PERMISSION/VISIBILITY:** `role`, `muted`, `archived`, `muted_until`
- **Code vs live:** bot.py `CREATE TABLE` declares 8 columns, live db has 16 (live is a strict superset; +8 added by `add_columns_if_missing`)
- **Indexes:** `idx_pulse_part_user`(user_id), `uq_pulse_conversation_participants_member`(conversation_id,user_id), `idx_pulse_conversation_participants_conversation_only`(conversation_id), `idx_pulse_conversation_participants_user_only`(user_id), `idx_pulse_conversation_participants_conversation`(conversation_id,user_id), `idx_pulse_conversation_participants_user`(user_id,conversation_id)

- **All columns:** `id INTEGER` · `conversation_id INTEGER` [OWNER] · `user_id INTEGER` [OWNER] · `role TEXT` [PERM] · `muted INTEGER` [PERM] · `archived INTEGER` [PERM] · `last_read_at TEXT` · `created_at TEXT` · `muted_until TEXT` [PERM] · `joined_at TEXT` · `left_at TEXT` · `last_seen_at TEXT` · `last_read_message_id INTEGER` · `unread_count INTEGER` · `pinned_at TEXT` · `pinned_rank INTEGER`

#### `pulse_messages`
*domain: messaging · rows: 2,108 · columns: 24 · PK: `id`*

- **OWNERSHIP:** `sender_user_id`, `receiver_user_id`, `conversation_id`
- **PERMISSION/VISIBILITY:** `status`, `deleted_at`, `delivery_status`
- **Code vs live:** bot.py `CREATE TABLE` declares 8 columns, live db has 24 (live is a strict superset; +16 added by `add_columns_if_missing`)
- **Indexes:** `idx_pulse_msg_conv`(conversation_id,id), `uq_pulse_messages_client_id`(conversation_id,sender_user_id,client_message_id), `idx_pulse_messages_client_id`(conversation_id,sender_user_id,client_message_id), `idx_pulse_messages_conversation_created`(conversation_id,created_at), `idx_pulse_messages_conversation_id`(conversation_id,id), `idx_pulse_messages_sender`(sender_user_id,created_at), `idx_pulse_messages_conversation`(conversation_id,created_at)

- **All columns:** `id INTEGER` · `thread_id INTEGER` · `sender_user_id INTEGER` [OWNER] · `receiver_user_id INTEGER` [OWNER] · `body TEXT` · `read_at TEXT` · `created_at TEXT` · `conversation_id INTEGER` [OWNER] · `media_url TEXT` · `message_type TEXT` · `status TEXT` [PERM] · `edited_at TEXT` · `deleted_at TEXT` [PERM] · `thumbnail_url TEXT` · `media_metadata TEXT` · `file_size INTEGER` · `duration_seconds REAL` · `delivery_status TEXT` [PERM] · `reply_to_id INTEGER` · `updated_at TEXT` · `client_message_id TEXT` · `local_created_at TEXT` · `delivered_at TEXT` · `seen_at TEXT`

#### `pulse_message_receipts`
*domain: messaging · rows: 0 · columns: 7 · PK: `id`*

- **OWNERSHIP:** `conversation_id`, `user_id`
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 7 columns, live db has 7
- **Indexes:** `idx_pulse_message_receipts_conversation`(conversation_id,user_id), `sqlite_autoindex_pulse_message_receipts_1`(message_id,user_id,status)

- **All columns:** `id INTEGER` · `message_id INTEGER` · `conversation_id INTEGER` [OWNER] · `user_id INTEGER` [OWNER] · `status TEXT` [PERM] · `created_at TEXT` · `updated_at TEXT`

#### `pulse_message_reactions`
*domain: messaging · rows: 14 · columns: 7 · PK: `id`*

- **OWNERSHIP:** `conversation_id`, `user_id`
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 7 columns, live db has 7
- **Indexes:** `idx_pulse_message_reactions_message`(message_id), `sqlite_autoindex_pulse_message_reactions_1`(message_id,user_id)

- **All columns:** `id INTEGER` · `message_id INTEGER` · `conversation_id INTEGER` [OWNER] · `user_id INTEGER` [OWNER] · `reaction_type TEXT` · `created_at TEXT` · `updated_at TEXT`

#### `pulse_chat_rooms`
*domain: messaging · rows: 8 · columns: 9 · PK: `id`*

- **OWNERSHIP:** `conversation_id`
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 9 columns, live db has 9
- **Indexes:** `idx_pulse_chat_rooms_key`(room_key), `sqlite_autoindex_pulse_chat_rooms_1`(room_key)

- **All columns:** `id INTEGER` · `room_key TEXT` · `name TEXT` · `description TEXT` · `notice TEXT` · `conversation_id INTEGER` [OWNER] · `status TEXT` [PERM] · `created_at TEXT` · `updated_at TEXT`

#### `comm_v2_conversations`
*domain: messaging · rows: 219 · columns: 29 · PK: `id`*

- **OWNERSHIP:** `owner_user_id`, `created_by_user_id`
- **PERMISSION/VISIBILITY:** `public_id`, `privacy`, `visibility`, `status`, `is_discoverable`, `archived_at`, `deleted_at`
- **Code vs live:** no `CREATE TABLE` for this name in `bot.py` (created in `services/` or pre-existing)
- **Indexes:** `idx_comm_v2_conversations_activity`(last_activity_at), `idx_comm_v2_conversations_type`(conversation_type,status), `sqlite_autoindex_comm_v2_conversations_1`(public_id)

- **All columns:** `id INTEGER` · `public_id TEXT` [PERM] · `conversation_type TEXT` · `title TEXT` · `description TEXT` · `avatar_url TEXT` · `owner_user_id INTEGER` [OWNER] · `created_by_user_id INTEGER` [OWNER] · `linked_group_id INTEGER` · `linked_community_id INTEGER` · `linked_channel_id INTEGER` · `linked_live_id INTEGER` · `linked_project_id INTEGER` · `privacy TEXT` [PERM] · `visibility TEXT` [PERM] · `status TEXT` [PERM] · `is_discoverable INTEGER` [PERM] · `participant_limit INTEGER` · `member_count INTEGER` · `last_message_id INTEGER` · `last_message_at TEXT` · `last_activity_at TEXT` · `created_at TEXT` · `updated_at TEXT` · `archived_at TEXT` [PERM] · `deleted_at TEXT` [PERM] · `direct_key TEXT` · `community_id INTEGER` · `channel_id INTEGER`

#### `comm_v2_messages`
*domain: messaging · rows: 1,411 · columns: 19 · PK: `id`*

- **OWNERSHIP:** `conversation_id`, `sender_user_id`
- **PERMISSION/VISIBILITY:** `public_id`, `delivery_status`, `moderation_status`, `wallet_guardian_status`, `deleted_at`
- **Code vs live:** no `CREATE TABLE` for this name in `bot.py` (created in `services/` or pre-existing)
- **Indexes:** `idx_comm_v2_messages_sender`(sender_user_id,created_at), `idx_comm_v2_messages_convo_id`(conversation_id,id), `sqlite_autoindex_comm_v2_messages_1`(public_id)

- **All columns:** `id INTEGER` · `public_id TEXT` [PERM] · `conversation_id INTEGER` [OWNER] · `sender_user_id INTEGER` [OWNER] · `message_type TEXT` · `body TEXT` · `rich_body_json TEXT` · `media_id INTEGER` · `reply_to_message_id INTEGER` · `thread_root_message_id INTEGER` · `client_message_id TEXT` · `delivery_status TEXT` [PERM] · `moderation_status TEXT` [PERM] · `wallet_guardian_status TEXT` [PERM] · `metadata_json TEXT` · `created_at TEXT` · `updated_at TEXT` · `edited_at TEXT` · `deleted_at TEXT` [PERM]

#### `comm_v2_participants`
*domain: messaging · rows: 458 · columns: 17 · PK: `id`*

- **OWNERSHIP:** `conversation_id`, `user_id`
- **PERMISSION/VISIBILITY:** `role`, `membership_state`, `muted_until`, `notifications_level`
- **Code vs live:** no `CREATE TABLE` for this name in `bot.py` (created in `services/` or pre-existing)
- **Indexes:** `idx_comm_v2_participants_convo`(conversation_id,membership_state), `idx_comm_v2_participants_user`(user_id,membership_state)

- **All columns:** `id INTEGER` · `conversation_id INTEGER` [OWNER] · `user_id INTEGER` [OWNER] · `role TEXT` [PERM] · `membership_state TEXT` [PERM] · `joined_at TEXT` · `left_at TEXT` · `muted_until TEXT` [PERM] · `notifications_level TEXT` [PERM] · `last_seen_at TEXT` · `last_read_message_id INTEGER` · `last_read_at TEXT` · `unread_count INTEGER` · `pinned_at TEXT` · `created_at TEXT` · `updated_at TEXT` · `pinned_rank INTEGER`

#### `pulse_live_sessions`
*domain: live_streaming · rows: 793 · columns: 56 · PK: `id`*

- **OWNERSHIP:** `user_id`, `stream_id`
- **PERMISSION/VISIBILITY:** `status`, `moderation_status`, `publish_state`, `recording_status`, `active_scene`, `mux_live_status`, `is_live`, `livekit_egress_status`
- **Code vs live:** bot.py `CREATE TABLE` declares 14 columns, live db has 56 (live is a strict superset; +42 added by `add_columns_if_missing`)
- **Indexes:** `idx_pulse_live_sessions_discovery`(status,viewer_count,started_at), `idx_pulse_live_sessions_user_status`(user_id,status)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `title TEXT` · `category TEXT` · `thumbnail_url TEXT` · `audience TEXT` · `status TEXT` [PERM] · `stream_key TEXT` · `websocket_channel TEXT` · `chat_room_id TEXT` · `viewer_count INTEGER` · `created_at TEXT` · `started_at TEXT` · `ended_at TEXT` · `stream_uuid TEXT` · `stream_id INTEGER` [OWNER] · `ingest_url TEXT` · `rtmp_url TEXT` · `hls_url TEXT` · `webrtc_room_id TEXT` · `provider TEXT` · `protocols_json TEXT` · `studio_url TEXT` · `stream_health TEXT` · `bitrate_kbps INTEGER` · `fps INTEGER` · `chat_conversation_id INTEGER` · `analytics_json TEXT` · `moderation_status TEXT` [PERM] · `updated_at TEXT` · `feed_post_id INTEGER` · `playback_url TEXT` · `preview_url TEXT` · `replay_url TEXT` · `publish_state TEXT` [PERM] · `audio_tracks INTEGER` · `video_tracks INTEGER` · `peak_viewers INTEGER` · `replay_asset_id INTEGER` · `recording_status TEXT` [PERM] · `engagement_score INTEGER` · `active_scene TEXT` [PERM] · `audio_chain_json TEXT` · `destinations_json TEXT` · `recording_error TEXT` · `mux_live_stream_id TEXT` · `mux_stream_key TEXT` · `mux_playback_id TEXT` · `mux_live_status TEXT` [PERM] · `mux_recording_asset_id TEXT` · `mux_recording_playback_id TEXT` · `is_live INTEGER` [PERM] · `livekit_egress_id TEXT` · `livekit_egress_status TEXT` [PERM] · `livekit_egress_error TEXT` · `custom_category TEXT`

#### `pulse_live_streams`
*domain: live_streaming · rows: 122 · columns: 28 · PK: `id`*

- **OWNERSHIP:** `creator_user_id`
- **PERMISSION/VISIBILITY:** `status`, `mux_live_status`, `livekit_egress_status`
- **Code vs live:** bot.py `CREATE TABLE` declares 18 columns, live db has 28 (live is a strict superset; +10 added by `add_columns_if_missing`)
- **Indexes:** `idx_pulse_live_streams_creator_status`(creator_user_id,status), `sqlite_autoindex_pulse_live_streams_1`(stream_uuid)

- **All columns:** `id INTEGER` · `session_id INTEGER` · `creator_user_id INTEGER` [OWNER] · `stream_uuid TEXT` · `title TEXT` · `category TEXT` · `provider TEXT` · `ingest_url TEXT` · `rtmp_url TEXT` · `hls_url TEXT` · `webrtc_room_id TEXT` · `stream_key TEXT` · `stream_key_preview TEXT` · `status TEXT` [PERM] · `started_at TEXT` · `ended_at TEXT` · `created_at TEXT` · `updated_at TEXT` · `mux_live_stream_id TEXT` · `mux_stream_key TEXT` · `mux_playback_id TEXT` · `mux_live_status TEXT` [PERM] · `mux_recording_asset_id TEXT` · `mux_recording_playback_id TEXT` · `livekit_egress_id TEXT` · `livekit_egress_status TEXT` [PERM] · `livekit_egress_error TEXT` · `custom_category TEXT`

#### `pulse_live_viewers`
*domain: live_streaming · rows: 175 · columns: 10 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 7 columns, live db has 10 (live is a strict superset; +3 added by `add_columns_if_missing`)
- **Indexes:** `idx_pulse_live_viewers_live`(live_id,status)

- **All columns:** `id INTEGER` · `live_id INTEGER` · `user_id INTEGER` [OWNER] · `visitor_id TEXT` · `status TEXT` [PERM] · `joined_at TEXT` · `left_at TEXT` · `watch_seconds INTEGER` · `last_seen_at TEXT` · `device_json TEXT`

#### `pulse_live_chat`
*domain: live_streaming · rows: 370 · columns: 10 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `moderation_status`, `deleted_at`
- **Code vs live:** bot.py `CREATE TABLE` declares 6 columns, live db has 10 (live is a strict superset; +4 added by `add_columns_if_missing`)
- **Indexes:** `idx_pulse_live_chat_live`(live_id,created_at)

- **All columns:** `id INTEGER` · `live_id INTEGER` · `user_id INTEGER` [OWNER] · `body TEXT` · `moderation_status TEXT` [PERM] · `created_at TEXT` · `message_type TEXT` · `pinned INTEGER` · `deleted_at TEXT` [PERM] · `metadata_json TEXT`

#### `pulse_live_guests`
*domain: live_streaming · rows: 41 · columns: 24 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `status`, `audio_muted`, `video_enabled`, `guest_role`, `permissions_json`
- **Code vs live:** bot.py `CREATE TABLE` declares 18 columns, live db has 24 (live is a strict superset; +6 added by `add_columns_if_missing`)
- **Indexes:** `idx_pulse_live_guests_user`(live_id,user_id,status), `idx_pulse_live_guests_live_status`(live_id,status,updated_at)

- **All columns:** `id INTEGER` · `live_id INTEGER` · `user_id INTEGER` [OWNER] · `request_id INTEGER` · `status TEXT` [PERM] · `livekit_identity TEXT` · `livekit_room TEXT` · `audio_muted INTEGER` [PERM] · `video_enabled INTEGER` [PERM] · `layout_position INTEGER` · `joined_at TEXT` · `left_at TEXT` · `removed_at TEXT` · `removed_by INTEGER` · `created_at TEXT` · `updated_at TEXT` · `metadata_json TEXT` · `guest_role TEXT` [PERM] · `permissions_json TEXT` [PERM] · `audio_published INTEGER` · `video_published INTEGER` · `participant_sid TEXT` · `participant_joined_at TEXT` · `live_at TEXT`

#### `livestream_access`
*domain: live_streaming · rows: 128 · columns: 7 · PK: `user_id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `status`, `approved_by`, `suspended_reason`
- **Code vs live:** bot.py `CREATE TABLE` declares 7 columns, live db has 7
- **Indexes:** _none_

- **All columns:** `user_id INTEGER` [OWNER] · `status TEXT` [PERM] · `referral_count INTEGER` · `approved_by INTEGER` [PERM] · `suspended_reason TEXT` [PERM] · `created_at TEXT` · `updated_at TEXT`

#### `livestream_eligibility`
*domain: live_streaming · rows: 127 · columns: 7 · PK: `user_id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `status`, `approved_by`, `suspended_reason`
- **Code vs live:** bot.py `CREATE TABLE` declares 7 columns, live db has 7
- **Indexes:** _none_

- **All columns:** `user_id INTEGER` [OWNER] · `status TEXT` [PERM] · `referral_count INTEGER` · `approved_by INTEGER` [PERM] · `suspended_reason TEXT` [PERM] · `created_at TEXT` · `updated_at TEXT`

#### `communication_calls`
*domain: messaging · rows: 0 · columns: 17 · PK: `id`*

- **OWNERSHIP:** `conversation_id`, `created_by_user_id`
- **PERMISSION/VISIBILITY:** `public_id`, `call_scope`, `status`
- **Code vs live:** no `CREATE TABLE` for this name in `bot.py` (created in `services/` or pre-existing)
- **Indexes:** `idx_communication_calls_creator_created`(created_by_user_id,created_at), `idx_communication_calls_conversation_status`(conversation_id,status), `sqlite_autoindex_communication_calls_2`(room_name), `sqlite_autoindex_communication_calls_1`(public_id)

- **All columns:** `id INTEGER` · `public_id TEXT` [PERM] · `conversation_id INTEGER` [OWNER] · `room_name TEXT` · `provider TEXT` · `call_type TEXT` · `call_scope TEXT` [PERM] · `status TEXT` [PERM] · `created_by_user_id INTEGER` [OWNER] · `started_at TEXT` · `answered_at TEXT` · `ended_at TEXT` · `duration_seconds INTEGER` · `end_reason TEXT` · `metadata_json TEXT` · `created_at TEXT` · `updated_at TEXT`

#### `pulse_groups`
*domain: groups_spaces · rows: 410 · columns: 19 · PK: `id`*

- **OWNERSHIP:** `owner_user_id`
- **PERMISSION/VISIBILITY:** `status`, `trust_level`, `deleted_at`, `deleted_by`
- **Code vs live:** bot.py `CREATE TABLE` declares 9 columns, live db has 19 (live is a strict superset; +10 added by `add_columns_if_missing`)
- **Indexes:** `idx_pulse_groups_category_status`(category,status), `idx_pulse_groups_slug_status`(slug,status), `idx_pulse_groups_slug`(slug), `sqlite_autoindex_pulse_groups_1`(slug)

- **All columns:** `id INTEGER` · `owner_user_id INTEGER` [OWNER] · `slug TEXT` · `name TEXT` · `description TEXT` · `group_type TEXT` · `rules TEXT` · `created_at TEXT` · `updated_at TEXT` · `category TEXT` · `cover_image_url TEXT` · `tags_json TEXT` · `status TEXT` [PERM] · `member_count INTEGER` · `trust_level TEXT` [PERM] · `featured INTEGER` · `deleted_at TEXT` [PERM] · `deleted_by INTEGER` [PERM] · `delete_reason TEXT`

#### `pulse_group_members`
*domain: groups_spaces · rows: 410 · columns: 4 · PK: `group_id`, `user_id`*

- **OWNERSHIP:** `group_id`, `user_id`
- **PERMISSION/VISIBILITY:** `role`
- **Code vs live:** bot.py `CREATE TABLE` declares 4 columns, live db has 4
- **Indexes:** `sqlite_autoindex_pulse_group_members_1`(group_id,user_id)

- **All columns:** `group_id INTEGER` [OWNER] · `user_id INTEGER` [OWNER] · `role TEXT` [PERM] · `created_at TEXT`

#### `pulse_group_posts`
*domain: groups_spaces · rows: 10 · columns: 22 · PK: `id`*

- **OWNERSHIP:** `group_id`, `user_id`
- **PERMISSION/VISIBILITY:** `moderation_status`, `visibility`, `status`, `deleted_at`, `deleted_by`
- **Code vs live:** bot.py `CREATE TABLE` declares 5 columns, live db has 22 (live is a strict superset; +17 added by `add_columns_if_missing`)
- **Indexes:** `idx_pulse_group_posts_group`(group_id,created_at)

- **All columns:** `id INTEGER` · `group_id INTEGER` [OWNER] · `user_id INTEGER` [OWNER] · `body TEXT` · `created_at TEXT` · `post_type TEXT` · `title TEXT` · `content TEXT` · `media_url TEXT` · `thumbnail_url TEXT` · `media_type TEXT` · `media_metadata TEXT` · `moderation_status TEXT` [PERM] · `visibility TEXT` [PERM] · `status TEXT` [PERM] · `updated_at TEXT` · `edited_at TEXT` · `deleted_at TEXT` [PERM] · `deleted_by INTEGER` [PERM] · `delete_reason TEXT` · `pinned_at TEXT` · `pinned_by INTEGER`

#### `pulse_group_roles`
*domain: groups_spaces · rows: 407 · columns: 6 · PK: `id`*

- **OWNERSHIP:** `group_id`, `user_id`
- **PERMISSION/VISIBILITY:** `role`
- **Code vs live:** bot.py `CREATE TABLE` declares 6 columns, live db has 6
- **Indexes:** `sqlite_autoindex_pulse_group_roles_1`(group_id,user_id,role)

- **All columns:** `id INTEGER` · `group_id INTEGER` [OWNER] · `user_id INTEGER` [OWNER] · `role TEXT` [PERM] · `granted_by INTEGER` · `created_at TEXT`

#### `pulse_space_members`
*domain: groups_spaces · rows: 2 · columns: 4 · PK: `user_id`, `space_slug`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `role`
- **Code vs live:** bot.py `CREATE TABLE` declares 4 columns, live db has 4
- **Indexes:** `idx_pulse_space_members_user`(user_id), `idx_pulse_space_members_slug_user`(space_slug,user_id), `sqlite_autoindex_pulse_space_members_1`(user_id,space_slug)

- **All columns:** `user_id INTEGER` [OWNER] · `space_slug TEXT` · `role TEXT` [PERM] · `created_at TEXT`

#### `marketplace_sellers`
*domain: marketplace · rows: 2 · columns: 19 · PK: `id`*

- **OWNERSHIP:** `user_id`, `reviewed_by`
- **PERMISSION/VISIBILITY:** `status`, `state_region`, `verification_status`
- **Code vs live:** bot.py `CREATE TABLE` declares 7 columns, live db has 19 (live is a strict superset; +12 added by `add_columns_if_missing`)
- **Indexes:** `idx_marketplace_sellers_user_status`(user_id,status), `sqlite_autoindex_marketplace_sellers_1`(user_id)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `display_name TEXT` · `bio TEXT` · `status TEXT` [PERM] · `created_at TEXT` · `updated_at TEXT` · `seller_type TEXT` · `business_name TEXT` · `website TEXT` · `country TEXT` · `state_region TEXT` [PERM] · `phone TEXT` · `seller_intent_json TEXT` · `verification_status TEXT` [PERM] · `risk_score INTEGER` · `reviewed_by INTEGER` [OWNER] · `reviewed_at TEXT` · `review_notes TEXT`

#### `marketplace_listings`
*domain: marketplace · rows: 21 · columns: 34 · PK: `id`*

- **OWNERSHIP:** `seller_user_id`, `reviewed_by`
- **PERMISSION/VISIBILITY:** `status`, `approval_status`, `safety_flags_json`
- **Code vs live:** bot.py `CREATE TABLE` declares 9 columns, live db has 34 (live is a strict superset; +25 added by `add_columns_if_missing`)
- **Indexes:** `idx_marketplace_listings_category_status`(category,status,created_at)

- **All columns:** `id INTEGER` · `seller_user_id INTEGER` [OWNER] · `title TEXT` · `description TEXT` · `category TEXT` · `price_label TEXT` · `status TEXT` [PERM] · `created_at TEXT` · `updated_at TEXT` · `approval_status TEXT` [PERM] · `safety_score INTEGER` · `safety_flags_json TEXT` [PERM] · `featured INTEGER` · `media_url TEXT` · `reviewed_by INTEGER` [OWNER] · `reviewed_at TEXT` · `short_description TEXT` · `subcategory TEXT` · `tags_json TEXT` · `cover_image_url TEXT` · `gallery_json TEXT` · `video_url TEXT` · `currency TEXT` · `quantity INTEGER` · `delivery_type TEXT` · `product_type TEXT` · `refund_policy TEXT` · `estimated_delivery TEXT` · `seller_notes TEXT` · `digital_version TEXT` · `lesson_count INTEGER` · `duration TEXT` · `difficulty TEXT` · `prerequisites TEXT`

#### `marketplace_orders_placeholder`
*domain: marketplace · rows: 0 · columns: 6 · PK: `id`*

- **OWNERSHIP:** `buyer_user_id`, `seller_user_id`
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 7 columns, live db has 6 — **in code but NOT live:** `amount_cents`
- **Indexes:** _none_

- **All columns:** `id INTEGER` · `buyer_user_id INTEGER` [OWNER] · `seller_user_id INTEGER` [OWNER] · `listing_id INTEGER` · `status TEXT` [PERM] · `created_at TEXT`

#### `marketplace_merchant_applications`
*domain: marketplace · rows: 2 · columns: 35 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `state_region`, `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 26 columns, live db has 35 (live is a strict superset; +9 added by `add_columns_if_missing`)
- **Indexes:** `idx_merchant_applications_user_status`(user_id,status,created_at)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `full_name TEXT` · `display_name TEXT` · `country TEXT` · `state_region TEXT` [PERM] · `email TEXT` · `phone TEXT` · `pulse_username TEXT` · `business_name TEXT` · `seller_type TEXT` · `website TEXT` · `social_links TEXT` · `years_experience TEXT` · `business_description TEXT` · `seller_intent_json TEXT` · `verification_json TEXT` · `safety_answers_json TEXT` · `completeness INTEGER` · `risk_score INTEGER` · `status TEXT` [PERM] · `reviewer_id INTEGER` · `internal_notes TEXT` · `created_at TEXT` · `updated_at TEXT` · `reviewed_at TEXT` · `agreements_json TEXT` · `submitted_at TEXT` · `information_requested_at TEXT` · `information_request_message TEXT` · `withdrawn_at TEXT` · `expires_at TEXT` · `last_autosaved_at TEXT` · `source TEXT` · `decision_reason TEXT`

#### `business_os_mkt_orders`
*domain: marketplace · rows: 0 · columns: 31 · PK: `order_id`*

- **OWNERSHIP:** `buyer_user_id`, `seller_user_id`
- **PERMISSION/VISIBILITY:** `status`, `payout_status`
- **Code vs live:** no `CREATE TABLE` for this name in `bot.py` (created in `services/` or pre-existing)
- **Indexes:** `idx_mkt_orders_seller`(seller_user_id), `idx_mkt_orders_buyer`(buyer_user_id), `sqlite_autoindex_business_os_mkt_orders_1`(order_id)

- **All columns:** `order_id TEXT` · `buyer_user_id TEXT` [OWNER] · `seller_user_id TEXT` [OWNER] · `status TEXT` [PERM] · `currency TEXT` · `subtotal_cents INTEGER` · `total_cents INTEGER` · `platform_fee_bps INTEGER` · `platform_fee_cents INTEGER` · `seller_net_cents INTEGER` · `refunded_cents INTEGER` · `fulfillment_type TEXT` · `tracking_ref TEXT` · `capture_txn_ref TEXT` · `settle_txn_ref TEXT` · `created_at TEXT` · `updated_at TEXT` · `merchandise_gross_cents INTEGER` · `seller_discount_cents INTEGER` · `merchandise_net_cents INTEGER` · `shipping_cents INTEGER` · `tax_cents INTEGER` · `buyer_service_fee_cents INTEGER` · `seller_shipping_credit_cents INTEGER` · `fee_policy_version TEXT` · `fee_base TEXT` · `return_policy_version TEXT` · `listing_policy_version TEXT` · `payout_policy_version TEXT` · `payout_status TEXT` [PERM] · `policy_snapshot_json TEXT`

#### `business_os_mkt_order_items`
*domain: marketplace · rows: 0 · columns: 8 · PK: `id`*

- **OWNERSHIP:** _none — table is not user-scoped; any row-level filter must come from a join_
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** no `CREATE TABLE` for this name in `bot.py` (created in `services/` or pre-existing)
- **Indexes:** `idx_mkt_order_items_order`(order_id)

- **All columns:** `id INTEGER` · `order_id TEXT` · `product_id TEXT` · `title TEXT` · `unit_price_cents INTEGER` · `quantity INTEGER` · `line_total_cents INTEGER` · `created_at TEXT`

#### `business_os_mkt_sellers`
*domain: marketplace · rows: 0 · columns: 6 · PK: `seller_user_id`*

- **OWNERSHIP:** `seller_user_id`
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** no `CREATE TABLE` for this name in `bot.py` (created in `services/` or pre-existing)
- **Indexes:** `sqlite_autoindex_business_os_mkt_sellers_1`(seller_user_id)

- **All columns:** `seller_user_id TEXT` [OWNER] · `status TEXT` [PERM] · `display_name TEXT` · `notes TEXT` · `created_at TEXT` · `updated_at TEXT`

#### `business_os_mkt_products`
*domain: marketplace · rows: 0 · columns: 11 · PK: `product_id`*

- **OWNERSHIP:** `seller_user_id`
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** no `CREATE TABLE` for this name in `bot.py` (created in `services/` or pre-existing)
- **Indexes:** `idx_mkt_products_seller`(seller_user_id), `sqlite_autoindex_business_os_mkt_products_1`(product_id)

- **All columns:** `product_id TEXT` · `seller_user_id TEXT` [OWNER] · `title TEXT` · `description TEXT` · `price_cents INTEGER` · `currency TEXT` · `fulfillment_type TEXT` · `inventory_qty INTEGER` · `status TEXT` [PERM] · `created_at TEXT` · `updated_at TEXT`

#### `escrow_holds`
*domain: marketplace · rows: 2 · columns: 14 · PK: `id`*

- **OWNERSHIP:** `seller_user_id`
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 14 columns, live db has 14
- **Indexes:** `idx_escrow_holds_status_release`(status,release_after), `sqlite_autoindex_escrow_holds_1`(creator_transaction_id,seller_user_id)

- **All columns:** `id INTEGER` · `creator_transaction_id INTEGER` · `seller_user_id INTEGER` [OWNER] · `seller_type TEXT` · `amount_cents INTEGER` · `currency TEXT` · `status TEXT` [PERM] · `hold_reason TEXT` · `release_after TEXT` · `released_at TEXT` · `trace_id TEXT` · `metadata_json TEXT` · `created_at TEXT` · `updated_at TEXT`

#### `seller_payouts`
*domain: marketplace · rows: 0 · columns: 12 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 12 columns, live db has 12
- **Indexes:** _none_

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `seller_type TEXT` · `amount_cents INTEGER` · `currency TEXT` · `status TEXT` [PERM] · `provider TEXT` · `provider_payout_id TEXT` · `transaction_ids_json TEXT` · `failure_reason TEXT` · `created_at TEXT` · `updated_at TEXT`

#### `seller_payout_accounts`
*domain: marketplace · rows: 0 · columns: 15 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `onboarding_status`, `payouts_enabled`, `charges_enabled`, `requirements_json`
- **Code vs live:** bot.py `CREATE TABLE` declares 15 columns, live db has 15
- **Indexes:** `idx_seller_payout_accounts_user_type`(user_id,seller_type), `sqlite_autoindex_seller_payout_accounts_1`(user_id,seller_type)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `seller_type TEXT` · `provider TEXT` · `connected_account_id TEXT` · `onboarding_status TEXT` [PERM] · `payouts_enabled INTEGER` [PERM] · `charges_enabled INTEGER` [PERM] · `missing_requirements_json TEXT` · `last_checked_at TEXT` · `created_at TEXT` · `updated_at TEXT` · `provider_account_id TEXT` · `requirements_json TEXT` [PERM] · `last_synced_at TEXT`

#### `seller_transactions`
*domain: marketplace · rows: 2 · columns: 16 · PK: `id`*

- **OWNERSHIP:** `buyer_user_id`, `seller_user_id`
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 16 columns, live db has 16
- **Indexes:** _none_

- **All columns:** `id INTEGER` · `buyer_user_id INTEGER` [OWNER] · `seller_user_id INTEGER` [OWNER] · `seller_type TEXT` · `item_type TEXT` · `item_id INTEGER` · `amount_cents INTEGER` · `currency TEXT` · `platform_fee_cents INTEGER` · `seller_net_cents INTEGER` · `status TEXT` [PERM] · `stripe_checkout_session_id TEXT` · `stripe_payment_intent_id TEXT` · `metadata_json TEXT` · `created_at TEXT` · `updated_at TEXT`

#### `payout_queue`
*domain: payments_billing · rows: 2 · columns: 16 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `status`, `risk_status`
- **Code vs live:** bot.py `CREATE TABLE` declares 16 columns, live db has 16
- **Indexes:** `idx_payout_queue_user_status`(user_id,seller_type,status,scheduled_for)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `seller_type TEXT` · `wallet_id INTEGER` · `amount_cents INTEGER` · `currency TEXT` · `status TEXT` [PERM] · `scheduled_for TEXT` · `attempts INTEGER` · `provider TEXT` · `provider_reference TEXT` · `risk_status TEXT` [PERM] · `trace_id TEXT` · `metadata_json TEXT` · `created_at TEXT` · `updated_at TEXT`

#### `creator_wallets`
*domain: payments_billing · rows: 3 · columns: 11 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 11 columns, live db has 11
- **Indexes:** `idx_creator_wallets_user_type`(user_id,wallet_type,currency), `sqlite_autoindex_creator_wallets_1`(user_id,wallet_type,currency)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `wallet_type TEXT` · `currency TEXT` · `available_balance_cents INTEGER` · `pending_balance_cents INTEGER` · `lifetime_earnings_cents INTEGER` · `lifetime_fees_cents INTEGER` · `status TEXT` [PERM] · `created_at TEXT` · `updated_at TEXT`

#### `creator_balances`
*domain: payments_billing · rows: 2 · columns: 13 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 13 columns, live db has 13
- **Indexes:** `idx_creator_balances_user_type`(user_id,seller_type,currency), `sqlite_autoindex_creator_balances_1`(user_id,seller_type,currency)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `seller_type TEXT` · `currency TEXT` · `pending_balance_cents INTEGER` · `available_balance_cents INTEGER` · `lifetime_gross_cents INTEGER` · `lifetime_fees_cents INTEGER` · `lifetime_net_cents INTEGER` · `frozen INTEGER` · `freeze_reason TEXT` · `risk_score INTEGER` · `updated_at TEXT`

#### `creator_ledger_entries`
*domain: payments_billing · rows: 4 · columns: 16 · PK: `id`*

- **OWNERSHIP:** `user_id`, `related_user_id`
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 16 columns, live db has 16
- **Indexes:** `idx_creator_ledger_trace`(trace_id), `idx_creator_ledger_source`(source_type,source_id), `idx_creator_ledger_wallet_created`(wallet_id,created_at)

- **All columns:** `id INTEGER` · `wallet_id INTEGER` · `user_id INTEGER` [OWNER] · `related_user_id INTEGER` [OWNER] · `source_type TEXT` · `source_id TEXT` · `entry_type TEXT` · `amount_cents INTEGER` · `currency TEXT` · `status TEXT` [PERM] · `description TEXT` · `provider TEXT` · `provider_reference TEXT` · `trace_id TEXT` · `metadata_json TEXT` · `created_at TEXT`

#### `creator_payouts`
*domain: payments_billing · rows: 0 · columns: 11 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 11 columns, live db has 11
- **Indexes:** `idx_creator_payouts_user_status`(user_id,status,created_at)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `wallet_id INTEGER` · `amount_cents INTEGER` · `currency TEXT` · `provider TEXT` · `provider_payout_id TEXT` · `status TEXT` [PERM] · `failure_reason TEXT` · `created_at TEXT` · `paid_at TEXT`

#### `creator_profiles`
*domain: UNCLASSIFIED · rows: 1 · columns: 11 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `public_player_id`, `verification_status`, `monetization_enabled`
- **Code vs live:** bot.py `CREATE TABLE` declares 11 columns, live db has 11
- **Indexes:** `idx_creator_profiles_user`(user_id), `sqlite_autoindex_creator_profiles_1`(user_id)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `public_player_id TEXT` [PERM] · `display_name TEXT` · `call_sign TEXT` · `verification_status TEXT` [PERM] · `creator_score REAL` · `follower_count INTEGER` · `monetization_enabled INTEGER` [PERM] · `created_at TEXT` · `updated_at TEXT`

#### `platform_fee_rules`
*domain: payments_billing · rows: 3 · columns: 11 · PK: `id`*

- **OWNERSHIP:** _none — table is not user-scoped; any row-level filter must come from a join_
- **PERMISSION/VISIBILITY:** `status`, `active`
- **Code vs live:** bot.py `CREATE TABLE` declares 11 columns, live db has 11
- **Indexes:** `idx_platform_fee_rules_lookup`(seller_type,item_type,active), `sqlite_autoindex_platform_fee_rules_1`(seller_type)

- **All columns:** `id INTEGER` · `seller_type TEXT` · `fee_bps INTEGER` · `currency TEXT` · `status TEXT` [PERM] · `created_at TEXT` · `updated_at TEXT` · `item_type TEXT` · `fee_percent REAL` · `fixed_fee_cents INTEGER` · `active INTEGER` [PERM]

#### `fee_ledger`
*domain: payments_billing · rows: 2 · columns: 12 · PK: `id`*

- **OWNERSHIP:** _none — table is not user-scoped; any row-level filter must come from a join_
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 12 columns, live db has 12
- **Indexes:** `idx_fee_ledger_source`(source_type,source_id,fee_type), `sqlite_autoindex_fee_ledger_1`(source_type,source_id,fee_type)

- **All columns:** `id INTEGER` · `treasury_transaction_id INTEGER` · `source_type TEXT` · `source_id TEXT` · `fee_type TEXT` · `amount_cents INTEGER` · `currency TEXT` · `status TEXT` [PERM] · `provider TEXT` · `provider_reference TEXT` · `trace_id TEXT` · `created_at TEXT`

#### `stripe_events`
*domain: payments_billing · rows: 20 · columns: 11 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 11 columns, live db has 11
- **Indexes:** `sqlite_autoindex_stripe_events_1`(stripe_event_id)

- **All columns:** `id INTEGER` · `stripe_event_id TEXT` · `event_type TEXT` · `user_id INTEGER` [OWNER] · `status TEXT` [PERM] · `error_message TEXT` · `payload_summary TEXT` · `created_at TEXT` · `processed_at TEXT` · `event_id TEXT` · `payload_json TEXT`

#### `payment_records`
*domain: payments_billing · rows: 16 · columns: 18 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 13 columns, live db has 18 (live is a strict superset; +5 added by `add_columns_if_missing`)
- **Indexes:** _none_

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `stripe_event_id TEXT` · `stripe_session_id TEXT` · `stripe_customer_id TEXT` · `stripe_subscription_id TEXT` · `invoice_id TEXT` · `payment_intent_id TEXT` · `amount REAL` · `currency TEXT` · `status TEXT` [PERM] · `payment_type TEXT` · `created_at TEXT` · `manual INTEGER` · `metadata TEXT` · `stripe_payload TEXT` · `email_sent INTEGER` · `pro_activated_at TEXT`

#### `payment_webhook_events`
*domain: payments_billing · rows: 6 · columns: 7 · PK: `id`*

- **OWNERSHIP:** _none — table is not user-scoped; any row-level filter must come from a join_
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 7 columns, live db has 7
- **Indexes:** `idx_payment_webhook_events_type_status`(event_type,status), `sqlite_autoindex_payment_webhook_events_1`(provider_event_id)

- **All columns:** `id INTEGER` · `provider_event_id TEXT` · `event_type TEXT` · `processed_at TEXT` · `status TEXT` [PERM] · `error TEXT` · `raw_json TEXT`

#### `transactions`
*domain: payments_billing · rows: 12 · columns: 15 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 10 columns, live db has 15 (live is a strict superset; +5 added by `add_columns_if_missing`)
- **Indexes:** _none_

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `stripe_event_id TEXT` · `stripe_customer_id TEXT` · `stripe_subscription_id TEXT` · `amount REAL` · `currency TEXT` · `status TEXT` [PERM] · `transaction_type TEXT` · `created_at TEXT` · `manual INTEGER` · `metadata TEXT` · `stripe_payload TEXT` · `email_sent INTEGER` · `pro_activated_at TEXT`

#### `ledger_entries`
*domain: payments_billing · rows: 0 · columns: 9 · PK: `id`*

- **OWNERSHIP:** _none — table is not user-scoped; any row-level filter must come from a join_
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** no `CREATE TABLE` for this name in `bot.py` (created in `services/` or pre-existing)
- **Indexes:** `idx_ledger_entries_txn`(transaction_id), `idx_ledger_entries_account`(account,currency)

- **All columns:** `id INTEGER` · `transaction_id TEXT` · `account TEXT` · `direction TEXT` · `amount_cents INTEGER` · `signed_amount_cents INTEGER` · `currency TEXT` · `entry_type TEXT` · `created_at TEXT`

#### `pulse_ad_accounts`
*domain: ads · rows: 531 · columns: 11 · PK: `id`*

- **OWNERSHIP:** `owner_user_id`
- **PERMISSION/VISIBILITY:** `status`, `verification_status`
- **Code vs live:** bot.py `CREATE TABLE` declares 11 columns, live db has 11
- **Indexes:** `idx_pulse_ad_accounts_owner`(owner_user_id,status)

- **All columns:** `id INTEGER` · `owner_user_id INTEGER` [OWNER] · `business_name TEXT` · `business_email TEXT` · `business_phone TEXT` · `business_website TEXT` · `business_type TEXT` · `status TEXT` [PERM] · `verification_status TEXT` [PERM] · `created_at TEXT` · `updated_at TEXT`

#### `pulse_ad_campaigns`
*domain: ads · rows: 1 · columns: 19 · PK: `id`*

- **OWNERSHIP:** _none — table is not user-scoped; any row-level filter must come from a join_
- **PERMISSION/VISIBILITY:** `status`, `archived_at`, `approved_at`
- **Code vs live:** bot.py `CREATE TABLE` declares 13 columns, live db has 19 (live is a strict superset; +6 added by `add_columns_if_missing`)
- **Indexes:** `idx_pulse_ad_campaigns_delivery`(status,start_at,end_at,priority), `idx_pulse_ad_campaigns_account`(ad_account_id,status)

- **All columns:** `id INTEGER` · `ad_account_id INTEGER` · `campaign_name TEXT` · `objective TEXT` · `status TEXT` [PERM] · `budget_type TEXT` · `daily_budget_cents INTEGER` · `lifetime_budget_cents INTEGER` · `spent_cents INTEGER` · `start_at TEXT` · `end_at TEXT` · `created_at TEXT` · `updated_at TEXT` · `priority INTEGER` · `pacing_mode TEXT` · `archived_at TEXT` [PERM] · `submitted_at TEXT` · `approved_at TEXT` [PERM] · `completed_at TEXT`

#### `pulse_ad_creatives`
*domain: ads · rows: 2 · columns: 23 · PK: `id`*

- **OWNERSHIP:** _none — table is not user-scoped; any row-level filter must come from a join_
- **PERMISSION/VISIBILITY:** `status`, `moderation_status`, `archived_at`, `moderation_history_json`
- **Code vs live:** bot.py `CREATE TABLE` declares 15 columns, live db has 23 (live is a strict superset; +8 added by `add_columns_if_missing`)
- **Indexes:** `idx_pulse_ad_creatives_media`(media_asset_id,thumbnail_asset_id), `idx_pulse_ad_creatives_campaign`(campaign_id,moderation_status,status)

- **All columns:** `id INTEGER` · `ad_account_id INTEGER` · `campaign_id INTEGER` · `creative_type TEXT` · `title TEXT` · `body TEXT` · `media_url TEXT` · `thumbnail_url TEXT` · `destination_url TEXT` · `call_to_action TEXT` · `status TEXT` [PERM] · `moderation_status TEXT` [PERM] · `rejection_reason TEXT` · `created_at TEXT` · `updated_at TEXT` · `archived_at TEXT` [PERM] · `metadata_json TEXT` · `compatibility_json TEXT` · `moderation_history_json TEXT` [PERM] · `media_asset_id INTEGER` · `thumbnail_asset_id INTEGER` · `media_ready INTEGER` · `media_metadata_json TEXT`

#### `pulse_ad_wallets`
*domain: ads · rows: 531 · columns: 13 · PK: `id`*

- **OWNERSHIP:** `account_id`
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 13 columns, live db has 13
- **Indexes:** `idx_pulse_ad_wallets_account`(account_id,currency), `sqlite_autoindex_pulse_ad_wallets_1`(account_id,currency)

- **All columns:** `id INTEGER` · `account_id INTEGER` [OWNER] · `currency TEXT` · `available_balance_cents INTEGER` · `pending_balance_cents INTEGER` · `promotional_credits_cents INTEGER` · `bonus_credits_cents INTEGER` · `refund_credits_cents INTEGER` · `lifetime_funded_cents INTEGER` · `lifetime_spent_cents INTEGER` · `reserved_budget_cents INTEGER` · `created_at TEXT` · `updated_at TEXT`

#### `pulse_ad_wallet_transactions`
*domain: ads · rows: 531 · columns: 12 · PK: `id`*

- **OWNERSHIP:** `account_id`
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 12 columns, live db has 12
- **Indexes:** `idx_pulse_ad_wallet_tx_idempotency`(idempotency_key), `idx_pulse_ad_wallet_tx_account`(account_id,created_at), `sqlite_autoindex_pulse_ad_wallet_transactions_1`(idempotency_key)

- **All columns:** `id INTEGER` · `account_id INTEGER` [OWNER] · `campaign_id INTEGER` · `creative_id INTEGER` · `transaction_type TEXT` · `amount_cents INTEGER` · `currency TEXT` · `status TEXT` [PERM] · `idempotency_key TEXT` · `description TEXT` · `metadata_json TEXT` · `created_at TEXT`

#### `pulse_ad_impressions`
*domain: ads · rows: 5 · columns: 17 · PK: `id`*

- **OWNERSHIP:** `viewer_user_id`
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 12 columns, live db has 17 (live is a strict superset; +5 added by `add_columns_if_missing`)
- **Indexes:** `idx_pulse_ad_impressions_token`(delivery_token_hash,request_fingerprint), `idx_pulse_ad_impressions_viewer`(viewer_user_id,created_at), `idx_pulse_ad_impressions_campaign`(campaign_id,creative_id,created_at)

- **All columns:** `id INTEGER` · `campaign_id INTEGER` · `creative_id INTEGER` · `placement_key TEXT` · `viewer_user_id INTEGER` [OWNER] · `session_id TEXT` · `device_type TEXT` · `viewport TEXT` · `rendered_at TEXT` · `visible_ms INTEGER` · `viewable INTEGER` · `created_at TEXT` · `delivery_token_hash TEXT` · `request_fingerprint TEXT` · `country TEXT` · `language TEXT` · `contextual_category TEXT`

#### `pulse_ad_clicks`
*domain: ads · rows: 5 · columns: 11 · PK: `id`*

- **OWNERSHIP:** `viewer_user_id`
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 9 columns, live db has 11 (live is a strict superset; +2 added by `add_columns_if_missing`)
- **Indexes:** `idx_pulse_ad_clicks_token`(delivery_token_hash,request_fingerprint), `idx_pulse_ad_clicks_campaign`(campaign_id,creative_id,created_at)

- **All columns:** `id INTEGER` · `campaign_id INTEGER` · `creative_id INTEGER` · `placement_key TEXT` · `viewer_user_id INTEGER` [OWNER] · `session_id TEXT` · `clicked_at TEXT` · `destination_url TEXT` · `created_at TEXT` · `delivery_token_hash TEXT` · `request_fingerprint TEXT`

#### `pulse_ad_invoices`
*domain: ads · rows: 0 · columns: 10 · PK: `id`*

- **OWNERSHIP:** `account_id`
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 10 columns, live db has 10
- **Indexes:** `sqlite_autoindex_pulse_ad_invoices_1`(invoice_number)

- **All columns:** `id INTEGER` · `account_id INTEGER` [OWNER] · `invoice_number TEXT` · `amount_cents INTEGER` · `currency TEXT` · `status TEXT` [PERM] · `period_start TEXT` · `period_end TEXT` · `metadata_json TEXT` · `created_at TEXT`

#### `pulse_ad_team_members`
*domain: ads · rows: 0 · columns: 8 · PK: `id`*

- **OWNERSHIP:** `account_id`, `user_id`
- **PERMISSION/VISIBILITY:** `role`, `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 8 columns, live db has 8
- **Indexes:** `idx_pulse_ad_team_members_account`(account_id,user_id,status)

- **All columns:** `id INTEGER` · `account_id INTEGER` [OWNER] · `user_id INTEGER` [OWNER] · `role TEXT` [PERM] · `status TEXT` [PERM] · `invited_email TEXT` · `created_at TEXT` · `updated_at TEXT`

#### `pulse_ad_targeting`
*domain: ads · rows: 0 · columns: 13 · PK: `id`*

- **OWNERSHIP:** _none — table is not user-scoped; any row-level filter must come from a join_
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 13 columns, live db has 13
- **Indexes:** `idx_pulse_ad_targeting_campaign`(campaign_id)

- **All columns:** `id INTEGER` · `campaign_id INTEGER` · `country TEXT` · `language TEXT` · `interests_json TEXT` · `keywords_json TEXT` · `device_type TEXT` · `min_age INTEGER` · `max_age INTEGER` · `premium_audience INTEGER` · `contextual_category TEXT` · `created_at TEXT` · `updated_at TEXT`

#### `business_os_ad_sets`
*domain: ads · rows: 0 · columns: 16 · PK: `ad_set_id`*

- **OWNERSHIP:** `advertiser_user_id`, `created_by`
- **PERMISSION/VISIBILITY:** `status`, `archived_at`
- **Code vs live:** no `CREATE TABLE` for this name in `bot.py` (created in `services/` or pre-existing)
- **Indexes:** `idx_ad_sets_status`(status), `idx_ad_sets_owner`(advertiser_user_id), `idx_ad_sets_campaign`(campaign_id), `sqlite_autoindex_business_os_ad_sets_1`(ad_set_id)

- **All columns:** `ad_set_id TEXT` · `campaign_id TEXT` · `advertiser_user_id TEXT` [OWNER] · `name TEXT` · `status TEXT` [PERM] · `placements_json TEXT` · `audience_json TEXT` · `schedule_start_at TEXT` · `schedule_end_at TEXT` · `budget_allocation_json TEXT` · `review_reason TEXT` · `version INTEGER` · `archived_at TEXT` [PERM] · `created_by TEXT` [OWNER] · `created_at TEXT` · `updated_at TEXT`

#### `portfolio_items`
*domain: crypto · rows: 0 · columns: 9 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 9 columns, live db has 9
- **Indexes:** _none_

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `symbol TEXT` · `coin_name TEXT` · `amount REAL` · `average_buy_price REAL` · `notes TEXT` · `created_at TEXT` · `updated_at TEXT`

#### `manual_portfolio`
*domain: crypto · rows: 6 · columns: 3 · PK: `user_id`, `asset`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 3 columns, live db has 3
- **Indexes:** `sqlite_autoindex_manual_portfolio_1`(user_id,asset)

- **All columns:** `user_id INTEGER` [OWNER] · `asset TEXT` · `amount REAL`

#### `watchlists`
*domain: crypto · rows: 6 · columns: 2 · PK: `user_id`, `asset`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 2 columns, live db has 2
- **Indexes:** `sqlite_autoindex_watchlists_1`(user_id,asset)

- **All columns:** `user_id INTEGER` [OWNER] · `asset TEXT`

#### `watchlist_items`
*domain: crypto · rows: 0 · columns: 5 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 5 columns, live db has 5
- **Indexes:** `sqlite_autoindex_watchlist_items_1`(user_id,symbol)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `symbol TEXT` · `coin_name TEXT` · `created_at TEXT`

#### `user_alert_rules`
*domain: notifications · rows: 0 · columns: 10 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `active`
- **Code vs live:** bot.py `CREATE TABLE` declares 10 columns, live db has 10
- **Indexes:** _none_

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `alert_type TEXT` · `symbol TEXT` · `condition TEXT` · `target_value REAL` · `channels TEXT` · `active INTEGER` [PERM] · `created_at TEXT` · `updated_at TEXT`

#### `alert_rules`
*domain: notifications · rows: 30 · columns: 26 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `active`, `status`, `deleted_at`, `condition_state`, `state_changed_at`
- **Code vs live:** bot.py `CREATE TABLE` declares 18 columns, live db has 26 (live is a strict superset; +8 added by `add_columns_if_missing`)
- **Indexes:** `idx_alert_rules_last_checked`(last_checked_at), `idx_alert_rules_symbol_status`(symbol,status), `idx_alert_rules_status`(status), `idx_alert_rules_user_id`(user_id)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `alert_type TEXT` · `symbol TEXT` · `condition TEXT` · `target_value REAL` · `channels TEXT` · `active INTEGER` [PERM] · `created_at TEXT` · `updated_at TEXT` · `target TEXT` · `threshold_value REAL` · `channels_json TEXT` · `status TEXT` [PERM] · `cooldown_seconds INTEGER` · `last_checked_at TEXT` · `last_triggered_at TEXT` · `trigger_count INTEGER` · `source TEXT` · `source_ref TEXT` · `metadata TEXT` · `deleted_at TEXT` [PERM] · `condition_state TEXT` [PERM] · `trigger_seq INTEGER` · `last_observed_value REAL` · `state_changed_at TEXT` [PERM]

#### `alert_events`
*domain: notifications · rows: 10 · columns: 19 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `status`, `delivery_status`
- **Code vs live:** bot.py `CREATE TABLE` declares 15 columns, live db has 19 (live is a strict superset; +4 added by `add_columns_if_missing`)
- **Indexes:** `idx_alert_events_trigger_key`(trigger_key), `idx_alert_events_user_created`(user_id,created_at)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `watch_rule_id INTEGER` · `alert_type TEXT` · `title TEXT` · `body TEXT` · `status TEXT` [PERM] · `metadata TEXT` · `created_at TEXT` · `alert_rule_id INTEGER` · `symbol TEXT` · `condition TEXT` · `threshold_value REAL` · `observed_value REAL` · `message TEXT` · `notification_id INTEGER` · `delivery_job_id INTEGER` · `delivery_status TEXT` [PERM] · `trigger_key TEXT`

#### `price_history`
*domain: crypto · rows: 0 · columns: 4 · PK: `id`*

- **OWNERSHIP:** _none — table is not user-scoped; any row-level filter must come from a join_
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 4 columns, live db has 4
- **Indexes:** _none_

- **All columns:** `id INTEGER` · `asset TEXT` · `price REAL` · `created_at TEXT`

#### `whale_alerts`
*domain: crypto · rows: 0 · columns: 7 · PK: `id`*

- **OWNERSHIP:** _none — table is not user-scoped; any row-level filter must come from a join_
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 7 columns, live db has 7
- **Indexes:** _none_

- **All columns:** `id INTEGER` · `asset TEXT` · `side TEXT` · `notional_usd REAL` · `price REAL` · `source TEXT` · `created_at TEXT`

#### `simulator_accounts`
*domain: crypto · rows: 0 · columns: 8 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `training_level`
- **Code vs live:** bot.py `CREATE TABLE` declares 8 columns, live db has 8
- **Indexes:** `sqlite_autoindex_simulator_accounts_1`(user_id)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `starting_balance REAL` · `cash_balance REAL` · `training_level INTEGER` [PERM] · `practice_streak INTEGER` · `created_at TEXT` · `updated_at TEXT`

#### `pulse_notifications`
*domain: notifications · rows: 4,150 · columns: 15 · PK: `id`*

- **OWNERSHIP:** `user_id`, `actor_user_id`
- **PERMISSION/VISIBILITY:** `is_read`, `delivery_status`
- **Code vs live:** bot.py `CREATE TABLE` declares 15 columns, live db has 15
- **Indexes:** `idx_pulse_notifications_user_created`(user_id,created_at), `idx_pulse_notifications_user_read_created`(user_id,is_read,created_at)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `type TEXT` · `title TEXT` · `body TEXT` · `target_url TEXT` · `is_read INTEGER` [PERM] · `created_at TEXT` · `actor_user_id INTEGER` [OWNER] · `entity_type TEXT` · `entity_id TEXT` · `deep_link TEXT` · `read_at TEXT` · `delivery_status TEXT` [PERM] · `metadata_json TEXT`

#### `notifications`
*domain: notifications · rows: 1,365 · columns: 35 · PK: `id`*

- **OWNERSHIP:** `user_id`, `recipient_user_id`, `actor_user_id`
- **PERMISSION/VISIBILITY:** `status`, `deleted_at`, `delivery_status`
- **Code vs live:** bot.py `CREATE TABLE` declares 9 columns, live db has 35 (live is a strict superset; +26 added by `add_columns_if_missing`)
- **Indexes:** `idx_notifications_dedupe`(dedupe_key), `idx_notifications_category_priority`(category,priority,created_at), `idx_notifications_user_status_created`(user_id,status,created_at), `idx_notifications_recipient_read_created`(recipient_user_id,read_at,created_at), `idx_notifications_user_created`(user_id,created_at), `idx_notifications_user_type_status`(user_id,notification_type,status,created_at)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `notification_type TEXT` · `title TEXT` · `message TEXT` · `status TEXT` [PERM] · `metadata TEXT` · `created_at TEXT` · `read_at TEXT` · `recipient_user_id INTEGER` [OWNER] · `actor_user_id INTEGER` [OWNER] · `type TEXT` · `category TEXT` · `priority TEXT` · `urgency TEXT` · `body TEXT` · `preview TEXT` · `deep_link TEXT` · `source_type TEXT` · `source_id TEXT` · `icon_url TEXT` · `avatar_url TEXT` · `metadata_json TEXT` · `seen_at TEXT` · `delivered_at TEXT` · `opened_at TEXT` · `failed_at TEXT` · `failure_reason TEXT` · `updated_at TEXT` · `deleted_at TEXT` [PERM] · `dedupe_key TEXT` · `event_id INTEGER` · `delivery_status TEXT` [PERM] · `sound_key TEXT` · `vibration_json TEXT`

#### `pulse_notification_devices`
*domain: notifications · rows: 6 · columns: 12 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `active`
- **Code vs live:** bot.py `CREATE TABLE` declares 12 columns, live db has 12
- **Indexes:** `sqlite_autoindex_pulse_notification_devices_1`(endpoint)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `device_type TEXT` · `provider TEXT` · `endpoint TEXT` · `token_preview TEXT` · `subscription_json TEXT` · `user_agent TEXT` · `active INTEGER` [PERM] · `created_at TEXT` · `updated_at TEXT` · `last_seen_at TEXT`

#### `push_subscriptions`
*domain: notifications · rows: 6 · columns: 14 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `active`, `is_active`
- **Code vs live:** bot.py `CREATE TABLE` declares 8 columns, live db has 14 (live is a strict superset; +6 added by `add_columns_if_missing`)
- **Indexes:** `idx_push_subscriptions_user_active`(user_id,active), `sqlite_autoindex_push_subscriptions_1`(endpoint)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `endpoint TEXT` · `subscription_json TEXT` · `user_agent TEXT` · `active INTEGER` [PERM] · `created_at TEXT` · `updated_at TEXT` · `p256dh TEXT` · `auth TEXT` · `device_type TEXT` · `browser TEXT` · `last_seen_at TEXT` · `is_active INTEGER` [PERM]

#### `user_device_tokens`
*domain: notifications · rows: 6 · columns: 14 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `enabled`
- **Code vs live:** bot.py `CREATE TABLE` declares 14 columns, live db has 14
- **Indexes:** `idx_user_device_tokens_device`(device_id), `idx_user_device_tokens_user_enabled`(user_id,enabled)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `platform TEXT` · `device_id TEXT` · `push_token TEXT` · `push_provider TEXT` · `environment TEXT` · `app_version TEXT` · `device_label TEXT` · `enabled INTEGER` [PERM] · `created_at TEXT` · `updated_at TEXT` · `last_seen_at TEXT` · `revoked_at TEXT`

#### `notification_preferences`
*domain: notifications · rows: 43,682 · columns: 23 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `quiet_hours_enabled`, `muted_users_json`, `muted_conversations_json`, `blocked_users_json`
- **Code vs live:** bot.py `CREATE TABLE` declares 8 columns, live db has 23 (live is a strict superset; +15 added by `add_columns_if_missing`)
- **Indexes:** `sqlite_autoindex_notification_preferences_1`(user_id,category)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `category TEXT` · `in_app INTEGER` · `push INTEGER` · `email INTEGER` · `telegram INTEGER` · `updated_at TEXT` · `sms INTEGER` · `enable_push_notifications INTEGER` · `enable_notification_sound INTEGER` · `enable_notification_vibration INTEGER` · `notification_sound_type TEXT` · `quiet_hours_enabled INTEGER` [PERM] · `quiet_hours_start TEXT` · `quiet_hours_end TEXT` · `sound INTEGER` · `vibration INTEGER` · `lock_screen_preview INTEGER` · `muted_users_json TEXT` [PERM] · `muted_conversations_json TEXT` [PERM] · `blocked_users_json TEXT` [PERM] · `category_rules_json TEXT`

#### `email_logs`
*domain: notifications · rows: 405 · columns: 22 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `status`, `provider_status_code`, `delivery_status`
- **Code vs live:** bot.py `CREATE TABLE` declares 6 columns, live db has 22 (live is a strict superset; +16 added by `add_columns_if_missing`)
- **Indexes:** _none_

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `email TEXT` · `subject TEXT` · `status TEXT` [PERM] · `created_at TEXT` · `email_type TEXT` · `recipient_email TEXT` · `stripe_event_id TEXT` · `stripe_session_id TEXT` · `sent_at TEXT` · `error_message TEXT` · `provider TEXT` · `provider_message_id TEXT` · `metadata TEXT` · `provider_status_code INTEGER` [PERM] · `safe_error_reason TEXT` · `trace_id TEXT` · `retry_count INTEGER` · `delivery_status TEXT` [PERM] · `last_webhook_event TEXT` · `last_webhook_at TEXT`

#### `audit_logs`
*domain: UNCLASSIFIED · rows: 0 · columns: 8 · PK: `id`*

- **OWNERSHIP:** `actor_user_id`
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 8 columns, live db has 8
- **Indexes:** _none_

- **All columns:** `id INTEGER` · `actor_user_id INTEGER` [OWNER] · `actor_type TEXT` · `action TEXT` · `target_type TEXT` · `target_id TEXT` · `metadata TEXT` · `created_at TEXT`

#### `admin_audit_logs`
*domain: admin_rbac · rows: 64 · columns: 9 · PK: `id`*

- **OWNERSHIP:** `admin_user_id`
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 9 columns, live db has 9
- **Indexes:** `idx_admin_audit_logs_admin_created`(admin_user_id,created_at), `idx_admin_audit_logs_created`(created_at)

- **All columns:** `id INTEGER` · `admin_user_id INTEGER` [OWNER] · `admin_email TEXT` · `action TEXT` · `target_type TEXT` · `target_id TEXT` · `metadata TEXT` · `ip_hash TEXT` · `created_at TEXT`

#### `security_events`
*domain: security_trust_moderation · rows: 99 · columns: 8 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 8 columns, live db has 8
- **Indexes:** `idx_security_events_type_created`(event_type,created_at)

- **All columns:** `id INTEGER` · `event_type TEXT` · `user_id INTEGER` [OWNER] · `ip_address TEXT` · `path TEXT` · `status TEXT` [PERM] · `details_json TEXT` · `created_at TEXT`

#### `auth_events`
*domain: identity_auth · rows: 460 · columns: 16 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 16 columns, live db has 16
- **Indexes:** `idx_auth_events_failed_domain`(status,email_domain,created_at), `idx_auth_events_failed_email_hash`(status,email_hash,created_at), `idx_auth_events_failed_ip`(status,ip_address,created_at)

- **All columns:** `id INTEGER` · `event_type TEXT` · `email TEXT` · `user_id INTEGER` [OWNER] · `status TEXT` [PERM] · `details TEXT` · `db_engine TEXT` · `created_at TEXT` · `email_hash TEXT` · `email_domain TEXT` · `severity TEXT` · `ip_address TEXT` · `country TEXT` · `user_agent TEXT` · `device TEXT` · `route TEXT`

#### `moderation_cases`
*domain: security_trust_moderation · rows: 1 · columns: 13 · PK: `id`*

- **OWNERSHIP:** `reporter_user_id`
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 13 columns, live db has 13
- **Indexes:** `idx_moderation_cases_status`(status,priority)

- **All columns:** `id INTEGER` · `target_type TEXT` · `target_id TEXT` · `reporter_user_id INTEGER` [OWNER] · `assigned_admin_id INTEGER` · `status TEXT` [PERM] · `priority TEXT` · `reason TEXT` · `notes TEXT` · `decision TEXT` · `created_at TEXT` · `resolved_at TEXT` · `ai_risk_score INTEGER`

#### `account_restrictions`
*domain: security_trust_moderation · rows: 0 · columns: 9 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `status`, `public_summary`
- **Code vs live:** no `CREATE TABLE` for this name in `bot.py` (created in `services/` or pre-existing)
- **Indexes:** `idx_account_restrictions_status`(status), `idx_account_restrictions_user_id`(user_id)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `restriction_type TEXT` · `status TEXT` [PERM] · `public_summary TEXT` [PERM] · `internal_note TEXT` · `expires_at TEXT` · `created_at TEXT` · `updated_at TEXT`

#### `user_trust_profiles`
*domain: security_trust_moderation · rows: 130 · columns: 13 · PK: `user_id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 13 columns, live db has 13
- **Indexes:** _none_

- **All columns:** `user_id INTEGER` [OWNER] · `trust_score INTEGER` · `creator_score INTEGER` · `influence_score INTEGER` · `safety_score INTEGER` · `risk_score INTEGER` · `invite_score INTEGER` · `education_score INTEGER` · `market_accuracy_score INTEGER` · `scam_hunter_score INTEGER` · `frozen INTEGER` · `created_at TEXT` · `updated_at TEXT`

#### `user_privilege_profiles`
*domain: security_trust_moderation · rows: 133 · columns: 21 · PK: `user_id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `current_level`, `can_go_live`, `can_sell`, `can_teach`, `can_create_groups`, `can_upload_video`, `can_create_space`, `can_host_room`, `can_use_creator_filters`, `moderation_status`
- **Code vs live:** bot.py `CREATE TABLE` declares 10 columns, live db has 21 (live is a strict superset; +11 added by `add_columns_if_missing`)
- **Indexes:** _none_

- **All columns:** `user_id INTEGER` [OWNER] · `trust_score INTEGER` · `current_level TEXT` [PERM] · `can_go_live INTEGER` [PERM] · `can_sell INTEGER` [PERM] · `can_teach INTEGER` [PERM] · `can_create_groups INTEGER` [PERM] · `can_upload_video INTEGER` [PERM] · `created_at TEXT` · `updated_at TEXT` · `can_create_space INTEGER` [PERM] · `can_host_room INTEGER` [PERM] · `can_use_creator_filters INTEGER` [PERM] · `max_video_duration INTEGER` · `max_upload_mb INTEGER` · `posting_limit_per_day INTEGER` · `messaging_restricted INTEGER` · `posting_restricted INTEGER` · `moderation_status TEXT` [PERM] · `manual_override INTEGER` · `updated_by INTEGER`

#### `verification_requests`
*domain: security_trust_moderation · rows: 14,644 · columns: 21 · PK: `id`*

- **OWNERSHIP:** `user_id`, `reviewed_by`
- **PERMISSION/VISIBILITY:** `status`, `appeal_status`
- **Code vs live:** bot.py `CREATE TABLE` declares 8 columns, live db has 21 (live is a strict superset; +13 added by `add_columns_if_missing`)
- **Indexes:** `idx_verification_requests_user`(user_id), `idx_verification_requests_status`(status), `idx_verification_requests_user_id`(user_id)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `verification_type TEXT` · `status TEXT` [PERM] · `notes TEXT` · `reviewed_by INTEGER` [OWNER] · `created_at TEXT` · `reviewed_at TEXT` · `request_payload_json TEXT` · `decision_reason TEXT` · `appeal_of_request_id INTEGER` · `submitted_at TEXT` · `decision_at TEXT` · `updated_at TEXT` · `track TEXT` · `progress_percent INTEGER` · `risk_score INTEGER` · `reviewer_id INTEGER` · `rejection_reason TEXT` · `needs_more_info_reason TEXT` · `appeal_status TEXT` [PERM]

#### `admin_users`
*domain: admin_rbac · rows: 34 · columns: 27 · PK: `id`*

- **OWNERSHIP:** _none — table is not user-scoped; any row-level filter must come from a join_
- **PERMISSION/VISIBILITY:** `role`, `status`, `company_role`, `state`, `locked_until`
- **Code vs live:** bot.py `CREATE TABLE` declares 22 columns, live db has 27 (live is a strict superset; +5 added by `add_columns_if_missing`)
- **Indexes:** `sqlite_autoindex_admin_users_1`(email)

- **All columns:** `id INTEGER` · `full_name TEXT` · `email TEXT` · `phone TEXT` · `password_hash TEXT` · `role TEXT` [PERM] · `status TEXT` [PERM] · `job_title TEXT` · `company_role TEXT` [PERM] · `date_of_birth TEXT` · `address_line1 TEXT` · `address_line2 TEXT` · `city TEXT` · `state TEXT` [PERM] · `zip_code TEXT` · `country TEXT` · `emergency_contact_name TEXT` · `emergency_contact_phone TEXT` · `notes TEXT` · `created_at TEXT` · `updated_at TEXT` · `last_login_at TEXT` · `must_change_password INTEGER` · `password_changed_at TEXT` · `temp_password_created_at TEXT` · `failed_login_count INTEGER` · `locked_until TEXT` [PERM]

#### `admin_roles`
*domain: admin_rbac · rows: 25 · columns: 6 · PK: `id`*

- **OWNERSHIP:** _none — table is not user-scoped; any row-level filter must come from a join_
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 6 columns, live db has 6
- **Indexes:** `sqlite_autoindex_admin_roles_1`(name)

- **All columns:** `id INTEGER` · `name TEXT` · `description TEXT` · `status TEXT` [PERM] · `created_at TEXT` · `updated_at TEXT`

#### `admin_permissions`
*domain: admin_rbac · rows: 40 · columns: 4 · PK: `id`*

- **OWNERSHIP:** _none — table is not user-scoped; any row-level filter must come from a join_
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 4 columns, live db has 4
- **Indexes:** `sqlite_autoindex_admin_permissions_1`(key)

- **All columns:** `id INTEGER` · `key TEXT` · `description TEXT` · `created_at TEXT`

#### `admin_role_permissions`
*domain: admin_rbac · rows: 131 · columns: 4 · PK: `id`*

- **OWNERSHIP:** _none — table is not user-scoped; any row-level filter must come from a join_
- **PERMISSION/VISIBILITY:** `role_name`, `permission_key`
- **Code vs live:** bot.py `CREATE TABLE` declares 4 columns, live db has 4
- **Indexes:** `sqlite_autoindex_admin_role_permissions_1`(role_name,permission_key)

- **All columns:** `id INTEGER` · `role_name TEXT` [PERM] · `permission_key TEXT` [PERM] · `created_at TEXT`

#### `admin_user_roles`
*domain: admin_rbac · rows: 0 · columns: 6 · PK: `id`*

- **OWNERSHIP:** `admin_user_id`
- **PERMISSION/VISIBILITY:** `role_name`, `active`
- **Code vs live:** bot.py `CREATE TABLE` declares 6 columns, live db has 6
- **Indexes:** `sqlite_autoindex_admin_user_roles_1`(admin_user_id,role_name,department_slug)

- **All columns:** `id INTEGER` · `admin_user_id INTEGER` [OWNER] · `role_name TEXT` [PERM] · `department_slug TEXT` · `active INTEGER` [PERM] · `created_at TEXT`

#### `roles`
*domain: admin_rbac · rows: 25 · columns: 6 · PK: `id`*

- **OWNERSHIP:** _none — table is not user-scoped; any row-level filter must come from a join_
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 6 columns, live db has 6
- **Indexes:** `sqlite_autoindex_roles_1`(name)

- **All columns:** `id INTEGER` · `name TEXT` · `description TEXT` · `status TEXT` [PERM] · `created_at TEXT` · `updated_at TEXT`

#### `permissions`
*domain: admin_rbac · rows: 40 · columns: 4 · PK: `id`*

- **OWNERSHIP:** _none — table is not user-scoped; any row-level filter must come from a join_
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 4 columns, live db has 4
- **Indexes:** `sqlite_autoindex_permissions_1`(key)

- **All columns:** `id INTEGER` · `key TEXT` · `description TEXT` · `created_at TEXT`

#### `role_permissions`
*domain: admin_rbac · rows: 139 · columns: 4 · PK: `id`*

- **OWNERSHIP:** _none — table is not user-scoped; any row-level filter must come from a join_
- **PERMISSION/VISIBILITY:** `role_name`, `permission_key`
- **Code vs live:** bot.py `CREATE TABLE` declares 4 columns, live db has 4
- **Indexes:** `sqlite_autoindex_role_permissions_1`(role_name,permission_key)

- **All columns:** `id INTEGER` · `role_name TEXT` [PERM] · `permission_key TEXT` [PERM] · `created_at TEXT`

#### `employees`
*domain: admin_rbac · rows: 0 · columns: 17 · PK: `id`*

- **OWNERSHIP:** _none — table is not user-scoped; any row-level filter must come from a join_
- **PERMISSION/VISIBILITY:** `role`, `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 17 columns, live db has 17
- **Indexes:** `sqlite_autoindex_employees_2`(email), `sqlite_autoindex_employees_1`(employee_id)

- **All columns:** `id INTEGER` · `employee_id TEXT` · `full_name TEXT` · `email TEXT` · `phone TEXT` · `job_title TEXT` · `department_id INTEGER` · `manager_id INTEGER` · `role TEXT` [PERM] · `status TEXT` [PERM] · `start_date TEXT` · `address TEXT` · `date_of_birth TEXT` · `emergency_contact TEXT` · `notes TEXT` · `created_at TEXT` · `updated_at TEXT`

#### `departments`
*domain: admin_rbac · rows: 24 · columns: 7 · PK: `id`*

- **OWNERSHIP:** _none — table is not user-scoped; any row-level filter must come from a join_
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 7 columns, live db has 7
- **Indexes:** `sqlite_autoindex_departments_1`(name)

- **All columns:** `id INTEGER` · `name TEXT` · `description TEXT` · `status TEXT` [PERM] · `created_at TEXT` · `updated_at TEXT` · `slug TEXT`

#### `dashboard_entitlements`
*domain: payments_billing · rows: 0 · columns: 6 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `entitlement_key`, `active`
- **Code vs live:** bot.py `CREATE TABLE` declares 6 columns, live db has 6
- **Indexes:** `sqlite_autoindex_dashboard_entitlements_1`(user_id,entitlement_key)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `entitlement_key TEXT` [PERM] · `active INTEGER` [PERM] · `source TEXT` · `updated_at TEXT`

#### `dashboard_permissions`
*domain: dashboard · rows: 0 · columns: 8 · PK: `id`*

- **OWNERSHIP:** _none — table is not user-scoped; any row-level filter must come from a join_
- **PERMISSION/VISIBILITY:** `role_key`, `entitlement_key`, `access_level`, `locked_reason`
- **Code vs live:** bot.py `CREATE TABLE` declares 8 columns, live db has 8
- **Indexes:** `sqlite_autoindex_dashboard_permissions_1`(module_key,role_key,entitlement_key)

- **All columns:** `id INTEGER` · `module_key TEXT` · `role_key TEXT` [PERM] · `entitlement_key TEXT` [PERM] · `access_level TEXT` [PERM] · `locked_reason TEXT` [PERM] · `created_at TEXT` · `updated_at TEXT`

#### `dashboard_visibility`
*domain: dashboard · rows: 0 · columns: 6 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `visibility_state`
- **Code vs live:** bot.py `CREATE TABLE` declares 6 columns, live db has 6
- **Indexes:** `sqlite_autoindex_dashboard_visibility_1`(user_id,module_key)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `module_key TEXT` · `visibility_state TEXT` [PERM] · `sort_order INTEGER` · `updated_at TEXT`

#### `dashboard_widget_access_rules`
*domain: dashboard · rows: 0 · columns: 15 · PK: `widget_key`*

- **OWNERSHIP:** _none — table is not user-scoped; any row-level filter must come from a join_
- **PERMISSION/VISIBILITY:** `required_role`, `moderator_only`, `free_visible_locked`, `is_active`
- **Code vs live:** bot.py `CREATE TABLE` declares 15 columns, live db has 15
- **Indexes:** `sqlite_autoindex_dashboard_widget_access_rules_1`(widget_key)

- **All columns:** `widget_key TEXT` · `display_name TEXT` · `category TEXT` · `route TEXT` · `api_endpoint TEXT` · `required_role TEXT` [PERM] · `premium_required INTEGER` · `creator_required INTEGER` · `seller_required INTEGER` · `admin_only INTEGER` · `moderator_only INTEGER` [PERM] · `free_visible_locked INTEGER` [PERM] · `sort_order INTEGER` · `is_active INTEGER` [PERM] · `updated_at TEXT`

#### `arena_profiles`
*domain: arena · rows: 67 · columns: 22 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `online_status`, `public_player_id`, `privacy_settings_json`
- **Code vs live:** bot.py `CREATE TABLE` declares 21 columns, live db has 22 (live is a strict superset; +1 added by `add_columns_if_missing`)
- **Indexes:** `idx_arena_profiles_public_player_id`(public_player_id), `sqlite_autoindex_arena_profiles_1`(user_id)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `username TEXT` · `xp INTEGER` · `rank TEXT` · `arena_iq INTEGER` · `streak_count INTEGER` · `country TEXT` · `favorite_asset TEXT` · `online_status TEXT` [PERM] · `discipline_score INTEGER` · `scam_defense_score INTEGER` · `strategy_score INTEGER` · `prediction_accuracy_score INTEGER` · `last_seen_at TEXT` · `created_at TEXT` · `updated_at TEXT` · `public_player_id TEXT` [PERM] · `display_name TEXT` · `avatar_url TEXT` · `privacy_settings_json TEXT` [PERM] · `faction TEXT`

#### `arena_matches`
*domain: arena · rows: 37 · columns: 10 · PK: `id`*

- **OWNERSHIP:** `creator_id`
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 10 columns, live db has 10
- **Indexes:** _none_

- **All columns:** `id INTEGER` · `match_type TEXT` · `creator_id INTEGER` [OWNER] · `opponent_id INTEGER` · `room_id INTEGER` · `status TEXT` [PERM] · `starts_at TEXT` · `ends_at TEXT` · `rules_json TEXT` · `created_at TEXT`

#### `arena_match_participants`
*domain: arena · rows: 40 · columns: 6 · **PK: none declared***

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 6 columns, live db has 6
- **Indexes:** `idx_arena_match_participants_match_user`(match_id,user_id), `sqlite_autoindex_arena_match_participants_1`(match_id,user_id)

- **All columns:** `match_id INTEGER` · `user_id INTEGER` [OWNER] · `fake_balance REAL` · `score INTEGER` · `result_json TEXT` · `joined_at TEXT`

#### `arena_chat_messages`
*domain: arena · rows: 18 · columns: 8 · PK: `id`*

- **OWNERSHIP:** `sender_id`
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 8 columns, live db has 8
- **Indexes:** `idx_arena_chat_messages_thread_created`(thread_id,created_at), `idx_arena_chat_messages_thread_id`(thread_id,id)

- **All columns:** `id INTEGER` · `thread_id INTEGER` · `sender_id INTEGER` [OWNER] · `body TEXT` · `status TEXT` [PERM] · `source_request_id INTEGER` · `created_at TEXT` · `read_at TEXT`

#### `arena_blocks`
*domain: social_graph · rows: 2 · columns: 4 · **PK: none declared***

- **OWNERSHIP:** `blocker_id`, `blocked_id`
- **PERMISSION/VISIBILITY:** `blocked_id`
- **Code vs live:** bot.py `CREATE TABLE` declares 4 columns, live db has 4
- **Indexes:** `idx_arena_blocks_blocker_blocked`(blocker_id,blocked_id), `sqlite_autoindex_arena_blocks_1`(blocker_id,blocked_id)

- **All columns:** `blocker_id INTEGER` [OWNER] · `blocked_id INTEGER` [OWNER] · `reason TEXT` · `created_at TEXT`

#### `pulse_courses`
*domain: education · rows: 0 · columns: 17 · PK: `id`*

- **OWNERSHIP:** `teacher_user_id`
- **PERMISSION/VISIBILITY:** `access_level`, `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 11 columns, live db has 17 (live is a strict superset; +6 added by `add_columns_if_missing`)
- **Indexes:** _none_

- **All columns:** `id INTEGER` · `teacher_user_id INTEGER` [OWNER] · `title TEXT` · `description TEXT` · `category TEXT` · `access_level TEXT` [PERM] · `price_label TEXT` · `status TEXT` [PERM] · `safety_score INTEGER` · `created_at TEXT` · `updated_at TEXT` · `difficulty TEXT` · `language TEXT` · `thumbnail_url TEXT` · `pricing_mode TEXT` · `safety_disclaimer TEXT` · `published_at TEXT`

#### `pulse_student_enrollments`
*domain: education · rows: 0 · columns: 8 · PK: `id`*

- **OWNERSHIP:** `student_user_id`, `teacher_user_id`
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 8 columns, live db has 8
- **Indexes:** `sqlite_autoindex_pulse_student_enrollments_1`(student_user_id,course_id)

- **All columns:** `id INTEGER` · `student_user_id INTEGER` [OWNER] · `teacher_user_id INTEGER` [OWNER] · `course_id INTEGER` · `progress_percent INTEGER` · `status TEXT` [PERM] · `created_at TEXT` · `updated_at TEXT`

#### `pulse_teacher_profiles`
*domain: education · rows: 1 · columns: 19 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `teacher_level`, `status`, `public_slug`
- **Code vs live:** bot.py `CREATE TABLE` declares 19 columns, live db has 19
- **Indexes:** `sqlite_autoindex_pulse_teacher_profiles_1`(user_id)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `application_id INTEGER` · `display_name TEXT` · `bio TEXT` · `expertise TEXT` · `languages TEXT` · `country TEXT` · `timezone TEXT` · `teacher_level TEXT` [PERM] · `status TEXT` [PERM] · `trust_score INTEGER` · `safety_score INTEGER` · `student_count INTEGER` · `course_count INTEGER` · `lesson_count INTEGER` · `public_slug TEXT` [PERM] · `created_at TEXT` · `updated_at TEXT`

#### `education_user_progress`
*domain: education · rows: 0 · columns: 6 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 6 columns, live db has 6
- **Indexes:** `sqlite_autoindex_education_user_progress_1`(user_id,lesson_slug)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `lesson_slug TEXT` · `status TEXT` [PERM] · `score INTEGER` · `updated_at TEXT`

#### `referral_events`
*domain: growth_referral_rewards · rows: 0 · columns: 8 · PK: `id`*

- **OWNERSHIP:** `referrer_user_id`
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 8 columns, live db has 8
- **Indexes:** _none_

- **All columns:** `id INTEGER` · `referral_code TEXT` · `referrer_user_id INTEGER` [OWNER] · `session_id TEXT` · `landing_page TEXT` · `referrer TEXT` · `ip_hash TEXT` · `created_at TEXT`

#### `referral_rewards`
*domain: growth_referral_rewards · rows: 0 · columns: 9 · PK: `id`*

- **OWNERSHIP:** `referrer_user_id`, `referred_user_id`
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 9 columns, live db has 9
- **Indexes:** `sqlite_autoindex_referral_rewards_1`(referrer_user_id,referred_user_id,reward_type)

- **All columns:** `id INTEGER` · `referrer_user_id INTEGER` [OWNER] · `referred_user_id INTEGER` [OWNER] · `referral_code TEXT` · `reward_type TEXT` · `reward_days INTEGER` · `status TEXT` [PERM] · `granted_at TEXT` · `created_at TEXT`

#### `pulse_user_badges`
*domain: security_trust_moderation · rows: 88 · columns: 5 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 5 columns, live db has 5
- **Indexes:** `idx_pulse_user_badges_user`(user_id), `sqlite_autoindex_pulse_user_badges_1`(user_id,badge_key)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `badge_key TEXT` · `granted_by INTEGER` · `created_at TEXT`

#### `pulse_user_privileges`
*domain: security_trust_moderation · rows: 84 · columns: 7 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `enabled`
- **Code vs live:** bot.py `CREATE TABLE` declares 7 columns, live db has 7
- **Indexes:** `idx_pulse_user_privileges_user`(user_id,enabled), `sqlite_autoindex_pulse_user_privileges_1`(user_id,privilege_key)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `privilege_key TEXT` · `enabled INTEGER` [PERM] · `granted_by INTEGER` · `created_at TEXT` · `updated_at TEXT`

#### `pulse_ai_memory`
*domain: ai_undx · rows: 38 · columns: 6 · PK: `id`*

- **OWNERSHIP:** _none — table is not user-scoped; any row-level filter must come from a join_
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 6 columns, live db has 6
- **Indexes:** `sqlite_autoindex_pulse_ai_memory_1`(space_slug)

- **All columns:** `id INTEGER` · `space_slug TEXT` · `memory_json TEXT` · `recent_topics_json TEXT` · `recent_hooks_json TEXT` · `updated_at TEXT`

#### `pulse_ai_user_memory`
*domain: ai_undx · rows: 11 · columns: 9 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `status`, `deleted_at`
- **Code vs live:** no `CREATE TABLE` for this name in `bot.py` (created in `services/` or pre-existing)
- **Indexes:** `idx_pulse_ai_user_memory_user`(user_id,status)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `memory_key TEXT` · `memory_value TEXT` · `source TEXT` · `status TEXT` [PERM] · `created_at TEXT` · `updated_at TEXT` · `deleted_at TEXT` [PERM]

#### `pulse_ai_conversations`
*domain: ai_undx · rows: 7 · columns: 12 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `public_id`, `status`
- **Code vs live:** no `CREATE TABLE` for this name in `bot.py` (created in `services/` or pre-existing)
- **Indexes:** `idx_pulse_ai_conversations_user`(user_id), `sqlite_autoindex_pulse_ai_conversations_1`(public_id)

- **All columns:** `id INTEGER` · `public_id TEXT` [PERM] · `user_id INTEGER` [OWNER] · `title TEXT` · `status TEXT` [PERM] · `pinned_at TEXT` · `last_message_id INTEGER` · `last_message_at TEXT` · `reset_at TEXT` · `metadata_json TEXT` · `created_at TEXT` · `updated_at TEXT`

#### `pulse_ai_messages`
*domain: ai_undx · rows: 138 · columns: 12 · PK: `id`*

- **OWNERSHIP:** `conversation_id`, `user_id`
- **PERMISSION/VISIBILITY:** `role`
- **Code vs live:** no `CREATE TABLE` for this name in `bot.py` (created in `services/` or pre-existing)
- **Indexes:** `idx_pulse_ai_messages_user_created`(user_id,created_at), `idx_pulse_ai_messages_conversation`(conversation_id,id)

- **All columns:** `id INTEGER` · `conversation_id INTEGER` [OWNER] · `user_id INTEGER` [OWNER] · `role TEXT` [PERM] · `body TEXT` · `provider TEXT` · `provider_model TEXT` · `latency_ms INTEGER` · `error_code TEXT` · `correlation_id TEXT` · `metadata_json TEXT` · `created_at TEXT`

#### `pulse_ai_conversation_context_permissions`
*domain: ai_undx · rows: 7 · columns: 9 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `private_context_opt_in`
- **Code vs live:** no `CREATE TABLE` for this name in `bot.py` (created in `services/` or pre-existing)
- **Indexes:** `sqlite_autoindex_pulse_ai_conversation_context_permissions_1`(user_id)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `remember_preferences INTEGER` · `use_pulse_ai_chat_history INTEGER` · `assist_with_messages_when_asked INTEGER` · `improve_from_feedback INTEGER` · `private_context_opt_in INTEGER` [PERM] · `updated_at TEXT` · `created_at TEXT`

#### `pulse_ai_delegated_policies`
*domain: ai_undx · rows: 0 · columns: 13 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** `allowed_actions_json`, `entity_scope_json`, `status`
- **Code vs live:** no `CREATE TABLE` for this name in `bot.py` (created in `services/` or pre-existing)
- **Indexes:** `sqlite_autoindex_pulse_ai_delegated_policies_1`(policy_id)

- **All columns:** `id INTEGER` · `policy_id TEXT` · `user_id INTEGER` [OWNER] · `allowed_actions_json TEXT` [PERM] · `denied_actions_json TEXT` · `entity_scope_json TEXT` [PERM] · `maximum_frequency INTEGER` · `maximum_cost REAL` · `expires_at TEXT` · `revocation_method TEXT` · `status TEXT` [PERM] · `created_at TEXT` · `updated_at TEXT`

#### `ai_action_requests`
*domain: ai_undx · rows: 0 · columns: 17 · PK: `id`*

- **OWNERSHIP:** `requested_by`
- **PERMISSION/VISIBILITY:** `status`, `approved_by`, `approved_at`
- **Code vs live:** bot.py `CREATE TABLE` declares 17 columns, live db has 17
- **Indexes:** `idx_ai_action_requests_approval_required`(approval_required), `idx_ai_action_requests_status`(status)

- **All columns:** `id INTEGER` · `recommendation_id INTEGER` · `action_type TEXT` · `target_type TEXT` · `target_id TEXT` · `payload_json TEXT` · `status TEXT` [PERM] · `approval_required INTEGER` · `approved_by INTEGER` [PERM] · `approved_at TEXT` [PERM] · `requested_by INTEGER` [OWNER] · `executed_by INTEGER` · `executed_at TEXT` · `result_json TEXT` · `error_message TEXT` · `created_at TEXT` · `updated_at TEXT`

#### `ai_action_audit_logs`
*domain: ai_undx · rows: 0 · columns: 13 · PK: `id`*

- **OWNERSHIP:** `requested_by`
- **PERMISSION/VISIBILITY:** `approved_by`, `status`
- **Code vs live:** bot.py `CREATE TABLE` declares 13 columns, live db has 13
- **Indexes:** _none_

- **All columns:** `id INTEGER` · `recommendation_id INTEGER` · `action_type TEXT` · `target_type TEXT` · `target_id TEXT` · `requested_by INTEGER` [OWNER] · `approved_by INTEGER` [PERM] · `executed_by INTEGER` · `before_json TEXT` · `after_json TEXT` · `status TEXT` [PERM] · `error_message TEXT` · `created_at TEXT`

#### `business_os_undx_permissions`
*domain: ai_undx · rows: 0 · columns: 14 · PK: `permission_id`*

- **OWNERSHIP:** `org_id`
- **PERMISSION/VISIBILITY:** `permission_id`, `scope_ref`, `active`
- **Code vs live:** no `CREATE TABLE` for this name in `bot.py` (created in `services/` or pre-existing)
- **Indexes:** `uq_undx_permission_source_ref`(source,external_ref), `idx_undx_permission_action`(org_id,action_type,active), `idx_undx_permission_actor`(org_id,actor,active), `sqlite_autoindex_business_os_undx_permissions_1`(permission_id)

- **All columns:** `permission_id TEXT` [PERM] · `org_id TEXT` [OWNER] · `actor TEXT` · `action_type TEXT` · `effect TEXT` · `scope_ref TEXT` [PERM] · `max_risk TEXT` · `active INTEGER` [PERM] · `priority INTEGER` · `source TEXT` · `external_ref TEXT` · `expires_at TEXT` · `meta_json TEXT` · `created_at TEXT`

#### `business_os_undx_confirmations`
*domain: ai_undx · rows: 0 · columns: 10 · PK: `confirmation_id`*

- **OWNERSHIP:** `org_id`
- **PERMISSION/VISIBILITY:** `status`
- **Code vs live:** no `CREATE TABLE` for this name in `bot.py` (created in `services/` or pre-existing)
- **Indexes:** `idx_undx_confirmation_request`(request_id,status), `sqlite_autoindex_business_os_undx_confirmations_1`(confirmation_id)

- **All columns:** `confirmation_id TEXT` · `org_id TEXT` [OWNER] · `request_id TEXT` · `actor TEXT` · `status TEXT` [PERM] · `payload_hash TEXT` · `expires_at TEXT` · `confirmed_at TEXT` · `meta_json TEXT` · `created_at TEXT`

#### `analytics_events`
*domain: analytics_telemetry · rows: 3,159 · columns: 12 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 12 columns, live db has 12
- **Indexes:** _none_

- **All columns:** `id INTEGER` · `session_id TEXT` · `user_id INTEGER` [OWNER] · `event_name TEXT` · `page_url TEXT` · `referrer TEXT` · `device_type TEXT` · `browser TEXT` · `ip_hash TEXT` · `country TEXT` · `metadata TEXT` · `created_at TEXT`

#### `engagement_events`
*domain: analytics_telemetry · rows: 0 · columns: 5 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 5 columns, live db has 5
- **Indexes:** _none_

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `feature TEXT` · `query TEXT` · `created_at TEXT`

#### `pulse_creator_analytics`
*domain: analytics_telemetry · rows: 0 · columns: 7 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 7 columns, live db has 7
- **Indexes:** `sqlite_autoindex_pulse_creator_analytics_1`(user_id,metric_key,captured_at)

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `metric_key TEXT` · `metric_value REAL` · `metric_json TEXT` · `source TEXT` · `captured_at TEXT`

#### `visitor_logs`
*domain: analytics_telemetry · rows: 61,731 · columns: 13 · PK: `id`*

- **OWNERSHIP:** `user_id`
- **PERMISSION/VISIBILITY:** _none_
- **Code vs live:** bot.py `CREATE TABLE` declares 7 columns, live db has 13 (live is a strict superset; +6 added by `add_columns_if_missing`)
- **Indexes:** _none_

- **All columns:** `id INTEGER` · `user_id INTEGER` [OWNER] · `session_id TEXT` · `ip_address TEXT` · `user_agent TEXT` · `path TEXT` · `timestamp TEXT` · `referrer TEXT` · `device_type TEXT` · `browser TEXT` · `os TEXT` · `country TEXT` · `city TEXT`

---

## 4. Relationships

Every arrow below is a **convention, not a constraint** — see Section 5. The parenthesised column is the actual live column that carries the link. Row counts are from the live db so you can tell live paths from aspirational ones.

### 4.1 User → content → engagement → analytics

```
users (1,357)
  └─ creates → pulse_posts (1,140)              pulse_posts.user_id
       ├─ has → pulse_comments (38)             pulse_comments.post_id  · author = pulse_comments.user_id
       │          └─ self-nests →               pulse_comments.parent_comment_id
       ├─ has → pulse_reactions (10)            pulse_reactions.post_id · actor = user_id
       ├─ has → pulse_post_views (114)          pulse_post_views.post_id · user_id OR visitor_id (anonymous)
       ├─ has → pulse_post_saves (2)            pulse_post_saves.post_id · user_id · collection_name
       ├─ has → pulse_post_hides                pulse_post_hides.post_id · user_id
       └─ reposts → pulse_posts                 pulse_posts.repost_of_post_id
  └─ creates → pulse_reels (130)                pulse_reels.user_id
       └─ backed by → pulse_posts               pulse_reels.post_id      (a reel is a post + reel metadata)
  └─ creates → pulse_statuses / pulse_stories
       └─ viewed by → pulse_status_views / pulse_story_views
  └─ uploads → pulse_media_assets               .user_id  (media_ids_json on posts points here by id)
  └─ rolls up into → pulse_creator_analytics (0)   .user_id · metric_key
  └─ emits → analytics_events (3,159)           .user_id (nullable) + .session_id for anonymous
  └─ emits → engagement_events (0)              .user_id · feature
```

Two things to note. `pulse_posts.media_ids_json` is a **JSON array of ids**, not a join column — media ownership cannot be checked with SQL alone. And `pulse_post_views` / `pulse_ad_impressions` / `analytics_events` accept a **null `user_id` with a `visitor_id` / `session_id` instead**, so "filter by user_id" silently drops anonymous rows rather than erroring.

### 4.2 User → presence / group / live → content

There is no `pulse_pages` in this database (Section 1.3). The equivalent ownership context is `pulse_groups`:

```
users
  └─ owns → pulse_groups (410)                  pulse_groups.owner_user_id
       ├─ membership → pulse_group_members (410) (group_id, user_id, role)   ← role is the permission key
       └─ contains → pulse_group_posts (10)      .group_id · .user_id · .visibility · .moderation_status
  └─ joins → pulse_space_members (2)            (user_id, space_slug, role)  ← space is a slug string, no spaces table
  └─ hosts → pulse_live_sessions (793)          pulse_live_sessions.user_id · .audience · .status
       ├─ watched by → pulse_live_viewers (175)  .live_id · .user_id OR .visitor_id
       ├─ chat → pulse_live_chat (370)           .live_id · .user_id · .moderation_status
       ├─ co-hosts → pulse_live_guests (41)      .live_id · .user_id · .guest_role · .permissions_json
       ├─ gated by → livestream_access / livestream_eligibility   (.user_id)
       └─ surfaces as → pulse_posts              pulse_live_sessions.feed_post_id ↔ pulse_posts.live_session_id
```

`pulse_live_sessions` also carries `chat_conversation_id` → `pulse_conversations.id`, and `pulse_conversations.linked_live_id` points back. Two directions, no constraint keeping them agreeing.

### 4.3 Messaging (three parallel stacks)

```
STACK A (legacy, 2,108 messages)
users → pulse_conversations (494)               .created_by_user_id · .owner_user_id · .is_public · .privacy
      → pulse_conversation_participants (1,243)  (conversation_id, user_id, role, muted, archived)
      → pulse_messages (2,108)                   .conversation_id · .sender_user_id · .receiver_user_id
                                                 (also .thread_id — an older parallel key)
      → pulse_message_receipts (0)               .message_id · .user_id · .status

STACK B (comm_v2, live, DDL not in repo — 1,411 messages)
users → comm_v2_conversations (219)             .owner_user_id · .created_by_user_id · .privacy · .visibility
      → comm_v2_participants (458)               .conversation_id · .user_id · .role · .membership_state
      → comm_v2_messages (1,411)                 .conversation_id · .sender_user_id · .moderation_status
      → comm_v2_read_receipts / _reactions / _attachments / _blocks / _typing / _presence

BRIDGE: pulse_conversations.comm_v2_conversation_id → comm_v2_conversations.id
```

**Read-authorisation rule for both stacks:** a message is readable iff the requester has a live row in the participants table for that `conversation_id` (`pulse_conversation_participants` with `left_at IS NULL`, or `comm_v2_participants` with `membership_state`). `pulse_messages.sender_user_id`/`receiver_user_id` are *not* sufficient — group conversations have neither pointing at most legitimate readers. Any agent that filters messages by sender/receiver will both leak group messages and hide legitimate ones.

### 4.4 Social graph

```
users → follows       → pulse_follows (3)       (follower_user_id → followed_user_id)   [directional]
users → friends       → pulse_friends (2)       (user_id, friend_user_id, status)        [status-gated]
users → friendships   → pulse_friendships (4)   (user_id, friend_user_id)                [no status column]
users → friend req.   → pulse_friend_requests
users → blocks        → blocked_users (2)       (blocker_user_id → blocked_user_id)
users → mutes         → pulse_muted_users  AND  pulse_user_mutes                          [two tables]
```

`pulse_friends` and `pulse_friendships` are two independent representations of the same relation with different row counts (2 vs 4) — they are not kept in sync. Same for the two mute tables.

### 4.5 Buyer → order → payment → payout → seller

The commercial path is split across three generations. Following the money as it exists in the live db:

```
users
 └─ applies as seller → marketplace_sellers (2)          .user_id · .status · .verification_status
        └─ workflow  → seller_application_status_history / _notes / _assignments   (live-only tables)
 └─ lists            → marketplace_listings (21)         .seller_user_id · .status · .approval_status
 └─ buys             → seller_transactions (2)           .buyer_user_id → .seller_user_id
                                                          .item_type + .item_id (polymorphic, untyped)
                                                          .amount_cents / .platform_fee_cents / .seller_net_cents
                                                          .stripe_checkout_session_id / .stripe_payment_intent_id
        └─ funds held→ escrow_holds (2)                  .creator_transaction_id → seller_transactions.id
                                                          .seller_user_id · .status · .release_after
        └─ fee split → fee_ledger (2)                    .source_type + .source_id (polymorphic)
                                                          rate from platform_fee_rules (3)  .seller_type · .fee_bps
        └─ credited  → creator_wallets (3)               .user_id · .available_balance_cents
              └─ line → creator_ledger_entries (4)       .wallet_id · .user_id · .related_user_id
                                                          .source_type + .source_id (polymorphic)
              └─ out  → payout_queue (2)                 .user_id · .wallet_id · .status · .risk_status
                    → seller_payouts (0) / creator_payouts (0)   .user_id · .provider_payout_id

PARALLEL, EMPTY, NEWER: business_os_mkt_sellers (0) → business_os_mkt_products (0)
                        → business_os_mkt_orders (0) .buyer_user_id / .seller_user_id / .payout_status
                        → business_os_mkt_order_items (0) .order_id → .product_id
CODE-ONLY, NON-EXISTENT: marketplace_orders, marketplace_cart_items, marketplace_returns, …
```

The critical structural fact: **the buyer↔seller link is `seller_transactions`, and every downstream step reaches it through untyped polymorphic pointers** (`source_type`/`source_id`, `item_type`/`item_id`, `creator_transaction_id`). A ledger entry cannot be resolved to its order without knowing the `source_type` convention, and nothing in the database enforces that the pair is valid.

### 4.6 Advertiser → ad account → wallet → campaign → creative → impression/click → invoice

```
users
 └─ owns → pulse_ad_accounts (531)              .owner_user_id · .status · .verification_status
      ├─ staffed by → pulse_ad_team_members (0)  .account_id · .user_id · .role · .status
      │                    ← THIS is the delegated-access table, and it is EMPTY
      ├─ funded by  → pulse_ad_wallets (531)     .account_id  (1:1 with account)
      │      └─ ledger → pulse_ad_wallet_transactions (531)  .account_id · .campaign_id · .creative_id
      │                                                       .idempotency_key · .transaction_type
      ├─ billed via → pulse_ad_invoices (0)      .account_id · .period_start/.period_end · .status
      └─ runs → pulse_ad_campaigns (1)           .ad_account_id · .status · .daily_budget_cents · .spent_cents
             ├─ targeting → pulse_ad_targeting (0)   .campaign_id · .premium_audience · .min_age/.max_age
             ├─ ad sets   → business_os_ad_sets (0)  .campaign_id · .advertiser_user_id · .status
             │              (pulse_ad_adsets is code-only and does not exist)
             └─ creatives → pulse_ad_creatives (2)   .ad_account_id · .campaign_id
                                                     .status · .moderation_status · .rejection_reason
                    ├─ served → pulse_ad_impressions (5)  .campaign_id · .creative_id · .viewer_user_id
                    │                                      .delivery_token_hash · .viewable · .visible_ms
                    └─ clicked→ pulse_ad_clicks (5)       .campaign_id · .creative_id · .viewer_user_id
```

Note the ownership hop: **an ad account's owner is on `pulse_ad_accounts.owner_user_id`, and nothing below that level repeats it.** Campaigns carry `ad_account_id`, creatives carry `ad_account_id` *and* `campaign_id`, impressions and clicks carry only `campaign_id`/`creative_id`. So authorising an advertiser to read their own impression data requires a **three-table join back up to `pulse_ad_accounts`** — and `pulse_ad_impressions.viewer_user_id` is the *audience member*, an unrelated third party whose id must never be exposed to the advertiser. This is the single most misleading `[OWNER]`-tagged column in the schema.

`business_os_ad_sets.advertiser_user_id` denormalises the owner back down, but only in that one empty table.

### 4.7 User → entitlement → feature access

Five overlapping entitlement tables and four overlapping subscription tables:

```
users
 ├─ subscriptions (160)              .user_id · .plan · .plan_key · .status · .pro_expires_at   ← the populated one
 ├─ user_subscriptions (1)           .user_id · .plan_key · .provider_status · .cancel_at_period_end
 ├─ pulse_subscriptions (0)          .user_id · .plan_key · .status
 └─ subscription_plans (3)           .plan_key ← joined by string, no id relation

users
 ├─ premium_entitlements (179)       .user_id · .entitlement_key · .status · .source · .ends_at   ← the populated one
 ├─ user_entitlements (13)           .user_id · .entitlement_key · .status · .starts_at/.expires_at
 ├─ pulse_premium_entitlements (13)  .user_id · .entitlement_key · .granted_by · .expires_at
 ├─ dashboard_entitlements (0)       .user_id · .entitlement_key · .active
 └─ business_os_ent_grants (0)       .subject_type + .subject_id (polymorphic!) · .entitlement_key
                                      .grace_until · .limit_value · .limit_period · .region · .platform

DENORMALISED COPY on users itself:
   users.is_pro, .pro_active, .plan, .subscription_plan, .subscription_status, .premium_status,
   .premium_expires_at, .lifetime_premium, .trial_status, .trial_used, .pro_expires_at
```

`premium_entitlements` (179 rows) and `user_entitlements` (13 rows) have nearly identical shapes but differ in one column name — `ends_at` vs `expires_at`. Note also that `premium_entitlements` uses `ends_at` while `pulse_premium_entitlements` uses `expires_at`; a query written against one and pointed at the other returns rows with a missing expiry rather than an error.

### 4.8 Admin, roles and privileges

```
admin_users (34)  .role (a plain string on the row)  · .status · .locked_until · .must_change_password
   └─ admin_user_roles (0)  .admin_user_id · .role_name · .department_slug · .active     ← the join table is EMPTY
admin_roles (25) ──┐
roles (25) ────────┤ two identical role tables
admin_role_permissions (131) ──┐
role_permissions (139) ────────┤ two near-identical grant tables, 8 rows apart
permissions (40)  .key
pulse_user_privileges (84)  .user_id · .privilege_key · .enabled · .granted_by   ← end-user (not admin) grants
users.is_super_user, users.trust_level, users.access_enabled, users.login_enabled  ← denormalised flags
```

Effective admin authority today comes from **`admin_users.role` as a string**, because the normalised join table has zero rows. The 8-row divergence between `role_permissions` (139) and `admin_role_permissions` (131) means the two systems would grant differently; which one is consulted depends on the code path.

---

## 5. Constraints and integrity

### 5.1 What actually exists

| Constraint kind | Count in live db | Notes |
|---|---|---|
| PRIMARY KEY | 760 of 775 tables | Nearly all are `INTEGER PRIMARY KEY AUTOINCREMENT`; a few are natural keys (`business_os_mkt_orders.order_id`, `business_os_mkt_sellers.seller_user_id`). |
| UNIQUE index | 449 across 390 tables | Includes `sqlite_autoindex_*` implicit indexes from inline `UNIQUE(...)` table constraints. |
| Non-unique index | 623 | 363 `CREATE INDEX` statements live in `bot.py`; the rest come from service modules. |
| CHECK | not enforced in practice | SQLite records them in `sqlite_master` text only; none were found in the sampled DDL. |
| NOT NULL | sparse | Most columns are nullable, including many ownership columns. |
| **FOREIGN KEY** | **0** | See below. |
| VIEW / TRIGGER | **0 / 0** | No database-level derivation or cascade logic anywhere. |

### 5.2 The central finding: there is no referential integrity

This is stated four independent ways because it is the most consequential fact in this map.

1. **`PRAGMA foreign_key_list(t)` returns an empty list for all 776 tables.** Not "mostly empty" — the total FK count across the entire database is zero.
2. **`bot.py` contains 0 occurrences of `FOREIGN KEY` and 0 of `ON DELETE`.** It contains 8 occurrences of the string `REFERENCES`, and all 8 are false positives: seven are UNDX knowledge-graph UI strings (`'CREATED_FROM · REFERENCES · DEPENDS_ON'` at lines 56861, 59789, 64227, 64570, 68632, 68683, 68761) and one is the log label `PULSE_REGION_PREFERENCES_FAILED` at line 6656.
3. **`PRAGMA foreign_keys = ON` is never issued.** `services/db.py:836-841` sets `busy_timeout`, `journal_mode=WAL` and `synchronous=NORMAL` on every SQLite connection, and nothing else. SQLite defaults FK enforcement to *off*, so even if constraints were declared they would not fire.
4. **The only real FK declarations in the repo are in `migrations/*.sql`, and those files are never executed.** For example `migrations/pulse_ai_messenger.sql:25` declares `conversation_id BIGINT NOT NULL REFERENCES pulse_ai_conversations(id) ON DELETE CASCADE`, and `migrations/pulsesoc_communications_engine.sql:33` declares `call_id INTEGER NOT NULL REFERENCES communication_calls(id) ON DELETE CASCADE`. Neither reached the database: the live `communication_call_events` is `CREATE TABLE communication_call_events (id INTEGER PRIMARY KEY AUTOINCREMENT, call_id INTEGER, user_id INTEGER, event_type TEXT, event_payload_json TEXT, created_at TEXT)` — no FK, and `call_id` is nullable. `grep -rn "migrations/" --include=*.py` finds these files referenced only by audit scripts (e.g. `scripts/pulse_ai_intelligence_upgrade_audit.py:37`), and `executescript` appears **0** times in `bot.py` and `services/`. **The `migrations/` directory is documentation that describes a schema the application does not have.**

**What this means operationally.** Deleting a user does not cascade; their posts, messages, ledger entries and ad impressions remain with a dangling `user_id`. Nothing prevents an INSERT with a `user_id` that does not exist, a `conversation_id` for a deleted conversation, or an `order_id` pointing at the empty `business_os_mkt_orders`. Orphan rows are therefore expected, not exceptional — and an orphan row with a stale `user_id` that has since been reissued is a genuine cross-tenant exposure path.

**What this means for an agent answering questions.** No join can be assumed to be total. `COUNT(*)` on a child table is not a reliable proxy for parent activity. And critically: **the database will never reject a query that omits the tenancy filter.** There is no row-level security, no policy layer, no view layer. Data isolation in this system is *entirely* a property of the application code that constructs each SQL string.

### 5.3 Where integrity is enforced instead — the application-level substitutes

Evidence that the codebase knowingly substitutes application logic for database constraints:

* **`AUTO_PK_TABLES`** (`services/db.py:143`, 354 entries) exists purely to paper over a portability difference in returning generated ids — the layer that would naturally own constraints instead owns SQL string rewriting (`services/db.py:658-659`).
* **`add_column_if_missing` / `add_columns_if_missing`** (`bot.py:104350`) is the entire migration strategy: it `PRAGMA table_info`s, adds the column if absent, and **swallows every exception**, calling `_rollback_failed_migration` and returning `False`. A failed migration logs a warning and the app boots normally with a missing column. 117 call sites in `bot.py`. This is why live tables have far more columns than their `CREATE TABLE` text, and why a column can be silently absent in one environment and present in another.
* **`CREATE TABLE IF NOT EXISTS` everywhere** — schema creation is idempotent-by-suppression. A table whose definition changed will never be altered to match; the old shape survives.
* **Optional route packs are registered inside `except Exception` blocks** (per project notes), so a subsystem whose tables are missing fails to register rather than crashing — which is exactly how `marketplace_orders` and `pulse_pages` can be absent from the database without anything visibly breaking.
* **Polymorphic pointers instead of typed relations** — `source_type`/`source_id` (`fee_ledger`, `creator_ledger_entries`, `business_os_ent_grants` via `subject_type`/`subject_id`), `item_type`/`item_id` (`seller_transactions`), `entity_type`/`entity_id` (`pulse_notifications`), `target_type`/`target_id` (audit tables). These are unindexable as relations and uncheckable by definition.
* **JSON blobs carrying relational data** — `media_ids_json`, `transaction_ids_json`, `permissions_json`, `audience_json`, `policy_snapshot_json`, `metadata_json`. Anything inside them is invisible to SQL-level authorisation.
* **Idempotency by convention** — `pulse_ad_wallet_transactions.idempotency_key` and `pulse_messages.client_message_id` exist, but see 5.4 for whether they are actually unique.

### 5.4 The concrete integrity risks, ranked

1. **Zero foreign keys + zero row-level security.** Every isolation guarantee is a `WHERE` clause someone remembered to write. A single omitted predicate is a cross-user data leak with no second line of defence.
2. **237 of the 467 ownership-bearing tables have no index led by the ownership column** (127 of them already hold rows — full list in 5.5). Beyond the performance cost, this is a strong signal that those tables were never *designed* to be queried per-user, and are more likely to be read with a broad filter.
3. **Ambiguous duplicate table families.** An agent that picks the wrong member of a pair silently gets wrong answers or wrong permissions:
   * `subscriptions` (160) vs `user_subscriptions` (1) vs `pulse_subscriptions` (0)
   * `premium_entitlements` (179) vs `user_entitlements` (13) vs `pulse_premium_entitlements` (13) vs `dashboard_entitlements` (0) vs `business_os_ent_grants` (0)
   * `pulse_conversations`/`pulse_messages` vs `comm_v2_conversations`/`comm_v2_messages` vs `conversations`/`private_messages`
   * `roles`/`permissions`/`role_permissions` (25/40/139) vs `admin_roles`/`admin_permissions`/`admin_role_permissions` (25/40/131) — identical shapes, **divergent contents**
   * `pulse_friends` (2) vs `pulse_friendships` (4); `pulse_muted_users` vs `pulse_user_mutes`
   * `livestream_access` vs `livestream_eligibility` — identical column lists
   * `pulse_status` vs `pulse_statuses` vs `pulse_stories`
   * `notifications` (1,365) vs `pulse_notifications` (4,150)
   * `alert_rules` vs `user_alert_rules` vs `user_alerts`
4. **385 tables have no UNIQUE constraint at all**, so duplicate-prevention for things like memberships, follows, reactions and idempotency keys rests on application checks. A concurrent double-submit is not stopped by the database.
5. **15 tables have no PRIMARY KEY**, meaning individual rows cannot be addressed for update or deletion — including deletion requested by a user exercising a data right.
6. **198 tables have no index whatsoever.**
7. **`comm_v2_*` (20 tables, 1,411 live messages) has no DDL in the repo.** A rebuild from source would not recreate the messaging engine that is currently in production use.
8. **Ownership columns are nullable and sometimes anonymous.** `pulse_post_views`, `pulse_live_viewers`, `pulse_ad_impressions`, `pulse_ad_clicks` and `analytics_events` all accept a null `user_id` alongside a `visitor_id`/`session_id`. Filtering by `user_id` neither errors nor covers those rows.
9. **`pulse_ad_impressions.viewer_user_id` / `pulse_ad_clicks.viewer_user_id` are audience identities, not row owners.** Treating them as ownership keys would expose which specific users saw which ad to the advertiser.
10. **Empty control tables that look authoritative.** `admin_user_roles` (0), `pulse_ad_team_members` (0), `dashboard_entitlements` (0), `pulse_message_receipts` (0). Code that authorises by checking one of these gets "no rows" — which may be interpreted as deny (feature broken) or, if written as a negative check, as allow (privilege escalation). Neither can be determined from the schema alone.

### 5.5 Evidence appendix: ownership columns without a supporting index

Tables that carry an ownership column but have **no index whose leading column is that ownership column**, and already hold data (top 60 by row count). Every tenancy filter on these is a full table scan, and nothing at the DB level stops a query that forgets the filter entirely:

| table | rows | ownership column(s) | indexes present |
|---|---:|---|---|
| `visitor_logs` | 61,731 | `user_id` | _none_ |
| `pulse_live_events` | 8,814 | `actor_user_id` | _none_ |
| `pulse_notification_deliveries` | 7,951 | `user_id` | _none_ |
| `pulse_chat_health_traces` | 5,644 | `user_id` | idx_pulse_chat_health_traces_trace |
| `analytics_events` | 3,159 | `user_id` | _none_ |
| `performance_traces` | 1,490 | `user_id` | idx_performance_traces_level_duration, idx_performance_traces_path_created, idx_performance_traces_created |
| `users` | 1,357 | `user_id`, `telegram_user_id` | idx_users_roast_call_sign_slug |
| `notification_events` | 1,343 | `actor_user_id` | idx_notification_events_recipient_type, sqlite_autoindex_notification_events_1 |
| `failed_email_queue` | 1,236 | `user_id` | idx_failed_email_queue_idempotency, idx_failed_email_queue_due |
| `pulse_status` | 965 | `user_id` | _none_ |
| `pulse_live_audit_logs` | 958 | `actor_user_id`, `target_user_id` | idx_pulse_live_audit_logs_live |
| `pulse_media_assets` | 647 | `owner_user_id` | _none_ |
| `pulse_ad_billing_profiles` | 531 | `account_id` | _none_ |
| `intelligence_delivery_jobs` | 500 | `user_id` | idx_intel_delivery_jobs_event, idx_intel_delivery_jobs_status, sqlite_autoindex_intelligence_delivery_jobs_1 |
| `pulse_conversations` | 494 | `created_by_user_id`, `owner_user_id` | idx_pulse_conv_business, idx_pulse_conversations_type_updated, idx_pulse_conversations_updated, idx_pulse_conversations_type_activity, idx_pulse_conversations_group |
| `auth_events` | 460 | `user_id` | idx_auth_events_failed_domain, idx_auth_events_failed_email_hash, idx_auth_events_failed_ip |
| `pulse_group_creation_attempts` | 410 | `user_id` | _none_ |
| `pulse_group_members` | 410 | `user_id` | sqlite_autoindex_pulse_group_members_1 |
| `pulse_groups` | 410 | `owner_user_id` | idx_pulse_groups_category_status, idx_pulse_groups_slug_status, idx_pulse_groups_slug, sqlite_autoindex_pulse_groups_1 |
| `pulse_group_roles` | 407 | `user_id` | sqlite_autoindex_pulse_group_roles_1 |
| `email_logs` | 405 | `user_id` | _none_ |
| `pulse_live_chat` | 370 | `user_id` | idx_pulse_live_chat_live |
| `comm_v2_read_receipts` | 251 | `user_id` | idx_comm_v2_receipts_convo_user, sqlite_autoindex_comm_v2_read_receipts_1 |
| `comm_v2_conversations` | 219 | `owner_user_id`, `created_by_user_id` | idx_comm_v2_conversations_activity, idx_comm_v2_conversations_type, sqlite_autoindex_comm_v2_conversations_1 |
| `pulse_chat_room_members` | 206 | `user_id` | idx_pulse_chat_room_members_room, sqlite_autoindex_pulse_chat_room_members_1 |
| `pulse_live_viewers` | 175 | `user_id` | idx_pulse_live_viewers_live |
| `subscriptions` | 160 | `user_id` | _none_ |
| `pulse_status_reactions` | 148 | `user_id` | sqlite_autoindex_pulse_status_reactions_1 |
| `pulse_camera_captures` | 140 | `user_id` | _none_ |
| `user_privilege_profiles` | 133 | `user_id` | _none_ |
| `user_trust_profiles` | 130 | `user_id` | _none_ |
| `livestream_access` | 128 | `user_id` | _none_ |
| `livestream_eligibility` | 127 | `user_id` | _none_ |
| `pulse_status_replies` | 127 | `user_id` | _none_ |
| `pulse_post_attempts` | 125 | `user_id` | idx_pulse_post_attempts_created |
| `pulse_post_views` | 114 | `user_id` | idx_pulse_views_post |
| `pulse_live_guest_requests` | 101 | `user_id` | idx_pulse_live_guest_requests_user, idx_pulse_live_guest_requests_live_status |
| `security_events` | 99 | `user_id` | idx_security_events_type_created |
| `comm_v2_message_reactions` | 83 | `user_id` | sqlite_autoindex_comm_v2_message_reactions_1 |
| `pulse_ai_learning_events` | 80 | `user_id` | sqlite_autoindex_pulse_ai_learning_events_1 |
| `payment_email_logs` | 68 | `user_id` | _none_ |
| `pulse_live_reactions` | 64 | `user_id` | _none_ |
| `pulse_camera_previews` | 51 | `user_id` | sqlite_autoindex_pulse_camera_previews_1 |
| `pulse_music_events` | 46 | `user_id` | idx_pulse_music_events_track |
| `comm_v2_communities` | 45 | `owner_user_id` | sqlite_autoindex_comm_v2_communities_2, sqlite_autoindex_comm_v2_communities_1 |
| `arena_match_events` | 44 | `user_id` | idx_arena_match_events_match_created |
| `pulse_live_guests` | 41 | `user_id` | idx_pulse_live_guests_user, idx_pulse_live_guests_live_status |
| `arena_match_participants` | 40 | `user_id` | idx_arena_match_participants_match_user, sqlite_autoindex_arena_match_participants_1 |
| `portfolio_snapshots` | 40 | `user_id` | _none_ |
| `pulse_group_action_logs` | 39 | `user_id` | idx_pulse_group_action_logs |
| `pulse_comments` | 38 | `user_id` | idx_pulse_comments_post_visible_created, idx_pulse_comments_post_created |
| `arena_matches` | 37 | `creator_id` | _none_ |
| `command_history` | 36 | `user_id` | _none_ |
| `arena_play_sessions` | 34 | `user_id` | _none_ |
| `email_verification_tokens` | 34 | `user_id` | sqlite_autoindex_email_verification_tokens_1 |
| `admin_activity_logs` | 27 | `admin_user_id` | _none_ |
| `notification_logs` | 24 | `user_id` | _none_ |
| `stripe_events` | 20 | `user_id` | sqlite_autoindex_stripe_events_1 |
| `password_reset_tokens` | 19 | `user_id` | idx_password_reset_tokens_hash, sqlite_autoindex_password_reset_tokens_1 |
| `arena_chat_messages` | 18 | `sender_id` | idx_arena_chat_messages_thread_created, idx_arena_chat_messages_thread_id |

Full count: **237** tables of the 467 that have an ownership column are missing such an index (127 of them already contain rows).

**Tables with no PRIMARY KEY at all (15):** `arena_academy_progress`, `arena_blocks`, `arena_companions`, `arena_faction_members`, `arena_follows`, `arena_match_participants`, `arena_playbook_votes`, `arena_presence`, `arena_quest_progress`, `arena_reputation`, `arena_spectators`, `arena_team_members`, `arena_tournament_entries`, `arena_user_badges`, `arena_user_preferences`

**Tables with no index of any kind (198):** `account_audit_logs`, `active_sessions`, `ad_creatives`, `ad_images`, `ad_reports`, `ad_revenue`, `ad_reviews`, `ad_targeting`, `ad_videos`, `admin_activity_logs`, `admin_session_logs`, `admin_user_actions`, `admin_user_notes`, `advertisers`, `ai_action_audit_logs`, `ai_action_results`, `ai_agents`, `ai_analyses`, `ai_chat_history`, `ai_context_summaries`, `ai_conversations`, `ai_feedback`, `ai_memory_cards`, `ai_messages`, `alerts_history`, `analytics_events`, `arena_boss_attempts`, `arena_events`, `arena_leaderboards`, `arena_legacy`, `arena_match_chat`, `arena_matches`, `arena_mission_attempts`, `arena_missions`, `arena_os_activity`, `arena_play_sessions`, `arena_playbook_comments`, `arena_playbooks`, `arena_player_stories`, `arena_reports`, `arena_room_messages`, `arena_rooms`, `arena_share_events`, `arena_trades`, `arena_world_history`, `audit_logs`, `backend_management_audit_events`, `brand_deals`, `brevo_contact_sync_logs`, `business_os_mkt_offer_events`, `business_os_seller_profile`, `chat_memory`, `chat_reports`, `checkout_attempts`, `comm_v2_moderation_events`, `comm_v2_reports`, `comm_v2_user_settings`, `command_history`, `communication_call_device_sessions`, `creator_payouts_placeholder`, `creator_revenue_events`, `crypto_news_cache`, `dashboard_events`, `dashboard_recommendations`, `day_signal_results`, `education_ai_tutor_logs`, `education_lesson_views`, `education_quiz_questions`, `education_quizzes`, `education_sections`, `email_logs`, `email_verifications`, `engagement_events`, `enterprise_leads`, `global_intelligence_snapshots`, `i18n_missing_translations`, `intelligence_collector_runs`, `livestream_access`, `livestream_eligibility`, `marketplace_buyer_interest`, `marketplace_orders_placeholder`, `marketplace_reports`, `monetization_events`, `notification_logs`, `paper_simulator_trades`, `password_resets`, `payment_email_logs`, `payment_records`, `payment_verifications`, `payout_failures`, `payout_history`, `platform_payouts`, `portfolio_advice_history`, `portfolio_items`, `portfolio_snapshots`, `presence_last_seen`, `presence_privacy_settings`, `price_history`, `privacy_preferences`, `pulse_ad_billing_profiles`, `pulse_ad_refunds`, `pulse_ai_engagement`, `pulse_ai_rotation_state`, `pulse_camera_captures`, `pulse_content_sentiment`, `pulse_courses`, `pulse_filters`, `pulse_group_comment_reports`, `pulse_group_creation_attempts`, `pulse_group_invites`, `pulse_group_post_media`, `pulse_group_post_reports`, `pulse_group_reports`, `pulse_lesson_media`, `pulse_lessons`, `pulse_live_classes`, `pulse_live_clips`, `pulse_live_events`, `pulse_live_moderation`, `pulse_live_reactions`, `pulse_live_reports`, `pulse_media_assets`, `pulse_message_reports`, `pulse_notification_deliveries`, `pulse_payment_events`, `pulse_premium_audit_logs`, `pulse_quiz_questions`, `pulse_quizzes`, `pulse_reel_retention_events`, `pulse_region_preferences`, `pulse_status`, `pulse_status_live`, `pulse_status_media`, `pulse_status_music`, `pulse_status_replies`, `pulse_status_shares`, `pulse_statuses`, `pulse_stories`, `pulse_story_reactions`, `pulse_story_views`, `pulse_subscriptions`, `pulse_teacher_documents`, `pulse_teacher_reviews`, `referral_events`, `referral_invites`, `risk_scores`, `roast_matches`, `roast_rooms`, `saved_command_results`, `saved_insights`, `scam_alerts`, `scam_reports`, `scam_scans`, `security_devices`, `security_login_events`, `security_reports`, `seller_payouts`, `seller_transactions`, `sentinel_incident_transitions`, `sentinel_metrics`, `simulator_ai_coaching_logs`, `simulator_orders`, `simulator_trades`, `sms_delivery_logs`, `subscriptions`, `support_notes`, `support_ticket_messages`, `support_tickets`, `teacher_earnings_placeholder`, `teacher_lessons`, `telegram_delivery_logs`, `telegram_notifications`, `transaction_history`, `transactions`, `unmatched_payments`, `usage_events`, `user_activity`, `user_ai_interactions`, `user_alert_rules`, `user_alerts`, `user_portfolio_settings`, `user_privilege_profiles`, `user_privilege_snapshots`, `user_recovery_codes`, `user_reputation_scores`, `user_security_events`, `user_trust_events`, `user_trust_profiles`, `user_trust_score`, `user_trusted_devices`, `user_verifications`, `verification_appeals`, `verification_audit_logs`, `visitor_logs`, `wallet_risk_checks`, `watch_rules`, `whale_alerts`, `whale_intelligence`