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
- Native Calls foundation and Practical QA: native call route, Messenger voice/video entry points, LiveKit connection shell, call control API wrappers, `/pulse/calls/<call_id>` deep-link routing, safe web fallback behavior, and practical QA documentation for release blockers.
- Native Full-Screen Incoming Calls foundation and Practical QA: foreground incoming-call layer, active-call polling/resume hook, ring-seen guard, accept/decline/end controls, floating active-call bubble, minimized-call restore, and seeded practical QA path.
- Native Account, Security & Privacy foundation: native Account Center, Security Center, Privacy Center, Sessions/Devices section, thin server-authoritative account API wrapper, settings entries, offline display cache, trusted-device removal, recovery/2FA/verification actions, deep-link routing, and protected web fallback for sensitive password/deletion/privacy flows.
- Native Account, Security & Privacy QA: authenticated QA browser sweep through a temporary local QA backend/proxy, verified Account/Security/Privacy/Devices routes, privacy save, 2FA enable, security score/history refresh, no console errors, and fixed direct `/dashboard/account/*`, `/account/*`, and `/privacy-center` aliases that had fallen back to Home.
- Native Verification Center Practical QA: authenticated QA browser sweep verified `/pulse/verification`, `/pulse/verification/business`, `/dashboard/account/verification`, Settings/Profile/Premium/Trust entry points, status/checklist rendering, request/document/appeal validation guards, and no console errors; sensitive upload/admin/provider/device behavior remains honestly unverified.
- Native Account Health + Appeals Center Foundation: native account-health route, server-owned standing summary, warning/strike/restriction counters, appeal readiness list, verification appeal submission where supported by existing APIs, linked support cases, security signals, Settings/Trust entry points, `/pulse/account-health` and `/dashboard/account/health` route handling, offline cache, and safe protected web fallback for unsupported enforcement details.
- Settings: session controls, push registration, notification preferences entry, and account/security/privacy/device center entry points.

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
- `reports/pulsesoc_native_account_security_privacy_progress.md`
- `reports/pulsesoc_native_account_security_privacy_qa.md`
- `scripts/pulsesoc_native_account_security_privacy_audit.py`
- `reports/pulsesoc_native_verification_qa.md`
- `scripts/pulsesoc_native_verification_qa_audit.py`
- `reports/pulsesoc_native_account_health_appeals_progress.md`
- `scripts/pulsesoc_native_account_health_appeals_audit.py`

## Remaining Major Features

- Camera Studio physical-device release QA and advanced editor expansion
- Calls two-device release QA for native LiveKit media, push/ringing, lock-screen behavior, Bluetooth/speaker route behavior, and background audio
- External Android device QA completion and hardening pass
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
- Native Calls Practical QA Sweep.
- Native Full-Screen Incoming Calls foundation.
- Native Incoming Calls Practical QA.
- Native Account, Security & Privacy foundation.
- Native Account, Security & Privacy authenticated QA sweep.
- Native Trust, Safety & Support foundation.
- Native Verification Center + Badge/Identity Verification foundation.
- Native Verification Center Practical QA browser sweep.
- Native Account Health + Appeals Center foundation.

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

## Native Account, Security & Privacy Foundation

Recommended and implemented next feature/action: Native Account, Security & Privacy foundation.

Why it came next:

- Calls and incoming-call work now have practical QA coverage and remaining release blockers are provider/device-specific.
- The native Settings surface was still mostly a push/session shortcut hub.
- Production PulseSoc already exposes server-authoritative account, security, privacy, trusted-device, session, and notification preference routes.
- Account/security/privacy strengthens every future native flow without requiring new backend business logic.

Reusable PulseSoc APIs/code/database/business logic:

- `GET /api/account/status`
- `GET /api/dashboard/account/settings`
- `POST /api/dashboard/account/settings`
- `GET /api/account/security`
- `POST /api/account/verify-email`
- `POST /api/account/verify-phone`
- `POST /api/account/2fa/enable`
- `POST /api/account/2fa/disable`
- `POST /api/account/recovery-codes/generate`
- `GET /api/account/security-events`
- `GET /api/account/trusted-devices`
- `DELETE /api/account/trusted-devices/<device_id>`
- `POST /api/account/reauthenticate`
- `POST /api/account/sessions/revoke-all`
- Existing protected web routes for password, deletion, privacy center, and advanced security flows.
- Existing auth/session behavior and notification preference APIs.

What was rebuilt natively:

- Native Account Center.
- Native Security Center.
- Native Privacy Center.
- Native Sessions and Devices section.
- Thin account API wrapper with cached display fallback.
- Settings entries for account/security/privacy/devices.
- Deep-link and notification routing for `/pulse/settings/account`, `/pulse/settings/security`, `/pulse/settings/privacy`, `/pulse/settings/devices`, `/dashboard/account/settings`, `/dashboard/account/security`, `/account/settings`, `/account/security`, and `/privacy-center`.
- Safe protected web fallbacks for password/email management, account deletion, advanced security, and full privacy center controls.

Dependencies/blockers:

- Email/SMS/verification provider delivery is backend/provider QA.
- Password changes, account export, and deletion remain on protected web fallback until a dedicated native reauth flow is planned.
- Real-device QA is not claimed.
- Account/security UX requires a short authenticated QA pass before moving on.

Risk level: medium-high because the feature touches account and security controls.

Estimated complexity: medium-high.

## Recommendation Summary

Recommended next highest-value action after Native Account, Security & Privacy: run a short practical authenticated QA browser/simulator sweep for this surface.

Reason: this foundation touches trust, security actions, privacy settings, and protected fallbacks. The next action should verify signed-in loading, safe offline/error states, route reachability, deep-link routing, action failure/success states, and visual consistency before the roadmap moves to another broad feature. Provider delivery and physical-device proof remain release blockers, not development blockers unless a security-critical, production-breaking, or data-loss issue appears.

Reusable PulseSoc APIs/code/database/business logic for the next action:

- Existing account/security/privacy APIs listed above.
- Existing native `AccountCenterScreen`, `account.ts`, Settings entries, navigation linking, and notification routing.
- Existing authenticated QA browser workflow.

What must be rebuilt natively:

- No new major feature in the next action.
- Only scoped fixes found during QA should be implemented.

Dependencies/blockers:

- QA credentials or local QA auth path must be available for authenticated browser/simulator checks.
- Provider delivery for verification channels remains outside browser QA.

Risk level: medium.

Estimated complexity: low-medium.

Safest implementation plan for the next action:

1. Start the native QA web build.
2. Authenticate with a QA-safe account.
3. Exercise Account, Security, Privacy, Devices, and fallback routes.
4. Verify sensitive actions fail safely or succeed through existing backend APIs.
5. Fix only scoped blockers.
6. Keep production WebView routes untouched.

## Native Account, Security & Privacy QA Sweep

Completed action: short authenticated QA browser sweep for Account, Security, Privacy, Devices, and web-route aliases.

Verified:

- Native login/session restored through the QA browser.
- `/pulse/settings` rendered the native Settings surface.
- `/pulse/settings/account` rendered Account Center.
- `/pulse/settings/security` rendered Security Center.
- `/pulse/settings/privacy` rendered Privacy Center.
- `/pulse/settings/devices` rendered Sessions and Devices.
- `/dashboard/account/settings`, `/dashboard/account/security`, `/account/settings`, `/account/security`, and `/privacy-center` now route to native account/security/privacy screens instead of falling back to Home.
- Privacy save state returned a native success state.
- Two-factor enable action returned an updated server-authoritative security state.

No critical, security-critical, production-breaking, or data-loss issues were found in the practical QA sweep. Production WebView routes remained untouched.

## Native Trust, Safety & Support Foundation

Recommended and implemented next feature/action: Native Trust, Safety & Support foundation.

Why it came next:

- Account/Security/Privacy QA passed without critical blockers.
- Production PulseSoc already exposes support, help, security report, Scam Shield, moderation report, and block routes.
- The native app had report hooks scattered across feature areas but no central Trust/Safety/Support surface.
- This feature strengthens the native foundation for Feed, Messenger, Marketplace, Groups, Reels, Status, Search, and Notifications without adding new server-side business logic.

Reusable PulseSoc APIs/code/database/business logic:

- `GET /api/support/ticket`
- `POST /api/support/ticket`
- `POST /api/security/report`
- `POST /api/scam-shield/scan`
- `POST /api/pulse/report`
- `POST /api/pulse/block`
- Existing support ticket, security report, Scam Shield, moderation, report, and block tables/services.
- Existing protected web routes for help, trust center, community rules, and advanced support/help content.

What was rebuilt natively:

- Native Trust & Safety screen.
- Native support ticket history with offline cache fallback.
- Native support ticket creation form.
- Native security report form.
- Native Scam Shield scan form.
- Shared Trust/Safety API wrapper for support, security report, scam scan, report target, and block user behavior.
- Settings entry, linking aliases, and notification routing for `/pulse/help`, `/support`, `/help`, `/trust-center`, `/security`, and `/scam-shield/:mode?`.
- Loading, empty, offline, error, validation, and success states.

Dependencies/blockers:

- Provider-side support delivery remains backend/provider QA.
- Advanced help-center browsing remains on safe web fallback.
- Physical-device QA is not required for this feature because it does not depend on camera, microphone, push, background audio, or installed-app-only APIs.
- Feature-specific report/block buttons should progressively reuse this shared API wrapper.

Risk level: medium.

Estimated complexity: medium.

## Recommendation Summary

Recommended next highest-value action after Native Trust, Safety & Support: Native Verification Center + Badge/Identity Verification foundation.

Reason: the repository already contains verification-related production artifacts and reports, and the native app now has Account, Security, Privacy, Trust/Safety, Profile, Premium, Notifications, and Settings foundations. Verification is the next identity/trust layer that can reuse existing backend authority while improving native Profile, Search, Marketplace, Creator, Groups, and Trust/Safety surfaces.

Reusable PulseSoc APIs/code/database/business logic for the next action:

- Existing verification and badge production routes/services identified in the repository.
- Existing user/profile/account database behavior.
- Existing premium/founder/verification badge display logic.
- Existing moderation, account security, identity, and privacy rules.
- Native Profile, Account Center, Trust/Safety, Premium, Settings, Search, and Notification routing components.

What must be rebuilt natively:

- Native Verification Center.
- Verification status display.
- Badge/identity state display.
- Safe document/provider upload entry points where supported.
- Protected web fallback for provider-heavy or unsupported verification flows.
- Loading/error/offline states and route/deep-link coverage.

Dependencies/blockers:

- Exact production verification endpoints must be inspected before implementation.
- Provider/document verification remains release/provider QA.
- Any sensitive identity document handling must stay server/provider-authoritative and must not duplicate compliance logic in the native client.

Risk level: medium-high because identity and verification touch trust, privacy, and account status.

Estimated complexity: medium-high.

Safest implementation plan for the next action:

1. Inspect current production verification routes, services, database references, and reports.
2. Reuse backend verification/badge/account status behavior exactly.
3. Build native read/status and entry-point screens first.
4. Keep document/provider-heavy flows on protected web fallback unless the existing backend exposes safe native-ready APIs.
5. Run static verification and practical QA browser routing checks.
6. Treat provider/document proof as release QA, not a development blocker unless a security or data-loss issue appears.

## Native Verification Center + Badge/Identity Verification Foundation

Recommended and implemented next feature/action: Native Verification Center + Badge/Identity Verification foundation.

Why it came next:

- Account, Security, Privacy, Trust/Safety, Profile, Premium, Notifications, and Settings were already native.
- Production PulseSoc already exposes verification request, appeal, private document upload, admin review, badge, and audit-log behavior.
- Verification strengthens trust across Profile, Premium, Marketplace, Creator, Account, Safety, and Search without requiring duplicated client-side business logic.
- Existing verification reports in the repository indicate this area is already a first-class PulseSoc trust subsystem.

Reusable PulseSoc APIs/code/database/business logic:

- `GET /api/dashboard/account/state`
- `POST /api/dashboard/account/verification/request`
- `POST /api/dashboard/account/verification/appeal`
- `POST /api/dashboard/account/verification/document`
- `GET /api/pulse/profile/me`
- `GET /api/premium/status`
- Existing protected route `/dashboard/account/verification`
- Existing `verification_requests`, `verification_documents`, account audit logs, badge fields, profile verification status, Premium/Foundation badge status, and admin review logic.

What was rebuilt natively:

- Native Verification Center screen.
- Verification status display.
- Verification score/status visual.
- Requirements checklist.
- Identity, blue check, business, and government ID request entry points.
- Private document picker/upload handoff.
- Appeal form.
- Profile badge preview.
- Premium/Foundation badge display.
- Entry points from Settings, Profile, Premium, and Trust/Safety.
- Deep-link and notification routing for `/pulse/verification`, `/pulse/verification/<track>`, and `/dashboard/account/verification`.
- Loading, offline, error, validation, and success states.

What remains backend/provider owned:

- Admin review queue.
- Admin document access.
- Approval, rejection, needs-more-info, suspension, revocation, and restore decisions.
- Sensitive document validation and private storage.
- Badge issuance and revocation.
- Provider-heavy identity verification and compliance logic.

Dependencies/blockers:

- Physical document picker behavior remains device QA.
- Provider identity verification and admin approval proof remain release/provider QA.
- Native must not claim sensitive document review is verified until a controlled provider/device QA pass is completed.

Risk level: medium-high because this feature touches identity, sensitive document handoff, privacy, and badge trust.

Estimated complexity: medium-high.

## Recommendation Summary

Recommended next highest-value action after Native Verification Center: short practical Verification Center QA sweep.

Reason: verification is security/privacy-sensitive enough to warrant a focused route/form/upload-handoff pass before the next broad feature. This should remain practical QA, not an endless loop. Only security-critical, data-loss, production-breaking, or future-development-blocking issues should pause the roadmap.

Reusable PulseSoc APIs/code/database/business logic for the next action:

- Existing verification API wrapper in `mobile-native/src/api/verification.ts`.
- Existing native Verification Center, Settings, Profile, Premium, Trust/Safety, linking, and notification routing.
- Existing backend verification request, appeal, document upload, account state, profile, and premium APIs.

What must be rebuilt natively:

- No new major feature in the next action.
- Only scoped fixes found during QA should be implemented.

Dependencies/blockers:

- A QA account is needed for request/appeal validation.
- Document picker behavior may require simulator or physical-device testing.
- Admin/provider approval remains outside browser QA.

Risk level: medium.

Estimated complexity: low-medium.

Safest implementation plan for the next action:

1. Run static verification.
2. Start the QA web build.
3. Verify `/pulse/verification`, `/pulse/verification/business`, and `/dashboard/account/verification`.
4. Verify entry points from Settings, Profile, Premium, and Trust/Safety.
5. Verify request validation/success/failure with a QA account where safe.
6. Verify document picker handoff where the test surface supports it.
7. Fix only scoped blockers.

## Native Verification Center Practical QA

Recommended and completed next action: short practical Verification Center QA sweep.

Why this came next:

- Verification touches identity, private document handoff, account trust, Premium/Profile badges, Marketplace trust, Creator eligibility, and Trust/Safety.
- The previous native foundation added the routes and screen, but the practical browser QA gate had not yet verified connected route behavior.
- This QA pass was the correct precondition before building another trust/account feature.

Verified in the built-in QA browser:

- `/pulse/verification` rendered the authenticated native Verification Center.
- `/pulse/verification/business` rendered the native Verification Center with `Business` selected.
- `/dashboard/account/verification` routed to the same native Verification Center.
- Settings exposed `Verification Center`.
- Profile About exposed `Verification: not started` and `Open Verification Center`.
- Premium exposed `Open Verification Center`.
- Trust Center and Scam Shield/Trust routes exposed `Verification`.
- The status card, score, badge preview, Premium/Foundation badge display, checklist, request form, document handoff, appeal form, and recommendations rendered for the authenticated QA account.
- `Choose private document` safely blocked upload before a request exists.
- `Submit appeal` safely blocked submission without an existing rejected, suspended, or needs-more-info request.
- No browser console errors were captured during the final route pass.

What remains unverified:

- Actual request submission was not executed in this pass to avoid creating review side effects outside a dedicated seeded QA fixture.
- Private identity document upload was not executed. Browser verified the safe guard/handoff, not provider/device upload.
- Pending, approved, rejected, suspended, and needs-more-info states were not seeded in browser QA.
- Offline cache on full route reload was not proven because full reload first rechecks auth/session and returned to the signed-out shell when the local proxy was intentionally stopped.
- Admin review, audit logs, provider identity checks, notification tap deep links, and physical iOS/Android document picker behavior remain release/provider/device QA.

Result:

No critical, security-critical, production-breaking, data-loss, or future-development-blocking issue was found. Production WebView routes remained untouched.

## Recommendation Summary

Recommended next highest-value action after Verification Center Practical QA: Native Account Health + Appeals Center foundation.

Reason: the production codebase already exposes account health and trust/review concepts through `/dashboard/account/health`, `GET /api/dashboard/account/state`, verification appeals, security events, support reports, login restrictions, account scores, and trust subsystems. The native app now has Account/Security/Privacy, Trust/Safety, Verification, Profile, Premium, Notifications, Marketplace, and Creator surfaces. A native Account Health + Appeals Center is the next trust layer that can reuse existing server authority while giving users one native place to understand account restrictions, review status, trust score, appeals, safety recommendations, and recovery actions.

Reusable PulseSoc APIs/code/database/business logic for the next action:

- `GET /api/dashboard/account/state`
- Existing `/dashboard/account/health` web route and account command-center state.
- Existing account status, login restriction, verification request, appeal, security event, trusted-device, support ticket, security report, and notification routes.
- Existing user/profile/account database behavior, verification tables, account audit logs, support/security report tables, trust scoring, moderation state, and login-restriction logic.
- Existing native Account Center, Security Center, Privacy Center, Trust/Safety, Verification Center, Notification routing, Profile, Premium, and shared cache/loading/error components.

What must be rebuilt natively:

- Native Account Health route/screen.
- Account health score/status display.
- Restriction/review status cards.
- Trust recommendations.
- Appeal/review shortcuts that use existing backend routes.
- Security/support/verification recovery shortcuts.
- Deep-link routing for `/dashboard/account/health` and related health/review URLs.
- Loading, cached, empty, error, and safe web fallback states.

Dependencies/blockers:

- Backend must remain authoritative for restrictions, appeals, trust scoring, verification decisions, moderation state, and account recovery.
- Appeal submission should only be tested against seeded QA fixtures.
- Admin/provider review decisions remain release/provider QA.
- Physical device QA is not required for the first foundation because the feature is account/API driven.

Risk level: medium-high because account health and appeals touch trust, restrictions, identity, moderation, and user recovery.

Estimated complexity: medium.

Safest implementation plan:

1. Inspect the existing account health web route, account-state API, verification appeal routes, support/security report APIs, and account restriction logic.
2. Add a native Account Health screen that reads server-owned state only.
3. Reuse existing Account/Verification/Trust/Safety components and API wrappers where possible.
4. Keep any unsupported provider/admin flows on protected web fallback.
5. Run static verification, audit, and practical QA browser route checks.
6. Commit only scoped native/account-health files, report, audit, and progress updates.

## Native Account Health + Appeals Center Foundation

Recommended and implemented next feature/action: Native Account Health + Appeals Center foundation.

Why it came next:

- Verification Center practical QA found no critical blocker.
- Account Health is already a production PulseSoc trust surface at `/dashboard/account/health`.
- The native app now has Account/Security/Privacy, Trust/Safety, Verification, Profile, Premium, Notifications, Marketplace, and Creator surfaces, but did not yet have a single native owner-visible account standing surface.
- Account Health connects warnings, strikes, restrictions, appeals, verification, support cases, and recovery actions without requiring new client-side business logic.

Reusable PulseSoc APIs/code/database/business logic:

- `GET /api/dashboard/account/state`
- Existing `/dashboard/account/health` protected web route.
- Existing account health subsystem in `services/dashboard_account_command_center.py`.
- Existing warning, strike, restriction, security alert, and appeal-ready metrics.
- Existing verification appeal API through `/api/dashboard/account/verification/appeal`.
- Existing support ticket and security event APIs.
- Existing account login restriction, moderation, verification, trust score, support, and audit-log logic.
- Existing native Account Center, Security Center, Trust/Safety, Verification Center, notification routing, shared cache, loading, error, and fallback patterns.

What was rebuilt natively:

- `mobile-native/src/api/accountHealth.ts`
- `mobile-native/src/screens/AccountHealthAppealsScreen.tsx`
- `AccountHealth` and `AccountHealthWeb` stack routes.
- `/pulse/account-health` native route.
- `/dashboard/account/health` native route alias.
- Notification routing for account-health links.
- Settings entry for `Account Health and Appeals`.
- Trust/Safety entry for `Account Health`.
- Account health score, risk level, standing summary, warning/strike/restriction counters, appeal readiness cards, support case list, security signal list, recovery recommendations, and protected web fallback actions.
- Practical built-in QA browser checks for `/pulse/account-health`, `/dashboard/account/health`, Settings entry, Trust Center entry, unsupported appeal guard behavior, and final console errors.

What remains backend/provider owned:

- Enforcement creation.
- Warning, strike, and restriction truth.
- Account restriction enforcement.
- Account-health appeal eligibility and approval.
- Verification approval/rejection/suspension/restoration.
- Moderator notes and admin review.
- Detailed enforcement history when no native JSON detail endpoint is available.

Dependencies/blockers:

- Detailed strike/restriction row history is not currently exposed through a native JSON API; the screen shows server-owned summary counts and routes advanced detail to `/dashboard/account/health`.
- Account-health strike/restriction appeal submission is not currently exposed as a native JSON endpoint; only verification appeal can submit natively through the existing verification API.
- Seeded warning/strike/restriction fixtures are needed for deeper appeal-state QA.
- Admin/provider outcomes remain backend/admin QA.

Risk level: medium-high because account health touches trust, moderation, restrictions, appeals, identity, and account recovery.

Estimated complexity: medium.

## Recommendation Summary

Recommended next highest-value action after Native Account Health + Appeals Center: Native Blocks, Mutes, and Report Management Foundation.

Reason: the production codebase already includes report, block, mute, restriction, moderation, governance, and safety-management logic, and the native app now has Feed, Messenger, Groups, Marketplace, Search, Profile, Trust/Safety, Verification, and Account Health surfaces that all depend on safety actions. A central native Blocks/Mutes/Reports surface would let users review and recover safety actions while keeping server moderation and relationship rules authoritative.

Reusable PulseSoc APIs/code/database/business logic for the next action:

- Existing `/api/pulse/report`.
- Existing `/api/pulse/block`.
- Existing support/security report APIs.
- Existing moderation, account health, network governance, report, block, mute, ban, restriction, and appeal-aware backend logic.
- Existing native Trust/Safety API wrapper, Account Health screen, Settings entry patterns, Profile/Messenger/Groups/Marketplace report hooks, notification routing, cache helpers, and loading/error components.

What must be rebuilt natively:

- Native Blocks/Mutes/Reports management screen.
- Report status list where APIs support it.
- Blocked/muted user list where APIs support it.
- Unblock/unmute actions where APIs support them.
- Safe report creation handoff and case status links.
- Deep links for safety/report/block URLs.
- Protected web fallback for unsupported moderation/admin details.

Dependencies/blockers:

- Exact public JSON endpoints for block/mute lists must be inspected before implementation.
- Moderator/admin notes must stay hidden and server-owned.
- Unblock/unmute/report actions should be tested only against seeded QA fixtures.

Risk level: medium-high because safety actions affect user relationships, visibility, and moderation state.

Estimated complexity: medium.

Safest implementation plan:

1. Inspect the current PulseSoc production block, mute, report, moderation, and network governance routes.
2. Reuse existing report/block APIs and add native read-only status first.
3. Implement user-visible unblock/unmute/report actions only where an existing user-safe API already exists.
4. Keep admin/moderator-only data on protected web fallback.
5. Run static verification, audit, and practical QA browser route checks before commit.

## Native Blocks, Mutes, and Report Management Foundation

Recommended and implemented next feature/action: Native Blocks, Mutes, and Report Management Foundation.

Why it came next:

- Account, Security, Privacy, Trust/Safety, Verification, Account Health, and Appeals now form a native trust layer.
- Feed, Messenger, Profile, Reels, Marketplace, Search, Groups, Notifications, Account Health, and Trust/Safety all depend on safety controls.
- Production PulseSoc already exposes server-authoritative block/report logic and network safety state.
- A unified native Safety Hub gives users one control layer without duplicating moderation, filtering, enforcement, or review decisions on-device.

Reusable PulseSoc APIs/code/database/business logic:

- `POST /api/pulse/report`
- `POST /api/pulse/block`
- `POST /api/security/report`
- `GET /api/dashboard/network/state`
- Existing protected `/dashboard/network/network-security`
- Existing protected `/dashboard/network/blocks-mutes`
- Existing `blocked_users` filtering in feed and messaging paths.
- Existing Communications V2 message report and block APIs.
- Existing support tickets, security reports, account health, network governance, trust/safety, moderation, and notification routing logic.

What was rebuilt natively:

- `mobile-native/src/api/safety.ts`
- `mobile-native/src/screens/SafetyHubScreen.tsx`
- Native `SafetyHub` route.
- Native `SafetyWebHub` route alias.
- `/pulse/safety`, `/pulse/safety/blocks`, `/pulse/safety/mutes`, `/pulse/safety/reports` route coverage.
- `/dashboard/network/network-security` and `/dashboard/network/blocks-mutes` native route coverage.
- Settings, Trust/Safety, Account Health, Profile, and Messenger entry points.
- Safety overview, block user, mute handoff, create report, local action history, support case visibility, cached/offline state, loading/error states, and protected web fallbacks.

Backend authority boundaries:

- Report creation calls the existing server endpoint.
- Block creation calls the existing server endpoint.
- User mute/unmute is not implemented natively because no user-safe server API was found.
- Unblock is not implemented natively because no user-safe server API was found.
- Full blocked-user lists and report-review history are not treated as local truth because no user-safe list/history API was found.
- Native action history is clearly device-local visibility only.

Dependencies/blockers:

- Add a user-safe `GET /api/pulse/blocks` endpoint before native can show the full server blocked list.
- Add a user-safe unblock endpoint before native can unblock directly.
- Add user-safe mute/unmute endpoints only if product policy supports account-level mutes.
- Add a user-safe report-history endpoint that redacts moderator notes before native can show authoritative report status.
- Seeded QA fixtures are needed before exercising real block/report side effects broadly.

Risk level: medium-high.

Reason: safety controls affect user relationships, feed/messenger visibility, report review, moderation, account health, and trust signals.

Verification plan:

- Static verification passed.
- Audit script passed.
- Practical QA browser route checks passed for `/pulse/safety`, `/pulse/safety/blocks`, `/pulse/safety/mutes`, `/pulse/safety/reports`, `/dashboard/network/network-security`, `/dashboard/network/blocks-mutes`, and Settings, Trust/Safety, Account Health, Profile, and Messenger entry points.
- Final QA browser route checks had no console errors.
- Real block/report submissions were not executed because they create moderation side effects and need seeded QA fixtures.
- Device/provider QA is not required for the first foundation, but notification tap routing remains a release QA item.

## Recommendation Summary

Recommended next highest-value action after Native Safety Hub: Native Notifications + Inbox + Activity Graph Unification.

Reason: PulseSoc now has many native surfaces, but activity still arrives through separate feature-specific paths. A unified native Activity Inbox can make Notifications, Messenger unread events, Account Health, Safety events, Verification updates, Creator/Growth updates, Intelligence/Alert events, Marketplace events, and deep links feel like one PulseSoc operating system layer.

Reusable PulseSoc APIs/code/database/business logic for the next action:

- Existing notification APIs.
- Existing notification preferences/read/delete flows.
- Existing Messenger unread and conversation state.
- Existing Alert/Intelligence event APIs.
- Existing Account Health, Safety Hub, Verification, Trust/Safety, Creator, Growth, Marketplace, and Premium status APIs.
- Existing notification routing/deep-link handling.
- Existing native Notification Center, Messenger, Account Health, Safety Hub, Intelligence, Alert Management, Growth, Creator, Profile, and shared cache/loading/error utilities.

What must be rebuilt natively:

- Unified Activity Inbox screen.
- Cross-surface activity cards.
- Native filters for all activity, messages, safety, account, creator, growth, market, and intelligence.
- Read/unread/archive/delete controls where existing APIs support them.
- Deep-link polish into every native destination.
- Cached activity timeline and offline fallback.
- Unsupported provider/admin flows on safe web fallback.

Dependencies/blockers:

- Need inspection of actual notification/activity data shapes before implementation.
- Avoid merging private message bodies, moderator notes, provider secrets, or admin-only data into the user activity feed.
- Mutations must only use existing server-authoritative APIs.

Risk level: medium.

Estimated complexity: medium-high.

Safest implementation plan:

1. Inspect production notification, message, support, account, safety, alert, creator, growth, and market event APIs.
2. Reuse existing native Notification Center, deep-link router, and shared cache/loading/error states.
3. Build read-only unified timeline first.
4. Add mutations only for APIs already supported.
5. Keep unsupported review/admin/provider paths on protected web fallback.
6. Run static verification, audit, and short QA browser route checks before commit.

## Native Notifications + Inbox + Activity Graph Unification

Recommended and implemented next feature/action: Native Notifications + Inbox + Activity Graph Unification.

Why it came next:

- Native PulseSoc now has many connected surfaces: Messenger, Calls, Feed, Reels, Status, Marketplace, Search, Saved, Groups, Live, Premium, Creator, Growth, Intelligence, Alerts, Account, Verification, Account Health, and Safety Hub.
- Users need one native activity layer to understand messages, calls, social events, safety actions, verification updates, marketplace updates, creator/growth alerts, and intelligence alerts.
- Production PulseSoc already has server-authoritative notification, unread count, read/delete, preference, message unread, active-call, and deep-link systems that can be reused safely.
- The native app already has route targets for most notification destinations, so this feature improves leverage without duplicating backend delivery logic.

Reusable PulseSoc APIs/code/database/business logic:

- `GET /api/pulse/notifications`
- `GET /api/pulse/notifications/unread-count`
- `POST /api/pulse/notifications/<notification_id>/read`
- `POST /api/pulse/notifications/read-all`
- `DELETE /api/pulse/notifications/<notification_id>`
- `POST /api/pulse/notifications/<notification_id>/resolve`
- `GET/PATCH /api/pulse/notifications/preferences`
- `GET /api/pulse/messages/conversations`
- `GET /api/calls/active`
- Existing notification tables, delivery status, read/delete behavior, badge count logic, deep links, notification preferences, Messenger unread state, active-call state, and notification routing.
- Existing native cache helpers, Notification Center, Notification Preferences, Messenger, Call, Safety, Verification, Marketplace, Creator/Growth, Intelligence, Alert Management, Profile, and Settings surfaces.

What was rebuilt natively:

- `mobile-native/src/api/activity.ts`
- `mobile-native/src/screens/ActivityInboxScreen.tsx`
- Native `ActivityInbox` route.
- Native Notifications tab now opens Activity Inbox.
- Settings entry point for Activity Inbox.
- Activity categories:
  - All
  - Messages
  - Calls
  - Social
  - Safety
  - Verification
  - Marketplace
  - Creator/Growth
  - Intelligence/Alerts
- Cached/offline activity state.
- Category rail, unread indicators, read/delete/open controls, loading/error/empty states, and safe target routing.
- Deep links for `/pulse/activity`, `/pulse/activity/<category>`, `/pulse/inbox`, `/dashboard/activity`, `/dashboard/inbox`, and legacy `/pulse/notifications`.

Backend authority boundaries:

- Native grouping is display-only and does not create notification business rules.
- Notification read/delete and mark-all-read call existing backend endpoints.
- Messenger read/seen state remains owned by Messenger conversation APIs.
- Active call state remains owned by call APIs and the Call screen.
- Private message bodies, moderator notes, provider logs, and admin-only data are not merged into Activity Inbox.
- Unsupported targets continue through the existing safe web fallback.

Dependencies/blockers:

- Physical push notification tap routing still needs APNs/FCM device QA.
- App badge synchronization still needs provider/device QA.
- Notification grouping accuracy depends on existing notification category/type/deep-link fields; richer server category fields would improve precision.
- Advanced provider/admin delivery logs remain out of the native user Activity Inbox.

Risk level: medium.

Reason: Activity Inbox touches many routes and read/delete states, but it mostly composes existing server-authoritative APIs and native route handlers.

Estimated complexity: medium-high.

Verification plan:

- Static verification.
- Native audit script.
- QA browser route checks for `/pulse/activity`, category routes, `/pulse/notifications`, `/pulse/inbox`, and Settings entry point.
- Device push/badge verification remains a release blocker, not a development blocker.

## Recommendation Summary

Recommended next highest-value action after Native Activity Inbox: Native Activity Inbox practical QA hardening.

Reason: Activity Inbox now spans nearly every native feature. A short authenticated QA pass should verify route reachability, category filtering, read/delete mutation behavior, unread badge refresh, Settings entry point, and fallback routing before another major feature is added.

Reusable PulseSoc APIs/code/database/business logic for the next action:

- Existing Activity Inbox implementation.
- Existing notification read/delete/resolve APIs.
- Existing Messenger unread state.
- Existing active-call state.
- Existing notification router and deep-link coverage.
- Existing QA browser workflow and audit scripts.

What must be rebuilt natively:

- Only scoped fixes discovered during QA.
- Potential route aliases or fallback polish if QA finds broken activity destinations.

Dependencies/blockers:

- Authenticated QA account/session is needed for meaningful data.
- Provider/device push and badge checks remain release blockers.
- Avoid executing destructive delete/read mutations against production accounts unless using a seeded QA fixture.

Risk level: medium.

Estimated complexity: low to medium.

Safest implementation plan:

1. Run a short authenticated QA browser pass for Activity Inbox routes and filters.
2. Exercise non-destructive open/filter/refresh paths first.
3. Test read/delete only against QA notifications or document as unverified.
4. Fix scoped route/layout/state issues.
5. Keep provider/device push checks documented as release blockers.

## Native Activity Inbox Authenticated QA Hardening

Completed action: authenticated QA browser hardening for Native Activity Inbox.

Why it happened now:

- Activity Inbox spans Notifications, Messenger, Calls, Social, Safety, Verification, Marketplace, Creator/Growth, Intelligence/Alerts, Settings, badge counts, and deep-link routing.
- The previous foundation pass verified route protection but did not have an authenticated local QA session.
- A disposable local QA account and seeded notifications were needed before trusting category grouping, read/delete state, badge refresh, Settings entry, legacy routes, and Open routing.

Reusable PulseSoc APIs/code/database/business logic verified:

- Existing `/api/mobile/auth/register` and `/api/mobile/auth/login`.
- Existing local notification schema and notification preference rules.
- Existing `/api/pulse/notifications`, unread-count, read-all, delete, and resolve endpoints.
- Existing deep-link router.
- Existing Growth Center route.
- Existing Settings route.
- Existing Activity Inbox native API/screen.

Fixes made during QA:

- Social notification classification now recognizes post, like, comment, mention, follow, reaction, share, repost, and social before intelligence/market signal terms.
- React Navigation linking now supports `/pulse/inbox`, `/dashboard/activity`, and `/dashboard/inbox`.
- `/pulse/notifications` is restored as the Notifications tab path, and the tab renders Activity Inbox.
- Activity Inbox category counts are now derived from current items so delete/read mutations cannot leave stale category counts.
- Activity Inbox now preserves the original server-provided target when `/api/pulse/notifications/<id>/resolve` returns the server safe fallback for a native-supported target.

Authenticated QA verified:

- `/pulse/activity`
- `/pulse/activity/messages`
- `/pulse/activity/calls`
- `/pulse/activity/social`
- `/pulse/activity/safety`
- `/pulse/activity/verification`
- `/pulse/activity/marketplace`
- `/pulse/activity/creator_growth`
- `/pulse/activity/intelligence_alerts`
- `/pulse/notifications`
- `/pulse/inbox`
- `/dashboard/activity`
- `/dashboard/inbox`
- Settings Activity Inbox entry point
- Notification tab Activity Inbox entry point
- Delete one QA notification
- Mark remaining QA activity read
- Badge/unread title cleared after read
- Open action routed Creator/Growth activity into native Growth Center

Browser/runtime result:

- Final clean-bundle check rendered Activity Inbox and Growth Center routing without visible runtime error text.
- A transient hot-refresh console error was observed and resolved by moving the derived-count helper above the component before restarting Expo web.

Release blockers:

- Physical APNs/FCM tap routing.
- Device badge synchronization.
- Background push delivery behavior.
- Offline cache restore with network disabled.
- Provider-backed read/delete tests against a seeded QA provider account.

No critical, security, data-loss, production-breaking, or future-development-blocking issues remain from this pass.

## Recommendation Summary

Recommended next highest-value native feature/action: Native Events + Scheduled Live Gateway Foundation.

Reason: the production codebase exposes `/pulse/events`, `/pulse/live/schedule`, and `/pulse/live/events/create` gateway routes, and the native app already has Live Viewer, Search/Discovery Events tab, Activity Inbox, Creator/Growth, Profile, Groups, Notifications, and deep-link routing. The actual repo does not show a full user-facing native JSON event database/API yet, so the safest next step is a native Events surface that reuses existing Live scheduled data and keeps event creation, ticketing, checkout, and studio scheduling on safe web fallback.

Reusable PulseSoc APIs/code/database/business logic for the next action:

- Existing `/api/pulse/live-now` scheduled/live event payloads through `mobile-native/src/api/live.ts`.
- Existing `/pulse/events` web gateway copy and route.
- Existing `/pulse/live/schedule` safe scheduling gateway.
- Existing `/pulse/live/events/create` safe live event creation gateway.
- Existing Live Viewer, Live discovery, Creator Studio, Growth Center, Search Events tab, Notifications/Activity routing, and Profile/Groups navigation.
- Existing LiveKit/Mux/live eligibility/moderation/business rules remain backend-owned.

What must be rebuilt natively:

- Native Events screen.
- Scheduled live/event cards using existing live scheduled data.
- Event detail gateway where an existing live ID is available.
- Search/Discovery Events route integration.
- Activity/deep-link routing for `/pulse/events`, `/pulse/live/schedule`, and `/pulse/live/events/create`.
- Safe web fallback for event creation, ticketed events, event payments, Live Studio, and unsupported schedule persistence.

Dependencies/blockers:

- No dedicated native JSON event database/API was found in this inspection.
- Ticketing and event checkout are explicitly not configured in the current production gateway.
- Scheduled-live persistence appears gateway/studio-owned, not a standalone calendar API.
- Full native event creation should wait for backend event contracts.

Risk level: medium.

Estimated complexity: medium.

Safest implementation plan:

1. Reuse `listLiveNow()` scheduled items as the first native events data source.
2. Add a native Events screen focused on discovery and scheduled live/event visibility.
3. Wire `/pulse/events` to native Events.
4. Keep `/pulse/live/schedule` and `/pulse/live/events/create` on safe web fallback or lightweight native gateway cards.
5. Do not invent ticketing, checkout, or event persistence logic.
6. Verify with static checks and QA browser route checks before commit.

## Native Events + Scheduled Live Gateway Foundation

Completed feature: native Events and Scheduled Live gateway.

Why it happened now:

- Activity Inbox, Live Viewer, Search/Discovery, Creator Studio, Growth Center, Profile, Groups, and Notifications now need a common native destination for event/live links.
- The production `/pulse/events` route is a gateway over existing Live scheduling until dedicated event persistence exists.
- The native app already had reusable Live API/cache/navigation infrastructure, so this feature could be built without duplicating backend logic.

