# PulseSoc Native Migration Progress

Date: 2026-07-04

## Current Native State

The native app lives separately under `mobile-native/`. The current production WebView app remains untouched and continues to serve existing PulseSoc users.

Completed native foundations:

- App shell: Expo React Native, native stack/tab navigation, native deep-link configuration.
- Auth/session: login, signup, restore, logout through existing mobile auth APIs.
- API base URL/session safety: normalized API base URL, cookie-backed session reuse, network failure handling.
- Push registration: Expo push token registration through existing `/api/push/subscribe`.
- Mission Control: basic native connection to `/api/dashboard/mission-control`.
- Messenger: conversation list, conversation screen, text send, retry, receipts, typing, sync polling, offline cache, image/file/voice upload paths using existing Messenger APIs.
- Messenger hardening: corrupt-cache fallback, foreground/background sync recovery, upload-in-flight guard, long-thread list settings.
- Notifications: native notification center, unread/badge sync, mark read, mark all read, delete, preferences, push permission state, foreground badge refresh, background tap routing structure, and native/web target fallback.
- Home Feed + Post Detail: native feed list, pagination, pull-to-refresh, offline cache, post detail, comments, add comment, reactions, save, repost, share hook, image media cards, and `/pulse/post/<post_id>` deep-link routing through existing PulseSoc APIs.
- Pulse AI: basic chat through existing `/api/pulse/assistant/chat`.
- Profile: native current profile, public profile route, profile posts/media/about tabs, profile edit, avatar/cover upload/remove, profile theme selection, offline cache, and profile deep links through existing PulseSoc profile/feed/theme APIs.
- Reels Player + Reel Detail: native full-screen vertical Reels feed, Expo AV video playback, Mux/R2 media URL reuse, infinite scrolling, pull-to-refresh, metadata cache, comments, reactions, save, repost, share, follow creator, not interested, report, view tracking, profile navigation, and `/pulse/reels/<reel_id>` deep-link routing through existing Reels APIs.
- Status Viewer + Status Detail: native Status rail, Status list, full-screen viewer, image/video/text rendering, tap navigation, view tracking, reactions, replies, shares, music display, offline metadata cache, and `/pulse/status/<status_id>` deep-link routing through existing Status APIs.
- Media Capture + Upload Foundation: shared native image picker, video picker, camera entry point, permission states, file validation, upload progress, retry, cancellation, processing-status polling, reusable upload hook/service, and reusable media preview component over existing PulseSoc media APIs.
- Feed Composer Foundation: native composer entry from Home Feed, text/title publishing, visibility selector, image/video/camera attachment through the shared media upload layer, upload preview/progress/retry/cancel, publish states, and feed refresh through existing PulseSoc post APIs.
- Status Creator Foundation: native Status composer entry, text/image/video Status publishing, camera/gallery integration, shared upload preview/progress/retry/cancel, privacy/duration selectors, music search/trending hooks, AI Story generation hook, and Status rail refresh through existing PulseSoc Status APIs.
- Media Viewer Foundation: shared full-screen native image/video viewer, pinch-to-zoom image structure, swipe-down close, previous/next navigation, processing-status checks, share/save/profile hooks, metadata display, and integrations for Feed/Post/Profile, Messenger attachments, and Status media hooks.
- Marketplace Browse + Listing Detail Foundation: native Marketplace tab, search/browse through existing marketplace API, listing cards, listing detail modal, media gallery through NativeMediaViewer, save/report/contact seller hooks, safe checkout routing, offline cache, and marketplace deep-link routing.
- Search + Discovery Foundation: native Search tab/route, debounced global search through existing `/api/pulse/search`, recent and suggested searches, discovery tabs, grouped result cards, pull-to-refresh, cached result fallback, native destination routing, `/pulse/search` deep-link routing, and web fallback for unsupported result URLs.
- Saved Content + Collections Foundation: native Saved tab/route, saved item list, type filters, collection filters, create/rename/delete collection actions, remove/move saved item actions, saved search, offline cache, item deep-link routing, and `/pulse/saved` deep-link routing through existing saved APIs.
- Groups/Communities + Rooms Foundation: native Groups tab/detail route, thin read-only group JSON bridge, communities browse/search, room rail, group detail, rules/member metadata, compact group feed preview, join/leave, report, group chat open, room open, offline cache, and group/room deep-link routing through existing group, room, and Messenger APIs.
- Architecture Health Report + Shared Core Consolidation: native architecture audit, shared cache helper under `mobile-native/src/core/cache.ts`, first refactor of Groups/Saved/Marketplace cache wrappers, duplicate-pattern inventory, production WebView safety check, and next Live Discovery recommendation.
- Live Discovery + Live Viewer Foundation: native Live tab/detail route, Live Now discovery through existing `/api/pulse/live-now`, native viewer shell using existing playback manifest URLs, join viewer state, chat read/send, reactions, viewer count/state refresh, offline cache, deep-link routing for Live links, and safe web fallback for Go Live/Studio/hosting/co-hosting/unsupported playback.
- Live Viewer Device QA + Hardening: documented unavailable simulator/device tooling, added AppState foreground recovery for Live state/chat/list refresh, added playback failure fallback state, guarded host/profile navigation from empty profile keys, preserved safe web fallback for Studio/hosting/co-hosting/calls, and kept device-only playback claims unverified.
- Premium + Entitlements Foundation: native Premium route, server-authoritative status display through `/api/premium/status`, Founder/Premium badge display, entitlement list, cached fallback, app-resume refresh, existing checkout/billing portal provider handoff, Settings/Profile entry points, `/pulse/premium` deep-link routing, and explicit no-local-entitlement boundary.
- Creator Studio Foundation: native Creator Studio route, creator state through `/api/dashboard/creator/state`, Creator AI hooks through `/api/pulse/creator-ai/<tool>`, Content Planner draft save through `/api/dashboard/content-planner/item`, creator metric/recommendation cards, Premium eligibility messaging, shortcuts into existing native composer/status/reels/profile/premium surfaces, and safe web fallback for unsupported Studio/Live/monetization tools.
- Growth Center Foundation: native Growth Center route, read-only growth state through `/api/pulse/growth`, server-owned growth score/status cards, wallet/budget summary, audience/targeting preview, campaign overview, analytics snapshot, Feed/Post/Reel/Profile promote shortcuts, Settings entry, `/pulse/growth` and `/pulse/promote` routing, offline cache, and safe web fallback for campaign launch, wallet funding, billing, targeting, ad review, and unsupported promotion tools.
- Intelligence + Alerts Foundation: native Intelligence route, server-owned intelligence state through `/api/dashboard/intelligence/state`, crypto/market alert list through `/api/crypto/alerts`, stream/forecast cards, alert overview/detail, notification badge summary, Premium/Growth/Creator/Search/Profile navigation, offline cache, `/dashboard/intelligence` and `/dashboard/crypto/alerts` deep-link routing, and safe web fallback for advanced intelligence, provider administration, collector management, alert creation/editing, and unsupported operations.
- Feature Parity + QA Readiness Report: native-vs-WebView parity matrix across core PulseSoc surfaces, route/deep-link inventory, backend reuse assessment, QA blocker inventory, recommended hardening order, release readiness statement, and device-QA-first next action.
- Device QA Setup: added Expo web QA dependencies, QA start/build scripts, EAS development/simulator/preview/production profiles, optional Expo project ID support for push-token registration, exact iOS/Android/browser/physical-device QA commands, and a remaining-blocker inventory.
- QA Browser Readiness: verified Expo web boot through the built-in QA browser, fixed duplicate Reels deep-link routing, captured login screenshots, and confirmed signed-out feature routes safely land on the auth gate.
- Authenticated QA Browser Pass: verified login, session restore, logout, authenticated top-level navigation, Settings, Pulse AI, and Intelligence routes through the built-in QA browser against a local temporary QA backend/proxy; fixed web session storage, browser cookie handling, Settings/Pulse AI deep links, and Intelligence object-shaped card normalization.
- Short Authenticated QA Browser Sweep: verified authenticated Home, Messenger, Notifications, Profile, Reels, Status, Marketplace, Search, Saved, Groups, Live, Premium, Creator, Growth, Intelligence/Alerts, Settings, Pulse AI, notification preferences, and fallback routes through the built-in QA browser; fixed Login/Settings semantic accessibility roles/labels for reliable web QA automation; confirmed no current console warnings/errors during the sweep; kept device-only claims explicitly unverified.
- Native Alert Management + Crypto/Market Alert CRUD: native Alert Management route, crypto/market alert list, alert detail/history, create/edit form, pause/resume/delete/duplicate/test actions, channel readiness/test UI, offline cache, Settings/Intelligence/notification routing, `/pulse/alerts` and `/dashboard/crypto/alerts` route handling, and safe web fallback for unsupported advanced/provider tools through existing PulseSoc alert APIs and backend business logic.
- Native Alert Management QA Hardening: browser-verified alert validation, inline delete confirmation/cancel/confirm, pause/resume/duplicate/delete/test success and failure states, channel readiness success/failure states, long-history and empty-history states, alert deep links, preserved success notices after refresh, and selected-alert stability through the built-in QA browser with seeded alert fixtures.
- Alert Provider + Device QA Setup: documented APNs/FCM/Expo push readiness, SMS/email/Telegram readiness, notification tap deep links, lock-screen behavior plan, physical-device alert test plan, provider success/failure states, channel readiness accuracy checks, delivery debugging logs, and the critical app identity split between `com.pulsesoc.nativeapp` and `com.pulsesoc.app`; selected `com.pulsesoc.nativeapp` as the native provider/device QA identity while protecting production `com.pulsesoc.app`; no provider/device delivery was claimed verified.
- Settings: session controls, push registration, notification preferences entry.

