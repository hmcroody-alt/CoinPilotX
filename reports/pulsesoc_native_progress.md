# PulseSoc Native Migration Progress

Date: 2026-07-18

## Latest Mission Status: Native WebView Replacement Readiness Audit

- Date: 2026-07-19.
- Scope: full static release-readiness check for replacing the production WebView app with the native PulseSoc app under the rule that no user flow should redirect to web.
- Result: **NO-GO** for a once-and-for-all WebView replacement today.
- Added `scripts/pulsesoc_native_webview_replacement_audit.py`.
- Added `reports/pulsesoc_native_webview_replacement_readiness.md`.
- Added machine-readable evidence at `reports/pulsesoc_native_webview_replacement_readiness.json`.
- Audit found 96 native routes and 45 native screen files, but only 13 of 26 critical surface groups pass the strict native-only static gate.
- Audit found no mounted `react-native-webview` component in `mobile-native/src`; the blockers are remaining URL exits and fallback policies.
- Hard web-exit/fallback findings: 63.
- Main blocker families: dashboard safe-web fallback routing, notification web target opening, profile web fallback buttons, marketplace/seller URL exits, camera advanced fallback, live fallback copy, search fallback copy, course/event fallback copy, and API helpers whose only behavior is `Linking.openURL`.

Next highest-value mission:

- Native Web Redirect Elimination Phase 1: navigation, dashboard, notification, profile, and search.

Reason:

- These are shared escape hatches that can redirect users out of native from many places. Fixing route policy first creates the enforcement gate required before commerce, creator, live, education, and provider-heavy flows can become native-only.

## Latest Mission Status: Native Voice Message Compact Bubble

- Replaced the nested native `VOICE PULSE` media card with one compact horizontal player inside the canonical incoming/outgoing message bubble.
- Removed the redundant heading, security subtitle, duplicate current/total time row, and all technical filename/path/URL fallbacks.
- Expanded legacy normalization to reuse attachment duration, waveform, playback URL, and attachment ID without changing production records.
- Added one shared voice playback coordinator with play, pause, replay, seek, `1x`/`1.5x`/`2x`, localized 250 ms progress updates, retry, Pulse Radio coordination, background cleanup, row cleanup, and call-entry cleanup.
- Preserved Communications V2 upload/send contracts, message IDs, attachment IDs, WebView player markup, and backend serialization.
- Release builds passed for iPhone Simulator and arm64 iPhone; the side-by-side `PulseSoc Native Dev` build was installed and launched on the connected iPhone 16 Pro under `com.pulsesoc.nativeapp.dev`.
- Internal-beta gate remains open for hands-on audible playback, cross-client playback, Bluetooth/headset/interruption, authenticated simulator visual, and measured performance checks.
- Evidence: `reports/pulsesoc_native_voice_message_compact_redesign_2026-07-18.md`.

## Latest Mission Status: Native Messenger Voice Attachment MIME Repair

- Date: 2026-07-18.
- Scope: Pulse Command voice-message attachment failure reported from the native conversation screen.
- Root cause: iOS voice recordings can arrive through multipart as `audio/x-m4a`, `audio/m4a`, `audio/mp4a-latm`, or `application/octet-stream` while the durable Messenger attachment was initialized as `audio/mp4`; the backend foundation required exact MIME equality and rejected the upload before completion.
- Native fix: `mobile-native/src/api/messenger.ts` now canonicalizes iOS voice MIME aliases to `audio/mp4` before `/api/messages/media/init`.
- Backend fix: `services/messenger_media_foundation.py` now normalizes M4A aliases and allows multipart octet-stream to inherit the already initialized, server-supported attachment MIME.
- Existing Messenger foundation preserved: `/api/messages/media/init`, `/api/messages/media/upload`, `/api/messages/media/complete`, and Communications V2 `attachment_ids` delivery remain the only path.
- Added regression coverage to `scripts/pulsesoc_native_voice_message_audit.py`.
- Report: `reports/pulsesoc_native_messenger_voice_attachment_mime_fix_2026-07-18.md`.

## Latest Mission Status: Native Home Generated Concept Visual Reconstruction

- Date: 2026-07-18.
- Scope: owner-requested native Home visual reconstruction using the generated image as direct visual inspiration while preserving existing production Home wiring.
- Reused existing `HomeScreen`, `HomePulseComposer`, `LogiNexusGlobalHeader`, and `LogiNexusBottomNavigation`; no `HomeV2`, duplicate composer, duplicate feed, duplicate status rail, or duplicate navigation implementation was created.
- Tightened the authenticated Home first viewport to match the generated concept's visual economy: centered PulseSoc command strip, compact glass Pulse Network hero, static atmospheric network art, compact Status rail, compact Create a signal composer, visible feed-filter rail, and floating bottom dock.
- Preserved backend/business wiring for feed loading, status loading, composer publishing, media upload, Pulse Radio, UNDX, Safety, Live, drawer, and bottom-tab route dispatch.
- Performance decision: background depth is code-native static atmosphere rather than animated image loops, avoiding render-loop cost while retaining the intended futuristic visual mood.
- Xcode iPhone Simulator Release build/install/launch passed on `PulseSoc iPhone 16 Pro`.
- Final authenticated simulator evidence: `reports/screenshots/native-home-generated-concept/iphone16pro-native-concept-final-home.png`.

Remaining Home caveat:

- This was a generated-concept visual pass, not a replacement for full production-WebView parity. Account-specific live data can still change label wrapping and status density.

## Latest Mission Status: Approved Native Home Reference

- Reworked the existing native Home hierarchy to the approved compact reference without creating parallel feed, Status, Composer, radio, identity, or navigation systems.
- Preserved production APIs/routes and removed fabricated hero fallbacks; network state is now truthful (`Connected`/`Cached`) and empty metrics use an em dash.
- Added performance-safe native-driver hero ambience gated by route focus, app foreground state, Reduce Motion, and Low Power Mode.
- Isolated Pulse Radio rendering, kept playback paused by default, and retained explicit user-start behavior.
- Compacted hero metrics/system cards and the canonical collapsed Composer; `DRAFT` now appears only for a real draft.
- Replaced approximate navigation glyphs with native iconography and brief press haptics while retaining Home, Reels, Create, Messages, Profile order.
- Kept Home visible during feed loading so a correct first-content loading/empty/error state appears immediately below filters.
- Xcode Release simulator build and iPhone 16 Pro runtime passed; live inspection drove a second density correction so the filter rail now remains visible in the first viewport.
- The production-API Release artifact was signed, installed, and launched on the physical iPhone 16 Pro as `PulseSoc Native Dev` / `com.pulsesoc.nativeapp.dev`; its process remained alive after launch.
- TypeScript, both Home audits, Expo Doctor 17/17, Release simulator/device builds, and `git diff --check` passed.
- Evidence and matrix: `reports/pulsesoc_native_home_approved_reference_2026-07-18.md`.

Remaining gates are owner visual approval, real-account interaction checks, authenticated compact/Pro Max Home evidence, runtime accessibility passes, and profiler measurements.

## Latest Mission Status: Native Call-State P0 and Compact Messenger Composer

- Traced the phantom `ACTIVE PULSESOC CALL — Vilson` banner through the globally mounted `IncomingCallLayer`, `/api/calls/active`, canonical participant serialization, and stale non-terminal backend rows.
- Removed the active-call mini-controller branch entirely while retaining full-screen incoming ringing and the dedicated Call route.
- Added server-authoritative stale-call expiry and participant-status validation; isolated behavior QA proves stale connected calls become terminal and cannot reappear as active.
- Corrected native call creation to the production Communications V2 voice/video start routes and replaced the global upload-specific 404 misclassification.
- Compacted the Messenger header, Call screen, and `PULSE LINK` composer; multiline input is bounded and the software keyboard no longer leaves a dead panel gap.
- TypeScript, Expo Doctor 17/17, call audits, stale-state behavior audit, Debug simulator runtime, and Release simulator build passed.
- Built, signed, installed, and launched the production-API Release artifact on the USB-connected iPhone 16 Pro as `PulseSoc Native Dev` / `com.pulsesoc.nativeapp.dev`. The production `com.pulsesoc.app` identity was never targeted.
- Evidence: `reports/pulsesoc_native_call_p0_root_cause_2026-07-18.md` and `reports/screenshots/native-call-p0-2026-07-18/`.

Remaining release gate: real two-account audio/video calls, Bluetooth/headset routing, background recovery, native-to-WebView interoperability, and measured LiveKit quality remain unverified. Calls are not approved for internal beta until those physical tests pass.

## Latest Mission Status: Native Auth Immediate Logout Fix

- Date: 2026-07-17.
- Scope: native existing-account session continuity.
- Owner symptom: native login immediately returned to signed out.

Completed action:

- Native API requests now send the existing secure mobile access token as `Authorization: Bearer ...` when it is still valid.
- Native API requests now proactively refresh missing or near-expiry access tokens through the existing single-flight refresh path before protected calls.
- Backend `account_user_id()` now accepts valid mobile Bearer access tokens before falling back to persistent-cookie refresh.
- Refresh-token recovery, cookie compatibility, WebView sessions, logout, and logout-all behavior remain intact.
- Added `scripts/pulsesoc_native_auth_immediate_logout_audit.py`.
- Added `reports/pulsesoc_native_auth_immediate_logout_fix_2026-07-17.md`.

Verification:

- `venv/bin/python scripts/pulsesoc_native_auth_immediate_logout_audit.py`
- `npm run --prefix mobile-native typecheck`
- `python3 -m py_compile bot.py`
- `git diff --check`

Remaining release QA:

- Owner must sign into the installed native app with private credentials and confirm it stays signed in.
- Physical iPhone force-quit/relaunch session restoration remains the final proof.

## Latest Mission Status: Native Chat State Correction and Global Call Popup Removal

- Date: 2026-07-16.
- Scope: Pulse Command conversation state hierarchy plus global active-call mini-popup removal.
- Home status: frozen exact parity preserved.
- Call functionality: preserved.

Completed action:

- Removed the rejected active voice/video call mini popup globally from `IncomingCallLayer`; the visual caller-name, `Voice in progress` / `Video in progress`, and `End` mini-controller branch no longer mounts on any route.
- Kept active call state, active-call polling, QA call fixture handling, incoming full-screen call handling, canonical Call route navigation, and call APIs intact.
- Replaced the native Chat contradictory loading/error/empty rendering with mutually exclusive initial-loading, cached-history reconnecting, fatal-error, and successful-empty states.
- Added `scripts/pulsesoc_native_home_call_overlay_audit.py`.
- Added `scripts/pulsesoc_native_chat_parity_overlay_audit.py`.
- Added `reports/pulsesoc_native_home_call_overlay_removal.md`.
- Added `reports/pulsesoc_native_chat_conversation_parity_overlay.md`.

Simulator evidence:

- `/Users/hmcherie/Desktop/CoinPilotX/reports/screenshots/native-chat-popup-removal/current-simulator-state.png`
- `/Users/hmcherie/Desktop/CoinPilotX/reports/screenshots/native-chat-popup-removal/native-app-after-reopen.png`

Remaining release QA:

- Physical-device audio routing, Bluetooth, lock-screen push behavior, background call audio, and app-killed call behavior remain physical-device-only.
- Controlled account QA remains required to prove the exact WebView conversation history appears in native for the same canonical conversation ID.

## Latest Mission Status: Native Profile V2 Living Identity

- Date: 2026-07-15.
- Profile is the active subsystem and remains gated on owner visual review before another mission begins.
- The supplied Profile/customization/theme/layout/stats/media/music mockups informed hierarchy and atmosphere only; the app uses canonical PulseSoc data and existing native tokens.
- Native public profiles now load from a canonical JSON adapter over the existing identity, posts, follow graph, and theme records instead of being inferred from the first feed post.
- Living Profile motion respects system Reduce Motion, and customization persists through the existing Premium profile-theme contract with additive layout/module/motion columns.
- Public Message opens the canonical Messenger direct conversation; Follow uses the existing follow-toggle contract.
- Xcode iPhone 16 Pro UI test passed with 1 test and 0 failures. Visual geometry also passed on compact and iPhone 16 Pro Max simulators.
- Evidence: `reports/pulsesoc_native_profile_progress.md` and `reports/screenshots/native-profile-v2-2026-07-15/`.
- The signed standalone Release development build passed installation on the physical iPhone 16 Pro as `PulseSoc Native Dev` / `com.pulsesoc.nativeapp.dev`, with an embedded JavaScript bundle and production API configuration. The installer did not target the production WebView identity.
- Automated launch was refused because the phone was locked; final unlocked launch, side-by-side visual confirmation, and controlled real-account Profile actions remain the device gates.

Recommended next action: Reconnect and unlock the physical iPhone 16 Pro, launch the installed side-by-side development app, complete controlled Profile smoke checks, and stop for owner inspection.

