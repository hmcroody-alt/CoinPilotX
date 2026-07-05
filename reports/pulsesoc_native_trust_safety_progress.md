# PulseSoc Native Trust, Safety & Support Progress

## Scope

Built a native Trust, Safety, and Support foundation for the parallel `mobile-native` app without touching production WebView routes.

## Reuse-First Inventory

Existing PulseSoc production behavior reused:

- `GET /api/support/ticket`
- `POST /api/support/ticket`
- `POST /api/security/report`
- `POST /api/scam-shield/scan`
- `POST /api/pulse/report`
- `POST /api/pulse/block`
- Existing support ticket database behavior.
- Existing security report database behavior.
- Existing Scam Shield analysis pipeline.
- Existing moderation/reporting routes.
- Existing protected web routes for help, trust, rules, and advanced support content.

The native app remains a client of the existing backend. It does not duplicate support, moderation, scam detection, or security-report business logic.

## Implemented

- Native `TrustSafetyScreen`.
- Native support ticket history with offline cache fallback.
- Native support ticket creation form.
- Native security report form.
- Native Scam Shield scan form.
- Server-authoritative report/block helper API functions for future feature integration.
- Settings entry for Trust and Safety.
- Native route aliases for:
  - `/pulse/help`
  - `/support`
  - `/help`
  - `/trust-center`
  - `/security`
  - `/scam-shield/:mode?`
- Notification/deep-link routing for support, trust, security, and Scam Shield links.
- Safe web fallback buttons for Trust Center, Community Rules, and Web Help.
- Loading, offline, error, and notice states.

## Native UI/Device Layer

The native layer rebuilds only the UI and interaction shell:

- Touch-friendly cards and forms.
- Native loading/error/notice states.
- Pull-to-refresh support-ticket history.
- Cached support-ticket display if network fails.
- PulseSoc visual language with dark surface depth, glowing accent states, and premium spacing.

## QA Status

Static verification completed:

- `npm run --prefix mobile-native typecheck`
- `cd mobile-native && EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose`
- `venv/bin/python scripts/pulsesoc_native_trust_safety_audit.py`
- `git diff --check`

Practical QA browser checks completed:

- `/pulse/help`
- `/support`
- `/help`
- `/trust-center`
- `/security`
- `/scam-shield/scan`
- Settings entry navigation.
- Scam Shield scan response.
- Support/security validation states.

Observed QA evidence:

- All route aliases rendered the native Trust & Safety screen with support tickets, support ticket form, security report form, and Scam Shield.
- `/pulse/settings` rendered the `Trust and Safety` Settings entry.
- A QA-only Scam Shield scan for a fake seed-phrase support scam returned `Critical · 100%` with red flags and safe actions from the existing backend.
- Support and security report submissions were not executed in the browser because those actions may create ticket/report side effects. Validation states remain implemented and audited.
- Browser log review showed only existing web warnings and a browser clipboard bridge error unrelated to this feature.

Physical-device verification is not required for this foundation because it does not depend on camera, microphone, push, background audio, or installed-app-only device APIs.

## Remaining Gaps

- Advanced help/support article browsing remains on safe web fallback.
- Provider-side support email delivery is backend/provider QA, not native UI QA.
- Abuse reports from individual feature surfaces should gradually call the shared Trust/Safety helpers where appropriate.
- Real production support queues should be validated with a controlled support QA account before release.

## Risk

Risk level: medium.

The feature touches support and safety flows, but the backend remains authoritative and the native implementation is limited to UI, routing, and API wrappers.

## Next Recommendation

After this foundation passes practical QA, the next highest-value action should be selected from the current repository state. Likely candidates are:

- Native Verification Center + Badge/Identity Verification, if production verification APIs are ready.
- A short Trust/Safety QA sweep if support/security validation or Scam Shield routes reveal issues.
- Shared report/block integration across Feed, Reels, Status, Marketplace, Groups, and Messenger if duplicate reporting UI continues to appear.