Completed supporting reports/audits:

- `reports/pulsesoc_native_app_api_contract.md`
- `reports/pulsesoc_native_app_migration_plan.md`
- `reports/pulsesoc_native_dependency_graph.md`
- `reports/pulsesoc_native_phase1_device_qa.md`
- `reports/pulsesoc_native_messenger_progress.md`
- `reports/pulsesoc_native_messenger_device_qa.md`
- `reports/pulsesoc_native_notifications_progress.md`
- `reports/pulsesoc_native_feed_progress.md`
- `reports/pulsesoc_native_profile_progress.md`
- `reports/pulsesoc_native_reels_progress.md`
- `reports/pulsesoc_native_status_progress.md`
- `reports/pulsesoc_native_media_upload_progress.md`
- `reports/pulsesoc_native_feed_composer_progress.md`
- `reports/pulsesoc_native_status_creator_progress.md`
- `reports/pulsesoc_native_media_viewer_progress.md`
- `reports/pulsesoc_native_marketplace_progress.md`
- `reports/pulsesoc_native_search_progress.md`
- `reports/pulsesoc_native_saved_progress.md`
- `reports/pulsesoc_native_groups_progress.md`
- `reports/pulsesoc_native_architecture_health.md`
- `reports/pulsesoc_native_live_progress.md`
- `reports/pulsesoc_native_live_device_qa.md`
- `reports/pulsesoc_native_premium_progress.md`
- `reports/pulsesoc_native_creator_progress.md`
- `reports/pulsesoc_native_growth_progress.md`
- `reports/pulsesoc_native_intelligence_progress.md`
- `reports/pulsesoc_native_feature_parity_qa_readiness.md`
- `reports/pulsesoc_native_device_qa_setup.md`
- `reports/pulsesoc_native_qa_browser_report.md`
- `reports/pulsesoc_native_authenticated_qa_browser_report.md`
- `reports/pulsesoc_native_short_qa_browser_sweep.md`
- `reports/pulsesoc_native_alert_management_progress.md`
- `reports/pulsesoc_native_alert_management_qa_hardening.md`
- `reports/pulsesoc_alert_provider_device_qa_setup.md`
- `scripts/pulsesoc_native_app_foundation_audit.py`
- `scripts/pulsesoc_native_phase1_device_qa_audit.py`
- `scripts/pulsesoc_native_messenger_audit.py`
- `scripts/pulsesoc_native_messenger_device_qa_audit.py`
- `scripts/pulsesoc_native_notifications_audit.py`
- `scripts/pulsesoc_native_feed_audit.py`
- `scripts/pulsesoc_native_profile_audit.py`
- `scripts/pulsesoc_native_reels_audit.py`
- `scripts/pulsesoc_native_status_audit.py`
- `scripts/pulsesoc_native_media_upload_audit.py`
- `scripts/pulsesoc_native_feed_composer_audit.py`
- `scripts/pulsesoc_native_status_creator_audit.py`
- `scripts/pulsesoc_native_media_viewer_audit.py`
- `scripts/pulsesoc_native_marketplace_audit.py`
- `scripts/pulsesoc_native_search_audit.py`
- `scripts/pulsesoc_native_saved_audit.py`
- `scripts/pulsesoc_native_groups_audit.py`
- `scripts/pulsesoc_native_architecture_health_audit.py`
- `scripts/pulsesoc_native_live_audit.py`
- `scripts/pulsesoc_native_live_device_qa_audit.py`
- `scripts/pulsesoc_native_premium_audit.py`
- `scripts/pulsesoc_native_creator_audit.py`
- `scripts/pulsesoc_native_growth_audit.py`
- `scripts/pulsesoc_native_intelligence_audit.py`
- `scripts/pulsesoc_native_feature_parity_audit.py`
- `scripts/pulsesoc_native_device_setup_audit.py`
- `scripts/pulsesoc_native_qa_browser_audit.py`
- `scripts/pulsesoc_native_authenticated_qa_browser_audit.py`
- `scripts/pulsesoc_native_short_qa_browser_sweep_audit.py`
- `scripts/pulsesoc_native_alert_management_audit.py`
- `scripts/pulsesoc_native_alert_management_qa_audit.py`
- `scripts/pulsesoc_alert_provider_device_qa_audit.py`