Reason: the simulator implementation is visually and structurally verified, but physical media, keyboard, network, Message, and Follow behavior must be observed before Profile can freeze.

## Latest Mission Status: Native Authentication, Session Refresh, and Reels Recovery

- Date: 2026-07-14.
- Authentication and Reels recovery are the active release gate; Home, Messenger New Chat, and Profile V2 remain sequenced behind it.
- Native login continues to use the canonical production mobile authentication routes and production `user_id`; no native identity database or mirrored profile was introduced.
- Production login responses now persist the rotating access/refresh credential set as one device-bound iOS Keychain envelope, isolated between development and release services.
- Concurrent authenticated 401 responses now share one refresh operation, rotate credentials atomically, verify the same canonical user ID, and replay each request once.
- Temporary refresh/backend failures preserve secure credentials and render connectivity recovery; only a proven invalid session clears credentials and opens real Sign In.
- Reels no longer classifies every 403 as session expiration, and Sign In recovery preserves `/pulse/reels` for post-auth restoration.
- Xcode iPhone 17 Pro simulator build/runtime: passed. The locally installed Xcode runtime does not include an iPhone 16 Pro simulator profile.
- Earlier signed physical iPhone 16 Pro build, install, and launch: passed using `com.pulsesoc.nativeapp.dev` / `PulseSoc Native Dev`.
- Side-by-side protection: passed; production `com.pulsesoc.app` and development `com.pulsesoc.nativeapp.dev` are both installed.
- Follow-up physical diagnosis found that a later Debug install had overwritten the standalone development artifact and depended on Mac-only Metro, explaining the owner's blank/nonworking launch.
- Xcode iPhone 17 Simulator now passes a clean signed-out authentication UI test covering login, recovery, email verification, and return-to-login with 0 failures; a separate Release artifact launches the login screen with Metro stopped.
- The iPhone development copy has been replaced with a signed Release artifact using the development identity, production API, no QA fixture flags, and an embedded JavaScript bundle. Production remains untouched.
- Added `scripts/install_pulsesoc_native_dev_iphone.sh` to make the safe standalone side-by-side installation repeatable and to refuse production identity or missing-bundle artifacts.
- Existing-user physical login, force-quit restoration, and post-refresh Reels proof remain pending the owner entering existing credentials directly on the iPhone. No credential was requested or captured by Codex.
- Mission report: `reports/pulsesoc_native_auth_session_reels_recovery_2026-07-14.md`.

Recommended next mission: Complete the physical existing-account login/relaunch/Reels evidence, then execute Messenger New Chat recovery.

Reason for recommendation:

- The authentication implementation and build/install gates pass, but the release-blocking physical account-continuity proof requires the account owner’s private credential entry.
- Messenger New Chat depends on this canonical shared session and must not proceed while physical authentication remains unproven.

## Mandatory Native Mission QA Standard

- The Xcode iPhone Simulator is now the primary QA visibility and visual-parity environment throughout every native mission, not an end-only check.
- The current production WebView PulseSoc application remains the authoritative UI, feature, workflow, backend, and business-logic source; the native app remains a parallel implementation until full release readiness.
- Every mission must follow `docs/pulsesoc_native_mission_standard.md` and complete `reports/pulsesoc_native_mission_report_template.md`.
- The locally available representative coverage matrix is iPhone 17e, iPhone 17, iPhone 17 Pro, and iPhone 17 Pro Max on iOS 26.5; each mission must rediscover available devices before QA.
- Evidence belongs in a dedicated `reports/screenshots/<mission-slug>/` directory, with exact paths recorded in the mission report.
- Every check must be honestly classified as Simulator verified, Code-path verified, Mock-state verified, or Physical-device-only. Non-applicable required states need an explicit reason.
- `scripts/pulsesoc_native_mission_standard_audit.py` guards the permanent policy and report template. It does not substitute for opening and inspecting the actual simulator states.

## Latest Mission Status: Pulse Command Exact Production UI Parity

- Current native migration: 96%.
- Pulse Command production layout parity: 89%.
- Pulse Command production visual parity: 84%.
- Pulse Command feature parity: 88%.
- Pulse Command interaction parity: 84%.
- Xcode Simulator QA: 72%; all four device-size classes are now covered. Nested context-menu and attachment-sheet evidence passed; keyboard and reconnect reconciliation remain incomplete.
- Device-size simulator coverage: 100% after sequential CoreSimulator recovery.
- Latest report: `reports/pulsesoc_native_pulse_command_device_nested_parity_2026-07-12.md`.
- Release QA confidence: 90%.

Strategic correction:

- Home is frozen for exact parity and should not be reopened unless a regression, accessibility issue, performance issue, or owner-requested change appears.
- Pulse Command / Messenger is now the active exact-parity subsystem.
- The current production Messenger UI is the visual, structural, and workflow authority.

Completed action:

- Inspected the live production WebView Messenger V3 and corrected the native Messenger hierarchy to All/Direct/Groups/Rooms/AI/Unread, production search copy, three production quick actions, and recent conversations.
- Removed the divergent Messenger metric cards and active-user rail while preserving server-authoritative conversation/search/cache/navigation behavior.
- Removed internal LogiNexus branding from the visible global native header badge/default subtitle.
- Built the iOS native app successfully, opened the corrected populated screen in the iPhone 17 Pro simulator, fixed the filter overflow found there, and captured exact evidence under `reports/screenshots/native-pulse-command-production-parity-2026-07-12/`.
- Recorded remaining device/state gaps honestly in `reports/pulsesoc_native_pulse_command_exact_parity_2026-07-12.md`.

- Created the Pulse Command exact-parity inventory, layout parity, visual parity, interaction parity, code reuse, and simulator QA reports.
- Added `scripts/pulsesoc_native_pulse_command_exact_parity_audit.py`.
- Reused the existing `MessengerScreen`, `ChatScreen`, `PulseCommand` primitives, `pulseCommand/domain.ts`, and Messenger API wrappers.
- Tightened shared Pulse Command primitives to production Messenger density: 48px avatars, shorter search/tabs, and denser panels.
- Tightened native Messenger conversation rows toward production CSS values from `static/css/pulse_messages_v2.css`.
- Replaced non-production Messenger copy such as "transmissions" and "secure nexus" with production-recognizable messaging copy.
- Tightened native chat bubbles and composer geometry toward production Messenger values while preserving the existing send/upload/retry/offline pipelines.

Remaining Pulse Command exact parity work:

- Fresh authenticated Xcode iPhone Simulator side-by-side captures.
- Conversation header exact sizing.
- Date separators, unread divider, context menu, copy/forward/edit/message details.
- Attachment sheet, document/download, gallery, and voice-message depth.
- Calls tab exact row/filter parity.
- Groups and Rooms detail exact production visual parity.
- Offline/reconnect disruption QA.

Recommended next mission: Continue Pulse Command exact production UI parity.

Reason for recommendation:

- Pulse Command is not frozen and should not move to another subsystem until production layout, visual, interaction, and simulator evidence thresholds are met.

## Latest Mission Status: Native Home Exact Production UI Parity

- Current native migration: 96%.
- Home production layout parity: 95%.
- Home production visual parity: 92%.
- Home feature parity: 97%.
- Home interaction parity: 95%.
- Xcode Simulator QA: 95% code/evidence readiness; local QA proxy health restored at `127.0.0.1:5108`.
- Release QA confidence: 90%.

Strategic correction:

- The current production PulseSoc UI is now the visual, structural, and workflow authority.
- Native Home must look and behave like the existing production Homefeed, not a separate LogiNexus redesign.
- LogiNexus work is now treated as the existing production visual language/tokens, not a license to invent alternate layouts.

Completed action:

- Created the Home exact parity inventory from production CSS/source and current native implementation.
- Added a production UI token map translating production CSS values into native token targets.
- Reused the existing `HomeScreen`, `HomePulseComposer`, `PostCard`, global header, drawer, bottom navigation, feed/status/composer APIs, and media viewer.
- Removed non-production Home subtitle text from the native Home command strip.
- Restored production-facing Status rail copy: `Status`, `No Status yet.`, `Create one.`
- Extended the existing native `HomePulseComposer` in place with the production `Pulse Composer` title and visible production mode rail entries: Post, Reel, Live, Marketplace, Music, Poll, Question, More.
- Kept production controls discoverable without duplicating backend logic; unsupported advanced modes are explicit native boundaries.
- Refined the existing `PostCard` in place with production-shaped header controls, Like/Comment/Repost/Share/Save action order, social context, overflow safety actions, and inline comment composer.
- Reused `addPostComment` for Home inline comments and reconciled card preview/comment counts from the server response.
- Added `scripts/pulsesoc_native_home_exact_parity_audit.py`.

Remaining Home exact parity work:

- Fresh Xcode iPhone Simulator side-by-side captures against the current production WebView Home.
- Fresh iPhone Simulator capture after the feed-card and inline-comment pass.
- Tighten hero proportions, right rail dimensions, compact feed-card spacing, and wide side-rail proportions.
- Continue exact parity before moving to the next page.
- Final visual-size pass completed: hero proportions, side rails, feed-card density, inline comment density, feed-filter density, and composer mode rail sizing were tightened in existing components.
- Local QA proxy `127.0.0.1:5108` was listening but returned empty replies while targeting a healthy `127.0.0.1:5107`; the stale proxy process was restarted and `/health` now returns 200 through `5108`.
- Home is now frozen for exact parity except regression, accessibility, performance, release-device QA, or owner-requested changes.

Recommended next mission: Pulse Command / Messenger exact production UI parity.

Reason for recommendation:

- The structural parity map and production-token audit now exist.
- The next highest-value work is visual/proportional refinement against fresh simulator evidence, not another conceptual redesign.

## Latest Mission Status: Pulse Command Calls Foundation

- Current native migration: 96%.
- Overall LogiNexus transformation: 19%.
- Pulse Command transformation: 64%.
- Calls transformation: 68%.
- Release QA confidence: 88%.

Completed action:

- Continued inside Pulse Command instead of moving to Search / Discover.
- Mapped the production WebView Messenger, backend/services, and native API/screen sources into a formal reuse report.
- Added native rebuild boundaries that require reuse of server-authoritative contracts while rebuilding UI natively.
- Added a reuse audit that guards against duplicate native surfaces and missing API wrapper reuse.
- Reused the existing `CallScreen`, `useNativeCallRoom`, and call APIs.
- Migrated Calls onto shared Pulse Command and LogiNexus layout primitives.
- Added native readiness metrics for backend state, LiveKit token state, media runtime, and participant count.
- Preserved server-authoritative call start, accept, decline, hangup, controls, events, and safe web fallback.
- Added a dedicated calls report and updated the Pulse Command completion audit to prevent regression to one-off call chrome.

Remaining Pulse Command work:

- Extract shared TypeScript domain utilities for message previews, labels, timestamps, presence, delivery state, and action availability.
- Focused Xcode Simulator capture for Calls after this pass.
- Complete Groups / Rooms detail depth, member states, provider boundaries, and simulator evidence.
- Complete offline/reconnect disruption QA and large-text/reduced-motion accessibility pass.
- Physical-device-only call checks remain for microphone, camera, Bluetooth, push ringing, lock-screen presentation, and background audio.

Recommended next mission: PulseSoc Pulse Command shared domain utility extraction.

Reason for recommendation:

- The WebView reuse map shows several proven behavior rules embedded in DOM-heavy JavaScript.
- Extracting portable message/conversation/domain rules first prevents duplicate logic before deeper Groups, Rooms, and interaction work continues.

## Latest Mission Status: LogiNexus Phase 2 Simulator QA Foundation

- Current native migration: 96%.
- Overall LogiNexus transformation: 15%.
- Simulator QA foundation: 90%.
- Release QA confidence: 88%.

Completed action:

- Repaired the native simulator QA authentication path before continuing broader Phase 2 navigation work.
- Added an explicit `EXPO_PUBLIC_PULSESOC_QA_AUTO_LOGIN=1` development-only and local-API-only simulator bootstrap.
- Reused the existing `/api/mobile/auth/register` endpoint and native session cookie storage instead of adding a parallel auth path.
- Added QA-addressable Login selectors and Login-screen QA deep-link handling to reduce simulator timing fragility.
- Preserved production authentication, production WebView routes, and backend business logic.

Remaining Phase 2 blocker:

- Continue the Global Navigation LogiNexus foundation now that Xcode iPhone Simulator authenticated Home routing has been confirmed.

Simulator evidence:

- `reports/screenshots/logi-nexus-phase2-simulator-auth-home.png`
- `reports/screenshots/logi-nexus-phase2-simulator-auth-home-routed.png`

Recommended next mission: PulseSoc Native Global Navigation LogiNexus Foundation continuation.

Reason for recommendation:

- Simulator QA must be reliable before judging top bars, bottom navigation, drawer behavior, route state, badge layout, and shared screen spacing.
- Once authenticated simulator Home is stable, global navigation is still the highest-leverage shared subsystem for the full LogiNexus transformation.

