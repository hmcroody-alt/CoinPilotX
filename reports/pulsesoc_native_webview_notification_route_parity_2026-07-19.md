# PulseSoc Native and WebView Notification Route Parity

Date: 2026-07-19

Scope: PulseSoc native notification production-route parity

Source baseline: `origin/main` at `428c15f3e8510434481fffb283dce4b47bbff8b1`

## Executive summary

PulseSoc Native now uses the same production action and notification infrastructure as the working WebView client. Native message, call, post, Reel, Status, and follow actions continue to call the canonical backend routes; the backend remains solely responsible for durable notification creation, recipient policy, block/mute enforcement, realtime fan-out, and push-delivery jobs.

This change closes the native-only gaps around cold-start notification taps, taps received before authentication finishes, production payload field variations, native shorthand links, rapid duplicate taps, device-token refresh, stable installation identity, and server-defined notification preference categories. It does not add a native notification database, a native-only event vocabulary, or a second delivery channel.

## Production route comparison

| User action or event | Working WebView route/service | Native route/service | Production notification owner | Result |
| --- | --- | --- | --- | --- |
| Direct/group message and reply | `POST /api/pulse/communications/v2/conversations/:id/messages` | Same route in `src/api/messenger.ts` | Communications V2/backend notification service | Shared |
| Audio call start | `POST /api/pulse/communications/v2/conversations/:id/voice/start` | Same route in `src/api/calls.ts` | Production call engine | Shared |
| Video call start | `POST /api/pulse/communications/v2/conversations/:id/video/start` | Same route in `src/api/calls.ts` | Production call engine | Shared |
| Post reaction | `POST /api/pulse/posts/:postId/react` | Same route in `src/api/feed.ts` | Post backend | Shared |
| Post comment/reply | `POST /api/pulse/posts/:postId/comments` | Same route in `src/api/feed.ts` | Post backend | Shared |
| Follow | `POST /api/pulse/follows/toggle` | Same route in native profile/feed APIs | Follow backend | Shared |
| Reel reaction | `POST /api/pulse/reels/:reelId/react` | Same route in `src/api/reels.ts` | Reel backend | Shared |
| Reel comment | `POST /api/pulse/reels/:reelId/comments` | Same route in `src/api/reels.ts` | Reel backend | Shared |
| Reel share | `POST /api/pulse/reels/:reelId/share` | Same route in `src/api/reels.ts` | Reel backend | Shared |
| Status reaction/reply/share | `/api/pulse/status/:statusId/{react,reply,share}` | Same routes in native Status API | Status backend | Shared |
| Notification inbox | `GET /api/pulse/notifications` | Same route in `src/api/notifications.ts` | Notification backend | Shared |
| Notification preferences | `GET/PATCH /api/pulse/notifications/preferences` | Same routes in `src/api/notifications.ts` | Notification backend | Shared |
| Native push registration | `POST /api/push/subscribe` | Same route in `src/api/push.ts` | Push service and delivery jobs | Shared |
| Native push revocation | `POST /api/push/unsubscribe` | Same route in `src/api/push.ts` | Push service | Shared |

## Production backend behavior preserved

- Canonical authenticated user IDs remain the recipient and actor identities.
- Backend preference, quiet-hours, mute, block, and security-channel rules remain authoritative.
- Durable notification rows and `push_delivery_jobs` remain server-owned.
- Expo/APNs provider dispatch, receipt processing, retry policy, and invalid-token cleanup remain server-owned.
- Native clients never call a second notification-creation endpoint after an action.
- WebView and native can act on the same account without duplicating notification records.

## Files changed

- `mobile-native/App.tsx` — authenticated push sync on foreground and authentication-deferred notification navigation.
- `mobile-native/src/api/push.ts` — stable installation identity, coalesced registration, token-replacement revocation, and multi-endpoint logout cleanup.
- `mobile-native/src/api/notifications.ts` — server notification-category response typing.
- `mobile-native/src/navigation/notificationRouting.ts` — cold-start response recovery, production payload normalization, semantic fallback routing, native shorthand routes, and rapid-tap deduplication.
- `mobile-native/src/screens/NotificationPreferencesScreen.tsx` — server-authoritative categories and mandatory security channels.
- `scripts/pulsesoc_native_notification_route_parity_audit.py` — executable route, push-registration, preferences, and deep-link parity gate.
- `reports/pulsesoc_native_webview_notification_route_parity_2026-07-19.md` — implementation and QA evidence.