## Remaining Major Features

- Advanced camera/compression/editor tools
- Native LiveKit calls
- Full-screen incoming calls
- External device QA tooling completion and hardening pass
- Alert provider/device QA execution for push, SMS, email, Telegram, installed deep links, and notification tap routing

## Codebase Reconnaissance

The next recommendation is based on the current codebase, not guesswork.

Existing backend/web surfaces inspected:

- Feed page and shell: `pulse_page_html(...)`, `/pulse`
- Feed API: `GET /api/pulse/feed`
- Post create: `POST /api/pulse/posts`
- Post detail: `GET /api/pulse/posts/<post_id>` and `GET /api/pulse/post/<post_id>`
- Post reactions: `POST /api/pulse/posts/<post_id>/react`
- Post comments: `GET/POST /api/pulse/posts/<post_id>/comments`
- Save, pin, repost, delete, report, follow: existing `/api/pulse/posts/*`, `/api/pulse/follow`, `/api/pulse/report`, saved-content APIs
- Media upload: existing `/api/pulse/media/upload` and `media_service.save_upload(...)`
- Feed engine: `services/pulse_feed_engine.py`
- Feed ranking: `services/pulse_feed_ranking_engine.py`
- Moderation: `services/pulse_moderation_engine.py`
- Notifications into feed/post targets: `static/notifications.js` and server target resolver
- Profile routes: `/pulse/profile`, `/pulse/profile/<profile_key>`, `/pulse/profile/edit`
- Profile APIs/media/theme: `/api/pulse/profile/me`, `/api/pulse/profile/update`, `/api/pulse/profile/avatar`, `/api/pulse/profile/cover`, `/api/pulse/profile/avatar/remove`, `/api/pulse/profile/cover/remove`, `/api/pulse/premium/profile-theme`
- Reels APIs: `/api/pulse/reels/feed`, `/api/pulse/reels/<reel_id>/react`, comments, save, repost, share, not-interested, follow creator
- Status APIs: `/api/pulse/status/rail`, `/api/pulse/status`, view, react, reply, share
- Status data/business logic: `pulse_status`, `pulse_status_media`, `pulse_status_music`, `pulse_status_views`, `pulse_status_reactions`, `pulse_status_replies`
- Marketplace routes: `/pulse/marketplace`, `/pulse/marketplace/create`, `/pulse/merchant/dashboard`, `/pulse/merchant/<username>`
- Marketplace APIs: `/api/pulse/marketplace/search`, `/api/pulse/marketplace/seller/apply`, `/api/pulse/marketplace/listings/create`, `/api/pulse/marketplace/media/upload`, `/api/pulse/marketplace/listings/save`, `/api/pulse/marketplace/listings/report`
- Marketplace data/business logic: `marketplace_listings`, `marketplace_product_media`, `marketplace_sellers`, `marketplace_saved_products`, `marketplace_reports`, `marketplace_orders`, seller readiness, promotions, and moderation/revenue safety services
- Search APIs and web bridge: `/api/pulse/search`, `/pulse/search`, `static/js/pulse_search_bridge.js`, and search handling in `static/js/pulse_home_core.js`
- Saved APIs and web route: `/pulse/saved`, `GET/POST /api/pulse/saved`, saved collections, delete, and move endpoints
- Groups and rooms routes: `/pulse/groups`, `/pulse/groups/create`, `/pulse/groups/<group_slug>`, `POST /api/pulse/groups/create`, join/leave APIs, chat-open APIs, invite/report/update/moderation APIs, group post/comment APIs, `pulse_default_room_cards()`, and `pulse_ensure_default_rooms(...)`
- Live surfaces: `/pulse/live`, `/pulse/live/studio`, `/api/pulse/live-now`, `/api/pulse/live/<id>/state`, `/api/pulse/live/<id>/join`, `/api/pulse/live/<id>/chat`, `/api/pulse/live/<id>/react`, LiveKit direct playback fallback, Mux egress handling, live-session state, live chat, replay/feed insertion, and the existing live audit suite.
- Premium/entitlement surfaces: `/api/premium/status`, `/api/premium/checkout`, `/api/premium/billing-portal`, `/api/dashboard/economy/state`, Stripe hosted checkout/portal routes, `premium_entitlement_service`, `premium_capability_engine`, `premium_identity_engine`, `pulse_premium_profiles`, `pulse_subscriptions`, and `pulse_premium_entitlements`.
- Creator surfaces: `GET /api/dashboard/creator/state`, `/dashboard/creator`, `/dashboard/creator/posts`, `/dashboard/creator/reels`, `/dashboard/creator/videos`, `/dashboard/creator/statuses`, `/dashboard/creator/live-studio`, `/pulse/creator/dashboard`, `/pulse/creator-studio`, `/pulse/creator/analytics`, and `POST /api/pulse/creator-ai/<tool>`.
- Creator logic: `services/dashboard_creator_command_center.py`, creator cards/subsystems, owner-scoped creator metrics, content/moderation/processing summaries, creator event-bus recommendations, and creator AI hook routing.
- Growth Center surfaces: `GET /api/pulse/growth`, `/pulse/growth`, `services/pulsesoc_growth_engine.py`, growth account/workspace/wallet/audience/profile/score/risk tables, and promotion readiness state.
- Intelligence and alerts surfaces: `GET /api/dashboard/intelligence/state`, `/dashboard/intelligence`, `/dashboard/intelligence/<subsystem_key>`, `/dashboard/crypto/alerts`, `/api/crypto/alerts`, `services/alert_engine.py`, `services/notification_service.py`, `services/privacy_intelligence_engine.py`, `services/global_intelligence_graph.py`, `services/universal_intelligence_fabric.py`, `alert_rules`, `user_alert_rules`, notification delivery jobs, and crypto/market intelligence notification helpers.
- Alert management routes: `GET/POST /api/crypto/alerts`, `PATCH/DELETE /api/crypto/alerts/<alert_id>`, `POST /api/crypto/alerts/<alert_id>/duplicate`, `GET /api/crypto/alerts/<alert_id>/history`, `GET/POST /api/alerts`, `POST /api/alerts/<alert_id>/pause`, `POST /api/alerts/<alert_id>/resume`, `POST/DELETE /api/alerts/<alert_id>/delete`, `POST /api/alerts/<alert_id>/test`, `GET /api/alerts/events`, `GET /api/alerts/channel-readiness`, and `POST /api/alerts/test/<channel>`.
- Camera/media creation routes inspected: `/api/pulse/camera/config`, `/api/pulse/media/upload`, `/api/pulse/media/mux/direct-upload`, `/api/pulse/media/mux/direct-upload/complete`, `/api/pulse/camera/preview`, `/api/pulse/posts/create-from-camera`, `/api/pulse/reels/create-from-camera`, and `/pulse/camera/*`.
- Live/call routes and services inspected: `services/pulsesoc_communications_engine.py`, LiveKit config/token helpers, `start_call`, `join_token`, `accept_call`, `decline_call`, `call_status`, `active_calls`, `conversation_calls`, and existing LiveKit/Mux Live APIs.
- Current native reuse surface: repeated API wrappers, shared cache functions, screen-level loading/empty/error/offline states, native/web fallback routing, card layouts, action busy states, media preview/viewer hooks, tab/stack route patterns, Premium status/handoff patterns, Creator Studio shortcuts, and routing across Feed, Messenger, Notifications, Profile, Reels, Status, Marketplace, Search, Saved, Groups, Live, Premium, and Creator Studio.