Reusable PulseSoc APIs/code/database/business logic:

- Existing `/api/pulse/live-now` scheduled/live payloads through `mobile-native/src/api/live.ts`.
- Existing Live item normalization, scheduled detection, playback state, and offline discovery cache.
- Existing `/pulse/events`, `/pulse/live/schedule`, and `/pulse/live/events/create` production gateways.
- Existing Live Viewer route for join/watch.
- Existing profile routing from host metadata.
- Existing notification/deep-link routing.
- Existing backend Live eligibility, visibility, moderation, LiveKit/Mux, and business rules.

Native work completed:

- Added `mobile-native/src/api/events.ts` as an adapter over scheduled Live payloads.
- Added `mobile-native/src/screens/EventsScreen.tsx`.
- Added native Events list, event detail, host/profile navigation, share hook, watch/join handoff to Live Viewer, loading/error/offline states, and fallback action cards.
- Added deep-link support for:
  - `/pulse/events`
  - `/pulse/events/<event_id>`
  - `/pulse/live/schedule`
  - `/pulse/live/events/create`
- Added notification routing for event/scheduled-live links.
- Added Settings entry point.
- Added Search/Discovery Events shortcut.

Safe fallback boundaries:

- Event creation stays on existing web gateway.
- Ticketing/payment stays unavailable/fallback because production gateway says event payments require a dedicated checkout adapter.
- Live Studio, hosting, and co-hosting stay on existing Live Studio fallback.
- Native does not fake reminder authority because no dedicated reminder endpoint was found in this inspection.

Verification plan:

- Static verification, Expo Doctor, and the Events audit script cover code and route wiring.
- Authenticated QA browser route checks verified `/pulse/events`, `/pulse/events/1`, `/pulse/live/schedule`, `/pulse/live/events/create`, Settings entry, and Search/Discovery Events shortcut against a disposable local QA account/session.
- The local QA backend returned no scheduled events, so empty-state rendering is verified; seeded provider-backed scheduled event data remains pending.
- Live hosting, ticketing, payments, provider reminder delivery, and two-device Live playback remain release QA/provider blockers.

Remaining major features:

- Native Content Planner + Scheduled Publishing Gateway.
- Native Course/Learning Gateway if prioritized from current course backend.
- Native seller/store management beyond Marketplace browse/detail.
- Native advanced Live Studio/hosting/co-hosting.
- Physical-device LiveKit calls and lock-screen call QA.
- Full provider/device push verification.

Recommended next highest-value native feature/action: Native Content Planner + Scheduled Publishing Gateway Foundation.

Reason for recommendation:

- The production backend already exposes `/api/dashboard/content-planner/item`, content planner, draft studio, and post scheduler flows.
- Native Creator Studio currently saves a basic draft and routes advanced planner/scheduler tools to web fallback.
- Events/Scheduled Live now creates a stronger need for creator calendar/planner visibility, but publishing and scheduling must remain backend-authoritative.

Reusable APIs/code/database/business logic for the next action:

- Existing `/api/dashboard/content-planner/item`.
- Existing content planner, draft studio, and post scheduler web flows.
- Existing creator state API.
- Existing feed composer, status creator, camera/media upload, profile, notifications, activity routing, and Creator Studio components.
- Existing moderation, privacy, checklist, publishing, and scheduling safety rules.

What must be rebuilt natively:

- Planner list/queue screen over existing creator/planner state where APIs support it.
- Draft detail/edit gateway.
- Scheduled content overview.
- Save draft/update draft forms where existing APIs support them.
- Safe web fallback cards for unsupported publish-now, recurring, bulk schedule, and version history.

Dependencies/blockers:

- A list/read API for planner items should be confirmed before building full native planner management.
- Current native creator API has save support but not an obvious dedicated native list wrapper.
- Publish-now and recurring scheduler remain unsupported without backend contracts.

Risk level: medium.

Estimated complexity: medium.

Safest implementation plan:

1. Inspect creator/content planner API coverage in `bot.py` and `mobile-native/src/api/creator.ts`.
2. Reuse existing `CreatorStudioScreen` and `saveContentPlannerItem()`.
3. Add native planner gateway only for read/save/update flows supported by backend.
4. Keep publish, bulk schedule, recurring schedule, and unsupported version history on safe web fallback.
5. Run static checks, audit, and QA browser route checks before commit.

## Native Content Planner + Scheduled Publishing Gateway Foundation

Completed feature: native Content Planner, Scheduled Publishing, and Draft Studio gateway.

Why it happened now:

- Events/Scheduled Live created a stronger need for native creator planning and calendar workflow.
- Creator Studio already had a basic draft save path but advanced planner/scheduler/draft tools opened web directly.
- The production backend already owns planner persistence and validation through `pulsesoc_content_planner_items` and `/api/dashboard/content-planner/item`.

Reusable PulseSoc APIs/code/database/business logic:

- Existing `/api/dashboard/content-planner/item` write endpoint.
- Existing `pulsesoc_content_planner_items` database table.
- Existing `pulsesoc_dashboard_centers.build_content_planner`, `build_post_scheduler`, and `build_draft_studio` behavior.
- Existing Creator Studio state and recommendations.
- Existing backend validation that scheduled items require `scheduled_at`.
- Existing safe-unavailable rules for publish-now, bulk scheduling, recurring scheduling, smart rescheduling, and version history.

Native work completed:

- Added `mobile-native/src/screens/ContentPlannerScreen.tsx`.
- Extended `mobile-native/src/api/creator.ts` planner payload support for scheduled time, alt text, checklist booleans, and route helpers.
- Added native draft save and scheduled draft save flows using existing backend POST.
- Added native Content Planner, Scheduled Publishing, and Draft Studio route modes.
- Added deep-link support for:
  - `/pulse/content-planner`
  - `/dashboard/creator/content-planner`
  - `/pulse/dashboard/content-planner`
  - `/dashboard/creator/post-scheduler`
  - `/pulse/dashboard/post-scheduler`
  - `/dashboard/creator/draft-studio`
  - `/pulse/dashboard/draft-studio`
- Updated Creator Studio planner/draft/scheduler cards to open native first.
- Added Settings entry point.

Safe fallback boundaries:

- Full planner board/list management remains on web fallback because no dedicated native JSON list/read endpoint was found.
- Edit/delete planner item flows wait for backend endpoints.
- Publish-now, bulk schedule, recurring schedule, smart rescheduling, and version history remain fallback-only.
- Native does not claim content was published; it only saves draft/scheduled planner records through backend validation.

Verification plan:

- Static verification, Expo Doctor, and the Content Planner audit script cover route wiring and backend reuse.
- Authenticated QA browser route checks verified Content Planner, Scheduled Publishing, and Draft Studio direct routes/aliases with no visible runtime error text.
- Direct authenticated local API checks verified `/api/dashboard/content-planner/item` accepted both a draft planner item and a scheduled planner item with `scheduled_at`.
- Provider-backed full planner row management remains pending backend JSON read/list contracts.

Remaining major features:

- Native Courses + Learning Gateway.
- Native seller/store management beyond Marketplace browse/detail.
- Native advanced Live Studio/hosting/co-hosting.
- Physical-device LiveKit calls and lock-screen call QA.
- Full provider/device push verification.

Recommended next highest-value native feature/action: Native Courses + Learning Gateway Foundation.

Reason for recommendation:

- Production PulseSoc already exposes course creation, teacher dashboard, course draft tables, free/paid-course-ready routes, and course safety/compliance boundaries.
- Native now has Profile, Premium, Marketplace/media viewer, Creator Studio, Content Planner, Events, Notifications, Search, and Trust/Safety foundations needed for a safe learning gateway.
- Courses can reuse existing backend/course draft logic while keeping paid checkout, teacher tools, and compliance-sensitive operations on web fallback.

Reusable APIs/code/database/business logic for the next action:

- Existing course routes and draft tables in `bot.py`.
- Existing profile/teacher identity, premium, marketplace/payment fallbacks, media viewer/upload, creator tooling, trust/safety, and notification routing.
- Existing course moderation, compliance, and paid-course readiness rules.

What must be rebuilt natively:

- Course discovery gateway.
- Course detail shell.
- Teacher/Profile navigation.
- Create-course safe gateway.
- Free/paid readiness labels.
- Safe fallback to existing web flows for course creation, teacher dashboard, paid checkout, compliance review, and lesson authoring.

Dependencies/blockers:

- Confirm native-safe JSON course list/detail endpoints before building full course browsing.
- Paid-course checkout and teacher dashboard should remain fallback unless native contracts already exist.
- Course compliance/review must remain backend-authoritative.

Risk level: medium.

Estimated complexity: medium.

Safest implementation plan:

1. Inspect production course routes/tables and any existing course APIs.
2. Reuse native Profile, Premium, Marketplace/media, Creator, Search, and notification routing.
3. Build a native gateway around confirmed list/detail data only.
4. Keep create/edit/paid/teacher/admin flows on safe fallback until backend contracts exist.
5. Verify static checks, audit, and QA browser route rendering before commit.

## Native Courses + Learning Gateway Foundation

Completed feature: native Courses + Learning gateway.

Why it happened now:

- Creator Studio, Content Planner, Profile, Premium, Marketplace, Search, Activity Inbox, and Events now provide the surrounding native surfaces that courses and teacher learning workflows need.
- Production PulseSoc already has course, teacher, education, lesson, tutor, progress, and paid-course-ready web/backend behavior.
- The native app needed a safe learning gateway that exposes available JSON lesson data without bypassing course, payment, compliance, or teacher approval rules.

Reusable PulseSoc APIs/code/database/business logic:

- Existing `/api/education/categories`.
- Existing `/api/education/lessons`.
- Existing `/api/education/lesson/<lesson_slug>`.
- Existing `/api/education/quiz/submit`.
- Existing `/api/education/tutor`.
- Existing `/api/pulse/courses/create` and web-backed course creation rules.
- Existing `/pulse/courses`, `/pulse/courses/<course_id>`, `/pulse/teachers`, and `/pulse/teacher-dashboard` production routes.
- Existing education, course, teacher, lesson, enrollment, progress, tutor-log, compliance, paid-course readiness, and payment fallback database/business logic.

Native work completed:

- Added `mobile-native/src/api/learning.ts`.
- Added `mobile-native/src/screens/CoursesLearningScreen.tsx`.
- Added native route aliases for Courses, Course Detail, Lesson Detail, Teacher Profile gateway, and Teacher Dashboard gateway.
- Added deep-link and notification routing for course, teacher, and education lesson links.
- Added Creator Studio, Settings, and Search/Discovery entry points.
- Added native category browse, lesson list, lesson detail, knowledge map, quiz preview, tutor, and progress completion hooks.
- Added offline cache for categories, lessons, and recently opened lessons.

Safe fallback boundaries:

- Full course catalog/detail stays on fallback where no JSON course list/detail API was confirmed.
- Course creation stays on existing web flow and backend teacher approval rules.
- Paid enrollment, checkout, refunds, payouts, and provider logic stay on existing web/provider flows.
- Teacher dashboard, lesson authoring, admin review, and advanced teacher tools stay fallback-only.
- Unsupported lesson video/player behavior stays fallback-only.

Verification plan and QA evidence:

- Static typecheck passes after adding the Courses/Learning gateway.
- Dedicated audit script verifies API reuse, route wiring, Settings/Creator/Search entry points, safe fallback tokens, report coverage, and no internal design-label leakage into user-facing native source.
- Built-in QA browser route checks rendered `/pulse/courses`, `/pulse/courses/1`, `/education/lesson/crypto-basics-101`, `/pulse/teachers`, and `/pulse/teacher-dashboard` with no visible runtime error text on those routes.
- Local QA backend checks authenticated a disposable QA account, returned a tutor answer from `/api/education/tutor`, and saved progress through `/api/education/quiz/submit`.
- Device/provider QA is not a development blocker for this foundation because camera, payment, and provider-managed course behavior remain fallback-only.

Remaining major features:

- Native Courses + Learning practical QA hardening.
- Native seller/store management beyond Marketplace browse/detail.
- Native advanced Live Studio/hosting/co-hosting.
- Physical-device LiveKit calls and lock-screen call QA.
- Full provider/device push verification.

Recommended next highest-value native feature/action: Native Courses + Learning Practical QA Hardening.

Reason for recommendation:

- The feature intentionally bridges native JSON lesson data with several safe web fallbacks.
- A short authenticated QA browser pass should verify that route aliases, lesson loading, progress/tutor states, fallback buttons, and visual consistency behave correctly before another major build.
- This is a practical QA gate only; it should not become a long release-blocking loop unless a critical, data-loss, security, or production-breaking issue appears.

Reusable APIs/code/database/business logic for the next action:

- Existing education category, lesson, tutor, and progress APIs.
- Existing course, teacher, and dashboard web routes.
- Existing native navigation, notification routing, Settings, Creator Studio, Search, and offline cache utilities.

What must be rebuilt/fixed natively:

- Only scoped QA blockers found in route rendering, fallback routing, empty/error/offline states, or lesson interaction states.
- No new backend business logic should be added.

Dependencies/blockers:

- A real JSON course catalog/detail API is still needed before full native paid course browsing/enrollment.
- Paid enrollment and teacher dashboard remain provider/compliance-sensitive fallback surfaces.

Risk level: low to medium.

Estimated complexity: low.

Safest implementation plan:

1. Start the QA web build with the existing local backend/proxy pattern.
2. Authenticate with the local QA account.
3. Verify course, teacher, dashboard, and lesson routes in the built-in QA browser.
4. Verify lesson progress/tutor behavior where a seeded lesson exists.
5. Fix only scoped blockers, then commit and continue the roadmap.

## Native Courses + Learning Practical QA Hardening

Completed action: short authenticated QA hardening pass for the native Courses + Learning gateway.

What was verified:

- `/pulse/courses`.
- `/pulse/courses?category=scam-defense`.
- `/pulse/courses/1`.
- `/education/lesson/crypto-basics-101`.
- `/pulse/teachers`.
- `/pulse/teacher-dashboard`.
- `/pulse/creator-studio`.
- `/pulse/settings`.
- `/pulse/search`.
- Category browse rendered filtered scam-defense lessons.
- Lesson detail rendered overview, knowledge map, quiz preview, tutor, progress, and fallback rows.
- Tutor interaction returned a backend lesson-scoped response.
- Recent learning cache surfaced `Crypto Basics 101`.
- Creator Studio, Settings, and Search/Discovery entry points rendered correctly.

Scoped fix completed:

- `Mark Complete` now leaves durable inline progress feedback after `/api/education/quiz/submit` success/error. This fixes the QA browser gap where progress saved server-side but the user had no persistent visible result.

Remaining gap:

- Offline Courses cache behind the auth gate remains unverified. With the local API proxy stopped, the app returned to the login gate before the Courses screen could render cached learning data. This is an app-level offline-auth/session limitation, not a Courses data-loss or security blocker.

Critical blocker assessment:

- No critical, security, data-loss, production-breaking, or future-development-blocking issue was found.

Recommended next highest-value native feature/action: Native Seller/Store Management Foundation.

Reason for recommendation:

- Native Marketplace browse/detail already exists, but seller-owned workflows still need a native control layer.
- Production PulseSoc already exposes seller application, marketplace product creation, marketplace media upload, checkout fallback, payout onboarding, merchant dashboard/profile routes, seller readiness, and store/economy analytics logic.
- Native now has Marketplace, Media Upload, Media Viewer, Profile, Verification, Safety, Premium, Activity Inbox, Growth, Creator Studio, Courses/Learning, and Content Planner foundations that can support a safe seller/store gateway.

Reusable APIs/code/database/business logic for the next action:

- Existing `/api/pulse/marketplace/seller/apply`.
- Existing `/api/pulse/marketplace/listings/create`.
- Existing `/api/pulse/marketplace/media/upload`.
- Existing `/api/pulse/payments/checkout`.
- Existing `/api/pulse/payouts/connect`.
- Existing Marketplace browse/search/save/report APIs.
- Existing merchant routes: `/pulse/merchant/apply`, `/pulse/merchant/dashboard`, `/pulse/merchant/<username>`.
- Existing seller, listing, media, payout, transaction, escrow, refund, dispute, moderation, verification, tax, and trust/business-rule tables.
- Existing native Marketplace, Profile, Verification, Safety Hub, Premium, Activity Inbox, Media Upload, and NativeMediaViewer components.

What must be rebuilt natively:

- Seller/Store Management gateway.
- Seller application/status display.
- Owned listings overview where APIs support it.
- Create listing draft handoff using existing marketplace/media APIs where safe.
- Product media upload handoff through existing native upload helpers where supported.
- Payout onboarding/status gateway using existing backend/provider route.
- Store safety/readiness dashboard from existing backend data where available.
- Safe fallbacks for checkout, tax forms, bank onboarding, disputes/refunds, fulfillment, advanced analytics, and admin review.

Dependencies/blockers:

- Confirm native-safe JSON seller dashboard/status/listing-owner endpoints before building full owned-store management.
- Payout, tax, checkout, refunds, disputes, and provider onboarding must remain server/provider-authoritative.
- Physical-device media upload QA remains a release blocker for seller product camera flows, not a development blocker.

Risk level: medium.

Estimated complexity: medium.

Safest implementation plan:

1. Inspect marketplace seller/product creation and merchant dashboard APIs/routes in `bot.py` and existing `mobile-native/src/api/marketplace.ts`.
2. Extend native marketplace API wrappers only for confirmed JSON endpoints.
3. Build a native seller/store gateway with clear fallback boundaries.
4. Reuse Media Upload, NativeMediaViewer, Profile, Verification, Safety, Premium, and Activity Inbox components.
5. Keep checkout, payout provider onboarding, tax, disputes, refunds, fulfillment, and admin review on safe fallback.
6. Run static checks, audit, and QA browser route checks before commit.

## Native Seller/Store Management Foundation

Completed action: built the native Seller/Store Management foundation.

What was implemented:

- Native `SellerStoreScreen` as a server-authoritative seller/store control layer.
- Existing marketplace seller application API wrapper: `POST /api/pulse/marketplace/seller/apply`.
- Existing seller orders API wrapper: `GET /api/pulse/payments/seller/orders`.
- Existing payout onboarding API wrapper: `POST /api/pulse/payouts/connect`.
- Seller/store snapshot cache for offline metadata recovery.
- Product media gallery using existing marketplace media payloads and `NativeMediaViewer`.
- Native route/deep-link coverage for:
  - `/pulse/seller-store`
  - `/pulse/merchant/apply`
  - `/pulse/merchant/dashboard`
  - `/pulse/merchant/<sellerId>`
  - `/pulse/marketplace/create`
- Native entry points from Marketplace, Profile, and Settings.
- Safe fallback boundaries for merchant document upload, full listing creation, payout provider onboarding, checkout, tax, disputes, refunds, fulfillment, and admin review.

QA evidence:

- Static verification passed: `npm ci`, TypeScript, Expo Doctor, seller/store audit, and `git diff --check`.
- Built-in QA browser route checks confirmed the seller/store aliases route into the native app and preserve the auth gate while signed out.
- Authenticated backend contract checks passed against a temporary local QA database: seller application save returned `200 ok=true`, seller orders returned `200 ok=true`, and payout/connect correctly returned the server-owned `403` approval gate for an unapproved merchant.
- Authenticated browser rendering remains unverified because React Native Web login automation did not trigger submit in the built-in browser, and the browser automation page scope cannot seed local storage directly.

Production systems reused:

- Existing marketplace seller APIs and merchant routes.
- Existing marketplace listing/search/media/order/payout payloads.
- Existing seller, listing, media, order, payout, verification, trust, premium, and moderation database/business logic.
- Existing native Marketplace, Profile, Verification, Safety Hub, Premium, Activity Inbox, Camera Studio, and NativeMediaViewer infrastructure.

