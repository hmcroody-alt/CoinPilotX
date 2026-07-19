# PulseSoc Native Camera Studio Media QA Automation

Date: 2026-07-05

## Scope

This mission did not build LiveKit calls and did not add a major user-facing feature.

The goal was to finish the simulator-verifiable portion of Camera Studio media QA after authenticated simulator access was unblocked. Production auth, production WebView routes, and backend business logic were not changed.

## Automation Path Chosen

Available local tooling:

- `xcodebuild`: available.
- `xcrun simctl`: available.
- `xcrun simctl addmedia`: available and used.
- `xcrun simctl privacy`: available and used for microphone/photo-library.
- `cliclick`: available but not reliable for Simulator app touch input.
- Maestro: not installed.
- Appium: not installed.
- Detox: not installed.

Selected path:

- Seed simulator media with `xcrun simctl addmedia`.
- Add a QA-only Camera Studio media injection path for simulator/local QA.
- Keep the existing server-authoritative upload, preview, and publish APIs.
- Keep the QA path disabled outside development native builds and localhost API bases.

Rejected for this pass:

- Production backend seeded session endpoint.
- Production auth bypass.
- Production WebView route changes.
- New Appium/Detox/Maestro dependency installation.

## QA-Only Safety Boundary

The media automation is only active when the existing simulator QA auth boundary is active:

- `__DEV__` must be true.
- Platform must not be web.
- `EXPO_PUBLIC_PULSE_API_BASE_URL` must resolve to localhost, `127.0.0.1`, or `::1`.
- The app still authenticates through `/api/mobile/auth/login`.
- Upload still uses `/api/pulse/media/upload`.
- Preview still uses `/api/pulse/camera/preview`.
- Feed publish uses the canonical production route `/api/pulse/posts`.
- Status publish still uses existing Status APIs.
- Reel publish uses the canonical production route `/api/pulse/reels/create`.

No production WebView route was touched.

## Simulator Evidence

Simulator:

- iPhone 17 Pro
- UDID: `7B3BEEBC-6135-497D-91CD-A3E70C927D56`
- Native app identity: `com.pulsesoc.nativeapp`
- Local QA backend: `http://127.0.0.1:5107`
- Metro API base: `EXPO_PUBLIC_PULSE_API_BASE_URL=http://127.0.0.1:5107`

Screenshots:

- `/tmp/pulsesoc-media-qa-01-launch.png`
- `/tmp/pulsesoc-media-qa-02-feed-preview.png`
- `/tmp/pulsesoc-media-qa-03-feed-publish.png`
- `/tmp/pulsesoc-media-qa-05-status-publish-after-fix.png`
- `/tmp/pulsesoc-media-qa-06-reel-publish.png`
- `/tmp/pulsesoc-media-qa-07-foreground-recovery.png`
- `/tmp/pulsesoc-media-qa-09-safe-area-final.png`

Local backend evidence:

```text
chat_media_uploads: 4
pulse_camera_captures: 4
pulse_posts: 1
pulse_status: 2
pulse_reels: 1
```

Detailed records:

```text
media: [(1, 'image', 'image/png'), (2, 'image', 'image/png'), (3, 'image', 'image/png'), (4, 'image', 'image/png')]
captures: [(1, 'feed', 'photo'), (2, 'status', 'photo'), (3, 'status', 'photo'), (4, 'reel', 'video')]
post: (1, 'image', 'PulseSoc Camera simulator QA feed', '[1]')
status: [(1, 'photo', 'PulseSoc Camera simulator QA status publish'), (2, 'photo', 'PulseSoc Camera simulator QA status publish')]
reels: [(1, 'PulseSoc Camera simulator QA reel publish')]
```

## Verified

- Authenticated simulator Camera Studio access through the QA-only login deep link.
- Simulator media library seeding through `xcrun simctl addmedia`.
- QA media selection without unreliable touch automation.
- Preview/selected-media state in Camera Studio.
- Caption propagation into Camera Studio.
- Feed destination upload handoff.
- Feed destination publish routing to native Post Detail.
- Status destination upload handoff.
- Status destination publish routing to native Status viewer.
- Reel destination upload handoff.
- Reel destination publish routing to native Reels viewer.
- Camera config loading with provider `native_fallback`.
- Microphone permission grant/revoke through `xcrun simctl privacy`.
- Photo-library permission grant/revoke through `xcrun simctl privacy`.
- Foreground/background recovery through terminate/relaunch with authenticated session restored to Home.
- LogiNexus visual quality hardening: Camera Studio top controls and permission copy now clear the iPhone 17 Pro Dynamic Island/status area.

## Fixed During QA

- Camera Studio route param changes now update the active destination when the same screen instance receives new QA deep-link params.
- QA autopublish keys now include destination and caption context so repeat QA runs can publish multiple destinations.
- Camera Studio top controls moved below the iOS status/Dynamic Island area.
- Camera permission fallback text gained iOS top padding so it no longer sits under the Dynamic Island.

## Not Verified

- Real native gallery picker UI selection by touch. `cliclick` did not reliably affect the Simulator app surface, so QA media injection was used instead.
- Upload cancel/retry. The injected image upload is too small to reliably interrupt; this needs a larger media fixture, network throttling, or a dedicated upload-test harness.
- Real camera permission allow/deny prompt. This `simctl privacy` build does not expose a camera service.
- Real camera capture.
- Real microphone recording.
- Front/back camera hardware switching.
- Real video compression.
- Large image/video memory pressure.
- Physical iPhone behavior.
- Physical Android behavior.

## Next Recommendation

Do not move to Native LiveKit calls yet.

The next highest-value action is physical iPhone and Android Camera Studio QA, with a larger video fixture or network-throttled upload test for retry/cancel. LiveKit calls depend on the same camera/microphone/device-permission layer plus push/ringing/background behavior, so they should remain deferred until physical media QA is credible.