## Latest Mission Status: LogiNexus Global Navigation Continuation

- Current native migration: 96%.
- Overall LogiNexus transformation: 16%.
- Navigation foundation: 88%.
- Shared badge/identity consistency: 84%.
- Release QA confidence: 88%.

Completed action:

- Routed Home's shared command strip through the same `GlobalNavigationBadges` and `GlobalNavigationIdentity` state used by stack and tab headers.
- Preserved Home's current production layout and local top-bar ownership while eliminating the prior data-disconnected header gap.
- Updated the global navigation audit and reports to require Home badge/identity wiring.

Remaining navigation work:

- Physical iPhone safe-area, Dynamic Island, push-tap clearing, and background badge refresh remain release-device/provider QA.
- Some nested subsystem screens still carry local in-screen title treatments; those should be handled during each subsystem LogiNexus transformation pass.

Recommended next mission: PulseSoc Native Shared Motion System Foundation.

Reason for recommendation:

- The shared navigation foundation now has stack, tab, drawer, Home, identity, and badge state coverage.
- The next weakest shared layer is motion consistency: drawer, page transitions, composer, cards, tab selection, loading, success, failure, and reduced-motion behavior need one reusable system before deeper screen transformations.

## Latest Mission Status: LogiNexus Shared Motion System Foundation

- Current native migration: 96%.
- Overall LogiNexus transformation: 17%.
- Shared motion system: 38%.
- Accessibility motion readiness: 44%.
- Release QA confidence: 88%.

Completed action:

- Added `mobile-native/src/theme/logiNexusMotion.ts` as the shared native motion utility.
- Centralized standard easing, ambient pulse sequencing, and reduced-motion preference detection.
- Migrated Dashboard energy-ring motion to the shared ambient pulse helper.
- Migrated incoming-call and floating-call pulse motion to the shared ambient pulse helper.
- Preserved existing screen behavior, backend contracts, and route architecture.

Remaining motion work:

- Add shared reveal, press, success, failure, list-loading, drawer, bottom-sheet, and page-transition helpers.
- Migrate remaining one-off animation and interaction motion during subsystem transformations.

Recommended next mission: PulseSoc Native Shared Screen Layout System Foundation.

Reason for recommendation:

- Navigation and initial motion now have shared primitives.
- The next weakest shared foundation is screen layout consistency: safe areas, scroll containers, empty/error/offline sections, section headers, and responsive spacing need one reusable layer before Messenger/Profile/Reels transformations.

## Latest Mission Status: LogiNexus Shared Screen Layout System Foundation

- Current native migration: 96%.
- Overall LogiNexus transformation: 18%.
- Shared screen layout system: 46%.
- Shared screen shells: 52%.
- Responsive layout engine: 34%.
- Safe area handling: 66%.
- Release QA confidence: 88%.

Completed action:

- Evolved the existing `Screen` component into the authoritative shared layout module.
- Added shared shell, scroll, section, state panel, and responsive column primitives.
- Migrated representative Dashboard, Messenger, Profile, and Post Detail loading/error/empty states to the shared layout layer.
- Preserved production routes, backend contracts, feature behavior, and WebView compatibility.

Remaining layout work:

- Continue migrating remaining screens during subsystem transformations instead of doing a broad risky refactor.
- Add tablet split-pane and deeper keyboard-aware primitives when real screens require them.

Recommended next mission: PulseSoc Native Messenger / Pulse Command LogiNexus Transformation.

Reason for recommendation:

- Simulator QA, global navigation, shared motion, and shared screen layout foundations now exist.
- Messenger is the highest-value daily-engagement subsystem and can now inherit the shared navigation, motion, and layout systems instead of developing one-off structure.

## Latest Mission Status: LogiNexus Home Evolution

- Production layout parity: 98%.
- Feature parity: 97%.
- LogiNexus visual evolution: 88%.
- UI quality: 89%.
- UX quality: 91%.
- Typography: 86%.
- Motion: 72%.
- Spacing: 90%.
- Visual consistency: 87%.
- Accessibility: 84%.
- Performance confidence: 88%.
- Xcode Simulator QA: partially complete; iPhone 17 Pro local bundle loads, but fresh authenticated Home capture is blocked by QA login automation.
- Can existing PulseSoc users transition to this Home without confusion: YES.

Completed this mission:

- Kept the current production Home architecture intact: Global Header, Pulse Network Hero, Status Rail, Pulse Composer, Feed Categories, Feed, and mobile Bottom Navigation.
- Added a low-cost native atmosphere layer so Home no longer sits on a flat dark background.
- Added a wide-only native left command rail to match the current production WebView desktop layout while keeping iPhone Home unchanged.
- Preserved the center feed and right intelligence rail model on wide surfaces.
- Expanded the native Home canvas so left rail, center feed, and right rail can breathe without squeezing the primary feed.
- Preserved all existing Home workflows and backend contracts.
- Added scoped Home evolution, UI/UX, visual convergence, and motion reports.
- Added `scripts/pulsesoc_logi_nexus_home_evolution_audit.py`.
- Tightened compact iPhone hero density with a small-screen metric row instead of the wider telemetry map.
- Tightened the existing `HomePulseComposer` sizing in place; no duplicate composer or Home screen was created.
- Added a dev-safe dynamic Expo config bridge for local QA API bundles.
- Hardened dev-only simulator QA auth parsing and local `api_base` support.

Production layout changes:

- None.

Remaining Home work:

- Repair Xcode Simulator authenticated QA automation so fresh Home screenshots can be captured without manual form entry.
- Xcode iPhone Simulator visual captures for iPhone 17 Pro, iPhone 17 Pro Max, and compact iPhone after authenticated QA automation is reliable.
- Final motion polish after the rest of the native foundation is stable.
- Physical iPhone release QA for haptics, push/tap routing, background recovery, camera/media capture, and performance feel.

ONE next autonomous mission:

Continue shared global navigation and Messenger foundation after this Home evolution pass is verified and pushed.

## Latest Mission Status: LogiNexus Homefeed Native Reconstruction / WebView Homefeed Layout Parity Pass

- Home LogiNexus transformation: 82%.
- Home UX completeness: 91%.
- Native foundation/parity: 97%.
- System consistency: 90%.
- Release QA confidence: 87%.
- Can Homefeed be considered LogiNexus UI/UX complete: NO.

Completed this mission:

- Treated the generated Homefeed image as inspiration only, not a static implementation target.
- Reworked the native Home hero into a compact Pulse Network panel driven by real feed/status data.
- Preserved existing Home publishing, draft recovery, upload queue, feed refresh, event invalidation, Status rail, feed interaction, and media handoff contracts.
- Removed concept-only fake metrics/copy from native source.
- Added Home reconstruction reports and a scoped audit script.
- Used Xcode iPhone Simulator as the primary QA target for this milestone.
- Fixed the Metro runtime resolver gap for Expo Notifications' `@ide/backoff` dependency and verified the redbox no longer blocks simulator QA.
- Captured authenticated iPhone 17 Pro Simulator evidence for the reconstructed Home surface.
- Added a follow-up iPhone size balance pass after simulator review showed the first Home version was still oversized.
- Tightened the shared Home command strip, bottom dock, hero metrics, quick-action chips, Status rail, and default composer footprint.
- Verified in the Xcode iPhone 17 Pro Simulator that the feed section now enters the first viewport below the compact composer.
- Completed a blueprint-inspired native Home reconstruction pass that treats the generated image as inspiration only.
- Rebuilt the Pulse Network hero into a compact live overview with server-derived signal metric, live broadcast count, ambient network panel, and route-safe UNDX/Pulse Radio/Safety Shield chips.
- Converted the Transmission Console quick actions into a horizontal native action rail to preserve touch targets without oversized vertical footprint.
- Removed the extra feed-tab title/subtitle block so the signal filter rail behaves like a compact channel selector.
- Verified the final candidate in the Xcode iPhone 17 Pro Simulator with `pulsesoc:///pulse` after a fresh Metro dev-client bundle.
- Captured final simulator evidence at `reports/screenshots/logi-nexus-home-iphone17pro-blueprint-final.png`.
- Completed a follow-up space-efficiency pass based on the generated blueprint's tighter first-viewport proportions.
- Tightened the Home-mode command strip, Pulse Network hero, `Your Orbit` rail, Transmission Console, and feed filter rail without changing Home business logic or backend contracts.
- Verified on Xcode iPhone 17 Pro Simulator that the first Signal Card now begins inside the opening viewport.
- Captured simulator evidence at `reports/screenshots/logi-nexus-home-iphone17pro-space-efficient-clean.png`.
- Inspected the current authenticated production WebView Homefeed at `https://pulsesoc.com/pulse` and used it as the source of truth for structure, module order, and density.
- Re-aligned native Home to the production WebView layout: command strip, Pulse Network hero, Status rail, Pulse Composer, Pulse Radio layer, feed filter rail, feed cards, and supporting intelligence/right-side panels.
- Added a responsive Home canvas so iPhone keeps the compact native stack while wider QA/browser surfaces can show the WebView-style right-side rail.
- Added the server-derived hero mood/summary hierarchy and compact Pulse Radio pill without adding fake production metrics.
- Added the native `HomeWebSideRail` for PulseSoc Intelligence, Trending Signals, Sponsored Signal, and realtime sync readiness using existing feed/status data and safe route handoffs.
- Added a compact Pulse Radio dock between the Transmission Console and feed rail to preserve the WebView information architecture.
- Verified the current native bundle in the Xcode iPhone 17 Pro Simulator after local QA authentication returned to the native app.
- Captured simulator evidence at `reports/screenshots/logi-nexus-home-webview-parity-native-return.png`.

Remaining Home work:

- Remove or resolve the existing app-wide `expo-av` deprecation warning during the media dependency modernization pass; it currently appears only as a dev-client warning toast.
- Full physical iPhone release QA for haptics, push/tap routing, camera/media capture, background recovery, and native performance feel.
- Final LogiNexus motion/polish pass after foundation completion across the app.

ONE next autonomous mission:

Complete shared global navigation and Messenger LogiNexus foundation after this Home reconstruction is verified and pushed.

Date: 2026-07-09

## Latest Mission Status: LogiNexus Homefeed Inspiration Alignment Pass

- Home LogiNexus transformation: 68%.
- Home UX completeness: 82%.
- Visual fidelity to approved concept: 68%.
- Native foundation/parity: 96%.
- System consistency: 89%.
- Release QA confidence: 86%.
- Can Homefeed be considered LogiNexus-complete: NO.

Completed this mission:

- Extended the shared `logiNexus` token system with Home-specific colors, typography, and depth tokens.
- Rebuilt the Pulse Network hero hierarchy around real Home feed/status data, with UNDX, Pulse Radio, and Safety Shield tiles.
- Transformed Status presentation into the `Your Orbit` rail with circular identity treatment, unseen state, online dot, and clearer empty/cached language.
- Updated the Pulse Composer presentation into the `Transmission Console` while preserving existing draft recovery, upload queue, retry, validation, publish, and feed invalidation logic.
- Updated Home feed cards toward the `Signal Card` direction with stronger author hierarchy, verified marker treatment, media framing, and action clarity.
- Applied the approved Home reference image as inspiration for command-strip hierarchy, hero density, status orbit weight, compact composer layout, creator pill treatment, and floating Create dock emphasis.
- Added scoped Homefeed LogiNexus reports and an audit script.

What remains in Home:

- Full visible QA browser walkthrough after this pass.
- iPhone simulator visual QA.
- Reduced-motion-aware ambient motion and publish micro-interactions.
- Final bottom dock visual fidelity.
- Physical-device-only haptics, push/tap, camera/media capture, and background recovery checks.

ONE next autonomous mission:

Complete PulseSoc LogiNexus Home visible QA and interaction polish.

## Latest Mission Status: Native Home Feed Interaction + Media Handoff QA

- Home foundation: 96%.
- Feed interactions: 91%.
- Media handoff: 88%.
- Visible QA: 92%.
- Current native migration: 91%.
- Release QA confidence: 81%.
- Can Home remain foundation-complete: YES.

Completed this mission:

- Authenticated native Home was opened in the built-in QA browser at `127.0.0.1:8094` through the local QA API proxy.
- Roody visibly watched seeded Home feed cards for interaction, image media, and broken media behavior.
- Like, Save, Repost, Share no-crash, Comment/Post Detail routing, Profile/Follow routing, Report, Hide, Block, and Mute were exercised from Home feed cards.
- Image media opened the shared `NativeMediaViewer`; broken media opened and closed the same viewer layer without crashing Home.
- Add Status and Reels routing were checked from Home.
- Server-side disposable backend evidence confirmed reaction, save, saved-item, repost live-event, and feed media payload rows.
- Added stable QA selectors to Home feed actions/media thumbnails and `NativeMediaViewer` controls.
- Fixed a visible web QA issue where the outer feed card role caused nested-button warnings on web; child action buttons remain accessible and scoped.
- No QA credentials were committed or written into reports.

