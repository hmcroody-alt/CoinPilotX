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
- Native Camera Studio + Media Compression/Preview Foundation: native Camera Studio route/screen, `/pulse/camera/*` deep-link handling, camera config wrapper, preview wrapper, create-from-camera API wrappers, photo/video capture shell, front/back camera switch, microphone permission handling, gallery fallback, permission-denied and QA browser fallback states, caption/privacy/destination flow, compression policy metadata, shared upload handoff, Feed/Status/Reel/Profile/Messenger publishing hooks, and safe web fallback for advanced AR/Banuba/effects.
- Native Camera Studio Device QA + Hardening: audited the native Camera Studio device-readiness boundary, confirmed the parallel `com.pulsesoc.nativeapp` camera/mic/photo configuration, documented that `simctl`, physical iPhone, and physical Android access remain unavailable in this environment, installed and verified Android `adb`, kept browser/simulator/physical-device verification separated, and blocked LiveKit calls until real Camera Studio device QA is completed.
- Native Camera Studio iOS Simulator QA: booted the iPhone 17 Pro iOS 26.5 simulator, installed Expo Go, launched PulseSoc Native through Metro, verified the app bundled and rendered the login screen behind Expo Go's developer menu, verified Expo Go terminate/relaunch at the container level, and documented that Camera Studio interaction remains unverified because Expo Doctor reports Expo SDK 51 is incompatible with Xcode 26.6 and the Expo Go first-run overlay could not be dismissed through available automation.
- Native iOS Toolchain Compatibility: upgraded the parallel `mobile-native` app to Expo SDK 54/React Native 0.81 for Xcode 26.6 compatibility, aligned Expo modules, added the Reanimated worklets peer, fixed SDK 54 notification/file-system API changes, built and installed `com.pulsesoc.nativeapp` on the iPhone 17 Pro simulator, verified Metro bundles without Expo Go, and rendered the native login screen in the installed simulator app.
- Native Camera Studio iOS Simulator QA Through Installed Dev Build: built, installed, launched, and bundled `com.pulsesoc.nativeapp` on the iPhone 17 Pro simulator; verified native Login, signed-out session recovery, signed-out Camera Studio deep-link auth gating, and foreground/background relaunch at the auth gate; fixed protected deep-link parsing so signed-out Camera Studio links no longer emit React Navigation route-mismatch warnings; documented that authenticated Camera Studio, camera/mic/gallery/upload/publish, and physical-device behavior remain unverified.
- Native Camera Studio Authenticated Simulator QA Attempt: started a temporary local QA backend at `127.0.0.1:5107`, verified direct mobile auth and authenticated camera config outside the app, rebundled the installed simulator app with `EXPO_PUBLIC_PULSE_API_BASE_URL=http://127.0.0.1:5107`, and documented that Simulator text-entry automation could not reliably fill the username/email field. Authenticated Camera Studio remains unverified; do not claim simulator login, preview, upload, publish, or authenticated recovery from this attempt.
- Native Simulator Authenticated QA Path: added a QA-only simulator deep link that is enabled only in development native builds when `EXPO_PUBLIC_PULSE_API_BASE_URL` points to localhost; it still calls the existing `/api/mobile/auth/login` flow, stores the existing backend session cookie, and queues Camera Studio navigation after auth. Production auth, production WebView routes, and production app identity remain untouched.
- Native Camera Studio Authenticated Simulator QA Through QA Deep Link: verified that the QA-only simulator deep link authenticates `com.pulsesoc.nativeapp` against a localhost backend without text entry, opens Camera Studio in Feed/photo and Reel/video modes, renders provider `native_fallback`, supports microphone/photo-library permission grant/revoke through `xcrun simctl privacy`, and restores the authenticated session after terminate/relaunch. Gallery selection, preview, upload handoff, publish routing, real camera capture, and physical-device media behavior remain unverified.
- Native Camera Studio Media QA Automation: seeded simulator media with `xcrun simctl addmedia`, added a dev/native/localhost-only QA media injection path, verified selected-media preview state, upload handoff, Feed publish to native Post Detail, Status publish to native Status viewer, Reel publish to native Reels viewer, local backend media/camera/post/status/reel records, foreground/background session recovery, and Camera Studio safe-area visual hardening. The touch/media automation blocker is partially reduced by QA-only simulator media injection, but real gallery picker touch selection, upload retry/cancel, physical camera/microphone capture, video compression, and physical-device behavior remain unverified.
- Native Physical Camera Studio QA Plan: created the physical iPhone/Android Camera Studio QA plan for camera/microphone permissions, gallery picker behavior, large-video upload, retry/cancel, upload progress accuracy, compression metadata, foreground/background recovery, and device-specific visual checks; added native upload progress hardening so large media shows transferred/total size when available. No production WebView route or backend business logic was changed.
- Native Physical Camera Studio QA Attempt: WWDR G3 installation resolved local iOS identity validation; `security find-identity -v -p codesigning` now returns two valid Apple Development identities. `npx expo run:ios --device 00008140-000E2D9A2EE8801C` built, signed, and installed `com.pulsesoc.nativeapp` on the iPhone 16 Pro. `xcrun devicectl device process launch` launched the installed app, Metro bundled `index.ts` for iOS, and a Camera Studio payload URL launch for `pulsesoc://pulse/camera/photo?target=feed` was accepted at process level. Physical camera/mic/gallery/capture/upload/publish behavior remains unverified because no reliable physical screen/touch automation or manual evidence was captured. No Android device is visible to adb.
- Native iPhone Camera Studio Interaction QA: verified physical iPhone app launch, bundle load, Camera Studio payload launch, and process-level suspend/resume on the installed `com.pulsesoc.nativeapp` iPhone 16 Pro build. Installed Mac-side `libimobiledevice` for screenshot attempts, but `idevicescreenshot` could not start the iOS `screenshotr` service. No screenshot/video evidence, backend media IDs, upload IDs, post IDs, status IDs, or reel IDs were captured; real camera/mic/gallery/capture/upload/publish behavior remains unverified before moving to Native LiveKit calls.
- Native Physical Interaction Evidence Path: documented the safest current evidence path for physical iPhone Camera Studio QA: manual iPhone screen recording or QuickTime video capture plus backend ID logging for `chat_media_uploads`, `pulse_posts`, `pulse_status`, and `pulse_reels`. Confirmed `devicectl` can launch/deep-link/suspend/resume but cannot drive taps or screenshots, `idevicescreenshot` remains blocked by the device screenshot service, and no `PulseSocNativeUITests` target exists yet. This mission added no new user-facing feature and preserved production WebView paths.
- Native Captured iPhone Camera Studio QA Pass: collected machine-captured launch, bundle, deep-link, display, process, and syslog evidence on the connected iPhone 16 Pro build. The app foregrounded as `com.pulsesoc.nativeapp`, Metro bundled for iOS, and the Camera Studio payload URL launched at process level. No screenshot/video evidence or backend media/upload/post/status/reel IDs were captured, and syslog showed the camera service remained cold, so real camera/mic/gallery/capture/upload/publish behavior remains unverified before moving to Native LiveKit calls.
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
- `reports/pulsesoc_native_camera_studio_progress.md`
- `reports/pulsesoc_native_camera_studio_device_qa.md`
- `reports/pulsesoc_native_camera_studio_ios_simulator_qa.md`
- `reports/pulsesoc_native_ios_toolchain_compatibility.md`
- `reports/pulsesoc_native_camera_studio_media_qa.md`
- `reports/pulsesoc_native_physical_camera_qa_plan.md`
- `reports/pulsesoc_native_physical_camera_qa_results.md`
- `reports/pulsesoc_native_iphone_camera_interaction_qa.md`
- `reports/pulsesoc_native_physical_interaction_evidence_path.md`
- `reports/pulsesoc_native_iphone_camera_captured_qa.md`
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
- `scripts/pulsesoc_native_camera_studio_audit.py`
- `scripts/pulsesoc_native_camera_studio_device_qa_audit.py`
- `scripts/pulsesoc_native_camera_studio_ios_simulator_qa_audit.py`
- `scripts/pulsesoc_native_camera_studio_media_qa_audit.py`
- `scripts/pulsesoc_native_physical_camera_qa_audit.py`
- `scripts/pulsesoc_native_physical_camera_qa_results_audit.py`
- `scripts/pulsesoc_native_iphone_camera_interaction_qa_audit.py`
- `scripts/pulsesoc_native_physical_interaction_evidence_path_audit.py`
- `scripts/pulsesoc_native_iphone_camera_captured_qa_audit.py`
- `reports/pulsesoc_native_iphone_camera_manual_qa.md`
- `scripts/pulsesoc_native_iphone_camera_manual_qa_audit.py`
- `reports/pulsesoc_native_xctest_camera_qa.md`
- `scripts/pulsesoc_native_xctest_camera_qa_audit.py`