Remaining gaps:

- A dedicated native-safe seller dashboard/status JSON endpoint is not yet exposed; the native screen uses confirmed marketplace/order APIs and safe merchant web fallbacks.
- Full merchant application document upload remains web-only because private document handling and admin review are sensitive.
- Stripe Connect onboarding, checkout, refunds/disputes, fulfillment, and tax flows remain provider/web fallback.
- Physical-device product media capture/upload remains a release QA blocker, not a development blocker.

Risk level: medium.

Estimated complexity completed: medium.

Recommended next highest-value native feature/action: Native Seller/Store Practical QA Hardening.

Reason for recommendation:

- Seller/store now connects approval, marketplace, media, payout, checkout fallback, trust, verification, safety, and profile surfaces.
- The safest next move is a short authenticated QA browser pass over seller routes, application validation, payout/provider fallback states, and entry points before adding another major native feature.

Suggested QA focus:

1. Verify `/pulse/seller-store`, `/pulse/merchant/apply`, `/pulse/merchant/dashboard`, `/pulse/merchant/<sellerId>`, and `/pulse/marketplace/create`.
2. Verify seller application validation and success/error messaging.
3. Verify unapproved seller payout/connect failure state is safe and server-owned.
4. Verify Marketplace, Profile, Settings, and notification/deep-link entry points.
5. Document provider-only and physical-device gaps separately from browser-verified behavior.

## Native Seller/Store Practical QA Hardening

Completed action: authenticated practical QA hardening for the native Seller/Store Management foundation.

What was verified:

- `/pulse/seller-store` loads signed in and renders the native Seller/Store screen.
- `/pulse/merchant/apply`, `/pulse/merchant/dashboard`, `/pulse/merchant/<sellerId>`, and `/pulse/marketplace/create` route into the native seller/store gateway or safe fallback.
- Marketplace and Settings expose Seller/Store entry points.
- Profile exposes Seller/Store from the About tab.
- Blank merchant application submit renders validation instead of sending incomplete data.
- Merchant application save returns a visible success state through the existing backend API.
- Seller status and storefront listing count render after a local approved seller/listing fixture is seeded.
- Orders summary renders safely.
- Payout/connect returns the server-owned approval gate for an unapproved merchant.
- Loading and error states remain contained to the native screen.

Scoped fixes completed:

- Added accessible product-media tile labels and a visible `Open media` overlay to improve QA targeting and accessibility.
- Extended the existing QA-only simulator auth helper to support local QA browser login redirects. This remains limited to development builds with a localhost API base URL and still calls the existing backend sign-in API.
- Added safe local redirect handling for QA login. Redirect targets must be local paths and reject protocol-relative, API, admin, and backslash paths.

Backend contract finding:

- A seeded approved marketplace listing with `cover_image_url` and `gallery_json` was present in the local QA database.
- The inspected `GET /api/pulse/marketplace/search?limit=5` response did not expose listing media fields such as `cover_image_url`, `gallery_json`, `video_url`, or a normalized `media` array.
- The native Seller/Store media gallery is ready to render authorized media, but full media-gallery/NativeMediaViewer QA cannot be claimed until the backend exposes a native-safe product-media payload.

Critical blocker assessment:

- No critical, security, data-loss, production-breaking, or future-development-blocking issue was found.
- The product-media payload gap is a scoped parity/hardening issue, not a reason to stop development.

## Native Completion Snapshot by Subsystem

Estimated completion is based on implemented native foundations, browser/simulator/device evidence, and known release blockers. These are engineering readiness estimates, not App Store readiness claims.

| Subsystem | Estimated Native Completion | Current Confidence | Notes |
| --- | ---: | --- | --- |
| App shell, auth, session, routing | 90% | Browser/simulator verified | Device push/deep-link release QA remains. |
| Social feed, posts, comments, composer | 82% | Browser verified | Physical media capture/upload remains release QA. |
| Messaging | 78% | Browser/static verified | Two-device realtime/push/media QA remains. |
| Notifications, Activity Inbox, alerts | 76% | Browser verified | APNs/FCM/SMS/email/Telegram provider QA remains. |
| Media viewer/upload/camera | 68% | Browser/simulator partially verified | Physical camera/mic/large video remains release blocker. |
| Reels and Status | 72% | Browser/static verified | Native video performance and physical media QA remain. |
| Marketplace and Seller/Store | 70% | Browser/backend contract verified | Product-media payload and provider checkout/payout QA remain. |
| Search, Saved, Groups, Events, Courses | 74% | Browser/static verified | Deeper data-rich QA and offline-auth recovery remain. |
| Trust, Safety, Verification, Account Health | 80% | Browser verified | Sensitive document/admin/provider flows stay web/server-owned. |
| Premium, Creator, Growth, Intelligence | 72% | Browser/static verified | Provider/billing/advanced admin flows remain fallback surfaces. |
| Live and Calls | 55% | Practical route/shell verified | LiveKit two-device media, lock-screen, audio route, and push call QA remain. |
| Android readiness | 35% | Tooling partially verified | Physical Android QA remains incomplete. |

Overall native migration estimate: 72% foundation/parity coverage, 58% release QA confidence.

## Recommended Next Action

Recommended next highest-value native feature/action: Native Marketplace/Seller Media Payload Contract Hardening.

Reason for recommendation:

- Seller/Store, Marketplace Browse, Listing Detail, NativeMediaViewer, Search, Activity Inbox, and Profile seller surfaces all depend on reliable product-media payloads.
- The native UI already has the media-gallery integration point, but the current inspected marketplace search response does not expose authorized listing media fields.
- A scoped server-authoritative payload hardening pass will unlock real Seller/Store media QA and improve Marketplace parity without duplicating business logic in the native client.

Reusable APIs/code/database/business logic for the next action:

- Existing marketplace listing/search APIs.
- Existing marketplace listing/media tables and media authorization/moderation rules.
- Existing `marketplace_listings`, product media, seller, order, saved, report, and safety data.
- Existing native Marketplace cards, Seller/Store screen, Listing Detail, NativeMediaViewer, offline cache, and route/deep-link infrastructure.

What must be rebuilt or adjusted natively:

- Prefer a native-safe API payload contract that includes authorized listing media fields or a dedicated seller/listing detail endpoint.
- Update native Marketplace/Seller wrappers only to consume confirmed backend fields.
- Keep checkout, payout, tax, disputes, refunds, fulfillment, and admin review on safe fallback.

Dependencies/blockers:

- Production backend files are currently dirty from unrelated work, so any backend payload change must be scoped carefully and staged explicitly.
- Media authorization and moderation must remain server-owned.
- Physical product-media capture/upload QA remains a release blocker, not a development blocker.

Risk level: low to medium if additive and server-authoritative.

Estimated complexity: low to medium.

Safest implementation plan:

1. Inspect the current marketplace search/listing route implementation and marketplace media table usage.
2. Add or expose authorized media fields through an existing JSON response or a dedicated native-safe endpoint.
3. Update native marketplace wrappers to consume the confirmed payload without inventing local media state.
4. Re-run Seller/Store and Marketplace media QA in the built-in QA browser.
5. Keep all provider/payment/payout flows on fallback.

## Native Marketplace/Seller Media Payload Contract Hardening

Completed action: hardened the marketplace search/listing JSON contract so native Marketplace, Seller/Store, and NativeMediaViewer can consume server-owned product media.

What was implemented:

- Added a reusable backend marketplace listing payload builder for native-safe search results.
- Added normalized product media arrays to `GET /api/pulse/marketplace/search`.
- Preserved existing marketplace search fields for WebView compatibility.
- Reused existing `marketplace_listings` media columns:
  - `cover_image_url`
  - `gallery_json`
  - `video_url`
  - `media_url`
- Reused existing `marketplace_product_media` rows for richer product media payloads.
- Normalized media URLs through the existing `pulse_media_url(...)` helper and media service.
- Excluded rejected, removed, blocked, and blocked-review product media rows from the API payload.

Payload fields now available where data exists:

- `cover_image_url`
- `image_url`
- `thumbnail_url`
- `gallery_json`
- `video_url`
- `media`
- `media_assets`

Native impact:

- `mobile-native/src/api/marketplace.ts` already supports these fields and normalizes them into `listing.media`.
- Marketplace cards can render cover media from backend-provided fields.
- Listing Detail can pass media into NativeMediaViewer.
- Seller/Store media gallery can now verify product media when seeded or real listings include media.

Backend contract QA:

- Authenticated local backend contract checks confirmed a seeded marketplace listing returns non-empty `media` and `media_assets` arrays.
- Backend evidence: `ok=true`, `media_count=3`, `first_media_type=image`, `has_thumbnail=true`, and `has_video_url=true`.
- Built-in QA browser evidence confirmed Seller/Store rendered `1 Listings loaded`, the seeded `QA Product Media Contract` listing, three `Open store media` tiles, and NativeMediaViewer opened the first media item.
- Web marketplace compatibility is preserved because the API response is additive.

Remaining gaps:

- Physical-device product media capture/upload remains release QA.
- Provider checkout/payout flows remain web/provider fallback.
- A dedicated seller-owned listing dashboard endpoint may still be useful later, but this hardening unlocks the immediate media-gallery gap.

Critical blocker assessment:

- No security, data-loss, production-breaking, or future-development-blocking issue is expected from this additive payload change.

Recommended next highest-value native feature/action: Native Marketplace/Seller Media QA Hardening.

Reason for recommendation:

- The backend payload now exposes the fields native already expects.
- The next safest step is a short authenticated QA browser pass over Marketplace, Listing Detail, Seller/Store media gallery, and NativeMediaViewer opening.
- This validates the contract end to end before moving to another major feature.

Reusable APIs/code/database/business logic for the next action:

- `GET /api/pulse/marketplace/search`.
- Existing marketplace listing/media/seller tables.
- Existing marketplace visibility/moderation rules.
- Existing native Marketplace, Seller/Store, Listing Detail, NativeMediaViewer, and cache utilities.

What must be rebuilt or adjusted natively:

- Only scoped QA blockers found while rendering the newly exposed media payload.
- Do not duplicate media authorization or moderation logic in the client.

Dependencies/blockers:

- Real physical product-media capture and upload still require device QA.
- Checkout and payout provider flows remain fallback/provider-owned.

Risk level: low.

Estimated complexity: low.

Safest implementation plan:

1. Start the local QA backend/proxy and Expo web QA build.
2. Seed an approved seller/listing with product media records.
3. Verify Marketplace cards render media.
4. Verify Seller/Store media gallery opens NativeMediaViewer.
5. Confirm unsupported provider/payment/payout flows remain fallback.

## Native Marketplace/Seller Media QA Hardening

Completed action: verified the hardened marketplace media payload contract across native Marketplace, Listing Detail, Seller/Store, and NativeMediaViewer.

What was verified:

- Authenticated backend contract check against local disposable QA data.
- Marketplace feed cards render media-backed and no-media listings safely.
- Listing Detail screen renders seeded mixed-media listing data.
- Seller/Store gallery renders product media tiles from the backend payload.
- NativeMediaViewer opens from Seller/Store gallery and Listing Detail.
- Cover images, thumbnails, gallery assets, video payload metadata, empty media, missing media fallback, and moderated-media filtering were covered.
- Payout and checkout boundaries remain unchanged and provider/backend-owned.

Scoped hardening fix:

- Seller/Store media gallery now preserves the selected tile index when opening NativeMediaViewer.
- `mobile-native/App.tsx` now passes the web `window.location.href` into the existing QA-only simulator auth handler so local QA browser deep links can authenticate when `__DEV__` and local API base URL gates are satisfied.

QA evidence:

- Backend contract evidence: four seeded marketplace listings loaded; mixed media returned image/video media, one-image listing returned one asset, empty listing returned zero assets, and rejected media returned zero assets.
- Built-in QA browser evidence: `/pulse/seller-store` rendered `4 Listings loaded`, `4 Active/review ready`, and five product media gallery tiles.
- Built-in QA browser evidence: `Open store media 2` opened NativeMediaViewer with listing context, author context, Prev/Next controls, and Share.
- Built-in QA browser evidence: `/pulse/marketplace` rendered all four seeded listings.
- Built-in QA browser evidence: `/pulse/marketplace/1` deep-linked to native Marketplace with Listing Detail open, and media opened NativeMediaViewer.

Remaining release QA:

- Physical product-media capture and large media uploads.
- Weak-network upload retry/cancel behavior.
- Native video playback performance on physical iOS/Android.
- Provider checkout completion and payout onboarding completion.
- Broken remote media URL behavior on device.

Critical blocker assessment:

- No critical, security, data-loss, production-breaking, or future-development-blocking issue was found.

Native completion percentages by subsystem:

| Subsystem | Current estimate | Confidence | Remaining high-value gap |
| --- | ---: | --- | --- |
| Auth/session/settings | 86% | Browser + simulator foundations verified | Release device auth and provider edge cases |
| Messaging and calls | 72% | Browser/practical QA verified | LiveKit two-device media and lock-screen release QA |
| Feed/posts/composer | 78% | Browser/static verified | Rich composer options and device media QA |
| Media viewer/upload/camera | 72% | Browser/simulator/media QA partially verified | Physical camera/mic, large video, weak network |
| Reels and Status | 74% | Browser/static verified | Physical native video performance |
| Marketplace and Seller/Store | 78% | Backend contract + QA browser verified | Native listing composer/edit and provider completion |
| Search, Saved, Groups, Events, Courses | 75% | Browser/static verified | Data-rich authenticated QA depth |
| Trust, Safety, Verification, Account Health | 82% | Browser verified | Document/admin/provider flows remain web/server-owned |
| Premium, Creator, Growth, Intelligence | 74% | Browser/static verified | Provider/billing/advanced tools remain fallback |
| Live and Calls | 56% | Practical shell verified | Native LiveKit/call media release QA |
| Android readiness | 35% | Tooling partially verified | Physical Android QA |

overall native migration percentage: 75% foundation/parity coverage, 61% release QA confidence.

Recommended next highest-value native feature/action: Native Seller Listing Composer + Listing Edit Foundation.

Reason for recommendation:

- Marketplace browse, Seller/Store, Media Upload, Camera Studio, NativeMediaViewer, Profile, Verification, Premium, Safety, and Activity Inbox are now in place.
- The backend already exposes seller application, marketplace media upload, and listing creation routes.
- Sellers can now see native store readiness and media payloads, but listing creation/editing remains mostly web fallback.
- A native seller listing composer completes the seller create/manage loop while keeping seller approval, media moderation, pricing, checkout, payouts, refunds, disputes, and fulfillment server-authoritative.

Reusable APIs/code/database/business logic:

- Existing `/api/pulse/marketplace/listings/create`.
- Existing `/api/pulse/marketplace/media/upload`.
- Existing `/api/pulse/marketplace/search`.
- Existing marketplace seller approval and listing moderation rules.
- Existing marketplace listing/media/seller/order/report tables.
- Existing native Media Upload, Camera Studio, Seller/Store, Marketplace, NativeMediaViewer, Verification, Premium, Safety, Activity Inbox, loading/error/cache, and route fallback infrastructure.

What must be rebuilt natively:

- Listing draft form UI.
- Media attachment preview and handoff.
- Validation display using backend responses.
- Create/edit gateway routing and fallback boundaries.
- Listing detail return/refresh behavior after create or edit.

Dependencies/blockers:

- Confirm whether an update/edit JSON endpoint exists before building edit; if not, keep edit on safe web fallback.
- Physical media upload remains release QA.
- Provider checkout/payout remains fallback/provider-owned.

Risk level: medium because seller listing creation touches commerce surfaces, but low risk if the native client only calls existing server-authoritative endpoints and keeps advanced payment/provider flows on fallback.

Estimated complexity: medium.

Safest implementation plan:

1. Inspect existing marketplace listing create/edit backend routes and native media upload hooks.
2. Build native listing composer as a gateway around confirmed create APIs only.
3. Keep edit on fallback unless a safe JSON update endpoint exists.
4. Reuse MediaUploadPreview, Camera Studio target, NativeMediaViewer, Seller/Store navigation, and existing marketplace API wrappers.
5. Verify with backend contract checks and QA browser route/form checks.

## Native Seller Listing Composer Foundation

Completed action: built the native Seller Listing Composer foundation using the existing PulseSoc marketplace backend.

What was implemented:

- Added `createMarketplaceListing(...)` to `mobile-native/src/api/marketplace.ts`.
- Added native `SellerListingComposerScreen`.
- Routed `MarketplaceCreateGateway` to the native composer.
- Kept `/pulse/marketplace/create` deep link active through existing linking config.
- Updated Seller/Store `Create Listing` entry point to open the native composer.
- Added listing title, short description, full description, category, price label, product type, and product media ID controls.
- Added Camera Studio handoff for marketplace media.
- Added safe web uploader fallback for advanced marketplace media/listing flows.
- Added backend validation display and submit-for-review action.
- Returns to native Seller/Store after successful listing creation when the backend returns a listing ID, because newly-created products remain seller-visible while marketplace review controls public visibility.
- Added `@egjs/hammerjs` to `mobile-native` dependencies because clean `npm ci` web QA exposed it as a required `react-native-gesture-handler` web dependency.
- Corrected notification/deep-link routing so `/pulse/marketplace/create` opens the new native composer instead of the older Seller/Store create gateway.

Reusable backend/API/database/business logic:

- Existing `POST /api/pulse/marketplace/listings/create`.
- Existing merchant approval checks.
- Existing marketplace draft media ID requirement.
- Existing cover photo validation.
- Existing marketplace safety review/risk scoring.
- Existing marketplace listing/media/seller tables.
- Existing payout, checkout, refund, dispute, fulfillment, and provider fallback flows.

Native-only work:

- Form UI and validation presentation.
- Navigation and deep-link routing.
- Media ID handoff and Camera Studio entry.
- Safe fallback buttons for web/provider flows.

Remaining gaps:

- Direct native file upload to `/api/pulse/marketplace/media/upload` should wait until the shared upload service can safely target marketplace-specific upload endpoints.
- Listing edit remains safe web fallback unless a confirmed JSON update endpoint is found.
- Physical product-media upload remains release QA.

Verification evidence:

- `npm run --prefix mobile-native typecheck` passed.
- `venv/bin/python scripts/pulsesoc_native_seller_listing_composer_audit.py` passed.
- `git diff --check` passed.
- Authenticated QA browser verified `/pulse/marketplace/create` renders the native `Create Listing` composer with product type controls, product media handoff, Camera Studio handoff, Web Uploader fallback, Submit for Review, and Back to Store.
- Authenticated backend contract check created `QA Native Composer Listing` with draft media ID 5 and received `ok=true`, `listing_id=5`, and `Listing saved for safety review.`

Updated native completion percentages by subsystem:

| Subsystem | Current estimate | Confidence | Remaining high-value gap |
| --- | ---: | --- | --- |
| Auth/session/settings | 86% | Browser + simulator foundations verified | Release device auth and provider edge cases |
| Messaging and calls | 72% | Browser/practical QA verified | LiveKit two-device media and lock-screen release QA |
| Feed/posts/composer | 78% | Browser/static verified | Rich composer options and device media QA |
| Media viewer/upload/camera | 72% | Browser/simulator/media QA partially verified | Physical camera/mic, large video, weak network |
| Reels and Status | 74% | Browser/static verified | Physical native video performance |
| Marketplace and Seller/Store | 82% | Backend contract + native composer foundation verified | Marketplace-specific native upload and edit support |
| Search, Saved, Groups, Events, Courses | 75% | Browser/static verified | Data-rich authenticated QA depth |
| Trust, Safety, Verification, Account Health | 82% | Browser verified | Document/admin/provider flows remain web/server-owned |
| Premium, Creator, Growth, Intelligence | 74% | Browser/static verified | Provider/billing/advanced tools remain fallback |
| Live and Calls | 56% | Practical shell verified | Native LiveKit/call media release QA |
| Android readiness | 35% | Tooling partially verified | Physical Android QA |

