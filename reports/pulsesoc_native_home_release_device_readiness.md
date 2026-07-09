# PulseSoc Native Home Release Device Readiness

Date: 2026-07-09

## Scope

This pass focused only on Home release-device readiness. It did not add Home features, start final UI/UX polish, focus on Android, or touch production WebView paths.

PulseSoc Native is still the replacement path for the current WebView PulseSoc app through a normal app update. This report separates process-level device proof from manual release QA that still requires on-device observation or provider-backed push testing.

## Device And Build Context

- Device: iPhone 16 Pro
- Device name: P3r7or
- OS: iOS 18.7.3
- Hardware model: D93AP / iPhone17,1
- Native app identity: `com.pulsesoc.nativeapp`
- Deep-link scheme: `pulsesoc://`
- QA priority: iPhone/iOS only

Device tooling evidence:

```text
xcrun devicectl list devices
P3r7or ... available (paired) ... iPhone 16 Pro (iPhone17,1)
```

```text
xcrun devicectl device info details --device 00008140-000E2D9A2EE8801C
developerModeStatus: enabled
ddiServicesAvailable: true
name: P3r7or
```

## Verified This Pass

### Physical iPhone Home Launch

Verified at process level.

```text
xcrun devicectl device process launch --device 00008140-000E2D9A2EE8801C --terminate-existing com.pulsesoc.nativeapp
Launched application with com.pulsesoc.nativeapp bundle identifier.
```

Result: Passed. The installed native app launches on the connected iPhone.

### Home Deep Link Dispatch

Verified at process level.

```text
xcrun devicectl device process launch --device 00008140-000E2D9A2EE8801C --terminate-existing --payload-url 'pulsesoc://pulse' com.pulsesoc.nativeapp
Launched application with com.pulsesoc.nativeapp bundle identifier.
```

Result: Passed. The native app accepts the Home route payload without process-level failure.

### Native Routing/Readiness Contracts

Verified statically:

- `mobile-native/app.json` keeps the QA native identity at `com.pulsesoc.nativeapp`.
- `mobile-native/app.json` keeps `pulsesoc` as the native deep-link scheme.
- Home is mapped to `pulsesoc://pulse` in the React Navigation linking config.
- Notification response routing is installed through `setupNotificationResponseRouting()`.
- Push registration remains physical-device-aware and posts tokens through `/api/push/subscribe`.
- Home still exposes pull refresh, server-authoritative hide, and server-authoritative mute handlers.
- Post Detail keeps the semantic, QA-addressable comment submit path.

## Device Readiness Matrix

| Area | Current result | Evidence | Release implication |
| --- | --- | --- | --- |
| Physical iPhone Home launch | Passed at process level | `devicectl` launch succeeded | App/device path is available |
| Home deep link | Passed at process level | `pulsesoc://pulse` launch succeeded | Home route can be dispatched |
| Home feed scrolling | Not manually verified this pass | No screen/touch evidence captured | Needs manual iPhone QA |
| Home refresh | Browser verified previously; not manually iPhone verified this pass | Source uses `RefreshControl` | Needs manual iPhone QA |
| Home post publish | Browser-visible verified previously; not manually iPhone verified this pass | Home publish report and source contracts | Needs manual iPhone QA before release signoff |
| Home notification tap routing | Not provider/device verified | Routing code exists; push provider delivery not exercised | Release blocker |
| Foreground/background recovery | Not manually verified this pass | App launch and deep link process proof only | Release blocker |
| Push/tap behavior | Not provider verified | Push API and notification response routing exist | Release blocker |
| Media card behavior | Browser QA verified previously; not manually iPhone verified this pass | NativeMediaViewer and feed routing exist | Needs manual iPhone media pass |
| Comment submit accessibility | Browser-visible verified; source guard exists | `post-detail-comment-input`, `post-detail-submit-comment` | Needs broader device accessibility pass |
| Hide/mute persistence after app restart | Browser-visible verified previously; not manually iPhone verified this pass | Server persistence and feed filtering exist | Needs manual iPhone restart proof |
| Basic accessibility pass | Partial only | Comment submit path hardened | Broader Home controls still need pass |
| Performance feel on iPhone | Not manually verified this pass | Launch succeeded; no touch/scroll recording | Needs manual iPhone QA |

## Push/Tap Readiness

Current readiness is structural, not provider-proven.

Ready pieces:

- `expo-notifications` is configured in the native app.
- `registerPushDevice()` requests physical-device permissions.
- The native app subscribes push tokens through `/api/push/subscribe`.
- Notification response routing handles deep links into Home, Post Detail, Activity, Messages, Calls, Marketplace, and other native surfaces.

Still missing:

- Provider-backed APNs/Expo push delivery proof on the connected iPhone.
- Lock-screen tap proof.
- Foreground notification tap proof.
- Background/cold-start notification tap proof.
- Duplicate/stale notification replay proof on device.

## Background Recovery

Current readiness is not release-proven.

Known good:

- The app launches.
- The Home route dispatches at process level.
- Existing browser QA verified Home feed refresh, publish, hide, mute, and cursor-visible events.

Still required:

- Manual iPhone foreground/background transition while Home is loaded.
- Manual iPhone app restart after hide/mute and feed refresh.
- Manual iPhone post-publish recovery if the app backgrounds during or after publish.
- Provider-backed notification tap into Home/Post Detail from background.

## Accessibility Readiness

Current readiness is partial.

Completed:

- Comment input has a stable QA selector.
- Comment submit has a semantic button role, label, disabled state, busy state, and stable QA selector.

Still required:

- Home hero buttons accessibility labels and focus order review.
- Status rail focus order.
- Composer action labels for Photo, Video, Music, Feeling, Location, Mention, Topic, Audience, and Publish.
- Feed card action labels for Like, Comment, Save, Share, Report, Hide, Block, and Mute.
- Dynamic type and screen-reader pass on iPhone.

## What Was Not Claimed

This pass does not claim:

- physical Home feed scrolling passed
- physical Home publish passed
- push/tap passed
- background recovery passed
- physical Home interaction passed
- Home release-complete

The available tooling can launch and deep-link the app, but it cannot prove touch-level Home behavior or provider push delivery by itself.

## Next Release QA Steps

1. Run a manual iPhone Home pass with screen recording:
   - launch Home
   - scroll feed
   - pull refresh
   - open a media card
   - publish a safe text post
   - hide and mute, then restart app and confirm persistence
   - open Post Detail and submit a comment through the semantic button
2. Run provider-backed push tests:
   - foreground notification
   - background notification
   - lock-screen tap
   - cold-start notification tap into `/pulse` and `/pulse/post/<id>`
3. Run a basic iPhone accessibility pass:
   - VoiceOver labels
   - focus order
   - dynamic text sanity

## Status

Home is foundation-complete and browser-release blockers are closed. Home is not release-complete until the remaining manual iPhone and provider-backed push/tap/background checks pass.

The key remaining evidence gap is manual on-device Home interaction plus provider-backed push delivery.

Can Home now be considered release-complete?
NO