## Remaining Major Features

- Camera Studio device QA hardening and advanced editor expansion
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

Recommendation: either capture a real manual iPhone Camera Studio QA video using the documented evidence path or add a QA-only XCTest UI target to produce screenshots and drive permission/gallery/capture/upload flows before moving to Native LiveKit calls.

This is the highest-value next action based on the current codebase. Native Camera Studio is implemented as a foundation and the installed `com.pulsesoc.nativeapp` development build now compiles, installs, bundles, renders native Login in simulator, survives signed-out foreground/background relaunch in simulator, safely auth-gates Camera Studio deep links, authenticates through a dev-only localhost QA deep link in simulator, and exercises simulator media selection/preview/upload/publish through a QA-only media injection path. The physical iPhone build now launches, bundles, accepts Camera Studio deep links at process level, foregrounds under `com.pulsesoc.nativeapp`, and survives process-level suspend/resume. The captured attempt collected syslog/process evidence, but physical camera/microphone/gallery/upload/publish behavior must remain unverified until screen recording or physical UI automation captures real interactions and backend IDs.

Provider/device QA for Alert Management remains a release blocker, especially APNs/FCM/Expo push delivery, installed-app notification taps, lock-screen presentation, SMS/email/Telegram delivery, and physical-device deep links. That work should continue before any release claim, but it is external-credential/device gated. Among buildable native features, Camera Studio gives the most leverage while reusing existing backend/media logic.