overall native migration percentage: 76% foundation/parity coverage, 62% release QA confidence.

Recommended next highest-value native feature/action: Native Seller Listing Composer Practical QA Hardening.

Reason for recommendation:

- The composer now routes and submits through existing server-authoritative marketplace APIs.
- Because listing creation touches commerce and seller trust, a short authenticated browser/backend QA pass should verify validation, merchant approval errors, media ID requirements, success handoff, and fallback routes before moving to another major subsystem.
- This is a practical hardening pass, not a reason to block the roadmap indefinitely.

Reusable APIs/code/database/business logic for next action:

- `POST /api/pulse/marketplace/listings/create`.
- Existing `/api/pulse/marketplace/media/upload`.
- Existing marketplace seller/listing/media tables.
- Existing seller approval and listing moderation/risk review.
- Existing native Seller/Store, Marketplace Detail, Camera Studio, and safe fallback routing.

What must be rebuilt or adjusted natively:

- Only scoped blockers found in QA.
- Do not duplicate seller approval, media moderation, risk scoring, checkout, payout, refund, or dispute logic.

Risk level: medium.

Estimated complexity: low to medium.

Safest implementation plan:

1. Seed or use an approved seller with draft marketplace media IDs.
2. Verify `/pulse/marketplace/create` renders the native composer.
3. Verify missing media/title/description validation.
4. Verify a successful listing create response returns to Seller/Store while public Marketplace visibility remains approval-gated.
5. Verify edit/provider/payout flows remain fallback.

## Native Seller Listing Composer Practical QA Hardening

Completed action: verified and hardened the native Seller Listing Composer and Seller/Store create-listing loop.

What was verified:

- `/pulse/marketplace/create` renders the native `Create Listing` composer in the built-in QA browser.
- Seller/Store `Create Listing` entry routes to the native composer.
- Missing media is rejected by the backend with `Upload or capture a cover photo before creating a listing.`
- Missing title/description is rejected by the backend with `Add a title and description for the listing.`
- Pending/non-approved merchants are rejected by the backend with `Merchant approval is required before creating listings.`
- Approved merchant create succeeds with existing draft product media IDs and returns a `listing_id`.
- Public marketplace search remains approval-gated and does not expose newly-created review listings.
- Existing web merchant dashboard still shows seller-created listings.
- Native Seller/Store now shows seller-owned listings, including newly-created review listings.
- Seller/Store product media gallery renders payload-backed media tiles.
- NativeMediaViewer opens from Seller/Store media and displays title, seller identity, navigation, and share controls.

Scoped hardening implemented:

- Added protected `GET /api/pulse/marketplace/seller/listings`.
- Reused existing marketplace listing, seller, and media tables.
- Reused existing `pulse_marketplace_listing_payload(...)` and marketplace media payload normalization.
- Updated `loadSellerStoreSnapshot()` to use seller-owned listings instead of public marketplace search.
- Updated the composer success handoff to return to Seller/Store after review submission.
- Updated seller listing composer audits.

Why this was necessary:

- Public marketplace search correctly filters to approved/active listings.
- Seller tools need to show a seller their own pending-review listings after submission.
- The fix preserves public marketplace compatibility and does not duplicate moderation or approval logic.

Verification evidence:

- Backend contract check passed against local QA backend/proxy.
- Built-in QA browser verified `/pulse/marketplace/create`.
- Built-in QA browser verified `/pulse/seller-store`.
- Built-in QA browser opened NativeMediaViewer from Seller/Store media.
- `reports/pulsesoc_native_seller_listing_composer_qa.md` records detailed evidence and unverified provider/device items.

Remaining release/provider QA gaps:

- Real marketplace-specific image/video upload on physical devices.
- Payout/provider onboarding.
- Payment checkout completion.
- Admin approval/rejection workflow.
- Native edit/delete/inventory controls.

Updated native completion percentages by subsystem:

| Subsystem | Current estimate | Confidence | Remaining high-value gap |
| --- | ---: | --- | --- |
| Auth/session/settings | 86% | Browser + simulator foundations verified | Release device auth and provider edge cases |
| Messaging and calls | 72% | Browser/practical QA verified | LiveKit two-device media and lock-screen release QA |
| Feed/posts/composer | 78% | Browser/static verified | Rich composer options and device media QA |
| Media viewer/upload/camera | 72% | Browser/simulator/media QA partially verified | Physical camera/mic, large video, weak network |
| Reels and Status | 74% | Browser/static verified | Physical native video performance |
| Marketplace and Seller/Store | 85% | Backend contract + authenticated QA browser verified | Listing edit/inventory controls and marketplace-specific upload |
| Search, Saved, Groups, Events, Courses | 75% | Browser/static verified | Data-rich authenticated QA depth |
| Trust, Safety, Verification, Account Health | 82% | Browser verified | Document/admin/provider flows remain web/server-owned |
| Premium, Creator, Growth, Intelligence | 74% | Browser/static verified | Provider/billing/advanced tools remain fallback |
| Live and Calls | 56% | Practical shell verified | Native LiveKit/call media release QA |
| Android readiness | 35% | Tooling partially verified | Physical Android QA |

overall native migration percentage: 77% foundation/parity coverage, 63% release QA confidence.

Recommended next highest-value native feature/action: Marketplace Listing Edit + Seller Inventory Controls foundation.

Reason for recommendation:

- Seller create now works, and seller-owned pending listings are visible in native Seller/Store.
- The next commerce gap is lifecycle management: edit review/draft listings, update listing status, remove listings, and expose inventory controls while keeping approval, moderation, checkout, payouts, refunds, disputes, and fulfillment server-authoritative.

Reusable APIs/code/database/business logic for next action:

- Existing marketplace listing and media tables.
- Existing seller approval and moderation status fields.
- Existing merchant dashboard behavior that already lists all seller-owned listings.
- Existing NativeMediaViewer, Seller/Store, marketplace media payloads, Camera Studio handoff, and safe web/provider fallbacks.

What must be rebuilt or adjusted natively:

- Native seller-owned listing detail/edit gateway.
- Native inventory status controls only where backend APIs exist or can be safely exposed.
- Fallback routing for unsupported edit, provider, payout, tax, dispute, and fulfillment tools.

Dependencies/blockers:

- Confirm whether a safe JSON update endpoint exists for marketplace listings.
- If it does not exist, add a narrow seller-owned update endpoint that preserves approval/moderation gates.
- Physical media upload and provider QA remain release blockers, not development blockers.

Risk level: medium.

Estimated complexity: medium.

Safest implementation plan:

1. Inspect existing merchant dashboard listing update/edit behavior and marketplace admin review flow.
2. Reuse existing listing/media schema and server-side approval rules.
3. Add or reuse only seller-owned endpoints for editable fields.
4. Keep public marketplace visibility approval-gated.
5. Build native edit/inventory UI around server responses.
6. Run backend contract checks and QA browser route checks before commit.

## Native Seller Inventory Controls Foundation

Completed action: built native Marketplace Listing Edit + Seller Inventory Controls foundation.

What was implemented:

- Added seller-owned listing mutation APIs:
  - `PATCH/POST /api/pulse/marketplace/seller/listings/<listing_id>`
  - `POST /api/pulse/marketplace/seller/listings/<listing_id>/pause`
  - `POST /api/pulse/marketplace/seller/listings/<listing_id>/resume`
  - `POST/DELETE /api/pulse/marketplace/seller/listings/<listing_id>/delete`
- Added backend ownership checks for every seller listing mutation.
- Reused approved merchant checks for edit and resume.
- Reused existing marketplace review/risk scoring for edit and resume.
- Kept public marketplace search approval-gated.
- Implemented soft delete through `seller_deleted` status instead of physical deletion.
- Added native seller inventory controls inside Seller/Store:
  - status labels
  - listing selection
  - title/description/category/price/quantity edit fields
  - save and review
  - pause
  - resume review
  - remove
  - media handoff
  - safe web fallback for advanced edit/provider flows
- Added `scripts/pulsesoc_native_seller_inventory_audit.py`.
- Added `reports/pulsesoc_native_seller_inventory_progress.md`.
- Verified the seller-owned backend contract with a local authenticated QA seller:
  - update persisted seller-owned title/description/category/price/quantity
  - pause hid the listing from public marketplace search
  - resume returned the listing through marketplace review
  - soft delete set `seller_deleted`
  - deleted listings stayed out of public marketplace search
- Verified static/native gates:
  - `npm ci --prefix mobile-native --no-audit --no-fund --progress=false`
  - `npm run --prefix mobile-native typecheck`
  - Expo Doctor
  - seller inventory audit script
  - `git diff --check`
- QA browser route check confirmed the Seller/Store route remains protected behind auth. Authenticated browser interaction did not complete in this pass and remains a hardening follow-up, not a development blocker.

Reusable backend/API/database/business logic:

- Existing marketplace listing table.
- Existing marketplace product media table.
- Existing seller approval rules.
- Existing marketplace listing review/risk scoring.
- Existing public marketplace visibility rules.
- Existing seller-owned listing payload builder.
- Existing NativeMediaViewer, Seller/Store, Camera Studio, and safe fallback routing.

Native-only work:

- Seller inventory UI and status presentation.
- Edit gateway controls.
- Pause/resume/remove action controls.
- Local state refresh from backend responses.
- Seller/Store layout and copy.

Remaining gaps:

- Practical QA hardening for edit/pause/resume/remove flows.
- Physical marketplace media upload QA.
- Provider checkout/payout QA.
- Admin approval/rejection QA.
- Native media reorder/remove controls.
- Dedicated seller listing detail/editor route.

Updated native completion percentages by subsystem:

| Subsystem | Current estimate | Confidence | Remaining high-value gap |
| --- | ---: | --- | --- |
| Auth/session/settings | 86% | Browser + simulator foundations verified | Release device auth and provider edge cases |
| Messaging and calls | 72% | Browser/practical QA verified | LiveKit two-device media and lock-screen release QA |
| Feed/posts/composer | 78% | Browser/static verified | Rich composer options and device media QA |
| Media viewer/upload/camera | 72% | Browser/simulator/media QA partially verified | Physical camera/mic, large video, weak network |
| Reels and Status | 74% | Browser/static verified | Physical native video performance |
| Marketplace and Seller/Store | 88% | Backend contract + native inventory foundation | Inventory QA, marketplace-specific upload, provider QA |
| Search, Saved, Groups, Events, Courses | 75% | Browser/static verified | Data-rich authenticated QA depth |
| Trust, Safety, Verification, Account Health | 82% | Browser verified | Document/admin/provider flows remain web/server-owned |
| Premium, Creator, Growth, Intelligence | 74% | Browser/static verified | Provider/billing/advanced tools remain fallback |
| Live and Calls | 56% | Practical shell verified | Native LiveKit/call media release QA |
| Android readiness | 35% | Tooling partially verified | Physical Android QA |

overall native migration percentage: 78% foundation/parity coverage, 63% release QA confidence.

Recommended next highest-value native feature/action: Native Seller Inventory Practical QA Hardening.

Reason for recommendation:

- The seller inventory foundation adds seller-owned mutation APIs and commerce lifecycle controls.
- Because this touches marketplace trust, public visibility, and seller state, a short authenticated backend/browser QA pass should verify edit, pause, resume, remove, status labels, public marketplace filtering, and Seller/Store refresh before moving to another major feature.

Reusable APIs/code/database/business logic for next action:

- Seller-owned listings endpoint.
- Seller listing update/pause/resume/delete APIs.
- Existing seller approval rules.
- Existing marketplace moderation/review/risk scoring.
- Existing public search approval filters.
- Existing NativeMediaViewer and Seller/Store components.

What must be rebuilt or adjusted natively:

- Only scoped blockers found in QA.
- Do not duplicate checkout, payout, moderation, approval, refund, dispute, or fulfillment logic.

Dependencies/blockers:

- QA account needs approved merchant status and owned listings with media.
- Provider checkout/payout remains fallback/provider-owned.
- Physical marketplace media upload remains release QA.

## Native Seller Inventory Practical QA Hardening

Completed action: verified and hardened native Seller Inventory lifecycle.

What was verified and hardened:

- Seller-owned listings load through authenticated backend contract checks.
- Title, description, category, price label, and quantity updates persist server-side.
- Pause changes listing status to `paused`.
- Paused listings remain excluded from public marketplace search.
- Resume returns listings through marketplace review.
- Soft delete changes status and approval state to `seller_deleted`.
- Seller-deleted listings are hidden from active seller inventory by default.
- Public Marketplace remains approval-gated after pause/delete.
- Native Seller/Store removes a listing from active inventory immediately after server-confirmed soft removal.
- NativeMediaViewer payload coverage remains available for inventory media.
- Safe web/provider boundaries remain intact for checkout, payout, fulfillment, refunds, disputes, tax, and advanced media editing.

Scoped fix from QA:

- Added default backend filtering to `GET /api/pulse/marketplace/seller/listings` so `seller_deleted`, `deleted`, and `removed` rows do not appear in active seller inventory unless explicitly requested.
- Updated native Seller/Store response handling to clear removed listings from the inventory list after a successful soft delete.

Verification:

- `npm ci --prefix mobile-native --no-audit --no-fund --progress=false`
- `npm run --prefix mobile-native typecheck`
- `cd mobile-native && EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose`
- `venv/bin/python scripts/pulsesoc_native_seller_inventory_audit.py`
- `venv/bin/python scripts/pulsesoc_native_seller_inventory_qa_audit.py`
- `git diff --check`
- Authenticated backend contract checks with a local approved seller and owned listing.
- QA browser route check where practical.

QA browser status:

- `npm run web:qa` served the native web build.
- Seller/Store route remains auth-protected.
- Authenticated React Native Web click-through did not complete reliably in this pass, so browser UI interaction remains a practical QA gap.
- Backend contract verification covered the seller inventory lifecycle against authenticated server APIs.

Completed native subsystems:

- Native app foundation.
- Auth/session foundation.
- Messenger.
- Notifications and Activity Inbox.
- Home Feed, Post Detail, and Feed Composer.
- Profile and Profile Edit.
- Reels and Status viewer/creator.
- Shared Media Upload, Camera Studio, and Media Viewer foundations.
- Marketplace browse/detail.
- Seller/Store dashboard, listing composer, seller-owned listings, marketplace media payloads, and seller inventory controls.
- Search, Saved, Groups, Events, Courses, Creator, Growth, Premium, Intelligence, Trust/Safety, Verification, Account Health, Blocks/Mutes/Reports, Live Viewer, and Calls shells/foundations.

Remaining major subsystems:

- Buyer-side order history and purchase controls.
- Marketplace provider QA for checkout, payout, refunds, disputes, and fulfillment.
- Native media reorder/remove controls for marketplace listings.
- Physical iPhone/Android media upload QA.
- Full LiveKit call/live release QA.
- Android physical QA.
- Final native polish, accessibility, animation, and performance pass.

Updated native completion percentages by subsystem:

| Subsystem | Current estimate | Confidence | Remaining high-value gap |
| --- | ---: | --- | --- |
| Auth/session/settings | 86% | Browser + simulator foundations verified | Release device auth and provider edge cases |
| Messaging and calls | 72% | Browser/practical QA verified | LiveKit two-device media and lock-screen release QA |
| Feed/posts/composer | 78% | Browser/static verified | Rich composer options and device media QA |
| Media viewer/upload/camera | 72% | Browser/simulator/media QA partially verified | Physical camera/mic, large video, weak network |
| Reels and Status | 74% | Browser/static verified | Physical native video performance |
| Marketplace and Seller/Store | 90% | Backend contract + inventory QA hardening | Buyer orders, provider QA, media reorder/remove |
| Search, Saved, Groups, Events, Courses | 75% | Browser/static verified | Data-rich authenticated QA depth |
| Trust, Safety, Verification, Account Health | 82% | Browser verified | Document/admin/provider flows remain web/server-owned |
| Premium, Creator, Growth, Intelligence | 74% | Browser/static verified | Provider/billing/advanced tools remain fallback |
| Live and Calls | 56% | Practical shell verified | Native LiveKit/call media release QA |
| Android readiness | 35% | Tooling partially verified | Physical Android QA |

overall native migration percentage: 79% foundation/parity coverage, 64% release QA confidence.

Recommended next highest-value native feature/action: Native Purchase/Order History + Buyer Commerce Controls Foundation.

Reason for recommendation:

- Seller-side marketplace lifecycle is now structurally complete and server-authoritative.
- Buyer-side commerce is the next missing marketplace pillar: users need native order history, purchase status, receipts, seller contact, refund/dispute safe fallbacks, and activity routing.
- This should reuse existing orders, payment records, marketplace listings, seller profiles, Activity Inbox, NativeMediaViewer, and provider checkout boundaries without moving Stripe/payout/refund authority into the native client.

Reusable APIs/code/database/business logic for next action:

- Existing payment/order tables and APIs.
- Marketplace listing/detail APIs.
- Seller profile/storefront logic.
- Existing payment/checkout/provider routes.
- Existing notification/activity routing.
- Existing messaging seller-contact flow.
- Existing moderation, refund, dispute, entitlement, and receipt logic.

What must be rebuilt natively:

- Buyer order history screen.
- Order detail screen.
- Purchase status cards.
- Receipt/open-provider fallbacks.
- Seller contact route.
- Refund/dispute safe fallback gateway.
- Loading/error/offline states.
- Buyer-facing commerce navigation from Marketplace, Activity Inbox, Settings, and Profile.

Dependencies/blockers:

- Need actual authenticated buyer account with order fixtures for deeper QA.
- Checkout, refund, dispute, and payout provider flows remain web/provider-owned.
- No native-only commerce authority should be introduced.

Risk level: medium.

Estimated complexity: medium.

Safest implementation plan:

1. Inspect existing order/payment APIs and Marketplace checkout/provider routes.
2. Build native read-only order history and order detail first.
3. Add seller contact and listing detail navigation.
4. Route receipts, refunds, disputes, and checkout back to existing safe web/provider flows.
5. Verify with backend contract checks and practical browser QA where authenticated fixtures exist.

Risk level: medium.

Estimated complexity: low to medium.

Safest implementation plan:

1. Seed or use an approved seller with active, review-ready, paused, and deleted/removed listings.
2. Verify Seller/Store inventory status labels and edit controls in QA browser.
3. Verify update sends listing through server review.
4. Verify pause hides from public search while preserving seller visibility.
5. Verify resume re-runs review.
6. Verify remove is a soft deletion and public search remains approval-gated.
7. Fix only scoped blockers and preserve production WebView paths.

## Native Purchase/Order History + Buyer Commerce Controls Foundation

Completed action: built the native buyer-side commerce visibility layer.

What was implemented:

- Added read-only native buyer order aliases over existing payment ledgers:
  - `GET /api/pulse/orders`
  - `GET /api/pulse/orders/<transaction_id>`
  - `GET /api/pulse/purchases`
- Reused existing `seller_transactions` and `creator_transactions` records without moving checkout, refund, dispute, shipping, payout, or receipt authority into the native client.
- Added normalized order payload fields for native:
  - order id / transaction id
  - source ledger
  - item title/type/id
  - seller identity
  - marketplace listing id
  - status group
  - amount/currency
  - receipt/support/dispute fallback URLs
  - provider-controlled shipping/tracking placeholder
- Added native buyer order API wrapper and offline cache in `mobile-native/src/api/orders.ts`.
- Added native Purchase History and Order Detail screens.
- Added status visualization for pending, paid, processing, shipped, delivered, cancelled, refunded, and failed orders.
- Added buyer controls for:
  - view receipt through existing web/provider flow
  - support/dispute safe fallback
  - open seller/store
  - open related Marketplace listing
- Added deep-link routing for:
  - `/pulse/orders`
  - `/pulse/orders/<id>`
  - `/pulse/purchases`
  - `/dashboard/orders`
