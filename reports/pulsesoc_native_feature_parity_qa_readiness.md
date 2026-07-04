# PulseSoc Native Feature Parity + QA Readiness

Date: 2026-07-04

## Executive Summary

PulseSoc Native now has broad first-pass native coverage across the core social, media, creator, premium, growth, and intelligence surfaces. The current production WebView app remains the fully verified production client. The native app is a parallel client that reuses the existing PulseSoc backend, database, authorization, moderation, media, notification, premium, growth, creator, intelligence, and payment/provider flows.

The native app is not ready to replace the WebView app yet. The main blocker is not lack of code breadth; it is QA validation. The project has many native foundations, but browser/device validation is blocked in the current environment by missing Expo web dependencies, missing Android tooling, and missing iOS simulator tooling.

Recommended next action: fix QA tooling and run real device/simulator QA before adding another major module.

Each parity row documents native status, Web parity level, reusable backend/API coverage, remaining gaps, device-only QA needed, risk level, and recommended fix order.

## QA Tooling Blockers

Current verification found these exact blockers:

- Expo web browser QA is blocked. `npx expo start --web --port 8094` fails because the project does not install `react-native-web@~0.19.10`, `react-dom@18.2.0`, or `@expo/metro-runtime@~3.2.3`.
- Android device/emulator QA is blocked. `adb` is not available in `PATH`; adb is not available in the current shell.
- iOS simulator QA is blocked. `/usr/bin/xcrun` exists, but `xcrun simctl list devices available` fails with `xcrun: error: unable to find utility "simctl", not a developer tool or in PATH`.
- Physical device QA flow is not established in this workspace. There is no recorded Expo Go/dev-client QR scan, EAS development build, USB device, simulator, or device log capture path.
- Real push notification QA is blocked until a physical device or simulator/device flow is available.
- Lock-screen, background audio, camera, microphone, Bluetooth, media picker, native notification permission, and LiveKit behavior cannot be honestly marked verified without device/simulator access.

Do not mark QA browser/device behavior as verified until one of those flows is working and has been exercised.

## Parity Matrix

