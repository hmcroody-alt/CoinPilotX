# PulseSoc Native Profile Routing P0 Report

Date: 2026-07-19

## Failure trace

| Stage | File/component | Input value | Output value before fix | Expected value | Defect |
| --- | --- | --- | --- | --- | --- |
| Search API | `bot.py` `/api/pulse/search` | Existing user match | Creator result with `id` and `/pulse/profile/<username-or-id>` but no stable native identity fields in the normalized native type | `user_id`, `public_player_id`, `public_pulse_id`, `username`, canonical `/pulse/@<handle>` | Native had to infer profile identity from a route string. |
| Search result normalization | `mobile-native/src/api/search.ts` | Creator result | Generic result model with `id`, `title`, `url` | Dedicated profile-target fields preserved | `id` could mean any result type, so profile routing could drift. |
| Search tap | `mobile-native/src/screens/SearchScreen.tsx` | Creator card | `routeNotificationTarget(item.url)` | Direct native `ProfileDetail` navigation with canonical target | Existing users were sent through generic route resolution. |
| Route resolution | `mobile-native/src/navigation/notificationRouting.ts` | `/pulse/@<handle>` | Unsupported route/fallback path | Native profile detail route | Web canonical profile deep links were not recognized. |
| Profile fetch | `mobile-native/src/screens/ProfileScreen.tsx` | `profileKey` string | Raw `/api/pulse/profile/<profileKey>` and raw error display | Resolved target, canonical cache key, mapped error state | Backend route/service errors looked like missing profiles. |
| UI fallback | `ProfileScreen.tsx` | Backend 404/5xx/generic error | `Profile unavailable` + `Open Web Profile` | Retry native profile first, controlled web fallback only for supported cases | WebView fallback was too prominent and error state was misleading. |

## WebView/native contract comparison

| Operation | WebView implementation | Backend handler | Native previous implementation | Required native implementation |
| --- | --- | --- | --- | --- |
| Search user | `/api/pulse/search` and Web profile links | `api_pulse_search` | Generic `PulseSearchResult` fields only | Preserve `user_id`, `public_player_id`, `public_pulse_id`, `username`. |
| Open current user | `/pulse/profile` redirects to canonical | `pulse_my_profile_page` | Bottom tab Profile | Keep current-user tab separate. |
| Open another user | `/pulse/@<handle>`, `/pulse/profile/<key>` | `pulse_profile_page` | `ProfileDetail` with a raw `profileKey` | Use shared resolver and route to `ProfileDetail`. |
| Open by user ID | `/pulse/id/<id>` supported | `pulse_profile_page` | Not recognized as native deep link | Resolve numeric `user_id` as canonical target. |
| Open by username/handle | `/pulse/@<handle>` | `pulse_profile_page` | `/pulse/@` unsupported in native route resolver | Native recognizes `/pulse/@`, `/pulse/u`, `/pulse/id`, `/pulse/profile`. |
| Profile API | `/api/pulse/profile/<path:profile_key>` | `api_pulse_public_profile` | Called with raw route-derived key | Called with canonical user ID or public handle. |
| Refresh profile | Same API | `api_pulse_public_profile` | Raw cache key | Canonical `user:<id>`/public alias cache keys. |
| Follow/unfollow | `/api/pulse/follows/toggle` | existing backend | Already wired | Preserved. |
| Message | `/api/messages/start` | existing backend | Already wired from profile payload | Preserved. |
| Block/report | Safety Hub | existing backend | Existing Safety routes | Preserved; route target remains canonical public id where available. |

## Fix summary

- Added `mobile-native/src/api/profileTarget.ts` as the one profile target resolver.
- Native now resolves profile targets from user ID, public Pulse ID, username, and canonical web/deep-link paths.
- Search profile results navigate directly to `ProfileDetail`; non-profile results keep generic route fallback.
- Notification routing, native route actions, and linking reuse the same profile URL resolver.
- Backend search now includes canonical identity fields and returns canonical profile URLs.
- Backend native profile API uses a strict resolver that does not resolve by display name or full name.
- Profile screen maps 401/403/404/410/429/5xx/offline to distinct states and hides internal service-router messages.
- Profile cache writes canonical aliases so a prior user profile cannot leak into another route key.

## Verification matrix

| Check | Result |
| --- | --- |
| Search result includes canonical user ID | Automated audit |
| Navigation uses canonical target | Automated audit |
| Current user route separate from other-user route | Automated audit |
| `/pulse/@<handle>` deep link compatibility | Automated audit |
| Production profile API reuse | Automated audit |
| No display-name profile lookup | Automated audit |
| 403/404/410 distinction | Automated audit |
| Web fallback not primary | Automated audit |
| Cache isolation by canonical profile key | Automated audit |

Manual two-account production validation is still required for private/blocked/deactivated fixtures because those states require live account data.