Known remaining Home gaps:

- Reply remains inside Post Detail/comment flow, not as a direct Home-card action.
- Follow from Home currently routes to Profile; it is not a Home-card follow mutation proof.
- Browser share was proven safe/no-crash, but native provider share sheets remain device-release QA.
- Physical media capture, microphone/camera permissions, gallery picker, native share sheets, and large video upload remain device-release QA items.

ONE next Home mission ONLY:

Native Home Activity/Notification invalidation visible QA. Prove a Home publish or feed action refreshes Activity/Notifications visibly through the existing event sync path, without adding new Home features or starting UI polish.

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

## Native User Dashboard Completion

Important roadmap rule:

- Do not focus on Android right now.
- Do not touch production WebView paths.
- Use the built-in QA browser visibly for dashboard review.
- Keep dashboard logic server-authoritative and reuse existing PulseSoc APIs.

Completed action: built the native User Dashboard foundation.

What changed:

- Created `mobile-native/src/api/dashboard.ts`.
- Created `mobile-native/src/screens/UserDashboardScreen.tsx`.
- Registered the Dashboard tab in `mobile-native/src/navigation/AppNavigator.tsx`.
- Added `/dashboard` and `/pulse/dashboard` native route handling.
- Added notification/deep-link routing for dashboard home links.
- Created `reports/pulsesoc_native_user_dashboard_progress.md`.
- Created `reports/pulsesoc_native_visible_dashboard_qa.md`.
- Created `scripts/pulsesoc_native_user_dashboard_audit.py`.

User Dashboard completion: 78%.

Fully native dashboard modules:

- dashboard home/overview
- profile/identity summary
- account status
- notifications/activity summary
- messages/calls summary
- posts/status/reels summary gateway
- marketplace/seller/buyer summary
- premium/verification/security/trust summary
- creator/growth/intelligence summary
- quick actions
- recent activity
- dashboard cards
- navigation to existing native modules

Dashboard modules still fallback to web:

- advanced payment provider checkout and billing pages
- advanced marketplace payout/provider setup
- advanced campaign launch tools
- advanced creator studio and Live Studio tools
- sensitive account deletion/password/provider workflows
- physical camera/microphone and provider push behavior

Visible QA status:

- Built-in QA browser used visibly.
- Chrome Incognito not used.
- Dashboard tab, `/pulse/dashboard`, and `/dashboard` were prepared for visible review.
- Roody visibly saw the signed-in native dashboard, full dashboard scroll, Seller Store action, Intelligence action, and Camera Studio browser fallback.
- QA tooling note: direct Expo web on `localhost:8095` rendered reliably; the `localhost:8094` same-origin proxy served API but did not mount the Expo root reliably in the in-app browser.

Current native migration percentage: 90% foundation/parity coverage, 91% system consistency confidence, 67% release QA confidence.

Recommended next native feature/action: Persistent Dashboard QA Fixture Pack.

Reason for recommendation:

- The dashboard is now native and route-complete, but repeatable visual review still depends on temporary local QA data.
- Persistent dashboard fixtures would make owner review, release QA, provider boundary checks, and system consistency validation much faster and more reliable.

## Native User Dashboard Parity Foundation

Important roadmap rule:

- Do not focus on Android right now.
- Do not touch production WebView paths.
- Treat the current production dashboard as the module and route migration map, not as the final native UI target.
- Foundation parity comes before final UI/UX polish.

Completed action: expanded the native User Dashboard to represent the current production dashboard module universe.

What changed:

- Added `mobile-native/src/data/dashboardModules.ts` with 135 user-visible production dashboard widgets across 11 dashboard groups.
- Updated `mobile-native/src/api/dashboard.ts` to expose dashboard module groups and production quick actions to the native dashboard state.
- Updated `mobile-native/src/screens/UserDashboardScreen.tsx` to render production dashboard groups, status labels, lock labels, module routes, fallback indicators, and quick-action links.
- Added `reports/pulsesoc_native_user_dashboard_parity.md`.
- Added `scripts/pulsesoc_native_user_dashboard_parity_audit.py`.

Dashboard foundation parity: 92%.

Current production dashboard modules now represented natively:

- Account Command Center
- Pulse Network
- Creator Studio
- Intelligence
- Economy & Earnings
- Pulse Radio & Media
- Crypto Command Center
- Moderation / Safety
- Ads & Sponsorships
- PulseSoc AI
- System Status

Modules still missing or fallback-only:

- Admin / Moderator Only modules are intentionally hidden from owner dashboard parity.
- Advanced ads/campaign provider tools remain fallback-safe.
- Advanced music/radio/video provider workflows remain fallback-safe.
- Advanced crypto/provider data tools remain fallback-safe.
- Advanced system diagnostic screens route to existing native umbrella screens until dedicated detail shells exist.

Current native migration percentage: 91% foundation/parity coverage, 91% system consistency confidence, 67% release QA confidence.

Recommended next dashboard task: native dashboard module detail shells for fallback-heavy groups.

Reason for recommendation:

- The dashboard now represents the production module universe, but several cards still land on umbrella native screens or safe web fallback.
- Lightweight native detail shells would close the remaining route-parity gap without duplicating backend/provider business logic.

## Native Dashboard Module Detail Shells

Important roadmap rule:

- Continue focusing only on the User Dashboard until the foundation is complete.
- Do not focus on Android right now.
- Do not touch production WebView paths.
- Foundation parity comes before final UI/UX polish.

Completed action: added native module detail shells for dashboard cards that were fallback-heavy.

What changed:

- Added `mobile-native/src/navigation/dashboardRouting.ts` so native dashboard cards and shells share one route/fallback helper.
- Added `mobile-native/src/screens/DashboardModuleDetailScreen.tsx`.
- Registered `DashboardModuleDetail` in stack navigation and deep linking at `/pulse/dashboard/module/:groupKey/:moduleKey`.
- Updated User Dashboard cards to open native module shells before routing to native surfaces or protected production web fallbacks.
- Added `reports/pulsesoc_native_dashboard_module_shells.md`.
- Added `scripts/pulsesoc_native_dashboard_module_shells_audit.py`.

Native detail-shell groups covered:

- Economy & Earnings
- Creator Studio
- Intelligence
- Pulse Radio & Media
- Crypto Command Center
- Ads & Sponsorships
- Moderation / Safety
- System Status

Dashboard foundation parity: 95%.

Native module detail shell coverage: 100% of represented dashboard cards through the reusable shell.

Visible QA coverage:

- Creator, Intelligence, Media, Crypto, Ads, and Safety shell cards opened from visible dashboard card text in the built-in QA browser.
- Economy and System shell routes rendered visibly through native shell URLs after in-app browser automation could not reliably scroll/click the deeper virtualized dashboard cards.
- Screenshot evidence is saved under `reports/screenshots/native-dashboard-module-shells-2026-07-06/`.

Modules still fallback-only:

- Advanced provider/payment/payout/billing flows.
- Advanced campaign launch and sponsorship setup.
- Advanced music/radio/video provider workflows.
- Admin and moderator-only dashboard modules.
- Any production dashboard route that should deep-link directly into a specific shell but still resolves to a legacy umbrella route.

Current native migration percentage: 92% foundation/parity coverage, 91% system consistency confidence, 67% release QA confidence.

Recommended next dashboard task: direct legacy dashboard route alias mapping into module detail shells.

Reason for recommendation:

- Dashboard card clicks now open native shells, but direct production routes like `/dashboard/network/community-intelligence` can still resolve through older generic route handlers.
- Mapping those legacy aliases into the module shell route will finish the foundation-level navigation parity without adding final UI polish or duplicating backend business logic.

## Native Dashboard Legacy Route Alias Mapping

Section: Legacy Dashboard Route Alias Mapping

Important roadmap rule:

- Continue focusing only on the User Dashboard until the foundation is complete.
- Do not focus on Android right now.
- Do not touch production WebView paths.
- Foundation parity comes before final UI/UX polish.

Completed action: mapped legacy production dashboard URLs into the native module detail shell.

What changed:

- Added alias helpers in `mobile-native/src/navigation/dashboardRouting.ts`.
- Added `DashboardLegacyModuleScreen` as a native deep-link gateway.
- Registered `DashboardLegacyModule` in stack navigation and deep linking.
- Moved older exact legacy dashboard linking entries off `/dashboard/<group>/*` so the shell resolver handles production dashboard aliases consistently.
- Added dashboard module alias handling to notification/deep-link routing.
- Covered legacy dashboard group prefixes for Account, Network, Creator, Intelligence, Economy, Media, Crypto, Safety, Ads, AI, and System Status.
- Preserved the dashboard module registry as the source of truth for module title, status, lock state, description, route, and fallback behavior.

Legacy URLs now opening native shells:

- `/dashboard/account/security`
- `/dashboard/network/community-intelligence`
- `/dashboard/creator/content-planner`
- `/dashboard/intelligence/ai-advisor`
- `/dashboard/economy/earnings`
- `/dashboard/media/pulse-radio`
- `/dashboard/crypto/alerts/create`
- `/dashboard/safety/reports-submitted`
- `/dashboard/ads/campaign-builder`
- `/dashboard/ai/assistant`
- `/dashboard/system/feed`

Dashboard foundation parity: 97%.

Legacy dashboard alias coverage: 100% for represented native dashboard modules and requested group prefixes.

Remaining fallback-only dashboard URLs:

- Unknown dashboard URLs not represented in the native module registry.
- Admin/moderator-only dashboard modules intentionally hidden from owner dashboard parity.
- Provider-owned advanced payment, payout, campaign launch, radio/music distribution, and Live Studio tools.
- Future dedicated native detail screens for modules that currently use the reusable foundation shell.

Current native migration percentage: 93% foundation/parity coverage, 91% system consistency confidence, 67% release QA confidence.

Recommended next dashboard task: Dashboard module data contracts and state panels.

Reason for recommendation:

- Dashboard route parity is now wired at the foundation level.
- The next gap is per-module data richness: lightweight native state panels should reuse existing APIs for counts, statuses, permissions, and warnings without building final UI polish or duplicating backend logic.

## Native Dashboard Live State Panels

Section: Dashboard Live State Panels

Important roadmap rule:

- Continue focusing only on the User Dashboard until the foundation is complete.
- Do not focus on Android right now.
- Do not touch production WebView paths.
- Foundation coverage comes before final UI/UX polish.

Completed action: added lightweight server-authoritative live state panels to dashboard module detail shells.

What changed:

- Added `mobile-native/src/api/dashboardLiveState.ts`.
- Reused the existing native dashboard aggregation layer instead of duplicating backend logic.
- Reused existing API wrappers for account, profile, activity, messenger, calls, feed, marketplace, seller, orders, premium, verification, account health, safety, creator, growth, intelligence, and crypto alerts.
- Updated `DashboardModuleDetailScreen` to render loading, live, cached, warning, fallback, and unavailable panel states.
- Every represented dashboard module now receives group-aware live metrics and server-derived signals.

Dashboard live-state groups covered:

- Account Command Center
- Pulse Network
- Creator Studio
- Intelligence
- Economy & Earnings
- Pulse Radio & Media
- Crypto Command Center
- Moderation / Safety
- Ads & Sponsorships
- PulseSoc AI
- System Status

Dashboard foundation parity: 98%.

Dashboard live-state coverage: 100% of represented dashboard modules through reusable group-aware panels.

Modules still using fallback-only data:

- Advanced provider-owned payment, payout, campaign launch, radio/music distribution, Live Studio, and AI provider operations.
- Admin/moderator-only modules intentionally hidden from the owner dashboard.
- Module-specific contracts not yet dedicated beyond group-level dashboard aggregation.

Current native migration percentage: 94% foundation/parity coverage, 92% system consistency confidence, 68% release QA confidence.

Recommended next dashboard task: Dashboard quick-action parity hardening.

Reason for recommendation:

- The dashboard now has route parity, legacy alias routing, module shells, and live state panels.
- The remaining foundation gap is quick-action reliability: every production dashboard quick action should land on a native route or explicit safe fallback with no dead links.

## Native Dashboard Quick Action Parity Hardening

Section: Dashboard Quick Action Parity Hardening

Important roadmap rule:

- Continue focusing only on the User Dashboard until the foundation is complete.
- Do not focus on Android right now.
- Do not touch production WebView paths.
- Foundation coverage comes before final UI/UX polish.

Completed action: hardened dashboard quick-action routing and classification.

What changed:

- Added route classification for dashboard actions: native route, native shell, safe web fallback, or missing/invalid.
- Updated dashboard quick-action targets so they no longer point to stale or ambiguous routes.
- Updated User Dashboard quick-action badges, module cards, and module detail shells to show route classification.
- Added direct aliases for `/pulse/compose` and `/pulse/music` so deep-link/URL entry matches dashboard click behavior.
- Preserved the Live Studio provider boundary and routed Pulse Radio through its native Media dashboard shell.

