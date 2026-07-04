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

## Remaining Major Features

- Advanced camera/compression/editor tools
- Native LiveKit calls
- Full-screen incoming calls
- Growth Center
- Crypto/market alerts
- Intelligence Center

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

## Recommended Next Feature

Recommendation: build Native Growth Center Foundation next.

This should come before native Go Live, native LiveKit hosting, native calls, advanced creator monetization, and deeper paid promotion tools.

## Why This Comes Next

- Creator Studio now gives creators a native dashboard and creation hub, which naturally leads to audience growth and promotion readiness.
- The backend already exposes Growth Center state through `GET /api/pulse/growth`.
- Growth Center can reuse native Creator Studio, Feed/Post, Reels, Marketplace, Profile, Premium, Search, Notifications, and web fallback patterns.
- Growth Center can remain read-mostly and server-authoritative in the first slice while paid promotion launch, wallet funding, targeting, billing, and ad review stay backend/web/provider owned.
- This recommendation is based on the current growth backend and native migration state after the Creator Studio foundation checkpoint.

## Reusable Existing PulseSoc Logic

Reuse directly:

- Existing `GET /api/pulse/growth`.
- Existing `/pulse/growth` route behavior.
- Existing `services/pulsesoc_growth_engine.py`.
- Existing `pulse_growth_accounts`, `pulse_growth_workspaces`, `pulse_growth_wallets`, `pulse_growth_audience_profiles`, `pulse_growth_audience_models`, `pulse_creator_growth_profiles`, `pulse_growth_scores`, `pulse_growth_risk_profiles`, and `pulse_growth_preferences`.
- Existing promotion readiness, trust/risk, billing profile, audience model, growth score, and AI growth summary logic.
- Existing native Creator Studio, Premium, Feed/Post, Reels, Marketplace, Profile, Search, Notifications, routing, cache, loading/error, and web fallback patterns.

Do not duplicate in native:

- Backend business logic.
- Growth score calculations.
- Audience modeling.
- Promotion readiness decisions.
- Ad targeting rules.
- Growth wallet funding or ledger changes.
- Billing profile/provider logic.
- Premium/growth entitlement checks.
- Campaign launch, ad review, refund, webhook, and provider business logic.

## What Must Be Rebuilt Natively

- Native Growth Center dashboard screen.
- Growth score/status summary.
- Audience and promotion readiness cards.
- Wallet/credit display where backend supports it.
- Growth recommendations.
- Promote-post, promote-reel, and marketplace promotion shortcuts where safe.
- Loading, empty, offline, error, active, locked, and unavailable states.
- Deep-link routing for `/pulse/growth`.
- Web fallback for campaign launch, wallet funding, billing, targeting, ad review, and unsupported promotion tools.

## Dependencies And Blockers

Dependencies:

- Confirm `GET /api/pulse/growth` payload shape.
- Confirm Growth Center auth and permission boundaries.
- Reuse existing Premium state for growth eligibility.
- Reuse Creator Studio and marketplace/feed/reel/profile navigation for growth shortcuts.

Blockers:

- Paid promotion, wallet funding, ad billing, and targeting are policy-sensitive and should stay backend/web/provider flows in the first slice.
- Growth Center may require real account data for meaningful cards.
- Provider fallback handoff needs real-device QA.

## Risk Level

Risk: Medium.

Reasons:

- Growth is monetization-adjacent and policy-sensitive.
- Backend risk remains contained if native only displays server-owned growth state and routes unsupported writes to web fallback.
- UX risk is moderate because growth cards must communicate locked/unavailable states without implying guaranteed results.

## Estimated Complexity

Complexity: Medium.

Recommended first slice:

- Inspect growth state payload and web Growth Center behavior.
- Build read-only Growth Center dashboard first.
- Add growth recommendation and readiness cards from server-owned state.
- Add shortcuts to Creator Studio, Feed/Post, Reels, Marketplace, Premium, and Profile.
- Add web fallback for promotion launch, wallet, billing, targeting, and unsupported tools.
- Add static audit that verifies no native targeting, billing, wallet, promotion, or growth score logic is duplicated.

Defer from first slice:

- Campaign launch writes.
- Wallet funding.
- Ad targeting.
- Billing/provider setup.
- Local growth/audience scoring.
- Native payment work.

## Safest Implementation Plan

1. Inspect `GET /api/pulse/growth`, `/pulse/growth`, and `services/pulsesoc_growth_engine.py`.
2. Add a native growth API wrapper that reads existing backend state only.
3. Build native Growth Center dashboard cards from server-owned score, workspace, wallet, audience, trust/risk, and recommendation state.
4. Reuse existing native navigation into Creator Studio, Feed/Post, Reels, Marketplace, Premium, Profile, Search, and Notifications.
5. Add web fallback for campaign launch, wallet funding, billing, targeting, ad review, and unsupported promotion tools.
6. Add progress report and audit script.
7. Run the standard native verification suite.
8. Keep growth scoring, audience modeling, promotion readiness, targeting, wallets, billing, and provider behavior server-authoritative.

## Recommendation Summary

Build Native Growth Center Foundation next. The backend already owns growth account state, score calculation, audience modeling, wallet/profile state, trust/risk checks, and promotion readiness, and the native app now has Creator Studio plus the social/media/marketplace/premium surfaces needed to present Growth Center safely without duplicating targeting, billing, wallet, or campaign-launch logic.