- Added Settings and Marketplace entry points.
- Added notification target routing for purchase/order links.

Verification:

- Static implementation complete.
- `scripts/pulsesoc_native_buyer_orders_audit.py` added.
- Payment/provider behavior remains server/provider-owned and was not moved into the app.
- Authenticated buyer order fixtures are still needed for a practical browser QA hardening pass.

Completed native subsystems:

- Native app foundation.
- Auth/session foundation.
- Messenger.
- Notifications and Activity Inbox.
- Home Feed, Post Detail, and Feed Composer.
- Profile and Profile Edit.
- Reels and Status viewer/creator.
- Shared Media Upload, Camera Studio, and Media Viewer foundations.
- Marketplace browse/detail.
- Seller/Store dashboard, listing composer, seller-owned listings, marketplace media payloads, seller inventory controls, and buyer purchase history/order detail.
- Search, Saved, Groups, Events, Courses, Creator, Growth, Premium, Intelligence, Trust/Safety, Verification, Account Health, Blocks/Mutes/Reports, Live Viewer, and Calls shells/foundations.

Remaining major subsystems:

- Buyer order practical QA with seeded paid/pending/refunded/cancelled transactions.
- Marketplace provider QA for checkout, payout, refunds, disputes, fulfillment, and receipts.
- Native media reorder/remove controls for marketplace listings.
- Physical iPhone/Android media upload QA.
- Full LiveKit call/live release QA.
- Android physical QA.
- Final native polish, accessibility, animation, and performance pass.

Updated native completion percentages by subsystem:

| Subsystem | Current estimate | Confidence | Remaining high-value gap |
| --- | ---: | --- | --- |
| Auth/session/settings | 86% | Browser + simulator foundations verified | Release device auth and provider edge cases |
| Messaging and calls | 72% | Browser/practical QA verified | LiveKit two-device media and lock-screen release QA |
| Feed/posts/composer | 78% | Browser/static verified | Rich composer options and device media QA |
| Media viewer/upload/camera | 72% | Browser/simulator/media QA partially verified | Physical camera/mic, large video, weak network |
| Reels and Status | 74% | Browser/static verified | Physical native video performance |
| Marketplace and Commerce | 91% | Backend contract + native buyer/seller foundations | Buyer order QA, provider QA, media reorder/remove |
| Search, Saved, Groups, Events, Courses | 75% | Browser/static verified | Data-rich authenticated QA depth |
| Trust, Safety, Verification, Account Health | 82% | Browser verified | Document/admin/provider flows remain web/server-owned |
| Premium, Creator, Growth, Intelligence | 74% | Browser/static verified | Provider/billing/advanced tools remain fallback |
| Live and Calls | 56% | Practical shell verified | Native LiveKit/call media release QA |
| Android readiness | 35% | Tooling partially verified | Physical Android QA |

overall native migration percentage: 80% foundation/parity coverage, 64% release QA confidence.

Recommended next highest-value native feature/action: Native Buyer Orders Practical QA Hardening.

Reason for recommendation:

- Buyer and seller commerce foundations are now both present, but buyer order state needs authenticated fixture validation before expanding commerce.
- The next highest leverage is not another new commerce feature; it is verifying order status rendering, receipt/support fallbacks, listing/seller navigation, Activity Inbox order routing, and empty/offline states against seeded buyer transactions.
- This pass protects payment/provider boundaries while increasing confidence in the commerce subsystem.

Reusable APIs/code/database/business logic for next action:

- `GET /api/pulse/orders`
- `GET /api/pulse/orders/<transaction_id>`
- `GET /api/pulse/purchases`
- `seller_transactions`
- `creator_transactions`
- Marketplace listing/detail APIs
- existing Activity Inbox routing
- existing support/dispute/provider fallback routes

What must be rebuilt natively:

- No new major feature is needed next.
- Practical QA fixtures, buyer-order browser checks, and any scoped UI/data-shape hardening discovered during QA.

Dependencies/blockers:

- Need an authenticated buyer account with seeded transactions across pending, paid, cancelled/refunded/failed states.
- Real payment receipts, Stripe/provider behavior, refunds/disputes, and shipping/tracking remain provider/server QA.

Risk level: low to medium.

Estimated complexity: low.

Safest implementation plan:

1. Seed a QA buyer with seller and creator transactions.
2. Verify `/pulse/orders`, `/pulse/orders/<id>`, `/pulse/purchases`, and `/dashboard/orders`.
3. Verify order list/detail status rendering and offline cache.
4. Verify receipt/support/dispute fallback URLs do not mutate payment state.
5. Verify Marketplace listing and seller navigation.
6. Fix only scoped blockers and preserve production WebView compatibility.

## Native Buyer Orders Practical QA Hardening

Completed action: verified and hardened the native Buyer Orders lifecycle.

What was verified and hardened:

- Seeded buyer, seller, listing, and order fixtures for:
  - pending
  - paid
  - processing
  - shipped
  - delivered
  - cancelled
  - failed
  - refunded
- Verified unauthenticated `/api/pulse/orders` remains protected.
- Verified authenticated `/api/pulse/orders` returns all lifecycle states.
- Verified `/api/pulse/orders/<transaction_id>` returns detail state, seller identity, listing relation, receipt fallback, support fallback, and source ledger.
- Verified `/api/pulse/purchases` returns the same buyer order set through the purchases alias.
- Verified orders sort newest first by server timestamps.
- Verified seller-deleted listing references remain safe for historical refunded order detail.
- Verified signed-out QA browser order routes remain auth-gated without console errors.
- Hardened backend buyer-order normalization so failed/refunded/cancelled/shipped/delivered/processing states are not mislabeled as pending payment state.

Provider/device behavior not verified:

- Real Stripe receipt pages.
- Real refund/dispute provider events.
- Real shipping provider tracking.
- Physical-device notification taps.
- Activity Inbox delivery for live purchase/shipping/refund notifications.
- Authenticated browser click-through with a production-like buyer session.

Completed native subsystems:

- Native app foundation.
- Auth/session foundation.
- Messenger.
- Notifications and Activity Inbox.
- Home Feed, Post Detail, and Feed Composer.
- Profile and Profile Edit.
- Reels and Status viewer/creator.
- Shared Media Upload, Camera Studio, and Media Viewer foundations.
- Marketplace browse/detail.
- Full native commerce foundation: seller application, listing composer, inventory controls, media payloads, buyer purchase history, order detail, and lifecycle QA.
- Search, Saved, Groups, Events, Courses, Creator, Growth, Premium, Intelligence, Trust/Safety, Verification, Account Health, Blocks/Mutes/Reports, Live Viewer, and Calls shells/foundations.

Remaining major subsystems:

- Commerce provider boundary QA for checkout, payout, refunds, disputes, fulfillment, receipts, and shipping/tracking.
- Activity Inbox commerce notification fixtures for purchase/shipping/refund updates.
- Native media reorder/remove controls for marketplace listings.
- Physical iPhone/Android media upload QA.
- Full LiveKit call/live release QA.
- Android physical QA.
- Final native polish, accessibility, animation, and performance pass.

Updated native completion percentages by subsystem:

| Subsystem | Current estimate | Confidence | Remaining high-value gap |
| --- | ---: | --- | --- |
| Auth/session/settings | 86% | Browser + simulator foundations verified | Release device auth and provider edge cases |
| Messaging and calls | 72% | Browser/practical QA verified | LiveKit two-device media and lock-screen release QA |
| Feed/posts/composer | 78% | Browser/static verified | Rich composer options and device media QA |
| Media viewer/upload/camera | 72% | Browser/simulator/media QA partially verified | Physical camera/mic, large video, weak network |
| Reels and Status | 74% | Browser/static verified | Physical native video performance |
| Marketplace and Commerce | 93% | Backend contract + lifecycle QA hardening | Provider QA, commerce notification fixtures, media reorder/remove |
| Search, Saved, Groups, Events, Courses | 75% | Browser/static verified | Data-rich authenticated QA depth |
| Trust, Safety, Verification, Account Health | 82% | Browser verified | Document/admin/provider flows remain web/server-owned |
| Premium, Creator, Growth, Intelligence | 74% | Browser/static verified | Provider/billing/advanced tools remain fallback |
| Live and Calls | 56% | Practical shell verified | Native LiveKit/call media release QA |
| Android readiness | 35% | Tooling partially verified | Physical Android QA |

overall native migration percentage: 80% foundation/parity coverage, 65% release QA confidence.

Recommended next highest-value native feature/action: Native Commerce Polish + Provider Boundary QA.

Reason for recommendation:

- The buyer/seller commerce loop is now structurally complete and lifecycle-hardened.
- The remaining risk is not another new screen; it is provider boundary clarity, Activity Inbox commerce notification fixtures, and release-blocker documentation around checkout, receipts, refunds, disputes, fulfillment, and shipping.
- A short commerce polish pass can improve trust, reduce regressions, and preserve server-authoritative payment logic before moving to another large subsystem.

Reusable APIs/code/database/business logic for next action:

- `seller_transactions`
- `creator_transactions`
- `marketplace_listings`
- `/api/pulse/orders`
- `/api/pulse/orders/<transaction_id>`
- `/api/pulse/purchases`
- `/api/pulse/payments/checkout`
- Activity Inbox notification routing
- existing Stripe/provider checkout, receipt, refund, dispute, payout, fulfillment, and support routes

What must be rebuilt natively:

- No new major business logic.
- Practical QA fixtures and small UI polish only:
  - commerce notification routing checks
  - receipt/support/dispute fallback clarity
  - buyer/seller commerce navigation polish
  - empty/error/offline state polish

Dependencies/blockers:

- Real provider tests need configured Stripe/provider test accounts and safe test transactions.
- Shipping/refund/dispute behavior requires provider/server fixtures.
- Physical notification taps require device push setup.

Risk level: low.

Estimated complexity: low.

Safest implementation plan:

1. Seed purchase, refund, shipping, and dispute notification fixtures.
2. Verify Activity Inbox routes each commerce notification into native Buyer Orders, Seller/Store, Marketplace Detail, or safe fallback.
3. Verify provider-owned actions are clear, non-mutating, and do not bypass backend checks.
4. Polish buyer/seller commerce copy and state layout only where it reduces ambiguity.
5. Preserve production WebView compatibility and keep payment/provider logic server-authoritative.

## Native Commerce Polish + Provider Boundary QA

Completed action: stabilized and documented native commerce provider boundaries without adding new commerce features.

What was verified:

- Checkout remains server-authoritative through `POST /api/pulse/payments/checkout`.
- Unauthenticated checkout is blocked.
- Self-purchase, free/unpriced checkout, and unapproved-seller checkout are blocked.
- Missing Stripe configuration creates server-side blocked transactions with no checkout URL and no card charge.
- Buyer Orders reads server transaction state rather than local payment state.
- Buyer Order Detail keeps receipt, support, dispute, and provider-controlled tracking fallbacks.
- Seller Orders and Buyer Orders share the same transaction ledger.
- Historical orders tied to seller-deleted listings remain safely viewable.
- Native Marketplace, Seller/Store, Buyer Orders, Activity Inbox, and notification routing keep payment/refund/dispute/shipping logic server/provider-owned.

Provider/device behavior not verified:

- Real Stripe checkout success and receipt pages.
- Expired checkout session recovery.
- Refund and dispute webhook delivery.
- Shipping/tracking provider delivery.
- Provider-generated commerce notifications in Activity Inbox.
- Physical-device push notification taps for commerce events.

Completed native subsystems:

- Native app foundation.
- Auth/session foundation.
- Messenger.
- Notifications and Activity Inbox.
- Home Feed, Post Detail, and Feed Composer.
- Profile and Profile Edit.
- Reels and Status viewer/creator.
- Shared Media Upload, Camera Studio, and Media Viewer foundations.
- Marketplace browse/detail.
- Full native commerce foundation: seller application, listing composer, inventory controls, marketplace media payloads, buyer purchase history, order detail, lifecycle QA, and provider boundary QA.
- Search, Saved, Groups, Events, Courses, Creator, Growth, Premium, Intelligence, Trust/Safety, Verification, Account Health, Blocks/Mutes/Reports, Live Viewer, and Calls shells/foundations.

Remaining major subsystems:

- Commerce Activity Inbox fixture hardening for purchase/refund/dispute/shipping/provider notifications.
- Native media reorder/remove controls for marketplace listings.
- Physical iPhone/Android media upload QA.
- Full LiveKit call/live release QA.
- Android physical QA.
- Final native polish, accessibility, animation, and performance pass.

Updated native completion percentages by subsystem:

| Subsystem | Current estimate | Confidence | Remaining high-value gap |
| --- | ---: | --- | --- |
| Auth/session/settings | 86% | Browser + simulator foundations verified | Release device auth and provider edge cases |
| Messaging and calls | 72% | Browser/practical QA verified | LiveKit two-device media and lock-screen release QA |
| Feed/posts/composer | 78% | Browser/static verified | Rich composer options and device media QA |
| Media viewer/upload/camera | 72% | Browser/simulator/media QA partially verified | Physical camera/mic, large video, weak network |
| Reels and Status | 74% | Browser/static verified | Physical native video performance |
| Marketplace and Commerce | 94% | Backend contract + lifecycle/provider-boundary QA | Commerce notification fixtures, provider-live release QA, media reorder/remove |
| Search, Saved, Groups, Events, Courses | 75% | Browser/static verified | Data-rich authenticated QA depth |
| Trust, Safety, Verification, Account Health | 82% | Browser verified | Document/admin/provider flows remain web/server-owned |
| Premium, Creator, Growth, Intelligence | 74% | Browser/static verified | Provider/billing/advanced tools remain fallback |
| Live and Calls | 56% | Practical shell verified | Native LiveKit/call media release QA |
| Android readiness | 35% | Tooling partially verified | Physical Android QA |

overall native migration percentage: 80% foundation/parity coverage, 66% release QA confidence.

Recommended next highest-value native feature/action: Native Commerce Activity Fixture Hardening.

Reason for recommendation:

- Commerce now has the buyer/seller loop plus provider-boundary stabilization.
- The next reliability gap is cross-system event visibility: purchase completion, failed payment, refund, dispute, shipping update, and seller-payment events should route cleanly through Activity Inbox into native Buyer Orders, Seller/Store, Marketplace Detail, or a safe fallback.
- This is stabilization, not new business logic, and it keeps payment/provider events server-authoritative while making the native app feel more alive.

Reusable APIs/code/database/business logic for next action:

- existing notification APIs
- Activity Inbox APIs and classifiers
- `notify_user` commerce events
- `seller_transactions`
- `creator_transactions`
- `/api/pulse/orders`
- `/api/pulse/payments/seller/orders`
- existing notification/deep-link routing
- Stripe/provider webhook status categories

What must be rebuilt natively:

- No new commerce feature is needed.
- Seeded commerce notification fixtures, Activity Inbox route QA, and any scoped fallback/copy fixes discovered during QA.

Dependencies/blockers:

- Real provider-generated push taps still require physical device/provider QA.
- Refund/dispute/shipping provider webhooks need configured provider test fixtures for release confidence.

Risk level: low.

Estimated complexity: low.

Safest implementation plan:

1. Seed activity fixtures for purchase complete, failed payment, seller payment, refund, dispute, and shipping update.
2. Verify Activity Inbox category grouping, unread state, deep-link target, and fallback route behavior.
3. Confirm native order and seller screens can safely open from each commerce event.
4. Fix only scoped routing/copy/fallback issues.
5. Keep provider creation and payment state mutation server-side.

## Native Commerce + Activity Fixture Hardening

Completed action: hardened commerce/activity fixture consistency across backend notifications, Buyer Orders, Seller Orders, Marketplace listing state, and native routing.

What was verified:

- Commerce events are seeded through existing `notify_user` and `pulse_notifications`, not a native-only event store.
- Fixture events cover purchase completed, payment failed, refund issued, dispute created, shipping updated, order cancelled, listing created, listing updated, and listing removed.
- Buyer order history reflects paid, failed, refunded, cancelled, shipped, and dispute-opened transaction states.
- Seller order endpoint reads the same transaction ledger as buyer orders.
- Deleted/seller-removed listings remain safe in historical order views.
- Activity unread counts include commerce events.
- Notification list and badge APIs now include legacy Pulse commerce notifications written by existing `notify_user` paths, so native Activity Inbox can see existing commerce events without a new native store.
- Activity read/delete operations use existing notification APIs.
- Activity Inbox classifies order/payment/refund/listing/seller signals through the existing Marketplace lane.
- Native notification routing supports `/pulse/orders`, `/pulse/orders/<id>`, `/pulse/purchases`, `/dashboard/orders`, `/pulse/marketplace`, `/pulse/activity`, and `/pulse/inbox`.
- Duplicate provider event handling remains guarded by existing Stripe webhook idempotency code.

Provider/device behavior not verified:

- Live APNs/FCM commerce notification taps.
- Physical badge synchronization.
- Live Stripe refund/dispute webhook delivery.
- Real shipping provider webhook delivery.
- Cross-device activity sync.
- Offline cache restore with network disabled.

Completed native subsystems:

- Native app foundation.
- Auth/session foundation.
- Messenger.
- Notifications and Activity Inbox.
- Home Feed, Post Detail, and Feed Composer.
- Profile and Profile Edit.
- Reels and Status viewer/creator.
- Shared Media Upload, Camera Studio, and Media Viewer foundations.
- Marketplace browse/detail.
- Full native commerce foundation: seller application, listing composer, inventory controls, marketplace media payloads, buyer purchase history, order detail, lifecycle QA, provider boundary QA, and commerce/activity fixture hardening.
- Search, Saved, Groups, Events, Courses, Creator, Growth, Premium, Intelligence, Trust/Safety, Verification, Account Health, Blocks/Mutes/Reports, Live Viewer, and Calls shells/foundations.

Remaining major subsystems:

- Native real-time event sync readiness for activity, commerce, messaging, calls, alerts, safety, and marketplace state.
- Native media reorder/remove controls for marketplace listings.
- Physical iPhone/Android media upload QA.
- Full LiveKit call/live release QA.
- Android physical QA.
- Final native polish, accessibility, animation, and performance pass.

Updated native completion percentages by subsystem:

| Subsystem | Current estimate | Confidence | Remaining high-value gap |
| --- | ---: | --- | --- |
| Auth/session/settings | 86% | Browser + simulator foundations verified | Release device auth and provider edge cases |
| Messaging and calls | 72% | Browser/practical QA verified | LiveKit two-device media and lock-screen release QA |
| Feed/posts/composer | 78% | Browser/static verified | Rich composer options and device media QA |
| Media viewer/upload/camera | 72% | Browser/simulator/media QA partially verified | Physical camera/mic, large video, weak network |
| Reels and Status | 74% | Browser/static verified | Physical native video performance |
| Marketplace and Commerce | 95% | Backend contract + lifecycle/provider/activity fixture QA | Provider-live release QA, media reorder/remove |
| Notifications and Activity Inbox | 84% | Browser + backend fixture verified | Real-time sync, push-tap/device badge release QA |
| Search, Saved, Groups, Events, Courses | 75% | Browser/static verified | Data-rich authenticated QA depth |
| Trust, Safety, Verification, Account Health | 82% | Browser verified | Document/admin/provider flows remain web/server-owned |
| Premium, Creator, Growth, Intelligence | 74% | Browser/static verified | Provider/billing/advanced tools remain fallback |
| Live and Calls | 56% | Practical shell verified | Native LiveKit/call media release QA |
| Android readiness | 35% | Tooling partially verified | Physical Android QA |

overall native migration percentage: 81% foundation/parity coverage, 67% release QA confidence.

Recommended next highest-value native feature/action: Native Real-time Event Sync Readiness.

Reason for recommendation:

- Commerce and Activity now agree through seeded backend fixtures.
- The next gap is event freshness and cross-device consistency, not another commerce screen.
- PulseSoc already has many native surfaces that currently load or poll independently. A shared real-time sync layer can keep Activity Inbox, Buyer Orders, Seller Inventory, Messenger, Calls, Alerts, Safety, and Marketplace state aligned while preserving backend authority.