Quick-action parity:

- Native route: Create Post, Upload Video, Add Status, Upgrade to Premium, Open Scam Shield.
- Native shell route: Invite Friends, Create Crypto Alert, Ask Crypto AI, Scan Token, Add Watchlist Asset, Open Pulse Radio.
- Safe web fallback: Go Live.
- Missing/invalid route: none.

Dashboard foundation parity: 99%.

Quick-action parity: 100% for registered dashboard quick actions.

Visible QA result: passed in the built-in QA browser. Representative quick actions opened native surfaces, native dashboard module shells, or the intentional Live Studio provider boundary with no missing route, auth wall, or unavailable module state.

Current native migration percentage: 95% foundation/parity coverage, 92% system consistency confidence, 69% release QA confidence.

Recommended next dashboard task: Dashboard fallback boundary labeling.

Reason for recommendation:

- The dashboard now has route parity, legacy aliases, module shells, live panels, and quick-action parity.
- The remaining dashboard foundation gap is clarity around fallback-heavy modules: every provider-owned or unsupported area should clearly show why it falls back and what native coverage already exists.

## Native Home Experience Foundation - Phase 1

Section: Native Home Experience Foundation.

Important roadmap rule:

- Dashboard foundation is paused and complete enough for this phase.
- Current priority is the native iPhone/iOS Home experience and shared native code.
- Do not focus on Android right now.
- Do not touch production WebView paths.
- Foundation parity comes before final UI/UX polish.

Completed action: built the first native Home foundation parity layer.

What changed:

- Replaced the feed-only Home surface with a full native Home foundation.
- Added Pulse Network hero with Pulse Radio, Live, Safety scan, refresh, and lightweight server-derived metrics.
- Added Home status rail using the existing native Status API/cache wrappers.
- Added native Add Status and Status detail routing from Home.
- Added a dedicated inline `HomePulseComposer` with Post/Reel/Live modes, attachment controls, audience selector, media upload preview, validation, publish progress, retry/cancel inherited from the shared upload pipeline, and safe fallback notes.
- Added native feed category tabs for For You, Following, Friends, Communities, Trending, Crypto, Scam Alerts, Arena Highlights, Roast Clips, Questions, and My Posts.
- Preserved existing feed loading, cursor pagination, pull refresh, cache/offline fallback, post detail routing, profile routing, media viewer routing, save/repost/share/promote behavior, and event sync invalidation.
- Extended feed card controls with visible Comment, Follow, Report, Hide, Block, and Mute paths without introducing client-authoritative moderation logic.

Reused backend/API/business logic:

- Existing PulseSoc feed APIs and ranking/filtering query contract.
- Existing Status rail APIs.
- Existing post creation APIs.
- Existing shared native media upload pipeline.
- Existing native event sync invalidation registry.
- Existing Profile, Status, Live, Camera Studio, Safety Hub, Growth, and dashboard routing.

Home foundation estimates:

- Home foundation: 82%.
- Hero: 86%.
- Status: 84%.
- Composer: 80%.
- Feed tabs: 90%.
- Feed cards: 84%.
- Feed interactions: 78%.
- Publishing: 68%.
- Navigation: 92%.
- Visible QA coverage: 86%.

Visible QA result:

- Authenticated Home rendered in the built-in QA browser.
- Verified Live, Safety Hub, Status Creator, Pulse Radio native shell, Post Detail, Profile Detail, and NativeMediaViewer destinations.
- Verified Trending selection, feed scrolling, Save state, composer modes, audience/topic/feeling state, and empty publish validation.
- Fixed native feed normalization so `author_public_player_id` is preserved for profile routing.
- Added the production resolver's display-name fallback for legacy posts without stable public IDs.
- No runtime console errors were observed; only known Expo web warnings/deprecations remained.

Current native migration percentage: 95% foundation/parity coverage, 92% system consistency confidence, 70% release QA confidence.

Recommended next Home task: Native Home publishing contract and draft recovery hardening.

Reason for recommendation:

- The native Home foundation now exposes and visibly verifies the production Home structure, feed controls, and navigation.
- The highest-risk remaining Home gap is successful text/media publishing across Post and Reel modes, durable draft recovery, and explicit server-backed location/mention selection.

## Native Home Publishing Contract + Draft Recovery Hardening

Section: Native Home publishing contract.

Important roadmap rule:

- Continue focusing only on Home until the foundation is complete.
- Do not focus on Android right now.
- Keep server-authoritative publishing, media processing, moderation, and visibility logic in the backend.
- Foundation coverage comes before final UI/UX polish.

Completed action: hardened Home Composer publishing state, durable draft recovery, retry behavior, and feed invalidation after publish.

What changed:

- Added durable Home Composer draft persistence through native storage.
- Restores body, Post/Reel/Live mode, visibility, topic, feeling, selected media metadata, and uploaded media result metadata.
- Added visible recovered-draft state and clear-draft action.
- Added retry for failed server publish requests without bypassing backend validation.
- Preserved shared media upload retry/cancel for image/video upload failures.
- Blocks publish while the upload queue is active.
- Clears draft state and resets composer mode/audience/media after successful publish.
- Invalidates Activity and Notifications through the existing native event sync registry after Home publish success.

Home publishing estimates:

- Home foundation: 87%.
- Publishing: 78%.
- Draft recovery: 86%.
- Upload queue: 74%.
- Feed invalidation after publish: 86%.
- Visible QA coverage: 72% for this publishing pass.

Visible QA result:

- Roody could visibly see authenticated native Dashboard -> Home navigation, the Home hero, Status rail, Pulse Composer, Post/Reel/Live modes, publishing controls, feed tabs, and feed cards in the built-in QA browser.
- Browser automation timed out before completing composer text entry, draft reload, and text-only publish verification.
- The publishing implementation is statically verified but not yet fully browser-proven end to end.

Current native migration percentage: 95% foundation/parity coverage, 92% system consistency confidence, 70% release QA confidence.

Recommended next Home task: Native Home visible text publish QA completion.

Reason for recommendation:

- Text-only publishing, draft recovery, retry state, and feed invalidation are structurally hardened.
- The immediate Home-specific risk is the remaining visible proof: type a text post, reload and recover the draft, publish to the local QA backend, confirm composer reset, and confirm feed refresh.

## Native Home Publishing Proof & Foundation Completion

Section: Native Home foundation completion.

Important roadmap rule:

- Continue focusing only on Home until visible publish proof is complete.
- No Android focus.
- No final UI/UX polish yet.
- Keep the backend authoritative for publishing, validation, media processing, moderation, visibility, ranking, and sync.

Completed action:

- Added stable Home Composer QA handles for input, counter, Post/Reel/Live modes, photo/video handoffs, publish, retry, recovered draft, clear draft, and composer status.
- Reconfirmed text-only publishing contract against a disposable local backend.
- Verified local QA login, `/api/pulse/posts` publish success, feed visibility for the new post, and `/api/pulse/sync/events` availability after publish.
- Created Home completion and visible publish QA reports.

Home foundation estimates:

- Home foundation: 91%.
- Hero: 90%.
- Status: 86%.
- Composer: 90%.
- Publishing: 84%.
- Draft recovery: 88%.
- Upload queue: 78%.
- Feed consistency: 84%.
- Navigation: 93%.
- Visible QA: 78%.

Current native migration percentage: 95% foundation/parity coverage, 92% system consistency confidence, 72% release QA confidence.

Can Home foundation be considered complete: NO.

Reason:

- The backend contract and implementation are hardened, but the built-in QA browser control channel timed out before Roody could watch the full text publish proof, draft recovery, composer reset, and visible feed refresh.
- Device media capture/upload remains separate release QA.

Recommended next Home task: Native Home visible browser publish proof recovery.

Reason for recommendation:

- It is the only remaining blocker to marking the Home foundation complete.
- The next task should stabilize the in-app browser path and prove the already-built publishing state machine end to end without adding new Home features.

## Native QA Runtime Stabilization

Section: Native QA runtime stabilization.

Important roadmap rule:

- Stop new Home feature work until visible QA runtime is stable.
- No Android focus.
- No UI/UX polish.
- Preserve production WebView behavior.
- Keep server-authoritative behavior unchanged.

Completed action: stabilized the native QA web runtime path after Home visible publish proof was blocked by browser-control timeouts and Metro resolver errors.

What changed:

- Added explicit `mobile-native` dependency for `nullthrows@1.1.1`.
- Kept `expo-modules-core@3.0.30` Expo-managed because Expo Doctor rejects direct installation of `expo-modules-core`.
- Preserved Expo SDK 54 / React Native 0.81 package versions already present in the lockfile.
- Added `scripts/pulsesoc_native_qa_runtime_audit.py` to verify project-level dependency resolution and required stabilization reports.
- Created QA runtime stabilization and visible runtime readiness reports.

Runtime evidence:

- `npm ci --prefix mobile-native --no-audit --no-fund --progress=false` passed.
- Clean Metro web run with `--clear` bundled `index.ts` successfully.
- `curl -I http://localhost:8094/Login` returned `HTTP/1.1 200 OK`.
- The previous `expo-modules-core` and `nullthrows` resolver failures did not reproduce from a clean install/cache state.
- Built-in QA browser visible Login route rendered with no runtime console errors.
- Authenticated visible route checks passed through `http://127.0.0.1:8094` for Home, Dashboard, Marketplace, and Messages.
- Documented the local QA host rule: use `127.0.0.1:8094` when the local API proxy is `127.0.0.1:5108`; `localhost:8094` can render unauthenticated routes but may not restore local API cookies.

Visible QA can resume: YES, with the rule that Metro must be started from a clean cache after dependency changes, localhost must be verified before opening the built-in QA browser, and authenticated local QA should use `127.0.0.1:8094`.

Current native migration percentage: 95% foundation/parity coverage, 92% system consistency confidence, 73% release QA confidence.

Recommended next mission: Native Home visible browser publish proof completion.

Reason for recommendation:

- Home publishing is structurally implemented and backend-contract verified.
- The QA runtime blocker has been removed at the Metro/dependency layer.
- The fastest path toward completing the Home foundation is now to visually prove text entry, draft recovery, publish, composer reset, and feed refresh in the built-in QA browser.

## Native Home Activity + Notification Invalidation Visible QA

Section: Home synchronization and cursor invalidation proof.

Completed action:

- Proved authenticated Home feed interaction state visibly in the built-in QA browser.
- Verified text publish from prior Home completion remains visible in feed and Activity.
- Verified Home like and comment interactions update the feed/detail UI visibly.
- Verified Activity and `/pulse/notifications` routes remain stable after Home actions.
- Fixed duplicate recipient notifications for like/comment by making API routes emit one cursor-aware notification while opting out of lower-level feed-engine owner notifications.
- Added explicit Activity/Notifications invalidation metadata to post-owner and follow notifications.
- Hardened feed payloads so saved, reposted, and follows state survives refresh.
- Wired Home Follow to the server-authoritative follow toggle endpoint.
- Wired Home Report/Block/Mute actions into Safety Hub with target context.

Visible QA evidence:

- Home seed post `6` rendered visibly in the QA browser.
- Fire reaction changed the visible card to `1 reactions` / `Fire 1`.
- Post Detail accepted a visible comment and showed `1 comments`.
- Activity route showed actor-side publish/report/block events.
- Notifications route resolved to the unified Activity Inbox.

Backend cursor evidence:

- Recipient owner notification rows for seed post `6` showed exactly one `like` and one `comment` notification.
- Both rows include `sync_cursor_key`.
- Both rows include `invalidates=["activity","notifications"]`.
- No duplicate like/comment notifications were produced after the fix.

Remaining Home release blockers:

- Persistent Hide is still local-only and restores after refresh.
- Native user Mute still uses Safety Hub local/web fallback instead of a server-authoritative native mute mutation.
- Comment submit works but is rendered as a non-semantic clickable view; it needs an accessible button path.
- Physical-device push notification delivery, lock-screen taps, and background recovery remain release QA.

Current native migration percentage: 95% foundation/parity coverage, 93% system consistency confidence, 76% release QA confidence.

Can Home be considered release complete: NO.

Recommended next Home mission: Native Home Hide/Mute Persistence + Comment Accessibility Hardening.

Reason for recommendation:

- Home foundation is complete enough and Home sync is now visually/backend verified.
- The remaining Home release blockers are narrow: persistent hide, native mute, and accessible comment submit.
- Fixing those closes the last Home-specific release blockers without starting a new subsystem or UI polish phase.

## Native Home Release Blocker Hardening

Section: Home release readiness.

Completed action:

- Replaced local-only Home Hide with server-authoritative `POST /api/pulse/posts/<post_id>/hide`.
- Added backend `pulse_post_hides` persistence and feed filtering so refresh does not restore hidden cards.
- Replaced Home Mute fallback with server-authoritative `POST /api/pulse/users/mute`.
- Added backend `pulse_user_mutes` persistence and feed filtering so muted user content stays removed after refresh.
- Added cursor-visible `pulse_post_hidden` and `pulse_user_muted` notification events.
- Added native feed API wrappers for hide and mute.
- Added semantic, QA-addressable comment submit path in Post Detail.

