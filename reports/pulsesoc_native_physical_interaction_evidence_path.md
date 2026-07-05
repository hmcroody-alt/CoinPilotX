# PulseSoc Native Physical Interaction Evidence Path

Date: 2026-07-05

## Result

Status: evidence path prepared; no new native user-facing feature was built.

The safest current path for physical iPhone Camera Studio QA is manual on-device screen recording plus backend ID logging. This is the only path available today that can capture real camera, microphone, gallery picker, upload, and publish behavior without weakening production auth or modifying production WebView routes.

## Current State

Verified already:

- `com.pulsesoc.nativeapp` installs on the iPhone 16 Pro.
- The app launches on the physical iPhone.
- Metro bundles the iOS app.
- `pulsesoc://pulse/camera/photo?target=feed` launches the app at process level.
- Process-level suspend/resume works through `devicectl`.

Still unverified:

- Login/session restore on the physical iPhone.
- Camera permission allow/deny.
- Microphone permission allow/deny.
- Gallery picker.
- Photo capture.
- Video capture.
- Front/back camera switch.
- Preview flow.
- Upload progress.
- Feed publish.
- Status publish.
- Reels publish.
- Retry/cancel under weak network.
- Visual quality on physical hardware.

## Tooling Investigation

Local tooling that works:

- `xcrun devicectl list devices`
- `xcrun devicectl device info details`
- `xcrun devicectl device info displays`
- `xcrun devicectl device info lockState`
- `xcrun devicectl device info apps`
- `xcrun devicectl device process launch`
- `xcrun devicectl device process suspend --pid <pid>`
- `xcrun devicectl device process resume --pid <pid>`
- `ideviceinfo`
- `idevicepair validate`
- `ideviceimagemounter list`
- `ideviceimagemounter devmodestatus`
- `idevicesyslog`

Local tooling that does not currently provide enough evidence:

- `devicectl` does not provide tap automation, camera control, gallery picker control, or screenshots.
- `idevicescreenshot` was installed through `libimobiledevice`, but failed with:

```text
Could not start screenshotr service: Invalid service
Remember that you have to mount the Developer disk image on your device if you want to use the screenshotr service.
```

Follow-up checks showed:

```text
DeveloperModeStatus: true
Status: Complete
```

So the current screenshot failure is a local tooling/device-service limitation, not proof of an app bug.

Xcode state:

- `PulseSocNative` is present as an Xcode scheme.
- No dedicated `PulseSocNativeUITests` target is currently present.
- Creating an XCTest UI target is practical, but it is a separate QA automation mission because it touches native iOS project structure and test code.

## Recommended Evidence Path

Primary path: manual physical iPhone QA with video evidence.

Use one of these recording methods:

1. iPhone built-in screen recording.
2. QuickTime Player device recording.
3. Xcode Devices and Simulators screenshots for still-state evidence.

This path is recommended because it captures real permission prompts, real camera/microphone behavior, real gallery picker behavior, and real upload/publish states without adding QA-only auth shortcuts to production or changing the WebView app.

## Exact Setup Commands

Start Metro for the installed development build:

```bash
cd /Users/hmcherie/Desktop/CoinPilotX/mobile-native
EXPO_PUBLIC_PULSE_API_BASE_URL=https://pulsesoc.com npx expo start --dev-client --host lan --port 8081
```

Confirm device:

```bash
xcrun devicectl list devices
xcrun devicectl device info lockState --device 00008140-000E2D9A2EE8801C
idevicepair -u 00008140-000E2D9A2EE8801C validate
```

Launch app:

```bash
xcrun devicectl device process launch --device 00008140-000E2D9A2EE8801C --terminate-existing com.pulsesoc.nativeapp
```

Launch Camera Studio deep link:

```bash
xcrun devicectl device process launch --device 00008140-000E2D9A2EE8801C --terminate-existing --payload-url 'pulsesoc://pulse/camera/photo?target=feed' com.pulsesoc.nativeapp
```

Collect device logs during QA:

```bash
mkdir -p /Users/hmcherie/Desktop/CoinPilotX/reports/device-logs
idevicesyslog -u 00008140-000E2D9A2EE8801C | tee /Users/hmcherie/Desktop/CoinPilotX/reports/device-logs/pulsesoc-native-iphone-camera-$(date +%Y%m%d-%H%M%S).log
```

If the app PID is needed for suspend/resume:

```bash
xcrun devicectl device info processes --device 00008140-000E2D9A2EE8801C | rg 'PulseSocNative'
xcrun devicectl device process suspend --device 00008140-000E2D9A2EE8801C --pid <PID>
xcrun devicectl device process resume --device 00008140-000E2D9A2EE8801C --pid <PID>
```

## iPhone Built-In Screen Recording

Use this as the primary evidence method.

1. On the iPhone, open Settings.
2. Go to Control Center.
3. Add Screen Recording if it is not already present.
4. Connect the iPhone to the Mac and keep it unlocked.
5. Start Metro using the command above.
6. Launch `com.pulsesoc.nativeapp`.
7. Open Control Center on the iPhone.
8. Long-press Screen Recording.
9. Enable microphone only if narration is needed.
10. Start recording.
11. Run the Camera Studio checklist.
12. Stop recording.
13. Save/export the video from Photos.
14. Store the file under:

```text
reports/device-evidence/iphone-camera-studio/YYYYMMDD-HHMMSS/
```

Required filename pattern:

```text
iphone16pro-ios18.7.3-camera-studio-<flow>.mov
```

Example:

```text
reports/device-evidence/iphone-camera-studio/20260705-153000/iphone16pro-ios18.7.3-camera-studio-feed-photo.mov
```

