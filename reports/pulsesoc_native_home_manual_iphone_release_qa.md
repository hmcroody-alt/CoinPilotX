# PulseSoc Native Home Manual iPhone Release QA

Date: 2026-07-09

## Status

Manual iPhone Home release QA was attempted, but no manual screen recording, QuickTime capture, screenshots, or human-operated tap-through evidence was produced in this workspace.

This report does not claim Home physical interaction passed. It records what was verified with connected-device tooling and what still requires a real manual iPhone pass or a QA-only UI automation target.

## Scope

Mission target:

- Use the connected iPhone.
- Use the installed `com.pulsesoc.nativeapp`.
- Verify Home manually with screen evidence.
- Do not focus on Android.
- Do not add Home features.
- Preserve production WebView paths.

## Device Target

- Device: iPhone 16 Pro
- Device name: P3r7or
- OS: iOS 18.7.3
- UDID: `00008140-000E2D9A2EE8801C`
- Native app identity: `com.pulsesoc.nativeapp`
- Production app identity preserved: `com.pulsesoc.app`
- Native route tested: `pulsesoc://pulse`

## Tooling Evidence Collected

### Device Availability

```text
xcrun xctrace list devices
P3r7or (18.7.3) (00008140-000E2D9A2EE8801C)
```

```text
xcrun devicectl device info displays --device 00008140-000E2D9A2EE8801C
Current Displays:
LCD (primary)
bounds: (0.0, 0.0, 1206.0, 2622.0)
Main display backlight state: backlight is on and active
Main display orientation: portrait
```

### App Launch

```text
xcrun devicectl device process launch --device 00008140-000E2D9A2EE8801C --terminate-existing --payload-url 'pulsesoc://pulse' com.pulsesoc.nativeapp
Launched application with com.pulsesoc.nativeapp bundle identifier.
```

Result: Passed at process/deep-link dispatch level.

### Running Process

```text
xcrun devicectl device info processes --device 00008140-000E2D9A2EE8801C
PulseSocNative.app/PulseSocNative
```

Result: Passed. The native app was present in the physical iPhone process list after launch.

### Background / Foreground Process Recovery

```text
xcrun devicectl device process suspend --device 00008140-000E2D9A2EE8801C --pid 22240
Signal to suspend process sent to pid 22240

xcrun devicectl device process resume --device 00008140-000E2D9A2EE8801C --pid 22240
Sent signal to resume process sent to pid 22240
```

Result: Passed at process level. This is not the same as a visual manual background/foreground Home recovery pass.

### Screen Evidence Attempt

```text
idevicescreenshot reports/screenshots/home_manual_iphone/home_release_attempt_<timestamp>.png
Could not start screenshotr service: Invalid service
Remember that you have to mount the Developer disk image on your device if you want to use the screenshotr service.
```

`xcrun devicectl device info ddiServices` reported DDI services usable, but `idevicescreenshot` still could not start the screenshot service. No screenshot or video file was produced.

### Syslog Attempt

A short filtered `idevicesyslog` capture was attempted after app launch, but it did not produce useful Home-specific excerpts in the capture window.

## Manual QA Matrix

| Area | iPhone result | Evidence |
| --- | --- | --- |
| App launch | Passed at process level | `devicectl` launch succeeded |
| Login/session restore | Not manually verified | No screen recording or manual tap-through evidence |
| Home open | Passed at route dispatch/process level | `pulsesoc://pulse` launch succeeded |
| Feed scroll | Not manually verified | No touch/screen evidence |
| Pull refresh | Not manually verified | No touch/screen evidence |
| Text post publish | Not manually verified on iPhone | Browser-visible publish proof exists, but no physical iPhone recording |
| Hide persistence | Not manually verified on iPhone | Browser-visible server-authoritative proof exists, but no app restart/tap recording |
| Mute persistence | Not manually verified on iPhone | Browser-visible server-authoritative proof exists, but no app restart/tap recording |
| Comment submit | Not manually verified on iPhone | Browser-visible semantic path proof exists, but no physical iPhone tap recording |
| Media card open | Not manually verified on iPhone | No media tap recording |
| Profile route | Not manually verified on iPhone | No route tap recording |
| Activity route after Home action | Not manually verified on iPhone | No action recording |
| Notification route | Not provider verified | No APNs/Expo push delivery or tap evidence |
| Background to foreground recovery | Passed at process level only | `devicectl suspend/resume` succeeded |
| Cold start to Home recovery | Passed at process/deep-link dispatch level only | `--terminate-existing --payload-url pulsesoc://pulse` succeeded |
| Basic accessibility/tap target check | Not manually verified on iPhone | No VoiceOver/manual tap evidence |
| Performance feel | Not manually verified | No manual scroll/tap observation |

## What Passed

- Connected iPhone is available and active.
- Installed native QA app can launch.
- Home route can be dispatched through `pulsesoc://pulse`.
- Native process remains visible after launch.
- Process-level suspend/resume works.
- Existing source contracts remain in place for Home refresh, push registration, notification routing, server-authoritative hide/mute, and accessible comment submit.

## What Failed Or Remains Blocked

- `idevicescreenshot` cannot capture physical iPhone screenshots in this environment.
- No iPhone Control Center screen recording path was provided.
- No QuickTime iPhone recording path was provided.
- No manual tap-through evidence was captured.
- No provider-backed push notification was delivered and tapped.
- No manual VoiceOver/accessibility pass was captured.

## Required Evidence To Mark Home Release-Complete

Home still needs one human-captured or UI-automated iPhone pass showing:

1. Launch app.
2. Confirm login/session restore.
3. Open Home.
4. Scroll feed.
5. Pull refresh.
6. Publish a safe text post if practical.
7. Hide a card, restart app, confirm it stays hidden.
8. Mute a user, restart app, confirm muted content stays removed.
9. Open Post Detail and submit a comment through the visible semantic button path.
10. Open a media card.
11. Open a profile from feed.
12. Open Activity after a Home action.
13. Tap a provider-backed notification into Home or Post Detail if provider access is available.
14. Background and foreground the app while Home is loaded.
15. Cold start into Home.
16. Check basic tap targets and VoiceOver labels.
17. Record performance feel during scroll/refresh.

## Release Decision

Home remains foundation-complete and browser-verified. Home is not release-complete because manual iPhone interaction and push/tap behavior are not proven with screen evidence.

Can Home now be considered release-complete?
NO