Backend files changed: none. WebView files changed: none.

## Gaps found and corrections

| Gap | Previous behavior | Correction |
| --- | --- | --- |
| Cold-start tap | The last notification response was not read | Process `getLastNotificationResponseAsync()` through the canonical router |
| Authentication race | A tap could arrive before the signed-in navigator was ready | Defer the normalized target and route it after authentication/navigation readiness |
| Payload parity | Only a narrow URL-field set was recognized | Accept production `route`, URL aliases, nested `data`, and semantic entity IDs |
| Native shorthand | `pulsesoc://post/:id` and related shorthand could miss native screens | Normalize post, Reel, message, call, and profile shorthand to canonical `/pulse/...` paths |
| Duplicate taps | Repeated response delivery could stack the same destination | Deduplicate the same notification identifier for five seconds |
| Token refresh | Registration only ran at login and did not retire a replaced token | Refresh on foreground without prompting and revoke a replaced endpoint first |
| Device identity | Registration had no stable native installation identifier | Persist a non-secret installation ID in iOS secure storage and send it as `device_id`/`installation_id` |
| Logout cleanup | Only one selected endpoint was revoked | Revoke all distinct cached/current endpoints while preserving account preferences |
| Preference categories | Native displayed a partial hard-coded list | Render server-provided categories and existing preference keys, with a contract-compatible fallback |
| Security channels | Only security in-app delivery was fixed on | Preserve mandatory security in-app and email channels, matching backend enforcement |

## Notification payload routing

Explicit destination fields are preferred in this order: `target_url`, `deep_link`, `route`, `url`, `web_url`, `native_url`, `app_url`, `mobile_deep_link`, and `deepLink`. Nested `data` payloads are supported.

If no URL is provided, the native router derives a canonical destination from production entity fields:

- `call_id` -> call route
- `conversation_id` -> Messenger conversation
- `reel_id` -> Reel
- `post_id` -> post detail
- `status_id` -> Status
- `group_slug` -> group
- actor public player ID or username for follow/profile events -> profile
- otherwise -> notification inbox

Untrusted external hosts, API paths, static paths, admin paths, protocol-relative paths, and backslash-containing paths remain rejected.

## Device registration and privacy

- Notification permission is requested only by the explicit registration path.
- Foreground refresh never re-prompts after denial.
- Concurrent registration calls are coalesced.
- A stable random installation identifier is stored in SecureStore; it is not a hardware identifier.
- Expo/native push tokens are never logged by these changes.
- A replaced token is revoked before the replacement is registered.
- Logout revokes current and cached endpoints without deleting server-side preference choices.

## Verification performed

Passed:

- `npm run --prefix mobile-native typecheck`
- `EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose`
- `python3 scripts/pulsesoc_native_notification_route_parity_audit.py` (11 shared action/notification routes)
- `python3 scripts/notification_delivery_audit.py`
- `python3 scripts/push_notification_audit.py`
- `python3 scripts/mobile_push_deeplink_audit.py`
- `python3 scripts/pulse_notifications_preferences_audit.py`
- `python3 scripts/pulse_notification_event_coverage_audit.py`
- `python3 scripts/pulsesoc_foreground_notifications_audit.py`
- `python3 scripts/expo_push_receipt_audit.py`
- `python3 scripts/notification_payload_deep_link_audit.py`
- `git diff --check`

Pre-existing audit drift, not caused by this change:

- `pulsesoc_native_notifications_audit.py` requires a literal React Navigation `tabBarBadge`; this app uses the custom global navigation badge implementation.
- `messenger_push_notification_audit.py` requires a legacy WebView service-worker literal for `silent`; it does not test the native changes in this mission.

## Xcode and device evidence

