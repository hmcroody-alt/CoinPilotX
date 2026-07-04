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

## Remaining Major Features

- Advanced camera/compression/editor tools
- Live discovery
- Native LiveKit calls
- Full-screen incoming calls
- Growth Center
- Crypto/market alerts
- Intelligence Center
- Marketplace
- Premium/entitlements
- Creator Studio
- Groups/communities
- Saved content
- Search/discovery

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

## Recommended Next Feature

Recommendation: build Native Marketplace Browse + Listing Detail Foundation next.

This should come before Marketplace seller tools, checkout/payment flows, Creator Studio, or advanced Camera tools.

## Why This Comes Next

- Marketplace is already exposed in the production PulseSoc web app and notification/dependency graph, but has no native browse surface yet.
- The native app now has the prerequisites Marketplace browse/detail needs: auth/session, profile identity, notifications, media upload, media viewer, messaging, save/share/report patterns, and web fallback routing.
- A browse/detail slice can reuse existing marketplace search/listing APIs and database/business rules without touching payments, payouts, seller onboarding, or checkout.
- Marketplace listing cards are media-heavy, so the new shared Media Viewer should be validated on another real product surface before Creator Studio or advanced seller creation flows.
- This recommendation is based on current backend routes/tables and native migration progress, not a predetermined roadmap.

## Reusable Existing PulseSoc Logic

Reuse directly:

- Existing marketplace search/list API: `GET /api/pulse/marketplace/search`.
- Existing marketplace web route: `/pulse/marketplace`.
- Existing save/report APIs: `/api/pulse/marketplace/listings/save` and `/api/pulse/marketplace/listings/report`.
- Existing seller messaging path through `/api/pulse/messages/start`.
- Existing listing creation and media upload contracts for later seller tools.
- Existing `marketplace_listings`, `marketplace_product_media`, `marketplace_sellers`, `marketplace_saved_products`, and `marketplace_reports` tables.
- Existing marketplace moderation, safety scoring, approval status, seller trust, promotions, notification, and revenue safety logic.
- Shared native media viewer and media upload helpers.

Do not duplicate in native:

- Listing approval logic.
- Marketplace moderation/safety scoring.
- Seller trust/readiness rules.
- Payment, payout, escrow, refund, dispute, or order rules.
- Listing save/report persistence.
- Seller messaging authorization.
- Media authorization or storage decisions.
- Server-side validation.

## What Must Be Rebuilt Natively

- Native Marketplace browse screen.
- Native listing card grid/list.
- Native listing detail screen.
- Search/filter entry using the existing search API.
- Listing image/media gallery using the shared Native Media Viewer.
- Save listing action.
- Report listing action.
- Contact seller action through existing Messenger start behavior where supported.
- Seller/profile navigation where payloads expose seller identity.
- Loading, empty, offline, error, retry, and web fallback states.
- Deep-link routing structure for `/pulse/marketplace?listing=<id>` where supported.

## Dependencies And Blockers

Dependencies:

- Confirm marketplace search response shape and listing media fields.
- Confirm whether a dedicated listing detail API exists or whether detail should be hydrated from search/list payload first.
- Confirm seller contact payload for `/api/pulse/messages/start`.
- Confirm notification/deep-link target patterns for marketplace listing IDs.
- Keep payment/checkout/payout flows out of the first slice unless existing APIs make a read-only status necessary.

Blockers:

- Marketplace browse/detail must avoid implying native checkout readiness before payments are QA-gated.
- Real-device media gallery, search input, and seller contact routing must be verified before production replacement.

## Risk Level

Risk: Medium-high.

Reasons:

- Marketplace affects seller trust, buyer expectations, reporting, and potential payment-adjacent behavior.
- Backend risk is controlled if native remains browse/detail only and reuses existing search/save/report/contact APIs.
- The main risk is presenting unsupported checkout/seller actions too early or misrepresenting listing approval/payment state.

## Estimated Complexity

Complexity: Medium-high.

Recommended first slice:

- Native Marketplace browse route/screen.
- Search/list results through existing marketplace search API.
- Listing cards with image/media viewer.
- Listing detail modal/screen from existing payload fields.
- Save/report actions.
- Contact seller hook only where existing API payloads support it.
- Web fallback for checkout, seller dashboard, listing creation, and unsupported listing fields.
- Static audit proving marketplace business rules stay backend-owned.

Defer from first slice:

- Checkout/payment.
- Orders, refunds, disputes, and payouts.
- Seller onboarding.
- Listing creation/editing.
- Inventory management.
- Merchant dashboard.
- Advanced promotion/ads controls.

## Safest Implementation Plan

1. Inspect `/api/pulse/marketplace/search` response shape and current marketplace listing renderer.
2. Add native marketplace API wrappers without changing backend routes.
3. Add a native Marketplace screen and route.
4. Render browse/search results using existing listing payload fields.
5. Add listing detail with shared Native Media Viewer.
6. Wire save/report/contact seller only through existing APIs.
7. Keep checkout/seller tooling on explicit web fallback until native QA gates are ready.
8. Add a focused Marketplace audit and keep production WebView untouched.

## Recommendation Summary

Build Native Marketplace Browse + Listing Detail Foundation next. The native app now has enough social, media, profile, messaging, notification, and shared viewer infrastructure to support marketplace discovery while preserving listing approval, seller trust, payments, payouts, moderation, reports, saves, and business rules on the existing PulseSoc backend.