Existing data/business logic that should remain server-authoritative:

- `pulse_posts`
- `pulse_comments`
- `pulse_reactions`
- `pulse_post_saves`
- `pulse_saved_items`
- `pulse_media_assets`
- `chat_media_uploads`
- `users`
- feed ranking and visibility rules
- moderation/risk status
- premium identity rendering data
- notification fanout
- mention notifications
- media storage/Mux processing
- saved-content collections
- follow graph
- marketplace listing moderation/safety
- seller trust/readiness
- marketplace save/report behavior
- marketplace order/payment/payout rules
- PulseSoc search ranking/grouping/result routing
- saved item collection ownership and removal
- group membership, roles, moderation, invite links, group chats, and group post/comment rules
- shared native routing, caching, error, card, and media-viewer behavior that should be consolidated before deeper Live/Calls/Premium work
- LiveKit/Mux session authority, live room state, live chat, replay creation, feed insertion, destination handling, and creator/host permissions
- premium subscription status, founder membership, entitlement grants/revocation, profile themes, premium badges, billing portal eligibility, and Stripe checkout state
- creator dashboard state, creator metrics, moderation/review counts, media processing state, creator AI provider routing, creator recommendations, and creator monetization/payout decisions
- growth account provisioning, growth score calculation, audience modeling, growth wallets, promotion readiness, ad billing, targeting, risk profiles, and growth AI session behavior
- intelligence state, alert rules, crypto/market event evaluation, notification delivery eligibility, alert dedupe windows, premium intelligence gates, and market/crypto data interpretation
- native QA browser/device validation status, feature parity gaps, release blockers, and WebView replacement readiness