## QuickTime Recording

Use this when direct iPhone screen recording is inconvenient.

1. Connect the iPhone by USB.
2. Unlock the iPhone.
3. Open QuickTime Player on the Mac.
4. Choose File > New Movie Recording.
5. Click the arrow beside the record button.
6. Select `P3r7or` as the camera source.
7. Select the iPhone microphone only if narration is needed.
8. Start recording.
9. Run the Camera Studio checklist.
10. Stop recording.
11. Save the video under:

```text
reports/device-evidence/iphone-camera-studio/YYYYMMDD-HHMMSS/
```

If QuickTime does not show the iPhone, fall back to iPhone built-in screen recording.

## Xcode Screenshot Workflow

Use this for still screenshots, not full interaction videos.

1. Open Xcode.
2. Choose Window > Devices and Simulators.
3. Select `P3r7or`.
4. Keep the iPhone unlocked.
5. Navigate the app manually to the desired state.
6. Click Take Screenshot.
7. Save screenshots under:

```text
reports/device-evidence/iphone-camera-studio/YYYYMMDD-HHMMSS/screenshots/
```

Required screenshots:

- Login or restored session state.
- Camera Studio initial state.
- Camera permission prompt.
- Microphone permission prompt.
- Gallery picker.
- Captured photo preview.
- Captured video preview.
- Upload progress.
- Publish success for Feed.
- Publish success for Status.
- Publish success for Reels.
- Any error state.

## Manual QA Checklist

Record each item as one of:

- `passed`
- `failed`
- `blocked`
- `not tested`

Checklist:

1. App launch.
2. Login.
3. Session restore after force close and relaunch.
4. Open Camera Studio from Home Feed.
5. Open Camera Studio through deep link.
6. Camera permission allowed.
7. Camera permission denied.
8. Microphone permission allowed.
9. Microphone permission denied.
10. Gallery picker for image.
11. Gallery picker for video.
12. Photo capture.
13. Video capture.
14. Front/back switch.
15. Preview screen.
16. Caption entry.
17. Privacy selector.
18. Feed destination publish.
19. Status destination publish.
20. Reel destination publish.
21. Upload progress display.
22. Retry after upload failure.
23. Cancel during upload.
24. Background app during capture.
25. Background app during upload.
26. Foreground recovery.
27. Large video behavior.
28. Weak-network behavior.
29. Visual quality and safe-area fit.
30. No production WebView regression.

## Backend ID Logging

Capture backend IDs after every successful upload or publish.

If direct database access is available, use server-authoritative queries. Replace `<QA_USER_ID>` with the authenticated QA user id.

Recent media uploads:

```sql
SELECT id, uploader_user_id, context_type, context_id, media_type, mime_type, file_size_bytes,
       processing_status, mux_status, media_url, thumbnail_url, created_at
FROM chat_media_uploads
WHERE uploader_user_id = <QA_USER_ID>
ORDER BY id DESC
LIMIT 20;
```

Recent posts:

```sql
SELECT id, user_id, title, post_type, visibility, moderation_status, created_at
FROM pulse_posts
WHERE user_id = <QA_USER_ID>
ORDER BY id DESC
LIMIT 20;
```

Recent statuses:

```sql
SELECT id, user_id, status_type, visibility, created_at, expires_at
FROM pulse_status
WHERE user_id = <QA_USER_ID>
ORDER BY id DESC
LIMIT 20;
```

Recent reels:

```sql
SELECT id, user_id, post_id, caption, status, created_at
FROM pulse_reels
WHERE user_id = <QA_USER_ID>
ORDER BY id DESC
LIMIT 20;
```

Expected evidence fields for the QA report:

- `media_upload_id`
- `media_url`
- `processing_status`
- `mux_status`
- `post_id`
- `status_id`
- `reel_id`
- `created_at`
- `device_model`
- `device_os`
- `app_bundle_id`
- `app_version`
- `api_base_url`
- `evidence_file_path`

## Recommended QA Report Template

Create or update:

```text
reports/pulsesoc_native_physical_camera_qa_results.md
```

Use this section format for each flow:

```text
### Feed Photo Publish

Device:
Build:
API base:
Evidence video:
Screenshots:
Result:
Media upload ID:
Post ID:
Observed upload progress:
Errors:
Fixes:
Remaining gaps:
```

Repeat for:

- Feed photo.
- Feed video.
- Status image.
- Status video.
- Reel video.
- Gallery image.
- Gallery video.
- Permission denied.
- Retry/cancel.
- Foreground/background recovery.

## XCTest / Automation Assessment

XCTest UI target:

- Practical, but not present today.
- Best next automation path if manual evidence capture remains too slow.
- Must remain QA-only.
- Must not add native-only auth logic or weaken production auth.
- Should use existing accessibility labels and add `testID` values only where needed.

Maestro/Appium/Detox:

- Not configured today.
- Useful only after confirming they can drive this physical iPhone and handle iOS permission prompts.
- Should not be introduced before the manual evidence workflow proves the expected flow once.

QA-only interaction helper:

- Should be avoided for physical production-api QA unless it only improves observability.
- Must never bypass login, permissions, upload validation, backend moderation, entitlement checks, or destination business rules.

## Safest Next Action

Do not move to Native LiveKit calls yet.

Run one manual captured iPhone Camera Studio QA pass using iPhone built-in screen recording or QuickTime, then update the physical Camera Studio QA results with evidence file paths and backend IDs.

If manual recording is still blocked or too unreliable, the next buildable action is a dedicated QA-only XCTest UI target for `PulseSocNative`.
