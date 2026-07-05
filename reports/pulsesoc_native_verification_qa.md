# PulseSoc Native Verification Center Practical QA

Date: 2026-07-05

## Scope

This pass verified the native Verification Center in the parallel `mobile-native` app without changing production WebView routes. The QA browser used:

- Local backend: `http://127.0.0.1:5107`
- Local CORS proxy: `http://127.0.0.1:5108`
- Expo web QA: `http://localhost:8094`
- Authenticated QA account shown in native UI: `Native Account QA` / `@nativeacctqa1783288873`

Backend verification, review, appeal, document review, audit logs, Premium badge status, profile badge status, and admin decisions remain server-authoritative.

## Verified In QA Browser

| Area | Result | Evidence |
| --- | --- | --- |
| `/pulse/verification` | Passed | Rendered native `Verification Center`, status orb, badge preview, checklist, request form, document handoff, appeal controls, and recommendations. |
| `/pulse/verification/<track>` | Passed | `/pulse/verification/business` rendered the native Verification Center and selected `Business`. |
| `/dashboard/account/verification` | Passed | Routed into the same native Verification Center instead of falling through to generic web fallback. |
| Authenticated route access | Passed | Existing QA browser session restored and the route did not show the login screen while the local backend/proxy was available. |
| Settings entry point | Passed | `/pulse/settings` showed `Verification Center` and `Trust and Safety` entries. |
| Profile entry point | Passed | `/pulse/profile` About tab showed `Verification: not started` and `Open Verification Center`. |
| Premium entry point | Passed | `/pulse/premium` showed `Open Verification Center` while keeping Premium status server-controlled. |
| Trust entry point | Passed | `/trust-center` showed the Trust & Safety native surface with a `Verification` action. |
| Scam Shield/Trust entry point | Passed | `/scam-shield/scan` routed to native Trust & Safety and exposed `Verification`. |
| Verification status display | Passed | QA account rendered `Not Started`, score `25`, and `Request #not started`. |
| Requirements checklist | Passed | Rendered profile, email, request, private evidence, and admin review checklist rows. |
| Request form presence | Passed | Native path selector and `Start verification request` button rendered. The request was not submitted during this sweep to avoid creating review side effects. |
| Document upload handoff guard | Passed | Clicking `Choose private document` before a request showed `Start a verification request before uploading private evidence.` |
| Appeal validation guard | Passed | Clicking `Submit appeal` without a supported request/context showed `Add an appeal note for an existing rejected, suspended, or needs-more-info request.` |
| Loading/error states | Passed for reachable QA states | Initial loading state exists in the screen and validation error states rendered safely in browser. |
| Console errors | Passed | No browser console errors were captured during the final verification route pass. |
| Design quality | Passed for browser QA | Native surface uses the established dark premium PulseSoc visual language, accent glow, layered panels, and internal LogiNexus design principles without exposing `LogiNexus` as user-facing copy. |

## Not Fully Verified

| Area | Status | Reason |
| --- | --- | --- |
| Actual request submission | Not executed | Would create a verification review record. Needs a dedicated seeded QA user or disposable local fixture. |
| Actual private document upload | Not executed | Browser QA can verify the guarded handoff, but document picker/provider upload needs controlled QA evidence and should not upload private identity files. |
| Pending/approved/rejected seeded states | Not seeded in browser | The native code supports these normalized states, but this pass used a `not_started` QA account. Needs seeded local fixtures for each review state. |
| Offline cache on full reload | Not proven | When the proxy was stopped and `/pulse/verification` was reloaded, auth/session restore failed first and the app returned to the signed-out shell. Verification cache fallback should be tested with a retained authenticated native device session or a targeted cache fixture. |
| Admin review/provider workflow | Not verified | Admin approval/rejection, audit logs, and review queue behavior remain backend/admin-provider QA. |
| Physical-device document picker | Not verified | Requires iPhone/Android device QA. |
| Push/deep-link notification tap | Not verified | Requires installed app and provider/device push setup. |

## Browser Signals

- `errors`: none captured in the final route pass.
- Known non-blocking warnings observed elsewhere in the QA browser session:
  - Expo Notifications push-token listener is not fully supported on web.
  - React Native Web `shadow*` and `pointerEvents` deprecation warnings.
  - Existing `expo-av` deprecation warning for web/video surfaces.

These warnings are not Verification Center blockers.

## Production Safety

- No production WebView route was modified during this QA pass.
- Native continues to reuse:
  - `GET /api/dashboard/account/state`
  - `GET /api/pulse/profile/me`
  - `GET /api/premium/status`
  - `POST /api/dashboard/account/verification/request`
  - `POST /api/dashboard/account/verification/appeal`
  - `POST /api/dashboard/account/verification/document`
- The native client does not duplicate verification approval, badge issuance, document review, moderation, Premium badge authority, or admin audit logic.

## Result

No critical, security-critical, data-loss, production-breaking, or future-development-blocking issue was found.

The Verification Center is browser-QA-ready for route, entry-point, status, checklist, validation, and safe fallback behavior. Release confidence still requires seeded review-state tests, controlled document upload QA, admin/provider verification QA, installed deep-link QA, and physical iOS/Android document picker checks.