- Xcode: 26.6 (17F113)
- Simulator target: PulseSoc iPhone 16 Pro
- Physical target: iPhone 16 Pro, iOS 18.7.3, paired and available, Developer Mode enabled
- Simulator build: PASS (`Debug`; Xcode `BUILD SUCCEEDED`)
- Simulator install and launch: PASS with Metro
- Simulator deep-link route: PASS; `pulsesoc:///pulse/notifications` opened the native Activity Inbox
- Standalone physical build: PASS; embedded 9.3 MB JavaScript bundle and valid Apple Development signature
- Physical install: PASS; `devicectl` installed bundle `com.pulsesoc.nativeapp.dev`
- Physical launch: PASS; `devicectl` launched `com.pulsesoc.nativeapp.dev` and the process remained present
- Installed display name: `PulseSoc Native Dev`
- Installed version: 1.0 (build 1)
- Production App Store WebView app: not modified or uninstalled
- Native development bundle: separate Debug identity; side-by-side safety retained

Production route boundary smoke:

- `GET /health` -> HTTP 200
- unauthenticated `POST /api/push/subscribe` -> HTTP 401
- unauthenticated `GET /api/pulse/notifications/preferences` -> HTTP 401
- unauthenticated `GET /api/pulse/notifications` -> HTTP 401

These checks confirm live route availability and authentication enforcement; they do not substitute for authenticated delivery observation.

## Physical APNs scenario matrix

No production credentials or second controlled account were available to the automation session. The app was installed and launched, but the following scenarios are therefore recorded as **BLOCKED — NOT OBSERVED**, not PASS or FAIL:

| Scenario | Result | Evidence or blocker |
| --- | --- | --- |
| Foreground delivery | BLOCKED — NOT OBSERVED | Requires authenticated receiver and second controlled sender |
| Background delivery | BLOCKED — NOT OBSERVED | Requires authenticated receiver and second controlled sender |
| Terminated/cold-start delivery | BLOCKED — NOT OBSERVED | Requires authenticated receiver and second controlled sender |
| Authentication-deferred notification tap | BLOCKED — NOT OBSERVED | Requires a real remote notification delivered while signed out |
| Activity Inbox routing | PASS on simulator; physical remote tap BLOCKED | Canonical native deep link opened Activity Inbox on simulator; no physical remote payload available |
| Message notification | BLOCKED — NOT OBSERVED | No second controlled sender credentials |
| Post interaction notification | BLOCKED — NOT OBSERVED | No second controlled sender credentials |
| Reel interaction notification | BLOCKED — NOT OBSERVED | No second controlled sender credentials |
| Status interaction notification | BLOCKED — NOT OBSERVED | No second controlled sender credentials |
| Follow/follow-request notification | BLOCKED — NOT OBSERVED | No second controlled sender credentials |
| Preference suppression/resumption | BLOCKED — NOT OBSERVED | Requires authenticated receiver and live delivery action |
| Token refresh | Static audit PASS; physical server record BLOCKED | Requires authenticated login and server-side device-record observation |
| Logout token revocation | Static audit PASS; physical server record BLOCKED | Requires authenticated login/logout and server-side device-record observation |
| Account switching and recipient isolation | BLOCKED — NOT OBSERVED | Requires two controlled account credentials |
| Duplicate-tap prevention | Static audit PASS; physical remote tap BLOCKED | No repeatable physical remote notification payload |
| No duplicate delivery | BLOCKED — NOT OBSERVED | Requires live provider delivery and second sender |

## Limits of this run

Simulator checks can validate compilation, launch, and deep-link routing but cannot receive a real APNs remote notification. A real two-account delivery test requires a second controlled signed-in account, notification permission on the physical device, and an actual production action. No test credentials or production user data are recorded in this report.

The requested physical two-account matrix remains the only release blocker. Verification/security, subscription/payment, system, creator-activity, follow-acceptance, and missed-call events remain backend-owned; no unsupported client event types were invented for checklist coverage.

## Rollback

Revert the scoped native commit. No database migration, backend route, notification schema, WebView asset, or App Store production bundle identity is changed by this work.