| Area | Native status | Web parity level | Reusable backend/API coverage | Remaining gaps | Device-only QA needed | Risk | Fix order |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Auth/session | Native foundation implemented | High for login/signup/session basics | `/api/pulse/mobile/auth/session`, login, register, refresh, logout, existing account/session rules | Email confirmation, recovery/reset, all edge cases need device walkthrough | Session restore after force quit, expired cookies, background/resume | Medium | 2 |
| Messenger | Native foundation plus hardening | Medium-high | Existing Messenger APIs, media upload paths, receipts, typing, sync polling, backend rules | Long-thread performance, attachment edge cases, realtime vs polling parity | Long scrolling, background recovery, push deep link, image/file/voice permissions | High | 3 |
| Notifications | Native foundation | Medium-high | `/api/push/subscribe`, `/api/pulse/notifications`, preferences, unread counts, resolve/read/delete | Real APNs/FCM behavior, lock-screen presentation, sounds/badges | Permission accept/deny, foreground/background tap, badge sync | High | 1 |
| Home Feed | Native foundation | Medium | `/api/pulse/feed`, feed ranking, visibility, moderation, reactions/saves/reposts | Advanced feed filters, ads, hidden/blocked/report flows may be partial | Scroll performance, pull refresh, offline restore | Medium | 4 |
| Post Detail | Native foundation | Medium-high | Post detail/comment/reaction/save/repost/share APIs | Rich embeds, unsupported media, moderation/report edge cases | Deep-link open, comment keyboard behavior, media viewer handoff | Medium | 4 |
| Feed Composer | Native foundation | Medium | Post creation, media upload, moderation, visibility, processing status | Advanced composer options, polls, tags, scheduling, creator tools fallback | Camera/gallery permissions, upload failure/retry/cancel | High | 5 |
| Profile | Native foundation | Medium-high | Profile APIs, public/current profile, avatar/cover/theme, profile posts | Full social graph views, privacy edge cases, verification/account settings depth | Avatar/cover upload, cache restore, owner/public navigation | Medium | 4 |
| Reels | Native foundation | Medium | Reels feed/detail/comment/reaction/save/repost/share/view/follow/report APIs, Mux/R2 URLs | 60fps device validation, audio policy, memory management, unsupported media fallback | Vertical swipe, buffering, resume, audio/mute, long session memory | High | 6 |
| Status Viewer | Native foundation | Medium | Status rail/detail/view/react/reply/share/music APIs | Full story interaction polish, unsupported media handling | Tap/press navigation, auto-advance, video/audio, background recovery | High | 6 |
| Status Creator | Native foundation | Medium | Status create, media upload, music search/trending, AI story APIs | Drawing/text effects, advanced editing, upload compression parity | Camera/gallery, privacy selector, publish retry/cancel | High | 5 |
| Media Upload | Shared native foundation | Medium | `/api/pulse/media/upload`, processing polling, media pipeline, authorization | Native compression tuning, large uploads, background uploads | Permissions, large files, cancellation, retry, memory pressure | High | 2 |
| Media Viewer | Shared native foundation | Medium | Existing media URLs, processing status, Mux/R2/first-party routes | Gesture polish, video controls, unsupported document types | Pinch/zoom, swipe close, next/previous, video playback | Medium-high | 6 |
| Marketplace | Native browse/detail foundation | Medium | Marketplace search/listing/save/report/media/seller APIs, checkout fallback | Listing creation/edit, order/payment flows remain web/provider fallback | Media gallery, save/report, checkout handoff, deep links | Medium | 7 |
| Search/Discovery | Native foundation | Medium | `/api/pulse/search`, discovery/ranking/moderation/privacy | Some categories route through fallback; advanced filters incomplete | Debounced search responsiveness, offline cache, deep links | Medium | 7 |
| Saved/Collections | Native foundation | Medium-high | Saved/bookmark/collection APIs and existing item ownership rules | Edge cases for unsupported saved item types | Offline cache, search/filter, move/remove actions | Medium | 8 |
| Groups/Communities/Rooms | Native foundation | Medium | Groups/communities/rooms APIs, membership, roles, moderation, Messenger integration | Admin/moderation, invites, group creation/editing likely partial | Join/leave, group feed, room routing, chat handoff | Medium-high | 8 |
| Live Viewer | Native discovery/viewer foundation | Low-medium | `/api/pulse/live-now`, live state/join/chat/react, Mux/LiveKit playback state | Native playback not device verified; Go Live/Studio/hosting/co-hosting intentionally fallback | Playback, chat send, reactions, foreground/background recovery | High | 9 |
| Premium/Entitlements | Native foundation | Medium-high | `/api/premium/status`, checkout/billing portal, economy state, Stripe/provider handoff | In-app purchase/native commerce not implemented; provider handoff needs QA | Billing portal return/resume, deep links, premium badge display | Medium-high | 9 |
| Creator Studio | Native foundation | Medium | `/api/dashboard/creator/state`, Creator AI hooks, content planner, premium gates | Advanced studio, analytics depth, monetization/live studio fallback | Navigation to composer/status/reels/profile/premium, fallback handoff | Medium | 10 |
| Growth Center | Native foundation | Medium | `/api/pulse/growth`, growth engine, wallets, ads/promotions, premium/eligibility | Campaign launch, targeting, billing, wallet funding stay web/provider fallback | Promote shortcuts, web fallback return/resume | Medium-high | 10 |
| Intelligence/Alerts | Native foundation | Medium | `/api/dashboard/intelligence/state`, `/api/crypto/alerts`, alert engine, notifications, premium gates | Alert create/edit/history advanced operations fallback; no local evaluation | Notification deep links, alert detail, badge sync, fallback handoff | Medium-high | 10 |
| Settings | Native foundation | Medium | Push registration, notification preferences, session logout | Account/security/language/verification settings mostly web/backend not fully native | Permission flows, logout/session restore | Medium | 3 |
| Deep links | Broad static coverage | Medium | `mobile-native/src/navigation/linking.ts`, notification router, existing backend targets | Unsupported targets fall back to web; untested on real app install | Custom scheme, universal links, cold start, notification tap | High | 1 |
| Push notifications | Native foundation | Medium | Expo token registration, Pulse notification APIs, preferences, backend delivery | Real APNs/FCM, lock-screen, sounds, badges not verified | Permission accept/deny, foreground/background/cold-start | High | 1 |
| Offline/cache behavior | Shared patterns across many screens | Medium | `mobile-native/src/core/cache.ts`, feature-specific cache wrappers | Cache corruption, stale-data strategy, eviction policy, large-list behavior | Airplane mode, app restart, stale/refresh recovery | Medium-high | 2 |
| Real-device readiness | Not ready | Low | Expo native dependencies, iOS/Android config, permissions declared | QA tooling unavailable; no physical device or simulator run captured | Full app smoke, performance, media, push, camera, LiveKit | Critical | 0 |

