# PulseSoc Database Knowledge Map

Static extraction found approximately 994 `CREATE TABLE IF NOT EXISTS`
declarations. This count includes duplicate declarations across migrations,
runtime schema bootstraps, and service-local schema modules.

## Principal Table Families

| Family | Representative tables | Relationships / ownership |
|---|---|---|
| Users/auth | `users`, `sessions`, `auth_events`, `failed_login_controls`, `password_reset_tokens`, `email_verification_tokens`, `user_settings`, `mobile_security_sessions` | User is the primary auth principal. Sessions/tokens/events attach to `user_id` or account identity. |
| Security/trust | `user_security_events`, `user_trusted_devices`, `user_recovery_codes`, `account_strikes`, `account_warnings`, `account_restrictions`, `account_health_events`, `account_strike_appeals` | Account protection, warnings, restrictions, device/session trust, appeals. |
| Feed/social | `pulse_posts`, `pulse_post_saves`, `pulse_saved_collections`, `pulse_saved_items`, `pulse_content_preferences`, `engagement_events` | Users/pages create posts; saves and reactions attach to user and post/item ids. |
| Comments/media | post comment routes, `message_attachments`, `chat_media_uploads`, `pulse_media_upload_sessions`, upload/session tables | Media belongs to posts, messages, reels, status, or marketplace products depending route. |
| Reels/videos/music | `pulse_reels`, `pulse_videos`, `pulse_video_views`, `pulse_video_reactions`, `pulse_video_comments`, `pulse_audio_tracks`, `pulse_music_events`, `pulse_music_reports`, `pulse_content_music`, `pulse_trending_sounds` | Creator/user owns media; interactions and events attach to content ids. |
| Messaging legacy | `pulse_conversations`, `pulse_conversation_participants`, `pulse_messages`, `pulse_message_reactions`, `pulse_message_reports`, `pulse_message_receipts`, `pulse_conversation_typing`, `private_messages`, `conversation_members` | Conversation participants bind users to threads; messages/receipts/reactions attach to conversation and sender. |
| Communications V2 | `comm_v2_conversations`, `comm_v2_participants`, `comm_v2_messages`, `comm_v2_attachments`, `comm_v2_read_receipts`, `comm_v2_reports`, `comm_v2_blocks`, `comm_v2_presence`, `comm_v2_channels`, `comm_v2_communities` | Canonical communications domain with conversation, participant, message, attachment, safety and presence records. |
| Calls | `communication_calls`, `communication_call_participants`, `communication_call_events`, `communication_call_quality_reports`, `communication_call_device_sessions` | Server-authoritative call sessions, participants, quality reports, device session tracking. |
| Live | `comm_v2_live_streams`, `live_events`, `live_ops_plans`, `livestream_access`, `livestream_eligibility`, live session model | Live sessions carry state, access, eligibility, host/viewer/guest relationships. |
| Presence/Page OS | `pulse_pages` family in page services, page links/team/content tables, `user_presence` | Person/profile is not the same as Page/Presence; a user acts as a page through roles. |
| Business OS core | `business_os_business`, `business_os_business_locations`, `business_os_business_members`, `business_os_business_policies`, `business_os_business_audit` | Business entity, members/roles, policies, audit events. |
| Ledger | `ledger_transactions`, `ledger_entries`, `ledger_balances`, `provider_webhook_events` | Money movement normalized through ledger transactions/entries/balances; webhooks are idempotent event sources. |
| Entitlements/Premium | `business_os_ent_products`, `business_os_ent_plans`, `business_os_ent_catalog`, `business_os_ent_grants`, `business_os_ent_usage`, `business_os_ent_provider_subs`, `premium_entitlements`, `subscriptions`, `founder_memberships` | Provider subscriptions and IAP normalize into entitlement grants/usage. |
| Marketplace | `business_os_mkt_sellers`, `business_os_mkt_products`, `business_os_mkt_orders`, `business_os_mkt_order_items`, `business_os_mkt_refunds`, `business_os_mkt_returns`, `business_os_mkt_disputes`, `marketplace_*` legacy/commercial tables | Sellers own listings/products; buyers create carts/orders; refunds/returns/disputes link to orders. |
| Store | `business_os_store_storefront`, `business_os_store_products`, `business_os_store_collections`, `business_os_store_collection_products`, `business_os_store_shipping_profiles`, `business_os_store_return_policy` | Storefront and products are separate from Marketplace product rows unless explicitly linked. |
| Advertising | `business_os_ad_advertisers`, `business_os_ad_campaigns`, `business_os_ad_sets`, `business_os_ad_creatives`, funding/spend/delivery/impression/click/billing/audit tables | Advertiser account owns campaigns; campaigns own ad sets; ad sets own creatives; delivery emits events. |
| Ads intelligence | `ads_intel_events`, `ads_intel_delivery_decisions`, `ads_intel_interest_affinity`, `ads_intel_campaign_daily`, `ads_intel_creative_daily`, diagnostics/pacing/frequency/policy tables | Analytics and decision records for ad delivery and performance. |
| Orders/payments/payouts | `creator_wallets`, `creator_ledger_entries`, `seller_payout_accounts`, `creator_transactions`, `creator_payouts`, `payment_audit_logs`, `payout_queue`, `settlement_batches`, `escrow_holds` | Creator/seller money flows, payout state, settlement and escrow. |
| Crypto/alerts | `watchlists`, `manual_portfolio`, `paper_portfolio`, `last_prices`, `alerts_history`, `alert_rules`, `alert_events`, `alert_delivery_jobs`, `crypto_*`, `portfolio_snapshots`, `connected_wallets` | User-owned portfolios/watchlists/alerts with delivery jobs and market observations. |
| Notifications | `notifications`, `notification_events`, `notification_delivery_jobs`, `notification_device_tokens`, `notification_preferences`, `expo_push_tickets`, logs/failures | Notification generation, preferences, token/device delivery, logs/receipts. |
| UNDX / AI | `pulse_ai_conversations`, `pulse_ai_messages`, `pulse_ai_knowledge_items`, `pulse_ai_user_memory`, `pulse_ai_feedback`, `pulse_ai_safety_reviews`, `business_os_undx_*`, `ai_*`, `global_intelligence_*` | Conversation memory, knowledge items, safety/review events, governed action requests/receipts/confirmations/tools. |
| Admin/Sentinel | `admin_*`, `backend_feature_registry`, `backend_management_audit_events`, Sentinel docs/tables | Administrative permissions, audit, moderation, backend-management surfaces. |
| Arena/Education/Progress | `arena_*`, `education_*`, `pulse_growth_*`, rewards/progress tables | Gamified/social/education/growth subsystems. |

## Core Relationships

- `users` is the auth principal for profiles, posts, messages, notifications,
  crypto, marketplace buyer actions, and native sessions.
- Page/Presence is a presentation actor controlled by authorized users; it is
  not a login principal.
- Business OS records use business/seller/account IDs and join to users through
  membership/ownership tables.
- Payments should be provider-specific at the edge and entitlement/ledger-based
  at the core.
- Messaging and commerce conversations are moving toward explicit domain
  separation; native cache keys already distinguish social and commerce threads.

## Database Gaps / Risks Needing Runtime Verification

- Static `CREATE TABLE IF NOT EXISTS` count does not prove migrations ran in
  production.
- Duplicate table definitions can drift between migrations and runtime bootstrap.
- Some docs identify financial correctness risks around concurrent ledger writes,
  refund idempotency, refund delta handling, and capture atomicity; these need
  live/database-specific verification before UNDX can state they are resolved.
- Feature readiness cannot be inferred from a table alone.
