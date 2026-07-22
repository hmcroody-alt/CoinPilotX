# PulseSoc Native App Review Readiness - 2026-07-21

## Decision

**NO-GO for App Store submission today, but the App Review-critical native replacement web-exit blocker is resolved in repository code.**

The remaining blockers are now concentrated around Apple/account ownership, App Store signing/provisioning, production APNs, production-clean release packaging, privacy/App Store metadata reconciliation, and physical production QA.

## Starting Blocker Count

- Starting strict native replacement hard blockers: **54**
- Starting critical surface coverage: **14/26**
- Source: `scripts/pulsesoc_native_webview_replacement_audit.py` at the beginning of this mission.

## Resolved Blockers

- Ending strict native replacement hard blockers: **0**
- Ending critical surface coverage: **26/26**
- Current blocker counts: `{}`
- Current strict audit: **PASS**

Resolved repository-side areas:

- Dashboard route classifier no longer exposes `safe_web_fallback`; unresolved routes are native provider boundaries.
- Dashboard unknown/video/legacy actions no longer open browser URLs.
- Master navigation Terms/Privacy entries are native provider boundaries.
- Notification legal targets route to native Settings instead of browser URLs.
- Signup Terms/Privacy taps no longer open Safari; they show a native legal acknowledgement while preserving auth state.
- Dashboard module detail removed the browser-facing `Open Production Route` action.
- API helpers no longer auto-open web URLs for account, account health, support, safety, premium, learning, creator, growth, intelligence, events, orders, calls, Live, marketplace checkout, or payout onboarding.
- UNDX result actions now resolve through native route dispatch.
- Camera unsupported destinations route to native Creator Studio or Marketplace create boundaries.
- Seller payout browser action was removed.
- User-facing WebView/fallback copy was removed from shippable native source.
- Route and notification tests now assert native-only handling.

## Remaining Blockers

### Repository-side release hardening

- Production-clean App Store target/profile still needs to remove Expo dev-client/dev-menu artifacts without breaking the guarded dev sidecar QA workflow.
- Privacy manifest and App Store Privacy labels still require owner-reviewed data collection reconciliation.
- Pre-existing unrelated dirty release-sensitive files must be reviewed, landed, or reverted before production archive preparation.

### External blockers

- Existing App Store bundle alignment with `com.pulsesoc.app`.
- Apple Developer/App Store Connect access and ownership verification.
- Apple Distribution certificate.
- App Store provisioning profile.
- App Store Connect build train, metadata, IAP/subscription state, and privacy answers.
- Production APNs entitlement and credentials.

### Physical QA blockers

Not observed in this mission:

- Production push delivery, locked-device taps, killed-app routing.
- Camera capture on production-signed build.
- Microphone recording and Live host microphone proof.
- Live guest/host two-client audio/video.
- Voice/video call two-device behavior.
- Bluetooth/speaker routing.
- Background audio and background call behavior.
- Large real-world media uploads.

## App Review Readiness

Current status: **PARTIAL**.

Repository code now passes the strict native-only replacement gate. App Review readiness is still blocked by production release packaging, privacy metadata, Apple-side signing/provisioning, and physical-device proof.

## Validation

Passed:

- `npm run --prefix mobile-native typecheck`
- `npm test --prefix mobile-native -- --runInBand --silent` (`38` suites, `369` tests)
- `cd mobile-native && EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose` (`17/17`)
- `scripts/pulsesoc_native_webview_replacement_audit.py`
- `scripts/pulsesoc_native_app_foundation_audit.py`
- `scripts/pulsesoc_native_feature_parity_audit.py`
- `scripts/pulsesoc_native_live_audit.py`
- `scripts/pulsesoc_native_mission_standard_audit.py`
- `scripts/pulsesoc_native_global_navigation_audit.py`
- `scripts/pulsesoc_native_notification_route_parity_audit.py`
- `scripts/pulsesoc_native_persistent_radio_home_reselect_audit.py`
- `scripts/pulsesoc_native_live_webrtc_guest_audio_repair_audit.py`
- `scripts/pulsesoc_native_calls_audit.py`
- `scripts/pulsesoc_native_music_upload_audit.py`
- `scripts/pulsesoc_native_undx_chat_conversation_audit.py`
- `scripts/pulse_store_submission_readiness_audit.py`
- `scripts/apple_review_compliance_audit.py`
- `scripts/pulse_app_store_review_fix_audit.py`
- `scripts/app_store_review_repair_audit.py`

Physical iPhone:

- Guarded dev sidecar build/install/launch passed on iPhone 16 Pro `P3r7or`.
- Installed bundle: `com.pulsesoc.nativeapp.dev`.
- Production bundle `com.pulsesoc.app` was not targeted.

## Updated GO / NO-GO

**NO-GO** for App Store upload today.

Reason: native replacement web exits are resolved, but production signing/provisioning, bundle alignment, production APNs, production-clean release packaging, privacy/App Store metadata, and physical production QA remain incomplete.

## Recommended Next Mission

Create and verify a production-clean iOS release target/profile:

1. Preserve the dev sidecar QA workflow.
2. Remove Expo dev-client/dev-menu artifacts from the App Store archive.
3. Keep the native replacement audit in the release gate.
4. Reconcile `PrivacyInfo.xcprivacy` and App Store Privacy labels.
5. Prepare Apple-side signing/provisioning once App Store Connect ownership and bundle alignment are confirmed.