Visible QA result:

- Built-in QA browser showed Home against the local QA stack.
- Hide -> refresh -> hidden state persisted for post `91001`.
- Mute -> refresh -> muted author content stayed removed for user `51003`.
- Comment submit through `post-detail-submit-comment` posted and rendered the comment for post `91003`.
- `/api/pulse/sync/events` exposed `pulse_post_hidden` and `pulse_user_muted` with cursor metadata.

Current Home status:

- Home foundation: 99%.
- Home release readiness: 96%.
- Current native migration: 95%.
- Release QA confidence: 84%.

Can Home be considered release complete: browser release blockers closed; physical-device push/background/accessibility sweep remains before final release-complete signoff.

Recommended next mission: PulseSoc Native Home Release Device Readiness Sweep.

Reason for recommendation:

- Home browser blockers are closed.
- Remaining Home risks are release-readiness validation items: physical-device push taps, background recovery, and broader accessibility scan.
- This is a narrow QA/hardening pass, not a new feature or polish phase.

## Native Home Release Device Readiness Sweep

Section: Home release readiness.

Completed action:

- Verified the connected iPhone 16 Pro is paired and available through `xcrun devicectl`.
- Verified `com.pulsesoc.nativeapp` launches on the physical iPhone at process level.
- Verified the Home deep link payload `pulsesoc://pulse` launches the native app at process level.
- Audited native Home release contracts for deep linking, push registration, notification response routing, Home refresh, server-authoritative hide/mute, and semantic comment submit.
- Documented the remaining release-device checks without claiming unobserved physical interaction behavior.

Device evidence:

- Device: P3r7or, iPhone 16 Pro, iOS 18.7.3.
- App identity: `com.pulsesoc.nativeapp`.
- `xcrun devicectl device process launch --device 00008140-000E2D9A2EE8801C --terminate-existing com.pulsesoc.nativeapp` returned `Launched application with com.pulsesoc.nativeapp bundle identifier.`
- `xcrun devicectl device process launch --device 00008140-000E2D9A2EE8801C --terminate-existing --payload-url 'pulsesoc://pulse' com.pulsesoc.nativeapp` also returned `Launched application with com.pulsesoc.nativeapp bundle identifier.`

Current Home status:

- Home foundation: 99%.
- Home release readiness: 97%.
- iPhone device readiness: 72%.
- Push/tap readiness: 55%.
- Background recovery: 60%.
- Accessibility readiness: 78%.
- Visible QA coverage: 95%.
- Current native migration: 95%.
- Release QA confidence: 85%.

Can Home be considered release complete: NO.

Reason:

- Physical iPhone launch and Home deep-link dispatch passed at process level.
- Home is not yet release-complete because manual on-device Home interaction, provider-backed push delivery, notification tap routing, background recovery, and broader Home accessibility still require device evidence.

Recommended next mission: PulseSoc Native Home Manual iPhone Release QA.

Reason for recommendation:

- The app/device path is available.
- The next useful work is not another Home feature; it is a short manual iPhone pass with screen recording plus provider-backed notification tap checks if credentials/provider access are available.
- That pass should verify Home scroll, refresh, text publish, media handoff, hide/mute persistence after app restart, comment submit accessibility, foreground/background recovery, and notification tap routing.

## Native Home Manual iPhone Release QA

Section: Home release readiness.

Completed action:

- Attempted the manual iPhone Home release QA path using the connected iPhone 16 Pro.
- Verified the device display is active and portrait through `xcrun devicectl device info displays`.
- Verified `com.pulsesoc.nativeapp` launches through `pulsesoc://pulse`.
- Verified `PulseSocNative.app/PulseSocNative` appears in the physical iPhone process list.
- Verified process-level suspend/resume on the active native app PID.
- Attempted physical screenshot capture with `idevicescreenshot`; the service failed with `Could not start screenshotr service: Invalid service`.
- Documented that no human-operated screen recording, QuickTime capture, screenshots, or provider-backed push/tap evidence was produced.

Current Home status:

- Home foundation: 99%.
- Home release readiness: 97%.
- iPhone manual QA: 42%.
- Push/tap readiness: 55%.
- Background recovery: 66%.
- Accessibility readiness: 78%.
- Release QA confidence: 85%.

Can Home be considered release complete: NO.

Reason:

- Home remains foundation-complete.
- Launch, Home deep-link dispatch, running process presence, and process-level background/foreground recovery are verified on the physical iPhone.
- manual iPhone interaction and push/tap behavior remain unproven because no screen recording, screenshots, human tap-through, or provider-backed notification tap evidence was captured.

Recommended next mission: PulseSoc Native Home Manual Screen Recording And Push Tap Proof.

Reason for recommendation:

- No new Home feature is needed.
- The only Home release blockers are evidence blockers: a human-captured iPhone Home interaction pass and provider-backed notification tap proof.
- The next pass should produce a video/screenshot path plus any backend IDs for publish/comment/activity events that happen during the run.

## Native Full Wiring Autopilot

Section: core native wiring.

Completed action:

- Added a native `Create` bottom-tab action that opens the existing Home composer.
- Added a native Home top bar for menu, search, activity, and profile.
- Added a Home hamburger drawer with 32 classified native/fallback actions.
- Added explicit Settings entries for Support Center, Privacy Policy, Terms of Service, and Telegram companion setup.
- Preserved server-authoritative behavior and production WebView compatibility.
- Added a reproducible full-wiring audit script.

Static audit result:

- Total static action surfaces discovered: 332.
- Dashboard modules: 146.
- Dashboard quick actions: 12.
- Bottom tabs: 15.
- Stack screens: 77.
- Home drawer actions: 32.
- Settings buttons: 26.
- Home composer pressables: 9.
- Feed card pressables: 15.

Current native status:

- Current native migration: 96%.
- Core native wiring: 94%.
- Release QA confidence: 86%.

Visible QA note:

- Built-in QA browser rendered native Login after a clean Metro rebuild.
- Authenticated visible wiring click-through was blocked because the local QA API/proxy at `127.0.0.1:5108` was not running.
- No Chrome Incognito was used.

Recommended next mission: PulseSoc Native Authenticated Wiring QA Pass.

Reason for recommendation:

- Core wiring is now foundation-complete by static audit.
- The next highest-value task is to restore the local QA API/proxy and run a focused visible click-through of representative drawer, Settings, dashboard, Home, marketplace, messaging, and profile actions to catch any route-level runtime misses before returning to release-device evidence tasks.

## Native Authenticated Wiring QA Pass

Section: authenticated route and action wiring.

Completed action:

- Restarted the local QA stack with a local backend on `127.0.0.1:5107`, a local QA proxy on `127.0.0.1:5108`, and Expo web on `127.0.0.1:8094`.
- Added a reusable local-only QA proxy utility for authenticated browser QA.
- Signed in through the visible native Login screen with a disposable local QA account; no credentials were committed or written to reports.
- Ran a visible built-in QA browser pass across 37 representative authenticated routes.
- Verified representative Home, Activity, Search, Profile, Profile Edit, Reels, Status, Camera Studio, Messenger, Calls, Marketplace, Seller Store, Buyer Orders, Premium, Creator, Growth, Intelligence, Alerts, Settings, Security, Privacy, Support, Verification, Account Health, Safety, Courses, Dashboard shell, and Pulse AI routes.
- Verified representative back navigation from Home, Profile, Marketplace, Settings, and Dashboard module shell flows.
- Fixed `/pulse/creator` so it opens native Creator Studio instead of falling back to Dashboard.
- Fixed `/pulse/support` so it opens the native Trust & Safety support shell instead of falling back to Dashboard.

Authenticated QA result:

- Representative routes tested: 37.
- Failures after scoped fixes: 0.
- Dead routes in the representative matrix: 0.
- Routing loops observed: 0.
- Back navigation checks passed: 5 of 5.
- Visible QA coverage: 80%.

Current native status:

- Current native migration: 96%.
- Authenticated routing production readiness: representative browser routing is production-ready.
- Release QA confidence: 87%.

Remaining release blockers:

- Physical iPhone push/tap routing.
- Provider-backed notification delivery.
- Camera/microphone hardware flows.
- Two-device calls/media sessions.
- Final legal-document strategy if Terms/Privacy should become native screens instead of safe provider fallbacks.

Recommended next mission: PulseSoc Native Messenger Foundation Replacement QA.

Reason for recommendation:

- Home and Dashboard foundations are complete enough, and representative app-wide authenticated routing is now verified.
- Messenger is the highest-value daily-engagement vertical and should be hardened next against the production WebView behavior before moving to Reels/Live and profile ecosystem polish.

## LogiNexus Transformation Phase 1

Section: Native Home transformation and shared design-system foundation.

Completed action:

- Added the shared LogiNexus token system for native colors, typography, spacing, radius, motion, and depth.
- Added reusable native primitives for panels, cards, badges, metrics, buttons, empty states, and signal indicators.
- Applied the primitives to native Home without changing backend contracts.
- Transformed the Home command strip, Pulse Network hero, status empty state, Transmission Console composer shell, feed empty state, and feed card shell.
- Replaced public-facing Home drawer "Pulse AI" copy with "UNDX" while preserving the existing native route.
- Updated Home hero copy to use "UNDX alerts" and "Powered by LogiNexus Intelligence" where requested by the transformation mission.

Current native status:

- Current native migration: 96%.
- Overall LogiNexus transformation: 8%.
- Home LogiNexus transformation: 38%.
- Release QA confidence: 87%.

Visible QA note:

- Static typecheck passes after the Home transformation.
- Visible built-in QA browser verification passed for the phase 1 Home transformation using the local QA stack.
- Roody could see the command strip, Pulse Network hero, status rail empty state, Transmission Console, feed filters, feed cards, and Home drawer UNDX label.
- Hardware-only Home checks remain unclaimed.

Recommended next mission: PulseSoc Native Master Navigation Drawer LogiNexus Transformation.

Reason for recommendation:

- The Home transformation established the shared visual foundation.
- The drawer is the highest-leverage next surface because it is the platform-wide navigation layer and must become searchable, classified, permission-aware, and visually coherent before transforming Messenger, Profile, Reels, Commerce, Trust, and Intelligence.

## LogiNexus Master Navigation Drawer Foundation

Section: shared native navigation layer.

Completed action:

- Replaced the Home-only drawer with a reusable `MasterNavigationDrawer`.
- Added centralized `masterNavigationSections` with 53 classified actions.
- Added shared `openNativeRoute` dispatcher for native routes, native shells, safe fallbacks, and dashboard module shells.
- Added drawer search, collapsible sections, route descriptions, and action status labels.
- Updated the public-facing tab title from `Pulse AI` to `UNDX`.

Current native status:

- Current native migration: 96%.
- Overall LogiNexus transformation: 11%.
- Master drawer foundation: 82%.
- Release QA confidence: 87%.

Recommended next mission: PulseSoc Native Global Navigation LogiNexus Foundation.

Reason for recommendation:

- The master drawer is now represented by a shared native component and central route inventory.
- The remaining weakest navigation layer is the global top/bottom navigation chrome, which still uses basic navigator styling and must become a coherent LogiNexus command layer before the next content verticals are transformed.

## LogiNexus Global Navigation Foundation

Section: shared top navigation, bottom navigation, drawer integration, route state, and badge foundation.

Completed action:

- Added `LogiNexusGlobalHeader` as the shared native command-strip header.
- Added `LogiNexusBottomNavigation` as the shared primary five-action navigation bar.
- Kept Home, Reels, Create, Messages, and Profile as the primary bottom actions while preserving the broader native tab registry for route dispatch.
- Wired notification/activity/message/alert badge state through existing notification APIs and the native event-sync invalidation layer.
- Added authenticated identity metadata from existing profile/session APIs.
- Added an authenticated identity header inside `MasterNavigationDrawer`.
- Connected stack and tab screens to the shared global header where safe.
- Replaced Home's local top-bar rendering with the shared command-strip primitive.

Current native status:

- Current native migration: 96%.
- Overall LogiNexus transformation: 14%.
- Global navigation foundation: 84%.
- Release QA confidence: 87%.

Remaining navigation gaps:

- Home uses the shared command-strip primitive but does not yet receive live global badge/identity props because Home still owns its header locally.
- Physical iPhone safe area, Dynamic Island, background badge refresh, and push-tap clearing remain release-device/provider QA.
- Some nested subsystem screens still carry local headers until their subsystem transformation pass.

Recommended next mission: PulseSoc Native Messenger / Pulse Command Foundation Hardening.

Reason for recommendation:

- The shared navigation foundation now exists across stack, tab, drawer, and Home command-strip surfaces.
- Messenger is the highest-value daily-engagement subsystem still needing full native foundation hardening across inbox, chat, unread state, calls, attachments, and UNDX/Pulse Command identity.

## LogiNexus Home iPhone Simulator Alignment

Section: native Home visual proof on Xcode iPhone Simulator.

Completed action:

- Used the Xcode iPhone 17 Pro Simulator to launch `com.pulsesoc.nativeapp` against the local QA proxy.
- Verified the native Home command strip, Pulse Network hero, UNDX / Pulse Radio / Safety Shield tiles, and shared bottom dock in the simulator.
- Fixed an iPhone-width Pulse Network hero overlap by adding a compact stacked layout under 430px width.
- Tightened shared bottom navigation spacing and fixed the Home activity glyph rendering issue.
- Cleaned scoped `pointerEvents` warning paths in dashboard/reels/status decorative layers.

Current native status:

- Current native migration: 96%.
- Overall LogiNexus transformation: 15%.
- Home LogiNexus transformation: 45%.
- Home simulator visual confidence: 78%.
- Release QA confidence: 87%.

Remaining Home visual/release gaps:

- Development builds still show the existing app-wide `expo-av` deprecation warning overlay; this is a media dependency migration task, not a Home crash.
- Physical iPhone Home haptics, push taps, background refresh, and camera/media hardware behavior remain release QA.
- Final Home animation/motion polish is still deferred until foundation parity is complete across the app.

Recommended next mission: PulseSoc Native Messenger / Pulse Command Foundation Hardening.

Reason for recommendation:

- Home and global navigation now have simulator-backed layout proof.
- Messenger remains the highest-impact daily-engagement subsystem that still needs foundation hardening before the native app can replace the WebView client.

## LogiNexus Pulse Command Milestone

Section: native Messenger, conversations, groups/rooms, calls entry points, and UNDX communications identity.

Completed action:

- Evolved the existing `MessengerScreen`, `ChatScreen`, `GroupsScreen`, and `PulseAiScreen` instead of creating replacement screens.
- Added shared `PulseCommand` primitives for command panels, contextual headers, search, segment rail, avatars, actions, and metrics.
- Preserved existing messenger APIs for conversation listing, search, message sync, send, typing, seen state, uploads, cache, and retry.
- Preserved existing native call entry routes from conversations.
- Preserved existing group/room APIs for listing, join/leave, room join, group chat, and report.
- Replaced visible Pulse AI labels in native screens with UNDX / Digital Intelligence Companion copy while leaving route/API identifiers compatible.
- Added Pulse Command reports and audit coverage.

Current native status:

- Current native migration: 96%.
- Overall LogiNexus transformation: 25%.
- Pulse Command transformation: 42%.
- Release QA confidence: 88%.

Remaining Pulse Command gaps:

- Full calls list transformation.
- Full rooms/detail and group settings transformation.
- Context menus for reply/forward/delete/report/block/mute.
- Message reactions UI.
- Hardware-only voice/camera/push/call checks.
- Long-thread, media-heavy, reduced-motion, and Dynamic Type simulator matrices.

Recommended next mission: Continue PulseSoc Pulse Command LogiNexus Transformation.

Reason for recommendation:

- Pulse Command now has server-backed message reactions, delete, report, retry, reply state, local-only populated QA fixtures, and in-place Chats / Calls / Groups / Rooms tabs.
- The subsystem is still not LogiNexus-complete because Calls, Groups, Rooms, offline/reconnect, nested safety, simulator evidence, and accessibility remain below the completion threshold.
- Search / Discover remains blocked until Pulse Command reaches deep vertical completion.

## Pulse Command Shared Domain Extraction

Section: Messenger / Chat reuse layer for portable production workflow logic.

Completed action:

- Added `mobile-native/src/pulseCommand/domain.ts` as the shared native presentation-domain boundary.
- Moved conversation title, preview, timestamp, active presence, signal badge, and accessibility-label rules out of `MessengerScreen`.
- Moved message preview, delivery/read label, typing summary, reaction icon, optimistic reaction, accessibility-label, and message action availability rules out of `ChatScreen`.
- Updated Pulse Command audits so future work cannot reintroduce duplicated preview/status/action logic in the inbox and conversation screen.
- Updated reuse and rebuild-boundary reports to document completed extraction and remaining portable logic.

Current native status:

- Current native migration: 96%.
- Overall LogiNexus transformation: 26%.
- Pulse Command transformation: 68%.
- Code reuse confidence: 86%.
- Release QA confidence: 88%.

Remaining Pulse Command gaps:

- Extend the shared domain module into group role labels, room/provider state, call history labels, attachment open/download/provider boundaries, and conversation-level mute/block/pin availability.
- Complete Groups, Rooms, offline/reconnect, and nested safety QA before moving to Search / Discover.

Recommended next mission: Pulse Command Groups / Rooms shared domain and detail completion.

Reason for recommendation:

- Inbox, Chat, and Calls now share more of the production workflow interpretation layer.
- Groups and Rooms are the weakest nested Pulse Command surfaces and still need shared role/provider state rules plus deeper native detail/action coverage.

## Pulse Command Groups / Rooms Domain Completion

Section: nested communications spaces inside Pulse Command.

Completed action:

- Extended `mobile-native/src/pulseCommand/domain.ts` with group and room presentation-domain rules.
- Refactored `GroupsScreen` to use shared group title, type, role, summary, badge, accessibility, and action-availability rules.
- Refactored room cards to use shared room title, summary, badges, accessibility, and provider-aware open/join action rules.
- Preserved the existing server-authoritative APIs for group join/leave, group chat open, group report, room list, and room join.
- Extended Pulse Command audits to require Groups / Rooms consumption of the shared domain layer.

Current native status:

- Current native migration: 96%.
- Overall LogiNexus transformation: 27%.
- Pulse Command transformation: 71%.
- Groups transformation: 66%.
- Rooms transformation: 59%.
- Code reuse confidence: 89%.
- Release QA confidence: 88%.

Remaining Pulse Command gaps:

- Calls history/provider labels, attachment open/download/provider boundaries, conversation-level mute/block/pin availability, offline/reconnect copy, and UNDX-specific interaction rules still need shared domain coverage.
- Groups still need member list, invitations, role management, group media/files/links, and moderation action depth.
- Rooms still need detail view, participant visualization, live presence, activity/provider boundaries, and scheduled-event surfaces.

Recommended next mission: Pulse Command group detail, member roles, and room detail foundation.

Reason for recommendation:

- Group and room cards now share domain rules, but their nested detail/action surfaces remain below production-ready depth.

## Pulse Command Group / Room Detail Foundation

Section: nested Groups and Rooms surfaces inside Pulse Command.

Completed action:

- Extended native group models with optional server-authoritative members, invitations, membership requests, media, files, and links.
- Extended native room models with optional participants, provider state, room type, activity, privacy, host, and current-user role.
- Added shared Pulse Command domain rules for group role priority, member action availability, invitation state, asset category labels, room provider state, and participant accessibility labels.
- Rebuilt the existing `GroupsScreen` detail layer into Overview, Members, Invitations, Media, Files, Links, and Settings sections.
- Added one native `RoomDetail` layer with Overview, Participants, Activity, and Provider sections.
- Preserved existing server-authoritative contracts for join/leave/chat/report and room join/open.
- Represented missing roster/invite/media/file/link/participant/provider contracts as explicit LogiNexus boundary panels instead of local-only fake states.

Current native status:

- Current native migration: 96%.
- Overall LogiNexus transformation: 28%.
- Pulse Command transformation: 74%.
- Groups transformation: 74%.
- Rooms transformation: 68%.
- Code reuse confidence: 90%.
- Release QA confidence: 88%.

Remaining Pulse Command gaps:

- Group member and invitation mutations need server-backed endpoint wiring.
- Room participant live-provider state remains provider/physical-device gated.
- Conversation-level mute/block/pin, attachment open/download boundaries, offline/reconnect, and full interaction QA remain incomplete.

Recommended next mission: Continue inside Pulse Command with conversation-level safety, mute/block/pin, and attachment boundary hardening.
## Pulse Command Final Simulator Closure — 2026-07-12

- Added keyboard-coordinate composer positioning, durable client-ID-deduplicated outbound text queuing through the existing Messenger send/sync/cache paths, selected-filter persistence, and restored-selection rail auto-scroll.
- Fresh simulator evidence proves the Pro Max keyboard/reply composer, restored Unread visibility, Groups/Rooms loading and populated room rendering, and the UNDX empty-history/composer state.
- Verification passed: dependency install, TypeScript, Expo Doctor 17/17, native iOS simulator build, Pulse Command exact parity, Messenger, Groups, group/room detail, navigation, and mission-standard audits.
- Freeze decision: **not simulator-parity frozen**. Live automatic reconnect/server-ID/read/reaction reconciliation, complete nested Group/Room/AI state matrices, and compact/standard keyboard captures remain unproven.
- Authoritative next recommendation: keep the next mission scoped to Pulse Command evidence and reconciliation; do not advance to another native subsystem yet.
- Detailed report: `reports/pulsesoc_native_pulse_command_final_simulator_closure_2026-07-12.md`.
## Pulse Command Live Reconciliation and Nested-State Closure — 2026-07-12

- Controlled-backend integration verified client-ID idempotency, stable server-ID reuse, reply send, realtime typing coalescing, and direct/group/room send-and-reload contracts.
- Native reconciliation now caches queued bubbles immediately and clears stale local status/error when the authoritative server message replaces the same client ID.
- Clean-install simulator QA identified and fixed the missing declared `@babel/runtime` dependency.
- Fresh Pro simulator evidence covers the Calls empty state and populated Groups/Rooms shell; internal API copy was removed from Calls.
- Freeze decision: **not simulator-parity frozen**. Interactive offline network restoration, compact/standard keyboard proof, and full nested Group/Room/AI/attachment/safety/call matrices remain incomplete.
- Next recommendation: remain on Pulse Command and repair deterministic authenticated compact/standard UI automation before another evidence mission.
- Report: `reports/pulsesoc_native_pulse_command_live_reconciliation_nested_closure_2026-07-12.md`.
## Focused Native Status Design and Deep Wiring — 2026-07-12

- Strategy changed to one complete native surface at a time; Status is the active focused subsystem.
- Added owner edit/privacy/delete management through existing PATCH/DELETE APIs, persistent creator drafts, live/multi-story rail signals, timed image progression, hold-to-pause viewer behavior, expanded accessibility semantics, and a premium creator visual pass.
- Reused canonical Status APIs, shared media upload/viewer/camera infrastructure, music and AI Story APIs, cache, authentication, navigation, deep links, and notification routing.
- Fresh Pro simulator evidence confirms the Status empty/creator-entry surface; creator modal automation and populated Status data were not reliable enough to claim full evidence.
- Status is **not focused-subsystem complete**. Complete populated/viewer/creator/owner/realtime/offline matrices and compact/standard/Pro Max QA remain required.
- Next recommendation: stay on Status; do not select another subsystem yet.

## Native Status Populated Lifecycle Closure — 2026-07-12

- Status remains the active focused subsystem.
- Added localhost-only, opt-in production-shaped fixtures for populated rail, text/photo/video/music/AI/live/muted/uploading/failed/private/offline/expired/deleted/reported/blocked states; production builds remain server-authoritative.
- Preserved the create entry in the populated rail and added an exact native `/pulse/status/create` route.
- Fresh Pro evidence now covers populated rail, viewer chrome, and the complete creator shell. Visual QA found and fixed Dynamic Island collisions in both viewer and creator headers.
- Status is **not frozen**: compact, standard, and Pro Max evidence plus physical-device media, reduced-motion, VoiceOver, realtime, and full controlled lifecycle execution remain open.
- Next recommendation: stay on Status until those remaining gates close.

## Native Status Final Width, Realtime, Accessibility, and Lifecycle Closure — 2026-07-12

- Status remains active and is **not simulator-parity frozen**.
- Controlled localhost lifecycle now passes text/image/video/music/AI creation, canonical IDs, rail insertion, seen deduplication, reaction replacement, reply, share, privacy revocation, owner aggregate analytics authorization, report, mute, block, delete, and expiration.
- Canonical Status mutations now emit backward-compatible events through the existing event ledger; native Status uses the shared event-sync invalidation system and deterministic duplicate/expired/deleted cleanup.
- Compact and Pro Max populated rail/viewer evidence is valid. Standard iPhone became blocked in Apple simulator data migration after removing a stale system URL-confirmation overlay; Pro evidence remains valid from the preceding mission.
- Production supports public/followers/private and aggregate owner analytics. It does not currently expose custom audiences, a Status viewer-list endpoint, unmute, or unblock through the inspected Status contracts; those were not invented.
- Connected physical iPhones were offline, so real camera/library/audio/upload routing remains `PHYSICAL-DEVICE-ONLY — NOT YET VERIFIED`.
- Next recommendation: stay on Status for standard-width recovery, interactive accessibility traversal, realtime multi-device/reconnect observation, and physical-device media before freeze.
- Report: `reports/pulsesoc_native_status_complete_design_deep_wiring_2026-07-12.md`.