## Why This Comes Next

- Production has substantial camera/media functionality behind `/pulse/camera`, `/pulse/camera/photo`, `/pulse/camera/video`, `/pulse/camera/status`, `/pulse/camera/reel`, and `/pulse/camera/post`.
- Production camera config already exposes `GET /api/pulse/camera/config` with provider, target, mode, fallback, Banuba readiness, upload endpoint, and supported targets.
- Production media upload already writes through `/api/pulse/media/upload`, `chat_media_uploads`, `pulse_media_assets`, `pulse_camera_captures`, Mux/R2 processing, validation, storage, thumbnails, playback URLs, and moderation state.
- Production create-from-camera APIs already exist for posts and reels: `/api/pulse/posts/create-from-camera` and `/api/pulse/reels/create-from-camera`; Status Creator already has native publishing through existing Status APIs.
- Production web camera includes capture modes, front/back camera, microphone toggle, flash/torch fallback, gallery fallback, lenses, beauty modes, filters, preview, privacy/caption, and destination routing.
- Native already has `expo-camera`, `expo-image-picker`, `expo-file-system`, shared `useNativeMediaUpload`, `MediaUploadPreview`, Feed Composer, Status Creator, Profile uploads, Messenger attachments, Marketplace media viewer, and Creator Studio shortcuts.
- Native now has a dedicated Camera Studio screen/route and deep link for `/pulse/camera/*`; signed-out simulator deep links are safely auth-gated, local backend auth/camera config works outside the app, the QA-only simulator login deep link verified authenticated Camera Studio access, and the QA-only media path verified simulator upload/publish routing without weakening production auth.
- Native LiveKit calls are tempting because `@livekit/react-native` and `livekit-client` are installed and backend call APIs exist, but calls depend on reliable push/ringing, lock-screen behavior, microphone/camera permissions, background audio, and real-device QA that is still not established.
- This recommendation is based on the current production routes/services and `mobile-native` implementation inspected on 2026-07-04.

## Reusable Existing PulseSoc Logic

Reuse directly for Native Camera Studio:

- `GET /api/pulse/camera/config`
- `POST /api/pulse/media/upload`
- `POST /api/pulse/media/mux/direct-upload`
- `POST /api/pulse/media/mux/direct-upload/complete`
- `POST /api/pulse/camera/preview`
- `POST /api/pulse/camera/preview/mark-published`
- `POST /api/pulse/posts/create-from-camera`
- `POST /api/pulse/reels/create-from-camera`
- Existing Status create APIs for Status camera publishing.
- Existing Profile avatar/cover APIs for profile camera publishing.
- Existing Messenger media send/upload paths for message camera publishing.
- Existing Marketplace media upload/listing APIs for marketplace camera publishing where supported.
- Existing `camera_filter_engine`, `pulse_lens_engine`, `preview_service`, `upload_progress_service`, `media_service`, `media_storage`, Mux/R2 processing, and moderation/validation.
- Existing database tables including `chat_media_uploads`, `pulse_media_assets`, `pulse_camera_captures`, `pulse_posts`, `pulse_reels`, `pulse_status`, profile media tables, and notification/media-processing logs.
- Existing native `useNativeMediaUpload`, `nativeMediaUpload.ts`, `MediaUploadPreview`, `NativeMediaViewer`, Feed Composer, Status Creator, Profile media upload, Messenger attachment flow, Creator Studio shortcuts, shared `Panel`, shared cache, native routing, and safe web fallback patterns.

Do not duplicate in native:

- Media validation rules.
- Premium filter/lens eligibility.
- Moderation decisions.
- Storage authorization.
- Mux/R2 processing state.
- Post/Reel/Status/Profile/Messenger/Marketplace publishing rules.
- Creator entitlement checks.
- Media repair/processing fallbacks.
- Backend business logic for destinations or visibility.

## What Must Be Hardened Next

- Execute the physical iPhone and Android Camera Studio QA plan for real gallery picker, camera capture, microphone capture, front/back switch, video recording, compression, and large media behavior.
- Add a larger fixture or network-throttled QA harness for upload retry/cancel because the injected simulator image uploads too quickly to interrupt.
- Keep the QA-only deep link disabled outside development native builds and localhost API bases.
- Attach/trust a physical iPhone and attach/authorize a physical Android device, or start an Android emulator.
- QA browser route/layout sweep for `/pulse/camera`, `/pulse/camera/photo`, `/pulse/camera/video`, `/pulse/camera/status`, `/pulse/camera/reel`, and `/pulse/camera/post`.
- Real-device camera permission accept/deny on iOS and Android.
- Real-device microphone permission accept/deny for video capture.
- Photo capture, video recording, front/back camera switch, gallery fallback, and retake flow.
- Upload progress/retry/cancel under real device network conditions.
- Publish handoffs for Feed, Status, Reels, Profile avatar/cover, and Messenger.
- Compression policy tuning only after real device evidence.
- Keep advanced AR/Banuba-native effects, Marketplace media creation, background uploads, and advanced video editing on safe fallback until separately planned.

## Dependencies And Blockers

Dependencies:

- Keep the existing media pipeline server-authoritative.
- Reuse the shared native media upload service instead of creating feature-specific upload logic.
- Preserve production WebView `/pulse/camera` routes and provider behavior.
- Keep unsupported Banuba/native AR SDK behavior on safe fallback unless separately planned.
- Continue Alert Management provider/device QA separately because push and lock-screen remain release blockers.

Blockers:

- Camera, microphone, gallery permissions, large-video handling, compression behavior, and upload memory pressure remain device-only and must not be claimed browser-verified.
- `adb` is now available at `/opt/homebrew/bin/adb`, but `adb devices` shows no attached or authorized device.
- `xcrun simctl` now works and the iPhone 17 Pro simulator boots.
- Expo Doctor now passes under Xcode 26.6 after the native Expo SDK 54 compatibility upgrade.
- `com.pulsesoc.nativeapp` now builds, installs, bundles, and renders the login screen on the iPhone 17 Pro simulator without Expo Go.
- Signed-out Camera Studio deep links now stay on the auth gate without React Navigation route-mismatch warnings after the scoped native linking fix.
- A temporary local QA account/session and local backend were verified outside the app, and a QA-only simulator login deep link now verifies authenticated app access without unreliable text-entry automation.
- `xcrun simctl` does not expose camera permission control in this environment.
- `cliclick` did not reliably affect the Simulator app surface for Gallery/Allow Camera taps, so native picker touch selection remains unverified. QA media injection covered selected-media preview and publish routing instead.
- Upload retry/cancel remains unverified because the QA image upload completes too quickly to interrupt.
- A physical iPhone is visible, trusted, and able to run `com.pulsesoc.nativeapp`.
- No physical Android device is attached.
- No real physical-device Camera Studio interaction flow has been recorded in this workspace; only launch/deep-link/process/syslog evidence has been captured.
- A manual iPhone Camera Studio QA capture pass was prepared, but no human-operated screen recording, QuickTime video, screenshots, syslog tap-through excerpt, backend media/upload IDs, or published post/status/reel IDs were available in this workspace. Manual login/session restore, permissions, gallery picker, photo/video capture, preview, upload, Feed/Status/Reels publish, retry/cancel, foreground/background recovery, and visual quality remain unverified on the physical iPhone.
- Native Camera Studio XCTest QA now has a QA-only `PulseSocNativeUITests` target for `com.pulsesoc.nativeapp`, a Camera Studio UI test, screenshot attachments, and a shared scheme test hook. `xcodebuild build-for-testing` passed and `xcodebuild test` passed with one intentional skip because no restored QA session, QA credentials, or QA Camera Studio deep link was supplied to reach the route. This prepares automation, but it does not yet verify Camera Studio controls, uploads, or publishes end-to-end.
- Provider/device Alert Management QA remains unverified for APNs/FCM/SMS/email/Telegram and should continue as a release-readiness track.
- Native LiveKit calls should stay deferred until push/ringing/device QA is credible.

## Risk Level

Risk: Medium-high.

Reasons:

- Camera touches device permissions, memory, large uploads, compression, video duration, orientation, and media processing.
- Risk is lower than Native LiveKit calls because the backend/media pipeline already exists and the native app already has a working shared media upload foundation.
- Risk is higher than another read-only screen because true camera/gallery behavior cannot be fully verified in the QA browser.
- Production WebView camera must remain untouched while native Camera Studio is built in parallel.

## Estimated Complexity

Complexity: Medium-high.

Recommended first slice:

- Run a built-in QA browser route/layout sweep for the Camera Studio fallback state.
- Run focused real-device QA for one iPhone and one Android device when tooling/devices are available.
- Verify camera/microphone permission-denied states, gallery fallback, capture, upload, retry/cancel, and destination publishing.
- Fix only Camera Studio blockers found.
- Keep unsupported advanced AR/Banuba/effects on safe web fallback.

Defer from first slice:

- LiveKit calls.
- Full AR face tracking or Banuba-native SDK work.
- Background uploads.
- Advanced video trimming.
- Complex drawing/sticker/text editor.
- Claims about device camera/microphone performance until physical-device QA runs.
- Any App Store replacement recommendation.

## Safest Implementation Plan

1. Preserve the current native Camera Studio foundation and production WebView camera routes.
2. Run Gate 1 static checks and audit after any changes.
3. Run Gate 2 built-in QA browser checks for route/layout/fallback behavior.
4. Run Gate 3 device QA before claiming camera, microphone, compression, gallery, or recording behavior is verified.
5. Fix only Camera Studio blockers found.
6. Continue Alert Management provider/device QA and native app identity work as release-readiness blockers, not as production replacement proof.

## Strategy Update

Do not stay stuck in QA loops. Camera Studio physical-device interaction evidence remains a release blocker, not a development blocker. The native app now has enough baseline QA infrastructure to keep building while still using practical quality gates:

- built-in QA browser,
- iOS simulator,
- physical iPhone install/launch,
- XCTest path,
- audit scripts,
- honest browser/simulator/device verification reports.

Only block the roadmap for critical, security-related, data-loss, production-breaking, or impossible-to-fix-later issues.

## Completed Native Features