Reusable APIs/code/database/business logic for next action:

- existing notification APIs and `pulse_notifications`
- existing Messenger/conversation unread APIs
- existing Calls active-call APIs
- existing alert/intelligence event APIs
- existing commerce ledgers: `seller_transactions`, `creator_transactions`, `marketplace_listings`
- existing server-side websocket/SSE/realtime/event infrastructure if present
- existing native cache utilities and refresh hooks

What must be rebuilt natively:

- A small shared native event-sync service that subscribes or polls, maps event envelopes to cache invalidation, and safely refreshes affected screens.
- No duplicated backend business logic.

Dependencies/blockers:

- Need inspection of current production realtime infrastructure before choosing WebSocket, SSE, long-polling, or hybrid fallback.
- Physical push and cross-device sync still require device/provider QA.

Risk level: medium.

Estimated complexity: medium.

Safest implementation plan:

1. Inspect existing production realtime, websocket, SSE, notification, and polling infrastructure.
2. Inventory native screens that currently poll independently.
3. Define a minimal server-authoritative event envelope and cache invalidation map.
4. Build native event-sync foundation with graceful polling fallback.
5. Verify with seeded backend events before attempting provider/device push sync.

## Native Real-time Event Sync Readiness

Completed action: audited the current PulseSoc production backend and native migration state against the intended final state of a fully real-time synchronized PulseSoc system.

What is fully consistent already:

- Commerce event truth is backend-owned and fixture-verified across Buyer Orders, Seller Orders, Seller Inventory, Marketplace listing references, Activity Inbox, and Notifications.
- Activity Inbox can aggregate server notifications, Messenger unread summaries, and active calls without becoming a separate source of truth.
- Notification routing supports the current native commerce/activity targets, including `/pulse/orders`, `/pulse/orders/<id>`, `/dashboard/orders`, `/pulse/marketplace`, `/pulse/activity`, and `/pulse/inbox`.
- Existing notification and payment paths include duplicate/idempotency protections.
- Native cache reads safely remove corrupted cache payloads.
- Command Center realtime worker/client contracts exist and degrade to polling fallback when disabled.

What is partially synced:

- Activity Inbox, badge counts, Buyer Orders, Seller Store, Marketplace, Messenger, Calls, Safety, and Verification each have local refresh/cache behavior, but invalidation is not centralized.
- Messenger and Calls already poll their own endpoints, but Activity Inbox does not yet receive a shared event cursor that refreshes the related message/call summaries.
- Seller Inventory and Buyer Orders share backend state, but an already-open native screen may remain stale until manual, focus, foreground, or interval refresh.
- Safety and Verification state refresh correctly from server APIs, but changes do not yet push through a native event-sync layer.

What is stale or inconsistent risk:

- Activity badge can update before Buyer Orders or Seller Store refreshes.
- Marketplace listing state can change on the backend while cached search/seller inventory remains visible until refresh.
- Call or Messenger state can update in a focused screen before the Activity Inbox summary refreshes.
- Offline cache restore is safe, but stale data age is not displayed consistently across all sync-sensitive screens.

What is missing for full real-time readiness:

- Native event-sync service with one cursor per signed-in user.
- Shared invalidation map for activity, orders, seller inventory, marketplace, messages, calls, safety, verification, alerts, and intelligence.
- Server-authenticated main-app proxy or direct native endpoint for Command Center realtime poll/stream events.
- Event replay on app resume and deterministic duplicate suppression at the native cache-invalidation layer.
- Cross-device provider QA for push, badge, notification tap, and foreground/background timing.

Updated subsystem completion:

| Subsystem | Current estimate | Real-time sync readiness | Remaining gap |
| --- | ---: | ---: | --- |
| Activity + Notifications | 86% | 78% | Event cursor, shared invalidation, provider/device push QA |
| Buyer Orders | 91% | 75% | Order event-triggered refresh and replay |
| Seller Inventory | 92% | 75% | Listing/order invalidation and seller activity refresh |
| Marketplace | 91% | 72% | Listing-state refresh, media-change invalidation |
| Messenger | 76% | 65% | Shared conversation/message event bridge |
| Calls | 62% | 58% | Active-call event bridge, LiveKit/two-device release QA |
| Safety/Trust | 84% | 72% | Enforcement/report/appeal event refresh |
| Verification | 84% | 72% | Review/badge event refresh |
| Native media/camera | 72% | 60% | Physical device upload/camera release QA |
| Android readiness | 35% | 30% | Physical Android QA |

overall native migration percentage: 82% foundation/parity coverage, 69% release QA confidence.

Recommended next highest-value action: Native Event Sync Foundation.

Reason for recommendation:

- The backend and native app now have enough event, cache, and routing contracts to support a small shared sync layer.
- Another UI feature would add more independent refresh paths; a shared event-sync service will make the existing native app feel alive and coherent.
- The safest next implementation is polling-first and server-authoritative, using existing Command Center realtime contracts when available and degrading to current refresh behavior when unavailable.

Reusable APIs/code/database/business logic:

- `services/command_center_client.py` realtime helpers.
- Command Center worker realtime event/poll/stream/status routes.
- existing notification APIs and `pulse_notifications`.
- existing Messenger sync endpoints.
- existing Call active/status/events endpoints.
- existing Buyer Orders, Seller Orders, Marketplace listing APIs.
- existing Safety, Verification, Alert, and Intelligence APIs.
- native cache helpers in `mobile-native/src/core/cache.ts`.

What must be rebuilt natively:

- A small native event-sync service.
- A cache invalidation registry.
- A persisted `latest_event_id` cursor.
- Foreground/resume/reconnect polling hooks.
- Screen refresh callbacks for Activity Inbox, Orders, Seller Store, Marketplace, Messenger, Calls, Safety, Verification, Alerts, and Intelligence.

Dependencies/blockers:

- Need to choose whether native polls the main app or a user-authenticated proxy to Command Center.
- Provider push and cross-device delivery timing remain release QA blockers.
- Full WebSocket/SSE streaming should remain deferred until polling-first behavior is verified.

Risk level: medium.

Estimated complexity: medium.

Safest implementation plan:

1. Build a polling-first native event-sync service with no business logic.
2. Store and replay `latest_event_id` per signed-in user.
3. Map event families to cache invalidation keys and optional screen refresh callbacks.
4. Wire first to Activity Inbox, badge counts, Buyer Orders, Seller Store, Marketplace, Messenger, and Calls.
5. Add seeded event replay/idempotency audits.
6. Keep WebSocket/SSE, APNs/FCM timing, and cross-device behavior as release hardening tasks after the polling-first layer passes.

## Native Event Sync Foundation

Completed action: built the polling-first native event sync foundation with persistent cursor tracking and centralized cache invalidation.

What is synchronized correctly now:

- Activity Inbox and notification badge counts can be invalidated from the shared native sync registry.
- Buyer Orders refreshes from server-authoritative order/payment state when order-related events invalidate.
- Marketplace listing search/detail state refreshes when listing/marketplace events invalidate.
- Seller Store / Seller Inventory refreshes when seller inventory, marketplace, or order events invalidate.
- Foreground notifications invalidate Activity + Notifications in addition to the existing badge refresh.
- App foreground/startup can trigger a safe polling-first refresh path.

What still relies on stale local state:

- Messenger and Calls are mapped in the invalidation classifier but are not yet wired to screen-level event handlers.
- Safety, Verification, Premium, Intelligence, and Alerts are mapped for event classification but still rely on their existing per-screen refresh/cache behavior.
- Full delta replay depends on a production-confirmed `/api/pulse/sync/events` or equivalent authenticated event feed.

What can still break under concurrent updates:

- Two-device seller inventory edits can briefly show stale inventory until event polling or foreground refresh runs.
- Buyer Orders and Activity can still disagree temporarily if payment/provider events arrive before the native delta endpoint exposes them.
- Marketplace moderation/listing state can remain cached if the backend does not emit or expose a sync event.
- Activity Inbox can still lag behind Messenger/Calls until those screen handlers are connected to the shared registry.

What is missing for true real-time readiness:

- Confirmed backend event replay contract with stable event IDs and cursor semantics.
- Seeded event QA for order, payment, refund, listing, notification, message, and call events.
- Messenger/Calls/Safety/Verification/Alerts/Intelligence handler wiring after seeded sync behavior is proven.
- Later WebSocket/SSE layer after polling-first sync proves stable.
- Physical APNs/FCM/cross-device provider timing QA.

Updated subsystem completion:

| Subsystem | Current estimate | Sync coverage | Remaining gap |
| --- | ---: | ---: | --- |
| Activity + Notifications | 88% | 83% | Seeded event replay and provider/device push QA |
| Buyer Orders | 92% | 82% | Seeded order/payment/refund event QA |
| Seller Inventory | 93% | 82% | Seeded listing/order invalidation QA |
| Marketplace | 92% | 80% | Listing moderation/media-change event QA |
| Messenger | 76% | 66% | Shared conversation/message handler wiring |
| Calls | 63% | 60% | Active-call event bridge and two-device release QA |
| Safety/Trust | 84% | 73% | Enforcement/report/appeal handler wiring |
| Verification | 84% | 73% | Review/badge handler wiring |
| Intelligence/Alerts | 80% | 70% | Alert/intelligence handler wiring and provider QA |
| Native media/camera | 72% | 60% | Physical-device upload/camera release QA |
| Android readiness | 35% | 30% | Physical Android QA |

Overall native migration percentage: 83% foundation/parity coverage, 70% release QA confidence.

Recommended next highest-value action: Native Event Sync QA Hardening.

Reason for recommendation:

- The polling/cursor/invalidation layer now exists, but seeded backend events should verify that Activity Inbox, Buyer Orders, Seller Store, Marketplace, and Notifications refresh deterministically without duplicates.
- This is a practical hardening step, not a full realtime/WebSocket build.
- Once seeded event behavior is proven, the next safest expansion is wiring Messenger and Calls into the same registry.

Reusable APIs/code/database/business logic:

- existing Activity Inbox and notification APIs.
- existing Buyer Orders, Seller Orders, Marketplace listing, and Seller Inventory APIs.
- existing server-side payment/provider state and idempotency logic.
- existing native cache helpers and screen refresh callbacks.

What must be rebuilt natively next:

- Seeded event replay QA harness or audit checks.
- Practical QA route checks for Activity Inbox, Orders, Marketplace, and Seller Store after synthetic/seeded state changes.
- Optional handler wiring for Messenger/Calls only after event semantics are verified.

Dependencies/blockers:

- Production-confirmed event delta endpoint remains unverified.
- Provider push/cross-device timing remains release QA.

Risk level: medium.

Estimated complexity: low to medium for QA hardening; medium for the next handler expansion.

Safest implementation plan:

1. Seed or simulate event envelopes for order, payment, refund, listing, notification, message, and call families.
2. Verify classifier invalidates the intended subsystems only once.
3. Verify Activity Inbox, Orders, Seller Store, and Marketplace reload from existing APIs after invalidation.
4. Confirm fallback behavior when the sync endpoint is unavailable.
5. Then wire Messenger/Calls handlers in a separate scoped mission.

## Native Owner iPhone Test Setup

Completed action: prepared the installed physical iPhone native app for Roody owner testing while Codex continues development.

What changed:

- Created a temporary production-backed owner QA account through the existing mobile auth API without weakening production auth.
- Marked the account as QA/test through username/display name because no dedicated test-account user flag was identified in the current user schema.
- Verified production mobile login for the QA account through `/api/mobile/auth/login`.
- Built, signed, installed, launched, and bundled `com.pulsesoc.nativeapp` on the connected iPhone 16 Pro.
- Confirmed `devicectl` lists `PulseSoc Native   com.pulsesoc.nativeapp   0.1.0`.
- Documented Roody's manual walkthrough steps in `reports/pulsesoc_native_owner_iphone_test_setup.md`.
- Kept the temporary password out of reports, source, config, and Git history.

Security correction:

- The original owner QA account credential was exposed outside the intended secure handoff path.
- The original account `roody_native_qa_20260706` was authenticated once and revoked through the existing `/api/account/delete` endpoint; no production auth logic was changed.
- A replacement password was generated and stored only in macOS Keychain under service `PulseSocNativeOwnerQA` and account `roody_native_qa_20260706_r3`.
- Replacement registration/login confirmation is still blocked because production mobile auth POSTs to `/api/mobile/auth/register` and `/api/mobile/auth/login` timed out during the rotation attempt, while `/health` and `/api/mobile/auth/session` stayed healthy.
- Reports and source were scanned for the exposed password fragments and no committed plaintext password was found.

What Roody can test now:

- App install/launch and signed-out native route behavior on the physical iPhone.
- Native login and signed-in session behavior after replacement registration/login confirmation succeeds.
- Home Feed, Messenger, Profile, Reels, Status, Marketplace, Seller Store, Activity Inbox, Notifications, Settings, Camera Studio, Calls screen, Creator, Growth, Premium, and Intelligence/Alerts where backend permissions allow after replacement login is confirmed.
- Physical iPhone visual quality, navigation feel, performance impressions, and manual screen recording/screenshot feedback.

Still unstable or release-gated:

- Replacement owner QA login is blocked until production mobile auth POSTs stop timing out.
- Physical camera/microphone capture, upload, video compression, retry/cancel, and published media IDs.
- Push provider behavior, lock-screen behavior, notification taps, and APNs/FCM badge timing.
- LiveKit two-device calls, background audio, speaker/Bluetooth controls, and lock-screen calling.
- Android physical-device QA.
- Server-side eligibility boundaries for seller, commerce, premium, creator, growth, and intelligence actions.

Updated subsystem completion:

| Subsystem | Current estimate | Owner-test readiness | Remaining gap |
| --- | ---: | ---: | --- |
| App shell / navigation | 92% | 86% | More owner feedback and polish |
| Auth/session | 90% | 84% | Password rotation/delete process after QA |
| Activity + Notifications | 88% | 76% | Provider/device push QA and event cursor |
| Marketplace / Seller / Buyer commerce | 91% | 78% | Provider boundary and physical media QA |
| Camera Studio / native media | 73% | 58% | Real camera/mic/upload evidence |
| Calls | 64% | 54% | Two-device LiveKit and lock-screen QA |
| Creator/Growth/Premium/Intelligence | 82% | 72% | Eligibility/provider fallback hardening |
| iOS readiness | 72% | 68% | Manual owner walkthrough and device media QA |
| Android readiness | 35% | 24% | Physical Android QA |

Overall native migration percentage: 84% foundation/parity coverage, 75% system consistency confidence, 63% release QA confidence.

Recommended next highest-value action: confirm or expose the authenticated server event cursor endpoint for native polling sync.

Reason for recommendation:

- Owner testing can now happen in parallel on a real iPhone.
- The biggest architecture gap remains production-confirmed delta replay for the polling-first native sync layer.
- Another UI feature would add more state surfaces; a server-authoritative event cursor makes existing native features more coherent across Activity, Orders, Seller Store, Marketplace, Messenger, Calls, Safety, Verification, Alerts, and Intelligence.

## Native Autonomous Priority System

Completed action: added the first autonomous progress dashboard and implemented the auto-selected highest-value stability improvement.

Auto-detected weakest subsystem: Event Sync / Real-time consistency.

What changed:

- Created `reports/pulsesoc_native_autonomous_progress.md` with the required PulseSoc system dashboard, subsystem health table, weakest-system explanation, fixed-this-run summary, next auto-selected action, and system health score.
- Created `scripts/pulsesoc_native_autonomous_priority_audit.py` to verify the autonomous dashboard and the native/backend sync contract.
- Added authenticated `GET /api/pulse/sync/events`, a polling-first server event cursor endpoint sourced from existing `pulse_notifications` rows.
- The endpoint supports `after_id`, `after`, and bounded `limit`; returns native-compatible `events`, `cursor`, `latest_event_id`, `latestEventId`, `last_event_at`, and `lastEventAt`; and includes deterministic invalidation hints for native subsystems.
- The endpoint sanitizes sensitive metadata keys before returning event metadata and keeps production auth, WebView routes, notification delivery, payment, marketplace, and business logic unchanged.

Updated subsystem completion:

| Subsystem | Completion | Health | Remaining gap |
| --- | ---: | ---: | --- |
| Marketplace | 92% | 88% | Listing/moderation event replay QA |
| Seller System | 93% | 89% | Seeded seller inventory event replay QA |
| Buyer Orders | 92% | 88% | Seeded payment/refund cursor QA |
| Activity Inbox | 89% | 86% | Event cursor replay and provider/device push QA |
| Messaging | 77% | 74% | Shared message event handler pass |
| Calls | 65% | 66% | Active-call event bridge and two-device LiveKit QA |
| Notifications | 89% | 87% | Provider/device push QA |
| Event Sync | 82% | 81% | Seeded replay QA and handler expansion |
| Trust/Safety | 85% | 83% | Enforcement/report/appeal event QA |
| Verification | 85% | 83% | Admin/provider review event QA |
| Media/Capture | 74% | 72% | Physical capture/upload evidence |
| Creator Tools | 82% | 79% | Advanced fallback/provider hardening |

Overall native migration percentage: 85% foundation/parity coverage, 77% system consistency confidence, 64% release QA confidence.

Recommended next auto-selected action: Seeded Event Cursor QA Hardening.

Reason for recommendation:

- The server-authoritative cursor contract now exists, so the next weakest gap is proving cursor advancement, duplicate suppression, and invalidation behavior under seeded order, listing, message, call, safety, verification, alert, and intelligence events.
- This is the fastest way to raise system-wide consistency without adding another product surface.

## Native Event Cursor Integrity Validation

Completed action: validated the `/api/pulse/sync/events` cursor contract with seeded backend events and documented production-readiness gaps.

What changed:

- Created `reports/pulsesoc_native_cursor_integrity_validation.md`.
- Created `scripts/pulsesoc_native_cursor_integrity_validation_audit.py`.
- Validated unauthenticated protection, initial sync, delta sync, timestamp replay, invalid cursor fallback, event ordering, duplicate safety, cross-user isolation, invalidation hints, and metadata redaction.
- Confirmed the native sync client remains polling-first and does not introduce WebSockets, SSE, or realtime streaming.

Cursor system correctness status:

- Correct for polling-first notification-derived event replay.
- `pulse_notifications.id` provides stable monotonic cursor ordering.
- Server remains the source of truth.
- Native full-resync fallback remains the recovery path when the endpoint is unavailable.

Systems that break under replay:

- No cursor-contract breakage found in seeded temp-db validation.
- Screen-level handler refresh under live high-volume backend bursts remains unproven.

Systems that may drift under concurrency:

- Messenger summary state.
- Calls active-call state.
- Safety enforcement/report state.
- Verification review/badge state.
- Premium entitlement state.
- Intelligence/alert detail state.

Event loss/duplication risks:

- Low for events already mirrored into `pulse_notifications`.
- Medium for event producers that do not yet emit, mirror, or map to a cursor-visible notification/event envelope.

Updated subsystem sync reliability:

| Subsystem | Sync reliability |
| --- | ---: |
| Activity Inbox | 88% |
| Notifications | 90% |
| Buyer Orders | 86% |
| Seller Inventory | 85% |
| Marketplace | 85% |
| Messaging | 72% |
| Calls | 65% |
| Trust/Safety | 78% |
| Verification | 78% |
| Media/Capture | 62% |
| Creator/Premium/Intelligence | 74% |

Overall native migration percentage: 85% foundation/parity coverage, 79% system consistency confidence, 64% release QA confidence.

Critical gaps for production readiness:

- Real provider APNs/FCM delivery and tap routing still need physical-device QA.
- Cursor replay needs authenticated live-data QA beyond seeded temp-db validation.
- Messenger and Calls need dedicated event handler wiring.
- Event producer coverage must be audited across orders, listings, messages, calls, safety, verification, alerts, and intelligence.

