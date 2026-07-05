# PulseSoc Native Verification Center Progress

## Scope

Built the Native Verification Center foundation for the parallel `mobile-native` app without changing production WebView routes or backend verification logic.

## Reuse-First Inventory

Existing PulseSoc production behavior reused:

- `GET /api/dashboard/account/state`
- `POST /api/dashboard/account/verification/request`
- `POST /api/dashboard/account/verification/appeal`
- `POST /api/dashboard/account/verification/document`
- `GET /api/pulse/profile/me`
- `GET /api/premium/status`
- Protected web route `/dashboard/account/verification`
- Existing verification request tables.
- Existing private verification document storage.
- Existing admin review/status logic.
- Existing account audit logs.
- Existing profile, Premium, Founder, and verified badge fields.

The backend remains authoritative for request creation, private document validation, admin review, approvals, rejections, revocation, badge state, and audit logs.

## Implemented

- Native Verification Center screen.
- Verification status display.
- Verification score/status ring.
- Verification requirements checklist.
- Native track selector for identity, blue check, business, and government ID paths supported by the current account command API.
- Verification request submission using the existing account verification API.
- Private document picker/upload handoff using the existing private verification document endpoint.
- Verification appeal submission using the existing account verification appeal API.
- Profile badge preview.
- Premium/Foundation badge display.
- Settings entry point.
- Profile About entry point for the profile owner.
- Premium entry point.
- Trust/Safety entry point.
- Native deep links:
  - `/pulse/verification`
  - `/pulse/verification/<track>`
  - `/dashboard/account/verification`
- Notification/deep-link routing into the native Verification Center.
- Loading, offline, error, validation, and success states.
- Safe protected web fallback for full verification center and advanced review flow.

## Native UI/Device Layer

The native layer rebuilds only the presentation and device handoff:

- Native touch controls.
- Cached verification state.
- Pull-to-refresh.
- Document picker handoff.
- Profile/Premium badge preview.
- PulseSoc visual identity with dark layered surfaces, glowing status accents, and compact premium spacing.

## Not Rebuilt Natively

- Admin review queue.
- Admin document access.
- Approval/rejection/revocation decisions.
- Provider-heavy identity verification workflows.
- Sensitive document review logic.
- Compliance/business rules.
- Badge issuance logic.

Those remain on existing backend/admin/provider flows.

## QA Status

Static verification completed:

- `npm ci --prefix mobile-native --no-audit --no-fund --progress=false`
- `npm run --prefix mobile-native typecheck`
- `cd mobile-native && EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose`
- `venv/bin/python scripts/pulsesoc_native_verification_audit.py`
- `git diff --check`

Practical QA browser checks completed:

- `/pulse/verification`
- `/pulse/verification/business`
- `/dashboard/account/verification`
- Settings to Verification Center navigation.

Observed QA evidence:

- `/pulse/verification` rendered the native Verification Center while authenticated.
- `/pulse/verification/business` rendered the native Verification Center and visibly selected `Business`.
- `/dashboard/account/verification` routed to the native Verification Center instead of the protected web dashboard shell.
- `/pulse/settings` rendered the `Verification Center` entry.
- The screen displayed badge preview, checklist, track selector, private document handoff, appeal form, and protected web fallback.
- Start request and document upload were not submitted in browser QA because they create verification/request-review side effects. Those flows are implemented and remain suitable for controlled QA account/device testing.

Remaining practical QA:

- Profile owner to Verification Center navigation.
- Premium to Verification Center navigation.
- Trust/Safety to Verification Center navigation.
- Start request success/failure state with a controlled QA account.
- Document picker handoff in simulator/physical device.

## Device/Provider Limitations

- Physical document picker behavior is device/provider QA.
- Provider identity verification, document review, and admin approvals remain release/provider QA.
- Native does not claim sensitive document review has passed device QA.

## Risk

Risk level: medium-high.

The feature touches identity, documents, badges, account trust, and privacy. Risk is controlled by keeping server-side verification authoritative and using native only for status, entry points, and upload handoff.

## Next Recommendation

Recommended next highest-value action after this foundation: short practical Verification Center QA sweep.

Reason:

- Identity/document flows are sensitive enough to deserve focused practical QA before another broad trust feature.
- This should not become an endless QA loop; only critical, security, data-loss, production-breaking, or future-development-blocking issues should pause the roadmap.