## Routing And Deep-Link Coverage

Native route coverage includes:

- Tabs: Home, Search, Saved, Groups, Live, Reels, Status, Messenger, Notifications, Pulse AI, Profile, Marketplace, Settings.
- Stack routes: Chat, Post Detail, Reels/Reel Detail, Status Detail, Marketplace Detail, Search, Saved, Group Detail, Live Detail, Profile Detail, Profile Edit, Premium, Creator Studio, Growth Center, Intelligence Center, Notification Center, Notification Preferences.
- Linking paths cover `/pulse`, `/pulse/search`, `/pulse/saved`, `/pulse/groups`, `/pulse/live`, `/pulse/reels`, `/pulse/status`, `/pulse/messages`, `/pulse/notifications`, `/pulse/profile`, `/pulse/marketplace`, `/pulse/post/<id>`, `/pulse/reels/<id>`, `/pulse/status/<id>`, `/pulse/marketplace/<id>`, `/pulse/premium`, `/pulse/creator-studio`, `/pulse/growth`, and `/dashboard/intelligence/<subsystem>`.
- Notification routing handles Messenger, Posts, Search, Saved, Groups, Live, Reels, Status, Profile, Premium, Creator Studio, Growth, Intelligence, Crypto Alerts, Marketplace, Notifications, and safe web fallback.

Deep-link risk remains high until custom scheme and universal links are tested on real devices from cold start, background, foreground, and logged-out states.

## Backend Reuse Assessment

Backend/API reuse is strong. The native app is consistently a client over existing PulseSoc APIs rather than a duplicated platform.

Reuse patterns observed:

- API wrappers under `mobile-native/src/api`.
- Shared cache under `mobile-native/src/core/cache.ts`.
- Shared native media upload under `mobile-native/src/media`.
- Shared cards/viewers under `mobile-native/src/components`.
- Web fallback for unsupported or policy-sensitive operations.
- Server-authoritative handling for auth/session, feed ranking, moderation, permissions, premium entitlements, billing, growth, intelligence, alert delivery, media processing, marketplace rules, and LiveKit/Mux state.

The main architectural gap is not backend reuse. It is device validation and parity hardening.

## Recommended Fix Order

0. Establish device/simulator QA tooling.
1. Verify push notifications and deep links on a physical device or simulator.
2. Verify auth/session restore, offline cache, and media upload permissions.
3. Harden Settings and Messenger because they exercise session, notifications, and communication reliability.
4. Harden Home Feed, Post Detail, and Profile.
5. Harden Feed Composer, Status Creator, and media upload.
6. Harden Reels, Status Viewer, and Media Viewer performance.
7. Harden Marketplace and Search.
8. Harden Saved and Groups/Rooms.
9. Harden Live Viewer and Premium/provider return flows.
10. Harden Creator Studio, Growth Center, and Intelligence/Alerts fallback flows.
11. Only after these pass, reassess native LiveKit hosting/calls and advanced camera/editor work.

## Release Readiness

Current status: not ready to replace WebView.

Reasons:

- No end-to-end real-device QA has been completed for the complete native surface.
- Push, lock-screen, background, camera, microphone, media picker, and LiveKit behavior are device-sensitive and unverified.
- Several major features intentionally use web fallback for advanced or provider-sensitive flows.
- Performance claims such as 60fps Reels/Status/Live cannot be confirmed without device profiling.

Safe release posture:

- Continue parallel native development.
- Keep production WebView app live.
- Do not submit or promote native replacement until device QA passes login, messaging, notifications, media upload, Reels/Status smoothness, Live viewer stability, deep links, and no major feature regressions.

## Mandatory Next Action Recommendation

Recommended next action: device QA setup, not another feature build.

The highest-value next mission should establish at least one working QA path:

- iOS simulator: install/select full Xcode developer tools so `xcrun simctl` works, then run `npm run ios` or an Expo dev-client flow.
- Android emulator/device: install Android platform tools so `adb` works, then run `npm run android`.
- Physical device: define Expo Go or EAS development build flow, scan/run the app, and capture device logs.
- Browser QA: only add `react-native-web`, `react-dom`, and `@expo/metro-runtime` if the team intentionally wants Expo web as a QA surface. Since this app is native/hybrid-native, device QA is the more important blocker.

Do not add another major native feature until at least one device QA route is working and the core smoke path is tested.