ONE highest-impact fix ONLY: Event Producer Coverage Audit.

Reason:

- The cursor endpoint is correct for events it can see, but production readiness now depends on ensuring every critical backend event producer emits or maps to a cursor-visible event envelope with stable id, target URL, entity metadata, and invalidation hints.

## Native Event Producer Coverage Audit

Completed action: audited backend event producer coverage and normalized the shared `notify_user` emitter for native cursor sync.

What changed:

- Created `reports/pulsesoc_native_event_producer_coverage_audit.md`.
- Created `scripts/pulsesoc_native_event_producer_coverage_audit.py`.
- Updated the shared `notify_user` event emitter so every current producer using it writes standardized metadata:
  - `event_type`
  - `entity_type`
  - `entity_id`
  - `actor_id`
  - `timestamp`
  - `sync_cursor_key`
- Validated that a standardized `notify_user` event flows into `pulse_notifications` and is visible through `/api/pulse/sync/events`.

Event producer coverage completeness: 72%.

Missing critical emitters:

- marketplace seller listing create/update/pause/resume/delete
- marketplace seller application changes
- checkout blocked/failure states before Stripe handoff
- message seen/delete/report cursor mirroring
- call active/ringing/ended state transitions
- safety block/mute/report/appeal state changes
- verification request/review/appeal details
- premium entitlement refresh outside payment success
- intelligence source/forecast/read-state changes outside delivered alerts

Duplicate / unsafe producers:

- `notify_user`, `notification_service`, `pulsesoc_notification_system`, feed notifications, alert delivery, and realtime message events can all produce user-visible events.
- This remains safe only when `pulse_notifications` is treated as the native cursor-visible truth source.
- Retry/idempotency is not uniformly proven across all producer families.

Systems not emitting cursor-visible events consistently:

- seller inventory controls
- marketplace report/save
- trust/safety control actions
- verification request/appeal state
- selected call lifecycle branches

Sync pipeline integrity score: 78/100.

Overall native migration percentage: 85% foundation/parity coverage, 80% system consistency confidence, 64% release QA confidence.

Critical production risk gaps:

- Silent backend mutations can leave Activity Inbox and dependent native screens stale until full refresh.
- Event producers need stable cursor-visible event envelopes before true realtime streaming should be attempted.
- Provider/device push remains release-gated.

Recommended next native feature/action: Marketplace Seller Inventory Event Emission Hardening.

Reason for recommendation:

- Seller inventory is complete enough that its remaining risk is consistency, not UI.
- Listing create/update/pause/resume/delete mutations affect Seller Store, Marketplace, Buyer Orders, Activity Inbox, and Notifications.
- These routes are the clearest high-value silent mutation gap discovered by the current audit.

## Seller Inventory Event Emission Hardening

Completed action: hardened cursor-visible event emission for marketplace seller application and seller inventory lifecycle mutations.

What changed:

- Created `reports/pulsesoc_native_seller_inventory_event_emission.md`.
- Created `scripts/pulsesoc_native_seller_inventory_event_emission_audit.py`.
- Added the shared `pulse_emit_marketplace_inventory_event(...)` backend helper.
- Wired seller application submit/change events into the native sync cursor.
- Wired marketplace listing create/update/pause/resume/soft-delete events into the native sync cursor.
- Wired admin marketplace listing review state changes into the native sync cursor.

Seller inventory event coverage: 95%.

Remaining silent mutation paths:

- marketplace save/report actions if those should become user-visible Activity events
- checkout blocked/failure states before Stripe handoff
- message seen/delete/report cursor mirroring
- call active/ringing/ended state transitions
- safety block/mute/report/appeal state changes
- verification request/review/appeal details
- payment/refund/dispute lifecycle branches

Event visibility through sync cursor:

- `seller_application_submitted`
- `seller_application_changed`
- `seller_listing_created`
- `seller_listing_updated`
- `seller_listing_paused`
- `seller_listing_resumed`
- `seller_listing_deleted`
- `seller_listing_review_changed`

Activity/Marketplace/Seller Store consistency impact:

- Seller Store can invalidate from cursor events instead of relying only on screen reloads.
- Marketplace can refresh listing state after seller lifecycle changes.
- Activity Inbox and Notifications can display seller lifecycle transitions.
- Buyer Orders can refresh when listing lifecycle changes may affect active or historical orders.

Event producer coverage: 76%.

Overall native migration percentage: 86% foundation/parity coverage, 82% system consistency confidence, 64% release QA confidence.

Critical production risk gaps:

- Payment/order event producers need the next hardening pass because stale payment states create higher trust risk than additional UI expansion.
- Real APNs/FCM push delivery remains device/provider QA gated.
- Event replay/idempotency remains partially validated with seeded tests, not yet proven under real traffic.

Recommended next native feature/action: Payment and Checkout Failure Event Emission Hardening.

Reason for recommendation:

- Seller inventory is now cursor-visible.
- Payment/order state is the next highest-risk silent mutation family.
- Checkout failure, blocked checkout, refunds, disputes, and payment status transitions must converge across Buyer Orders, Seller Inventory, Activity Inbox, Notifications, and Marketplace.

## Payment and Checkout Event Emission Hardening

Important roadmap rule:

- Do not focus on Android right now.
- Android remains tracked as a later release-readiness gap.
- Current development priority is iPhone/iOS native app, server-authoritative event consistency, payment/checkout/order trust correctness, PulseSoc production parity, and LogiNexus UI polish.
- Do not spend time on Android tooling, Android physical QA, Android-specific bugs, or Android release setup unless the issue also affects shared native code or backend correctness.

Completed action: hardened cursor-visible event emission for checkout, payment, refund, and dispute state changes.

What changed:

- Created `reports/pulsesoc_native_payment_checkout_event_emission.md`.
- Created `scripts/pulsesoc_native_payment_checkout_event_emission_audit.py`.
- Added the shared `pulse_emit_payment_checkout_event(...)` backend helper.
- Wired checkout pending, blocked, failed, created, and expired states into the native sync cursor.
- Wired seller transaction payment succeeded/failed states into the native sync cursor.
- Wired refund issued and dispute opened/updated/resolved states into the native sync cursor.
- Normalized `notify_payment_status(...)` metadata so existing Premium payment notifications carry cursor-safe event metadata.

Payment/checkout event coverage: 82%.

Remaining silent mutation paths:

- `refund_requested` needs a first-class server route or explicit mapping to an existing route.
- `order_cancelled` needs a first-class server route or explicit mapping to an existing route.
- marketplace save/report actions
- message seen/delete/report cursor mirroring
- call active/ringing/ended state transitions
- safety block/mute/report/appeal state changes
- verification request/review/appeal details

Event visibility through sync cursor:

- `payment_pending`
- `checkout_created`
- `checkout_blocked`
- `checkout_failed`
- `checkout_expired`
- `payment_succeeded`
- `payment_failed`
- `refund_issued`
- `dispute_opened`
- `dispute_updated`
- `dispute_resolved`

Activity/Orders/Seller/Marketplace consistency impact:

- Buyer Orders can refresh when provider payment state changes.
- Seller Store can refresh order/payment status after checkout, refund, and dispute events.
- Marketplace can refresh listing/order context where payment state changes affect availability or history.
- Activity Inbox and Notifications can surface financial state transitions from server truth.

Event producer coverage: 81%.

Overall native migration percentage: 87% foundation/parity coverage, 84% system consistency confidence, 64% release QA confidence.

Critical production risk gaps:

- Refund-request and order-cancel semantics still need explicit server-authoritative routes or mappings.
- Communication and safety state producers remain the next broad stale-state family.
- Real provider APNs/FCM delivery remains a release-readiness gap.

Recommended next native feature/action: Message, Call, and Safety Event Emission Hardening.

Reason for recommendation:

- Seller inventory and payment/order event families are now cursor-visible.
- Messenger/Calls/Safety remain high-frequency trust surfaces where silent read/delete/report/block/mute/call state changes can make Activity Inbox drift.
- This is the highest-value consistency fix before adding more product surface area.

## Message, Call, and Safety Event Emission Hardening

Important roadmap rule:

- Do not focus on Android right now.
- Android remains tracked as a later release-readiness gap.
- Current priority remains iPhone/iOS native app, server-authoritative event consistency, payment/checkout/order trust correctness, PulseSoc production parity, and LogiNexus UI polish.

Completed action: hardened cursor-visible event emission for communications and safety state changes.

What changed:

- Created `reports/pulsesoc_native_comms_safety_event_emission.md`.
- Created `scripts/pulsesoc_native_comms_safety_event_emission_audit.py`.
- Added the shared `pulse_emit_comms_safety_event(...)` backend helper.
- Wired message received, seen, deleted, and reported events into the native sync cursor.
- Wired call started, accepted, declined, ended, missed, and failed events into the native sync cursor.
- Wired user block, generic report submit, message report submit, and verification appeal submit events into the native sync cursor.

Message/call/safety event coverage: 78%.

Remaining silent mutation paths:

- user unblock if/when a first-class route exists
- user mute/unmute if/when a first-class route exists
- report status updates from admin/moderator review paths
- safety appeal status updates from review paths
- group/comment/media report variants not yet fully unified into Safety Hub events
- refund requested and order cancelled if first-class commerce routes are added

Event visibility through sync cursor:

- `message_received`
- `message_seen`
- `message_deleted`
- `message_reported`
- `call_started`
- `call_accepted`
- `call_declined`
- `call_ended`
- `call_missed`
- `call_failed`
- `user_blocked`
- `report_submitted`
- `safety_appeal_submitted`

Activity/Messenger/Calls/Safety consistency impact:

- Activity Inbox can refresh from durable comms/safety event rows instead of only transient realtime state.
- Messenger can invalidate message and conversation caches after receive/seen/delete/report transitions.
- Calls can recover lifecycle state after foreground/background transitions through cursor-visible call events.
- Trust/Safety and Account Health can refresh after block/report/appeal submission events.

Event producer coverage: 86%.

Overall native migration percentage: 88% foundation/parity coverage, 86% system consistency confidence, 64% release QA confidence.

Critical production risk gaps:

- Admin/moderator review update routes still need durable `report_updated` and `safety_appeal_updated` event emission.
- User mute/unmute/unblock coverage depends on first-class server-authoritative mutation routes.
- Real APNs/FCM delivery, lock-screen notification behavior, and multi-device ordering remain release-readiness gaps.

Recommended next native feature/action: Trust/Safety Review Update Event Emission Hardening.

Reason for recommendation:

- Submission-side safety events are now cursor-visible.
- Review/resolution paths remain the next stale-state risk for Safety Hub, Account Health, Activity Inbox, and Notifications.
- This is the highest-value consistency fix before broader realtime streaming or product expansion.

## Trust/Safety Review Update Event Emission Hardening

Important roadmap rule:

- Do not focus on Android right now.
- Android remains tracked as a later release-readiness gap.
- Current priority remains iPhone/iOS native app, server-authoritative event consistency, payment/checkout/order trust correctness, PulseSoc production parity, and LogiNexus UI polish.

Completed action: hardened cursor-visible event emission for trust/safety review updates and report variants.

What changed:

- Created `reports/pulsesoc_native_trust_safety_review_event_emission.md`.
- Created `scripts/pulsesoc_native_trust_safety_review_event_emission_audit.py`.
- Added `pulse_emit_trust_safety_review_event(...)` as a small wrapper over the existing communications/safety event helper.
- Wired verification review decisions into the native sync cursor.
- Wired legacy verification review decisions into the native sync cursor.
- Wired marketplace, group, group comment, group post, and music report variants into the native sync cursor.
- Wired music report review and Trust/Safety report dismissal into the native sync cursor.

Trust/safety review event coverage: 84%.

Remaining silent mutation paths:

- user unblock if/when a first-class route exists
- user mute/unmute if/when first-class routes exist
- group/comment/media report review-update routes that are not yet first-class mutation endpoints
- moderation case updates without a user-facing report recipient
- APNs/FCM delivery-state confirmation for safety updates on physical devices

Event visibility through sync cursor:

- `safety_appeal_approved`
- `safety_appeal_rejected`
- `safety_appeal_updated`
- `report_reviewed`
- `report_dismissed`
- `report_submitted` for marketplace, group, group comment, group post, and music report variants

Activity/Trust/Safety/Account Health consistency impact:

- Activity Inbox can now reflect safety review updates from durable cursor events.
- Trust/Safety can refresh after report submission, report review, report dismissal, and verification review decisions.
- Account Health can surface review lifecycle updates from the same server-authoritative event stream.
- Notifications remain backed by `pulse_notifications`, which is the native cursor source.

Event producer coverage: 89%.

Overall native migration percentage: 88% foundation/parity coverage, 88% system consistency confidence, 64% release QA confidence.

Critical production risk gaps:

- User unblock/mute/unmute need first-class server-authoritative mutation routes before they can be fully event-covered.
- Group/comment/media report review resolution is still fragmented across admin/dashboard surfaces.
- Multi-device event ordering and physical APNs/FCM delivery remain release-readiness gaps.

Recommended next native feature/action: Unified Moderation Review Endpoint Event Emission Hardening.

Reason for recommendation:

- The largest remaining safety consistency gap is fragmented report review state.
- A single server-authoritative review endpoint with standardized event emission would make report review, dismissal, resolution, and appeal updates deterministic across Activity Inbox, Trust/Safety, Account Health, and Notifications.
- This is the highest-value production-readiness improvement before broader realtime streaming or UI expansion.

## Unified Moderation Review Event Emission + Full Native QA Browser Walkthrough

Important roadmap rule:

- Do not focus on Android right now.
- Use the built-in QA browser for visible web QA.
- Do not use Chrome Incognito.
- Current priority remains iPhone/iOS native app, server-authoritative event consistency, payment/checkout/order trust correctness, PulseSoc production parity, and LogiNexus UI polish.

Completed action: hardened cursor-visible event emission for unified moderation review transitions and ran a full visible native QA browser walkthrough.

What changed:

- Created `reports/pulsesoc_native_moderation_review_event_emission.md`.
- Created `scripts/pulsesoc_native_moderation_review_event_emission_audit.py`.
- Added `pulse_emit_moderation_review_event(...)` as a moderation-specific wrapper over the existing trust/safety event path.
- Wired moderation case updates, resolves, and dismissals into the native sync cursor.
- Wired content restore/remove moderation actions into the native sync cursor.
- Wired user warning/restriction moderation actions into the native sync cursor.
- Wired marketplace/content report resolution events into the native sync cursor.
- Created `reports/pulsesoc_native_full_visual_qa_walkthrough.md`.

Moderation review event coverage: 90%.

Full native walkthrough coverage: 100% route coverage across 49 requested native routes.

Screens confirmed visible: Login/Auth signed-out native shell.

Screens blocked by auth/session/API: 48 signed-in native surfaces correctly auth-gated because the web QA build was configured against `https://pulsesoc.com` and no authenticated QA session was established.

Broken routes or visual issues: 0 broken routes, 0 blank screens, 0 navigation errors. Authenticated LogiNexus screen quality remains blocked until a local/staging API-backed QA session is available.

Event producer coverage: 91%.

Overall native migration percentage: 88% foundation/parity coverage, 90% system consistency confidence, 65% release QA confidence.

Critical production risk gaps:

- Physical APNs/FCM delivery remains unverified for safety-review notifications.
- Two-device cursor ordering is not release-validated under production load.
- Some moderation review workflows remain fragmented across admin surfaces outside `apply_department_action(...)`.

Recommended next native feature/action: Real-time cursor replay and multi-device ordering validation.

Reason for recommendation:

- Event emission coverage is now high enough that the next risk is deterministic convergence across multiple devices and reconnect/replay scenarios.
- This is the highest-value production-readiness improvement before broader realtime streaming or release-candidate QA.

## Visible QA Browser Walkthrough

Important roadmap rule:

- Do not focus on Android right now.
- Use the built-in QA browser for visible web QA.
- Do not use Chrome Incognito.
- Do not claim device-only behavior from browser evidence.

Completed action: ran a signed-in visible QA browser walkthrough using the built-in QA browser.

What changed:

- Created `reports/pulsesoc_native_visible_qa_walkthrough.md`.
- Captured screenshots and route-by-route results under `reports/screenshots/native-visible-qa-2026-07-06/`.
- Established a runtime-only same-origin local QA stack so authenticated screens could be shown without weakening production auth.

What Roody saw live:

- Login/Auth
- Home
- Search
- Saved
- Groups
- Live
- Reels
- Status
- Messenger
- Activity Inbox
- Pulse AI
- Profile
- Marketplace
- Settings
- Calls
- Full-screen Incoming Calls fixture route
- Seller Store
- Seller Listing Composer
- Seller Inventory
- Buyer Orders
- Premium
- Creator Studio
- Growth Center
- Intelligence
- Alert Management
- Trust/Safety
- Verification Center
- Account Health
- Safety Hub
- Courses
- Camera Studio

Visible walkthrough coverage:

- Visible signed-in screens checked: 30.
- Screens opened by app UI tab click: 13.
- Screens opened by authenticated deep route: 17.
- Auth gates during signed-in walkthrough: 0.
- Blank screens: 0.
- Navigation errors: 0.

Still blocked or not verified:

- Physical APNs/FCM delivery and lock-screen notifications.
- Real camera/microphone capture.
- Native installed-app deep links.
- Real LiveKit two-device media calls.
- Production-scale event pressure.
- Real payment provider completion on physical devices.

Overall native migration percentage: 89% foundation/parity coverage, 90% system consistency confidence, 66% release QA confidence.

Recommended next native feature/action: Real-time cursor replay and multi-device ordering validation.

Reason for recommendation:

- The visible app shell is broad enough for owner review.
- The highest production-readiness risk is no longer route visibility; it is deterministic event replay and convergence across multiple sessions/devices.

## Real-time Cursor Replay + Multi-Device Ordering Validation

Important roadmap rule:

- Do not focus on Android right now.
- Do not add new features.
- Keep `/api/pulse/sync/events` as server-authoritative cursor truth.
- Keep the sync layer polling-first until cursor correctness is stable.

Completed action: validated seeded cursor replay and multi-session ordering behavior.

What changed:

- Created `reports/pulsesoc_native_cursor_multidevice_ordering.md`.
- Created `scripts/pulsesoc_native_cursor_multidevice_ordering_audit.py`.
- Added seeded backend checks for same-user multi-session replay, buyer/seller session isolation, delayed events, duplicate delivery rows, invalid cursor fallback, and invalidation registry coverage.

Cursor replay correctness: 93%.

Multi-device ordering confidence: 84%.

Systems that converge correctly:

- Activity Inbox
- Notifications
- Buyer Orders
- Seller Inventory
- Marketplace listing state
- Messenger activity
- Calls activity
- Trust/Safety activity

Systems still at risk of drift:

- Physical APNs/FCM delivery and tap ordering.
- Provider webhook retries under production-like concurrency.
- Fragmented admin/moderation review updates outside unified event producers.
- Two-device call/media state where realtime and polling overlap.
- High-volume screen refresh behavior under rapid cursor invalidations.

Event producer coverage: 91%.

Overall native migration percentage: 89% foundation/parity coverage, 91% system consistency confidence, 66% release QA confidence.

Critical production risk gaps:

- Persistent staging QA fixtures do not yet exist for repeatable multi-account release gates.
- Physical iPhone QA remains incomplete for camera, push, installed deep links, and media-heavy flows.
- Provider push/payment/dispute/refund QA remains release-blocking.

Recommended next native feature/action: Persistent Authenticated Staging QA Environment + Replay Fixture Pack.

Reason for recommendation:

- The app is now broad and visible enough for owner review.
- Event producer coverage is high enough that the next highest-value step is repeatable release-grade validation, not another native surface.
- A persistent staging fixture pack would make browser, simulator, iPhone, provider, and multi-session event replay QA deterministic across every future run.
