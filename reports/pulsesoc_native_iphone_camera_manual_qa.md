# PulseSoc Native Manual iPhone Camera Studio QA

Status: manual iPhone Camera Studio interaction QA was prepared, but no manual screen recording, QuickTime capture, screenshots, or backend publish IDs were available in this workspace.

This report does not claim Camera Studio physical interaction passed. It records the current evidence state honestly so Native LiveKit calls remain deferred until Camera Studio has real hardware interaction proof.

## Scope

Mission:

- Use manual iPhone interaction with screen recording.
- Launch `com.pulsesoc.nativeapp`.
- Manually tap through Camera Studio.
- Capture evidence for login/session restore, permissions, gallery picker, capture, preview, upload, publish, foreground/background recovery, and native visual quality.

Rules honored:

- No Native LiveKit calls were built.
- No production WebView routes were modified.
- No production auth weakening was introduced.
- No native-only backend logic was added.
- The production app identity `com.pulsesoc.app` remains separate from the native QA identity `com.pulsesoc.nativeapp`.

## Device Target

Physical device target from the current QA track:

- Device: iPhone 16 Pro
- OS: iOS 18.7.3
- UDID: `00008140-000E2D9A2EE8801C`
- Native QA bundle: `com.pulsesoc.nativeapp`
- Production bundle protected: `com.pulsesoc.app`
- API base: `https://pulsesoc.com`

## Manual Evidence Availability

No new manual evidence file was present in the repository for this pass.

Checked local report media artifacts under `reports/` for common image/video formats. Existing assets are older browser QA, App Store review, and web/mobile screenshots; none are a new physical iPhone Camera Studio manual screen recording or capture artifact for this mission.

Required but not available:

- iPhone Control Center screen recording path.
- QuickTime iPhone recording path.
- Camera Studio screenshots.
- Permission prompt screenshots.
- Gallery picker screenshots.
- Capture preview screenshots.
- Upload progress screenshots.
- Feed/Status/Reels published destination screenshots.
- Backend media/upload IDs.
- Published post/status/reel IDs.

## Manual QA Matrix

| Area | Manual iPhone result | Evidence |
| --- | --- | --- |
| App launch | Not manually re-verified in this pass | Previous machine-captured launch evidence exists in `reports/pulsesoc_native_iphone_camera_captured_qa.md` |
| Login/session restore | Not manually verified | No manual recording or authenticated tap-through evidence |
| Open Camera Studio | Not manually verified | Previous deep-link process dispatch exists, but no manual UI evidence |
| Camera permission prompt | Not verified | No screenshot/video evidence |
| Microphone permission prompt | Not verified | No screenshot/video evidence |
| Gallery picker | Not verified | No screenshot/video evidence |
| Photo capture | Not verified | No screenshot/video evidence |
| Video capture | Not verified | No screenshot/video evidence |
| Front/back camera switch | Not verified | No screenshot/video evidence |
| Preview flow | Not verified | No screenshot/video evidence |
| Upload progress | Not verified on physical iPhone | No screenshot/video evidence and no backend upload ID |
| Feed publish | Not verified on physical iPhone | No published post ID |
| Status publish | Not verified on physical iPhone | No published status ID |
| Reels publish | Not verified on physical iPhone | No published reel ID |
| Retry/cancel | Not verified on physical iPhone | No weak-network or manual interruption evidence |
| Foreground/background recovery | Not manually verified in this pass | Previous process-level checks exist only |
| Native visual quality | Not manually verified | No screen recording or screenshots |

## Syslog And Backend Evidence

No new `idevicesyslog` excerpt was captured for a manual Camera Studio tap-through in this pass.

No backend media/upload/published destination IDs were produced or discovered for this pass.

The previous captured iPhone QA checkpoint remains the current hardware evidence baseline:

- Native bundle installed and launched.
- App foregrounded as `com.pulsesoc.nativeapp`.
- Camera Studio payload URL launched at process level.
- Metro bundled the app.
- Camera service remained cold.
- No screenshot/video evidence or backend IDs were produced.

## Why This Remains Blocked

The current automation environment can inspect files, run Metro/static verification, launch the iPhone app, and pass deep-link payloads. It cannot physically tap the iPhone UI or start a Control Center/QuickTime screen recording on behalf of the user.

`idevicescreenshot` was previously attempted and failed with:

```text
Could not start screenshotr service: Invalid service
```

Therefore, a human-operated iPhone recording or a dedicated UI automation target is still required before Camera Studio can be marked physical-device verified.

## Required Manual Capture Procedure

Use one of these evidence paths:

1. iPhone Control Center screen recording.
2. QuickTime `New Movie Recording` with the iPhone selected as the camera source.
3. A QA-only XCTest UI target that can drive the installed development build and save screenshots.

Minimum manual recording checklist:

1. Show device model/OS/build context if possible.
2. Launch `com.pulsesoc.nativeapp`.
3. Confirm login/session restore or log in with the QA account.
4. Open Camera Studio from the native UI.
5. Record camera permission allow/deny behavior.
6. Record microphone permission allow/deny behavior.
7. Open gallery picker and select media.
8. Capture a photo.
9. Capture a short video if possible.
10. Switch front/back camera.
11. Confirm preview flow.
12. Publish to Feed, then record the resulting post or backend post ID.
13. Publish to Status, then record the resulting status or backend status ID.
14. Publish to Reels, then record the resulting reel or backend reel ID.
15. Trigger upload retry/cancel under weak network if safe.
16. Background and foreground the app during or after upload.
17. Note any visual quality issues against the internal PulseSoc/LogiNexus design standard.

## Pass Criteria

Physical iPhone Camera Studio QA is not complete until the report includes:

- A concrete recording or screenshot path.
- Device model and OS.
- App build identity.
- Observed login/session result.
- Observed camera and microphone permission result.
- Observed gallery picker result.
- Observed photo/video capture result.
- Observed upload progress result.
- Backend media/upload IDs.
- Published Feed/Status/Reels IDs or explicit API failure evidence.
- Any failures and fixes.

## Recommendation

Do not move to Native LiveKit calls yet.

The next highest-value action is to run the manual iPhone capture procedure with a real screen recording, then update this report and `reports/pulsesoc_native_physical_camera_qa_results.md` with the video path, screenshots if any, backend media/upload IDs, published destination IDs, and any fixes required.
