# PulseSoc Native Home Publishing Proof & Foundation Completion

Date: 2026-07-08

## Scope

This pass stayed focused on the native Home foundation. It did not add a new subsystem, did not start UI polish, did not touch Android, and did not change production WebView routes.

## Completed Foundation Work

- Added stable QA handles to Home Composer input, character counter, mode controls, publish, retry, recovered draft, clear draft, photo, video, and status panels.
- Preserved server-authoritative publishing through the existing `/api/pulse/posts` contract.
- Preserved shared media upload handoff through the existing native upload hook and backend media pipeline.
- Preserved draft persistence, recovered-draft UI, retry state, upload queue metadata, success reset, and feed invalidation from the previous Home publishing hardening pass.

## Server-Authoritative Publish Proof

Validated against a disposable local QA backend:

- Local QA account login returned authenticated session.
- Text-only publish returned `ok=true` and `post_id=1`.
- Home feed query returned the newly published post.
- `/api/pulse/sync/events` returned successfully after publish.

No QA credentials were committed or written into reports.

## Visible QA Status

The built-in QA browser was opened visibly and the browser skill was used. The browser-control channel then timed out repeatedly when reading the selected tab or navigating, so the final visible publish walkthrough could not be completed honestly in this pass.

After shutting down the QA web server, Metro also surfaced dependency-resolution errors during the attempted web run:

- `Unable to resolve "expo-modules-core" from "node_modules/expo/src/Expo.ts"`
- `Unable to resolve "nullthrows" from "node_modules/react-native-web/dist/vendor/react-native/VirtualizedList/index.js"`

Static package install, TypeScript, and Expo Doctor still passed, so this is tracked as a QA-browser/web bundling blocker rather than a Home publishing contract failure.

Already visible from the prior Home walkthrough:

- Authenticated Home.
- Pulse Network hero.
- Status rail.
- Pulse Composer.
- Post/Reel/Live modes.
- Publishing controls.
- Feed tabs and feed cards.

Not completed visibly in this pass:

- Type a real text post while Roody watches.
- Refresh/reload and show automatic draft recovery.
- Continue editing the recovered draft.
- Publish from the visible composer.
- Show composer reset and draft disappearance after success.
- Show feed refresh in the browser after publish.
- Show Activity/Notifications invalidation in the visible browser.

## Error Recovery

Implemented and statically audited:

- Empty publish validation.
- Retry state after failed server publish.
- Draft retention after failure.
- Upload-in-flight publish blocking.

Still needs visible browser proof:

- Trigger server validation failure visibly.
- Retry visibly.
- Simulate offline/reconnect visibly if the QA browser supports it.

## Media Handoff

Implemented and statically audited:

- Photo and video actions use the shared native media upload hook.
- Upload preview/retry/cancel remains delegated to `MediaUploadPreview`.
- Reel mode requires video media or Camera Studio handoff.
- Live mode remains the existing safe Live Studio handoff.

Device QA required:

- Native camera permission prompts.
- Native microphone permission prompts.
- Physical photo/video capture.
- Large video upload.
- Native gallery picker behavior.

## Completion Assessment

- Home foundation: 91%.
- Visible QA: 78%.
- Release QA confidence: 72%.

The Home foundation is not yet complete because the final visible end-to-end publish proof is blocked by the in-app browser control timeout, not by the publishing contract or backend.

## Next Home Mission

Native Home visible browser publish proof recovery:

- Stabilize the in-app browser control path.
- Resolve the QA web bundling/runtime dependency issue if it reproduces.
- Open authenticated Home.
- Type a post visibly.
- Reload and prove draft recovery.
- Publish visibly.
- Confirm composer reset, feed refresh, and no duplicate publish.
