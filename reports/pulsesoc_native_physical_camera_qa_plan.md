# PulseSoc Native Physical Camera QA Plan

Date: 2026-07-05

## Scope

This is a physical-device QA preparation and hardening checkpoint. It does not build Native LiveKit calls, does not add a new user-facing feature, does not weaken production auth, and does not touch production WebView routes.

The native Camera Studio remains a parallel client for existing PulseSoc backend behavior. Upload, preview, publish, moderation, storage, processing, and destination rules remain server-authoritative.

## Current State

Simulator evidence now covers:

- Installed `com.pulsesoc.nativeapp` development build launch.
- QA-only localhost authenticated simulator access.
- Camera Studio route access for Feed, Status, and Reel destinations.
- Simulator media seeding through `xcrun simctl addmedia`.
- QA-only simulator media injection.
- Selected-media preview state.
- Upload handoff through `/api/pulse/media/upload`.
- Preview handoff through `/api/pulse/camera/preview`.
- Feed publish through `/api/pulse/posts/create-from-camera`.
- Status publish through existing Status APIs.
- Reel publish through `/api/pulse/reels/create-from-camera`.
- Foreground/background session recovery.
- iPhone 17 Pro safe-area visual hardening.

Still not verified:

- Physical camera permission prompts.
- Physical microphone permission prompts.
- Real camera capture.
- Real microphone capture.
- Front/back camera switching on hardware.
- Native gallery picker touch selection.
- Large video upload memory behavior.
- Upload retry/cancel under weak network.
- Physical iOS and Android compression metadata.
- Physical-device playback of captured media.

## Safe Hardening Implemented

The shared native upload layer now reports computable upload progress with percent and transferred/total size when XHR exposes upload length. This helps physical large-video QA confirm progress accuracy without changing backend APIs or business logic.

Example progress message:

```text
Uploading media 42% (84.0 MB of 200.0 MB).
```

No production WebView code was changed.

## Physical iPhone QA Checklist

Preparation:

- Build/install `com.pulsesoc.nativeapp` as a development build.
- Use the QA backend or QA-safe production-like account.
- Confirm `EXPO_PUBLIC_PULSE_API_BASE_URL` targets the intended QA backend.
- Confirm push/provider credentials remain separate from production `com.pulsesoc.app`.
- Confirm device has at least one large video fixture, one short video, and one high-resolution image.

Camera and microphone:

- Launch app cold.
- Login or restore session.
- Open Camera Studio from Home Feed.
- Deny camera permission and verify gallery fallback remains available.
- Grant camera permission and verify camera preview renders.
- Switch front/back camera twice.
- Capture a photo.
- Switch to video mode.
- Deny microphone permission and verify video can continue muted or shows a clear state.
- Grant microphone permission and record a short video with audio.
- Background the app while preview is open, then return.
- Background during recording only if the app safely stops or recovers without corrupt state.

Gallery:

- Open native gallery picker.
- Select a high-resolution image.
- Select a short video.
- Select a large video.
- Cancel picker and verify state remains stable.
- Deny photo-library permission and verify permission-denied state.

Publish destinations:

- Publish photo to Feed and verify native Post Detail opens.
- Publish photo/video to Status and verify native Status viewer opens.
- Publish video to Reel and verify native Reels viewer opens.
- Publish Profile avatar/cover handoff where supported.
- Publish Messenger attachment handoff where opened from a conversation.

Large media:

- Upload a video near 250 MB.
- Upload a video near 500 MB if device storage/network allows.
- Confirm progress percent moves steadily.
- Confirm transferred/total size is plausible.
- Cancel during upload and verify upload stops.
- Retry after cancel or failure.
- Repeat under weak network or throttled hotspot.

Visual quality:

- Verify controls clear Dynamic Island/status areas.
- Verify bottom controls clear home indicator.
- Verify text does not overlap controls.
- Verify caption/privacy/destination controls remain readable.
- Verify dark UI remains consistent with the native PulseSoc design standard.

## Physical Android QA Checklist

Preparation:

- Install `com.pulsesoc.nativeapp` development build on a QA Android device.
- Confirm camera, microphone, storage/media picker permissions are reset before the first pass.
- Confirm a large video fixture and high-resolution image exist on device.

Android-specific checks:

- Camera permission deny/allow.
- Microphone permission deny/allow.
- Android Photo Picker or media-library permission behavior.
- Back navigation from Camera Studio and picker.
- Hardware back during preview.
- Hardware back during upload.
- App switcher foreground/background recovery.
- Rotate device if supported by current app orientation.
- Upload over Wi-Fi and cellular/hotspot.
- Verify progress messages under weak network.
- Confirm retry/cancel works after network interruption.

## Weak Network / Retry-Cancel Plan

Preferred test paths:

1. Use a physical device on a throttled hotspot.
2. Use iOS Network Link Conditioner if available.
3. Use Android emulator/device network shaping if available.
4. Upload a large fixture so cancel and retry are interruptible.

Pass criteria:

- Cancel immediately stops the active XHR upload.
- Retry starts a fresh upload using the same selected asset.
- Failed uploads show a clear retry state.
- Progress never jumps backward except after a retry restart.
- Publish is disabled while upload is active.
- App recovers cleanly after foreground/background transitions.

## Compression Metadata QA

Verify the native client sends and displays the expected policy:

- Feed/photo: `native_photo_v1`
- Status/photo: `native_photo_v1`
- Feed/video: `native_video_v1`
- Reel/video: `native_video_v1`
- Status/video: `native_status_video_v1`

Device checks:

- Confirm selected/captured asset size, mime type, width/height, and duration where available.
- Confirm video quality request is `1080p` for general video and `720p` for Status video.
- Confirm backend response remains authoritative for processing status and playback URLs.
- Confirm unsupported/oversized media receives a validation or backend error instead of a silent failure.

## Failure Recovery

Test failures:

- Network drop during upload.
- Backend 500 or unavailable QA endpoint.
- Session expiry during upload/publish.
- Media processing failure.
- Unsupported file type.
- Oversized file.
- App backgrounded during upload.
- App killed after selection before publish.

Expected behavior:

- User sees an actionable error.
- Retry is available when safe.
- Cancel leaves the app usable.
- No duplicate publish unless the user retries intentionally.
- Existing backend idempotency and moderation rules remain authoritative.

## Required Evidence

Capture for each physical pass:

- Device model and OS version.
- App identity and build number.
- API base URL environment.
- Screenshots or short recordings for permission, preview, upload, retry/cancel, and publish routing.
- Backend media IDs and destination entity IDs for Feed/Status/Reel.
- Any console/device logs for upload failures.
- Clear separation of iOS physical, Android physical, simulator, and browser evidence.

## Current Recommendation

Do not move to Native LiveKit calls yet.

The next highest-value action is to run physical iPhone and Android Camera Studio QA using this plan, with emphasis on large-video upload, retry/cancel, camera/microphone permission behavior, and device-specific gallery picker behavior. LiveKit calls reuse the same camera/microphone/device-permission foundation and add push/ringing/background-audio complexity, so calls should remain deferred until physical Camera Studio QA is credible.