- Native app foundation and install/typecheck/start baseline.
- Auth/session/login/signup foundation.
- Messenger foundation and QA hardening.
- Notifications foundation.
- Home Feed and Post Detail.
- Feed Composer.
- Profile foundation.
- Reels Player and Reel Detail.
- Status Viewer and Status Creator.
- Shared Media Upload and Media Viewer.
- Marketplace foundation.
- Search and Discovery.
- Saved Content and Collections.
- Groups/Communities/Rooms.
- Live Discovery and Live Viewer.
- Premium and Entitlements.
- Creator Studio.
- Growth Center.
- Intelligence and Alerts.
- Alert Management and Crypto/Market Alert CRUD.
- Camera Studio foundation, simulator QA, XCTest QA path, and physical iPhone install/launch proof.
- Native Calls foundation.

## Native Calls Foundation

Recommended and implemented next feature/action: Native LiveKit Calls foundation.

Why it came next:

- PulseSoc already has a mature server-authoritative Communications V2 call engine.
- The native app already has Messenger, notifications/deep links, profile navigation, camera/media groundwork, Live discovery/viewer, and practical QA gates.
- Calls are high leverage for Messenger and real-time social behavior, and deferring all call work until perfect physical Camera Studio proof would slow the roadmap without reducing backend risk.

Reusable PulseSoc APIs/code/database/business logic:

- `POST /api/pulse/comm/v2/conversations/<conversation_ref>/voice/start`
- `POST /api/pulse/comm/v2/conversations/<conversation_ref>/video/start`
- `POST /api/calls/start`
- `POST /api/calls/<call_id>/accept`
- `POST /api/calls/<call_id>/ring-seen`
- `POST /api/calls/<call_id>/decline`
- `POST /api/calls/<call_id>/end`
- `POST /api/calls/<call_id>/join-token`
- `GET /api/calls/<call_id>/status`
- `GET /api/calls/active`
- `POST /api/calls/<call_id>/quality`
- `POST /api/calls/<call_id>/connected`
- `GET /api/calls/<call_id>/events`
- `GET /api/conversations/<conversation_ref>/calls`
- Native call control endpoints for mute, unmute, video enable/disable, camera switch, speaker, minimize, restore, and visibility.
- Existing `services/pulsesoc_communications_engine.py` call state, authorization, LiveKit token, event, device-session, quality-report, and notification logic.
- Existing database tables including `communication_calls`, `communication_call_participants`, `communication_call_events`, `communication_call_quality_reports`, and `communication_call_device_sessions`.

What was rebuilt natively:

- Native call route.
- Native call screen.
- Messenger voice/video entry points.
- LiveKit connection shell for installed native builds.
- Safe browser/web fallback behavior.
- Native deep-link handling for `/pulse/calls/<call_id>` and existing message links with `call_id`.
- Native controls that report state changes back to the existing backend.

Dependencies/blockers:

- Full two-device LiveKit media QA remains unverified.
- APNs/FCM incoming-call delivery and lock-screen behavior remain release blockers.
- Bluetooth/speaker route behavior remains device-only.
- Background audio behavior remains device-only.
- Physical iOS/Android camera/microphone permission behavior for calls remains device-only.

Risk level: high.

Estimated complexity: high.

Safest implementation plan:

1. Keep the backend authoritative for all call state, authorization, participants, notifications, and tokens.
2. Keep the native layer limited to route/UI/device connection/control behavior.
3. Use safe web fallback for unsupported or unverified environments.
4. Run static verification and audit on every call change.
5. Run QA browser routing checks where practical.
6. Schedule full two-device iOS/Android LiveKit call QA before production replacement or App Store submission.

## Recommendation Summary

Recommended next highest-value action after Native Calls foundation: run a short practical QA browser/static sweep focused on call route reachability, Messenger call entry points, notification/deep-link routing, safe fallback behavior, and no production WebView changes. Then continue building the next high-leverage native surface unless the sweep finds a critical/security/data-loss/production-breaking issue.

Reason: Native Calls now reuses the existing PulseSoc Communications V2 engine and LiveKit token flow instead of duplicating call business logic. The largest remaining call risks are device/provider QA items that should block release, not continued native app development.