## Native Feed Posts Reactive Card and Contract Correction — 2026-07-12

- Feed Posts are the active focused subsystem and are **not ready to freeze**.
- Reused the existing Home feed, post card, Post Detail, API/cache normalization, media viewer, event sync, navigation and server mutations.
- Removed fabricated reaction avatars and the dead photo-comment affordance; added production-key reaction selection, correct reaction replacement/removal reconciliation, long-text expansion, responsive image grids and persistent feed selection.
- TypeScript and the focused Feed Posts audit pass. Complete simulator state coverage, physical interaction evidence, poll/community/repost/music/link rendering, replies, owner edit/delete/analytics and controlled realtime/offline reconciliation remain open.
- Next recommendation: stay on Feed Posts for canonical post-type normalization and the missing controlled/device lifecycle matrix.
- Report: `reports/pulsesoc_native_feed_posts_complete_design_deep_wiring_2026-07-12.md`.

## Native Messenger WebView-Target Control Center Closure — 2026-07-12

- Pulse Command/Messenger is again the active focused subsystem and is **not ready to freeze**.
- Reused the existing inbox, chat, API/cache/offline queue, Pulse Command primitives, groups, rooms, Pulse AI, call stack, media viewer, deep links and notification routing.
- Added the missing native Conversation Control Center entry and production section order with search, section expansion, conversation-derived stats, transcript export, safe local-cache clearing, and Safety Hub report/block routing.
- Unsupported per-chat settings remain explicit capability boundaries rather than fake local or unenforced toggles.
- Next recommendation: stay on Messenger for production-backed preferences and remaining message actions, followed by the complete simulator and physical-device call/media matrix.
- Report: `reports/pulsesoc_native_messenger_webview_target_deep_wiring_2026-07-12.md`.

## Native Messenger Screenshot-Target Control Center Closure — 2026-07-12

- Rebuilt the native Control Center to match the supplied production dashboard and exact section hierarchy, with searchable production rows, real conversation-derived metrics, persisted device accessibility preferences, native transcript export, safe local-cache clearing, and Safety Hub routing.
- Added a visible Gear beside inbox search that restores a real recent conversation context, and made Gear the explicit active-chat entry point.
- No fake E2EE, storage, member, notification, privacy, security-session, or productivity values are shown. Missing production contracts remain visibly locked with precise explanations.
- Messenger is **not simulator-parity frozen** and cannot replace WebView Messenger yet. Production-backed per-conversation preferences and complete simulator/physical call-media-push matrices remain open.
- Report: `reports/pulsesoc_native_messenger_screenshot_target_control_center_closure_2026-07-12.md`.

## Native Reels Futuristic Design and Deep Wiring — 2026-07-12

- Reels is the active focused subsystem and is **not simulator-parity frozen**.
- Preserved production Reels feed/detail/comment/reaction/save/share/follow/music/Live/creator routes and reused the existing native player, API wrappers, media normalization, profile, Live and creator destinations.
- Comments are excluded from feed loading and remain hidden until Comment opens the native sheet. Reactions now have production-backed selection/replacement/removal states. Attached music is a micro-attribution with coordinated audio playback. Production Live records now survive native normalization and Join Live reuses `LiveDetail`.
- Remaining gaps are realtime event reconciliation, full comment ownership/moderation flows, authorized offline mutation queues, broader analytics, full simulator-width evidence, and physical playback/audio/gesture/thermal QA.
- Report: `reports/pulsesoc_native_reels_futuristic_deep_wiring_2026-07-12.md`.

## Native Home Lightweight Futuristic and Paused-Radio Redesign — 2026-07-16

- Home is **complete for the user-approved Xcode simulator mission**; physical iPhone-only evidence is deferred and not claimed.
- Removed the fabricated persistent Home mini-player and its layout/touch area; Pulse Radio now starts paused, performs no startup fetch/audio activation, and requires explicit Play.
- Added one shared native radio coordinator over the existing production catalog and play-event contracts, with connecting/buffering/playing/paused/offline/error semantics and Call/Reels/background priority.
- Composer now starts collapsed unless a real saved draft or explicit route intent exists; canonical feed, Status, media, navigation, and WebView behavior remain intact.
- Fresh Release simulator evidence passes compact, standard, iPhone 16 Pro, and Pro Max layouts. A final iPhone 16 Pro Release rerun also passed the existing-account login boundary, localhost-only authenticated Home, paused-default state, and terminate/relaunch session restoration. The controlled backend had no approved radio tracks, so explicit retry is proven but real playing is not.
- A signed, embedded Debug device artifact passes with the side-by-side identity `com.pulsesoc.nativeapp.dev` / `PulseSoc Native Dev`. Physical installation is blocked because the iPhone 16 Pro is currently unavailable to Xcode.
- The focused Home mission may move to user review. Physical install plus real radio/call/Low Power/VoiceOver evidence remains a later hardware-only gate when an iPhone is available.
- Report: `reports/pulsesoc_native_home_lightweight_futuristic_radio_paused_redesign_2026-07-16.md`.

## Native iPhone 16 Pro Installation Enablement — 2026-07-16

- The cable-connected iPhone 16 Pro is paired, available to Xcode, and running iOS 18.7.3.
- The current native build passed device compilation, automatic development signing, installation, launch, and a five-second no-immediate-crash process check.
- Side-by-side protection passed after installation: production `PulseSoc` remains `com.pulsesoc.app`; development `PulseSoc Native Dev` remains `com.pulsesoc.nativeapp.dev`.
- Physical-device testing is now enabled. Visible login, Status, permissions, media, upload, radio, backgrounding, accessibility, and interruption checks remain user-interaction gates rather than automated claims.
- Status remains the active physical-device test focus; installation does not freeze it or move work to another subsystem.
- Report: `reports/pulsesoc_native_iphone16pro_installation_2026-07-16.md`.

## Native Home Generated Concept Mapping — 2026-07-18

- Mapped the user-approved generated Home concept onto the existing native Home, composer, and global navigation primitives without creating `HomeV2` or changing backend contracts.
- Preserved Home feed, Status, Pulse Radio, composer publish/upload/draft, UNDX/Safety/Live routing, bottom-tab routing, and event-sync wiring.
- Added static atmospheric depth and larger concept-aligned proportions for the command strip, Pulse Network hero, Status rail, compact composer, feed filters, and floating dock.
- Performance guard: no animated background images, timers, or new render loops were added. The background is static layered native views that imply motion without per-frame overhead.
- iPhone 16 Pro simulator build/install/launch passed, but the current session lands on the real login screen. Home visual evidence is blocked until real-account sign-in or a safe authenticated QA path is available.
- This is **not frozen** until Xcode iPhone Simulator visual QA confirms compact/standard/Pro/Pro Max Home layouts and no clipping/regression.
- Report: `reports/pulsesoc_native_home_generated_concept_mapping.md`.

## Native UNDX Normal Messenger Conversation — 2026-07-19

- Converted the old UNDX command/form surface into the canonical native Messenger conversation path.
- Reused the production Pulse AI conversation contract from WebView: canonical conversation id `-9001001`, `/api/pulse-ai/conversation`, and `/api/pulse-ai/message`.
- Removed the standalone `Ask UNDX` input/card path from `PulseAiScreen`; the route now bridges into `ChatScreen`.
- Messenger now pins/open UNDX through the same ChatScreen, message list, bubbles, drafts, composer, keyboard behavior, and control sheet used by normal conversations.
- UNDX is text-first to match the inspected production contract. Attachment and voice controls are disabled with explicit backend boundaries instead of fake uploads.
- Audio/video call controls are hidden for UNDX, and the assistant control-center profile prevents human/group-only actions from hitting Communications V2 endpoints.
- Focused audit passes. The Xcode iPhone 16 Pro simulator now builds, installs, launches, and opens native Messenger; the pinned `UNDX` row is visible.
- Resolved unrelated conflict markers in `mobile-native/src/components/ReelPlayerCard.tsx` because they blocked Metro bundling and all simulator/physical QA.
- Physical iPhone 16 Pro (`P3r7or`) build/install/launch passes through Xcode/devicectl. Physical screenshot capture is not available through the installed `devicectl`.
- Full native typecheck is now blocked by unrelated errors in `mobile-native/src/screens/HomeScreen.tsx` and `mobile-native/src/screens/MusicScreen.tsx`.
- Manual visual proof remains required: tap UNDX on simulator or physical device, send a prompt, verify the response, and confirm the old command form is gone.
- Report: `reports/pulsesoc_native_undx_chat_conversation.md`.

## Native UNDX Real Brain Identity Pipeline — 2026-07-19

- Corrected the production assistant backend identity from the legacy public `Pulse AI` persona to canonical `UNDX` while preserving legacy `/api/pulse-ai/*` routes and `pulse_ai_*` tables for compatibility.
- Reused the same production conversation, message persistence, provider routing, web-search, safety, feedback, and memory code paths instead of creating a native-only assistant backend.
- Added server-owned UNDX identity constants: name `UNDX`, agent id `undx`, assistant id `undx`, participant id `-9001001`, and conversation type `undx_intelligence`.
- Replaced the core provider system prompt with UNDX as PulseSOC's AGI-class digital intelligence companion and added a server-side anti-drift instruction so providers do not identify as Pulse AI.
- Added backend response enforcement before persistence so identity questions and legacy-provider text cannot store `Pulse AI` as the assistant identity.
- Updated native `sendPulseAiMessage` to include canonical UNDX metadata while keeping the server authoritative.
- Added `scripts/pulsesoc_undx_identity_backend_audit.py`; focused backend/native identity audits, Python compile, `npm ci`, native typecheck, Expo Doctor, and `git diff --check` pass.
- Xcode iPhone Simulator build/install exited successfully after dependency refresh. Visual prompt-response proof remains blocked: the Debug app first redboxed with `No script URL provided`; Metro was started, but the follow-up simulator screenshot/relaunch step was rejected by the environment escalated-action usage limit.
- Physical iPhone verification for this exact identity response remains blocked by the same escalated-action usage limit. Prior UNDX work already proved physical build/install/launch, but not this final identity prompt-response.
- Report: `reports/pulsesoc_undx_real_brain_identity_pipeline.md`.

## Native Persistent Radio and Home Reselect — 2026-07-19

- Converted PulseSoc Radio into a persistent native player coordinated by `mediaPlaybackCoordinator` instead of a screen-owned Music player.
- Added iOS background audio configuration in Expo and native Info.plist, and configured the radio audio session to remain active in background with no app-media ducking.
- Preserved explicit user playback intent across call, voice-message, Reel, Status, feed-video, Live, viewer, and preview interruptions; radio resumes only when the higher-priority owner releases and the user did not manually pause.
- Stopped muted Reels, Status videos, and feed videos from unnecessarily claiming audio ownership, so silent video playback does not interrupt Pulse Radio.
- Added an active-Home bottom-tab reselect handler that scrolls Home to the top and performs one guarded refresh through the existing feed/status loading path.
- `npm ci`, native typecheck, Expo Doctor, Jest, focused persistent-radio audit, and `git diff --check` pass.
- Xcode iPhone Simulator launch evidence was captured on the booted PulseSoc iPhone 16 Pro. Home visual reselect proof remains code-path/audit verified in this run because the deep-link attempt opened a web surface.
- Physical devices were detected by Xcode but all were offline, so lock-screen/background/Bluetooth/call-interruption hardware proof remains physical-device-only release QA.
- Report: `reports/pulsesoc_native_persistent_radio_home_reselect_2026-07-19.md`.

## Native Live WebRTC Guest Playback and Host Audio Repair — 2026-07-20

- Repaired the native Live Detail transport decision so WebRTC/LiveKit lives no longer fall into the generic `Playback fallback required` state when the backend exposes a native `webrtc_room_id`, `supports_webrtc`, or `livekit.room`.
- Reused the existing `/api/pulse/live/<id>/livekit/token` production route with role `viewer` for native guest playback; HLS via Expo video remains the fallback when a real playback URL exists.
- Extended the shared `useLiveBroadcastRoom` hook to track local audio publications, remote audio/video counts, and native remote-audio enable/disable for viewer sound control.
- Added a host safety gate: native Live publishing now fails with `LIVE_LOCAL_AUDIO_NOT_PUBLISHED` if the microphone was enabled but no local audio publication is visible, preventing a silent “successful” broadcast.
- Added focused Jest coverage and a scoped static audit for the WebRTC viewer and host-audio repair.
- Physical two-client validation remains required before release confidence can be raised: host on physical iPhone, second client as guest/viewer, audible microphone, mute/unmute, speaker/Bluetooth, and background behavior.
- Report: `reports/pulsesoc_native_live_webrtc_guest_audio_repair_2026-07-20.md`.