## Recommended Next Action

Recommendation: run the first real Alert Management provider/device QA pass before building the next major feature.

This is the safest next action because Alert Management has now passed a seeded built-in QA browser hardening pass and has a provider/device QA setup report with the native QA identity set to `com.pulsesoc.nativeapp`, but the highest-risk remaining behaviors are still provider/device-only: APNs/FCM delivery, installed-app notification taps, lock-screen presentation, SMS/email/Telegram delivery, and physical-device deep links. Those must not be treated as browser-verified.

## Why This Comes Next

- Alert Management has now moved from native CRUD foundation to browser-hardened CRUD, with create/edit validation, inline delete confirmation, pause/resume/duplicate/delete/test, history, readiness, and deep links verified against seeded fixtures.
- Browser QA verified the bulk of the UI and API behavior, and the provider/device QA setup now documents the exact prerequisites, logs, pass criteria, and app identity decision required for the first real delivery pass.
- Production has complete server-authoritative alert management APIs and delivery logs, but real delivery still needs external setup and device/provider validation before any release claim.
- The parallel native app declares `com.pulsesoc.nativeapp`, and that is now the selected provider/device QA identity. The current APNs readiness helper still protects `com.pulsesoc.app`, so production identity must remain untouched while native QA credentials are configured separately.
- Camera, advanced media editor, Live hosting, and LiveKit calls are higher-risk because camera, microphone, native media, LiveKit, Bluetooth/audio, and background behavior remain unverified on real devices.
- This recommendation is based on the current production routes/services and `mobile-native` implementation inspected on 2026-07-04.

