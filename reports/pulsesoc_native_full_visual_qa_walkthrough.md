# PulseSoc Native Full Visual QA Browser Walkthrough

Date: 2026-07-06

Browser surface: built-in QA browser.

External browser use: none. Chrome Incognito was not used.

Server command:

- `npm run --prefix mobile-native web:qa`

Server verification:

- `curl -I --max-time 10 http://localhost:8094/Login`
- Result: HTTP 200.

## Authentication State

The web QA build was configured against `https://pulsesoc.com`.

The local QA simulator-login helper did not activate because that helper is intentionally guarded behind a local API base URL. The normal QA account login attempt remained on the Login screen. No credentials are recorded in this report.

Authenticated native content was therefore not verified in this pass. The pass validates visible browser routing, safe auth gating, and console/runtime health across all requested native routes.

## Full native walkthrough coverage %

Route coverage: 100%.

Requested routes walked: 49.

Screens confirmed visible: 1 signed-out Login/Auth surface.

Screens blocked by auth/session/API: 48 signed-in surfaces correctly auth-gated.

Broken routes: 0.

Blank screens: 0.

Navigation errors: 0.

## Screens Confirmed Visible

| Area | Route | Result |
| --- | --- | --- |
| Login/Auth | `/Login` | Visible signed-out native login shell |

## Screens Auth-Gated Correctly

| Area | Route | Result |
| --- | --- | --- |
| Home Feed | `/pulse` | Auth-gated |
| Post Detail | `/pulse/post/1` | Auth-gated |
| Feed Composer | `/pulse?composer=1` | Auth-gated |
| Messenger | `/pulse/messages` | Auth-gated |
| Chat Detail | `/pulse/messages/1` | Auth-gated |
| Calls | `/pulse/calls/qa-call-1` | Auth-gated |
| Full-screen Incoming Calls layer | `/pulse?qa_incoming_call=1&call_id=qa-call-1&caller=PulseSoc%20QA&call_type=video` | Auth-gated |
| Activity Inbox | `/pulse/inbox` | Auth-gated |
| Notifications | `/pulse/notifications` | Auth-gated |
| Notification Preferences | `/pulse/settings/notifications` | Auth-gated |
| Profile | `/pulse/profile` | Auth-gated |
| Profile Edit | `/pulse/profile/edit` | Auth-gated |
| Reels | `/pulse/reels` | Auth-gated |
| Reel Detail | `/pulse/reels/1` | Auth-gated |
| Status Viewer | `/pulse/status` | Auth-gated |
| Status Detail | `/pulse/status/1` | Auth-gated |
| Status Creator | `/pulse/status?create=1` | Auth-gated |
| Camera Studio | `/pulse/camera/photo?target=feed` | Auth-gated |
| Media Viewer | `/pulse/marketplace/1?media=1` | Auth-gated |
| Marketplace | `/pulse/marketplace` | Auth-gated |
| Seller Store | `/pulse/seller-store?title=Seller%20%2F%20Store` | Auth-gated |
| Seller Listing Composer | `/pulse/marketplace/create?title=Create%20Listing` | Auth-gated |
| Seller Inventory | `/pulse/seller-store?mode=inventory` | Auth-gated |
| Buyer Orders | `/pulse/orders` | Auth-gated |
| Buyer Order Detail | `/pulse/orders/1` | Auth-gated |
| Search/Discovery | `/pulse/search` | Auth-gated |
| Saved/Collections | `/pulse/saved` | Auth-gated |
| Groups/Communities/Rooms | `/pulse/groups` | Auth-gated |
| Group Detail | `/pulse/groups/qa-group` | Auth-gated |
| Live Viewer | `/pulse/live` | Auth-gated |
| Live Detail | `/pulse/live/1` | Auth-gated |
| Events/Scheduled Live | `/pulse/events` | Auth-gated |
| Premium | `/pulse/premium` | Auth-gated |
| Creator Studio | `/pulse/creator-studio` | Auth-gated |
| Content Planner | `/pulse/content-planner` | Auth-gated |
| Content Planner/Draft Studio | `/dashboard/creator/draft-studio` | Auth-gated |
| Growth Center | `/pulse/growth` | Auth-gated |
| Intelligence/Alerts | `/dashboard/intelligence` | Auth-gated |
| Alert Management | `/pulse/alerts` | Auth-gated |
| Account/Security/Privacy | `/pulse/settings/security` | Auth-gated |
| Trust/Safety/Support | `/pulse/help` | Auth-gated |
| Verification Center | `/pulse/verification` | Auth-gated |
| Account Health/Appeals | `/pulse/account-health` | Auth-gated |
| Safety Hub | `/pulse/safety` | Auth-gated |
| Courses/Learning | `/pulse/courses` | Auth-gated |
| Course Detail | `/pulse/courses/1` | Auth-gated |
| Settings | `/pulse/settings` | Auth-gated |
| Pulse AI | `/pulse/ai` | Auth-gated |

## Console Notes

No route-breaking browser errors were observed during navigation.

Repeated warnings observed:

- `expo-notifications` push token listener is not fully supported on web.
- React Native Web warns that `shadow*` style props are deprecated in favor of `boxShadow`.
- `expo-av` is deprecated and should move to `expo-audio` / `expo-video` before the relevant SDK deadline.

These warnings do not block native route rendering or auth-gate behavior in this pass.

Server shutdown note:

- After the walkthrough completed, stopping Metro emitted a local HMR graph error: `Error: Got unexpected undefined`.
- This happened during shutdown after route verification and did not block the browser walkthrough.
- Recommended follow-up: include Metro HMR shutdown stability in the next web-QA tooling pass if it repeats.

## Screenshots

Saved under:

- `reports/screenshots/native-full-qa-2026-07-06/login-auth.png`
- `reports/screenshots/native-full-qa-2026-07-06/home-feed.png`
- `reports/screenshots/native-full-qa-2026-07-06/activity-inbox.png`
- `reports/screenshots/native-full-qa-2026-07-06/marketplace.png`
- `reports/screenshots/native-full-qa-2026-07-06/seller-store.png`
- `reports/screenshots/native-full-qa-2026-07-06/buyer-orders.png`
- `reports/screenshots/native-full-qa-2026-07-06/camera-studio.png`
- `reports/screenshots/native-full-qa-2026-07-06/verification-center.png`
- `reports/screenshots/native-full-qa-2026-07-06/account-health-appeals.png`
- `reports/screenshots/native-full-qa-2026-07-06/pulse-ai.png`

Machine-readable route data:

- `reports/screenshots/native-full-qa-2026-07-06/walkthrough-results.json`

## LogiNexus Visual Quality

Confirmed visible state:

- Login/Auth preserves the dark premium PulseSoc native shell and avoids generic browser fallback.

Blocked state:

- Signed-in LogiNexus visual surfaces could not be judged in this browser pass because the production-configured web build did not establish an authenticated session.

## Remaining QA Need

Highest-value QA fix:

- Run the visible QA browser walkthrough against a local or staging API base with a runtime-only QA account/session so authenticated screens can be visually reviewed without committing credentials.

Release blockers remain separate:

- Physical push notification delivery.
- Device camera/mic behavior.
- Native video playback.
- Multi-device event ordering.