## Reusable Existing PulseSoc Logic

Reuse directly for the QA hardening pass:

- `GET/POST /api/crypto/alerts`
- `PATCH/DELETE /api/crypto/alerts/<alert_id>`
- `POST /api/crypto/alerts/<alert_id>/duplicate`
- `GET /api/crypto/alerts/<alert_id>/history`
- `GET/POST /api/alerts`
- `POST /api/alerts/<alert_id>/pause`
- `POST /api/alerts/<alert_id>/resume`
- `POST/DELETE /api/alerts/<alert_id>/delete`
- `POST /api/alerts/<alert_id>/test`
- `GET /api/alerts/events`
- `GET /api/alerts/channel-readiness`
- `POST /api/alerts/test/<channel>`
- Existing `services/alert_engine.py`
- Existing `services/dashboard_crypto_command_center.py`
- Existing `services/notification_service.py`
- Existing `services/pulsesoc_notification_system.py`
- Existing `alert_rules`, `alert_events`, `alert_delivery_jobs`, `notification_delivery_logs`, `user_alert_rules`, and related alert history tables.
- Existing premium gates through `api_pro_required(...)` and Premium status APIs.
- Existing native `mobile-native/src/api/intelligence.ts`, `IntelligenceCenterScreen`, `NotificationCenterScreen`, `NotificationPreferencesScreen`, shared `Panel`, cache helper, notification routing, and safe web fallback helpers.
- Existing native `mobile-native/src/api/alerts.ts` and `AlertManagementScreen`.

Do not duplicate in native:

- Alert trigger evaluation.
- Crypto/market interpretation.
- Alert dedupe/cooldown logic.
- Channel eligibility/readiness.
- Premium gating.
- Notification delivery routing.
- Provider polling.
- Delivery logging.
- Financial advice or buy/sell/hold recommendations.

## What Must Be Hardened Next

- Preserve or formalize a durable alert QA fixture with active, paused, duplicated, long-history, empty-history, success, and failure states.
- Device-test `/pulse/alerts`, `/pulse/alerts/<id>`, `/dashboard/crypto/alerts`, and crypto alert notification links in an installed native build.
- Use `com.pulsesoc.nativeapp` as the first APNs/FCM/Expo QA target while keeping production `com.pulsesoc.app` protected.
- Device-test push permission, Expo token registration, APNs/FCM delivery, notification tap routing, foreground/background recovery, and badge refresh.
- Provider-test SMS, email, and Telegram delivery with safe QA credentials.
- Keep provider administration, advanced Intelligence editing, unsupported alert types, and any unverified delivery behavior on safe web fallback.

## Dependencies And Blockers

Dependencies:

- Keep a short authenticated QA browser sweep as a pre-build checkpoint for future features, because the native app now has enough integrated surfaces that regressions matter.
- Use a QA fixture or local/prod-safe account with at least one alert rule and one alert event to verify non-empty states.
- Preserve backend Premium gating and channel readiness exactly as returned by the server.

Blockers:

- Real push delivery, lock-screen presentation, APNs/FCM, sounds, and installed-app notification taps remain device-only and must not be claimed browser-verified.
- `adb` is still not available in `PATH`.
- `/usr/bin/xcrun` exists, but `xcrun simctl list devices available` previously failed because `simctl` was not available.
- No physical-device QA flow has been recorded in this workspace.
- EAS project ID, Apple credentials, Android/Firebase push credentials, provisioning, and provider QA credentials remain external setup.
- The backend APNs readiness helper currently expects `com.pulsesoc.app`; a scoped multi-bundle readiness update or native-QA credential path is required for `com.pulsesoc.nativeapp` without changing production credentials.

## Risk Level

Risk: Medium.

Reasons:

- Alert management touches notification delivery, premium gates, and market/crypto safety expectations.
- The backend already owns the sensitive logic, so native risk is mostly UI/action wiring, payload correctness, and honest device-readiness reporting.
- Risk is lower than Native Calls or advanced Camera because Alert Management can be substantially verified in the built-in QA browser before device push QA.

## Estimated Complexity

Complexity: Medium.

Recommended first slice:

- Keep the first provider QA target identity set to `com.pulsesoc.nativeapp`.
- Add scoped multi-bundle APNs readiness support or native-QA credential validation without changing production WebView credentials.
- Run provider/device QA for APNs/FCM, installed-app deep links, SMS, email, and Telegram delivery when credentials/devices are available.
- Fix only Alert Management blockers found.
- Then decide whether the next build should be advanced Camera/media hardening or LiveKit calls based on remaining device QA readiness.

Defer from first slice:

- Real push delivery claims.
- Native provider administration.
- Native market/crypto evaluation.
- LiveKit calls.
- Advanced camera/editor expansion.
- Any App Store replacement recommendation.

## Safest Implementation Plan

1. Preserve the current browser-verified Alert Management baseline and report.
2. Use the provider/device QA setup report as the execution checklist.
3. Configure device/provider prerequisites for `com.pulsesoc.nativeapp`: APNs/FCM, Expo push, SMS, email, Telegram, and installed-app deep links.
4. Run Gate 3 device/provider QA without claiming browser-only confidence as device confidence.
5. Keep unsupported provider administration and advanced alert tooling on safe web fallback.
6. Verify through Gate 1 static checks, Gate 2 built-in QA browser checks after any alert code changes, and Gate 3 device-readiness documentation.
7. Commit/push only scoped native alert files, reports, audit, and screenshots.

## Recommendation Summary

Recommended next action: first real Native Alert Management provider/device QA pass.

Reason: Alert Management is now browser-hardened and has provider/device QA setup coverage while still reusing complete existing backend logic, but the remaining risk is external/device behavior, not another native screen. The next highest-value action is to configure native QA provider credentials/devices for `com.pulsesoc.nativeapp`, run the first physical-device/provider alert delivery pass, and fix only blockers found before higher-risk Camera, Live hosting, or LiveKit Calls.
