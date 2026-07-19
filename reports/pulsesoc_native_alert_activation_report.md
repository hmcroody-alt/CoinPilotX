# PulseSoc Native Alert Activation Report

Date: 2026-07-19

Scope: PulseSoc native alerts, notifications, push registration, badge counts, deep links, and activity inbox reuse. This pass does not create a new alert backend.

## Executive summary

PulseSoc already has a production notification pipeline in the web/backend code:

- `/api/pulse/notifications`
- `/api/pulse/notifications/unread-count`
- `/api/pulse/notifications/<id>/resolve`
- `/api/pulse/notifications/<id>/read`
- `/api/pulse/notifications/read-all`
- `/api/pulse/notifications/preferences`
- `/api/push/subscribe`
- `/api/push/unsubscribe`
- `/api/pulse/sync/events`

The native app now stays on those contracts and avoids native-only alert infrastructure. The code changes in this pass focus on:

- safer native push registration metadata through the existing push subscribe endpoint;
- per-device native logout cleanup through the existing push unsubscribe endpoint;
- preserving global push preferences during logout cleanup;
- canonical total/alert/chat badge count handling;
- an executable audit proving the native route reuse and no duplicate alert backend paths.

## Implementation matrix

| Alert class | Backend owner | Native source | Push path | Realtime path | Deep-link target | Default/preference source | Status |
|---|---|---|---|---|---|---|---|
| Messages / DMs / group chat | `notification_service`, `pulsesoc_notification_system`, Communications V2 | `mobile-native/src/api/activity.ts`, `mobile-native/src/api/messenger.ts` | `/api/push/subscribe` + notification delivery jobs | `/api/pulse/sync/events` invalidates `messenger`, `activity`, `notifications` | `/pulse/messages`, `/pulse/messages/<conversation_id>` | `notification_preferences`, muted conversations | Code-wired |
| Calls | call APIs + notification OS semantic target resolution | Activity Inbox active calls summary + `Call` route | existing push routes | sync invalidates `calls`, `activity`, `notifications` | `/pulse/calls/<call_id>` | notification OS rules, quiet hours except urgent priority | Code-wired |
| Social reactions/comments/follows | notification OS + legacy pulse notification mirror | Activity Inbox social category, post/status/reel routing | existing push routes | sync invalidates `activity`, `notifications` | `/pulse/post/<id>`, `/pulse/status/<id>`, `/pulse/reels/<id>`, `/pulse/profile/<id>` | notification category preferences, actor block/mute checks | Code-wired |
| Live / cohost / creator events | Live services + notification OS semantic targets | native routing to `LiveDetail`, Reels live, or web studio fallback | existing push routes | sync invalidates `activity`, `notifications` and live-related views | `/pulse/live/<id>`, `/pulse/reels?live=<id>`, `/pulse/live/studio/<id>` | notification category preferences | Code-wired |
| Crypto / market / intelligence alerts | `alert_engine_service`, `/api/crypto/alerts`, `/api/alerts/*` | `mobile-native/src/api/alerts.ts`, `AlertManagementScreen` | existing alert delivery jobs and push routes | sync invalidates `intelligence`, `activity`, `notifications` | `/dashboard/crypto/alerts`, `/pulse/alerts/<id>` | alert channels + notification preferences | Code-wired |
| Security / account | notification OS security categories | Activity Inbox safety/security routes | existing push routes | sync invalidates `safety`, `activity`, `notifications` | `/account/security`, `/dashboard/account/security` | security preferences; urgent events can bypass noisy suppression rules as backend allows | Code-wired |
| Marketplace / orders / purchases | marketplace/order APIs + notification OS | Activity Inbox marketplace category + buyer/seller routes | existing push routes | sync invalidates `orders`, `marketplace`, `seller_inventory`, `activity` | `/pulse/marketplace`, `/pulse/purchases`, `/dashboard/economy/subscriptions` | notification preferences + delivery jobs | Code-wired |
| Premium / billing | billing/premium backend + notification OS | Premium/native routing and Activity Inbox | existing push routes | sync invalidates `premium`, `activity`, `notifications` | `/pulse/premium`, billing/order routes | notification preferences | Code-wired |
| Verification / trust safety | verification/trust safety backend + notification OS | Verification Center, Safety Hub, Activity Inbox | existing push routes | sync invalidates `verification`, `safety`, `activity`, `notifications` | `/pulse/verification`, `/pulse/safety`, `/trust-center` | notification preferences, block/mute rules | Code-wired |
| System announcements | notification OS | Activity Inbox and notification center | existing push routes | sync invalidates `activity`, `notifications` | `/pulse/notifications` fallback | default notification preferences | Code-wired |

## Backend reuse verification

The native app does not define a separate notification server, native-only alert IDs, or a native-only alert route. It consumes the existing backend contracts:

- List/pagination basis: `/api/pulse/notifications?limit=...` for inbox lists and `/api/pulse/sync/events?after_id=...` for realtime delta invalidation.
- Read state: `/api/pulse/notifications/<id>/read` and `/api/pulse/notifications/read-all`.
- Dismiss/delete: `/api/pulse/notifications/<id>`.
- Target resolution: `/api/pulse/notifications/<id>/resolve`.
- Preferences: `/api/pulse/notifications/preferences` and `/api/notification-preferences`.
- Push registration: `/api/push/subscribe`.
- Push cleanup: `/api/push/unsubscribe`.

## Changes made

### Native push registration

`mobile-native/src/api/push.ts` now:

- requests notification permission only on a physical device;
- keeps Expo push token registration;
- also collects the platform native push token when Expo exposes it;
- sends `platform`, `environment`, `app_version`, `device_label`, `native_provider`, `apns_token`/`fcm_token` metadata to the existing `/api/push/subscribe` route;
- caches the native registration in SecureStore for later cleanup;
- exposes `unregisterPushDevice()` for logout/account switching.

### Logout/account switching cleanup

`mobile-native/src/session/auth.ts` now calls `unregisterPushDevice({ preservePreferences: true, reason: "logout" })` before logout/logout-all clears session credentials.

### Backend unsubscribe safety

`bot.py` keeps the existing `/api/push/unsubscribe` route but adds an optional `preserve_preferences` flag. Existing callers that do not pass this flag still disable push preferences. Native logout cleanup passes the flag so removing one device does not globally turn off push for the account.

### Canonical badge counts

`mobile-native/src/api/notifications.ts` now exposes:

- `alertUnreadCount`
- `chatUnreadCount`
- `totalUnreadCount`

`AppNavigator` uses total unread for the OS/header activity badge, chat unread for Messenger, and alert unread for alert chips. `ActivityInbox` uses total unread when the backend returns `total_unread_count`.

## Default-on coverage

The backend notification OS owns default provisioning through `ensure_user_notification_defaults()` and `backfill_notification_defaults()`. That code inserts missing default rows without overwriting existing rows and keeps quiet hours/muted user/muted conversation logic in the backend rules path.

This pass does not run a production migration. The code audit verifies the functions exist and are connected; production dry-run/backfill evidence still needs to be gathered from the production environment before this mission can be considered fully complete.

## Deep-link handling

Native notification routing supports the main backend targets:

- messages
- calls
- live
- reels
- statuses
- posts/comments
- profiles
- crypto alerts
- security/account
- marketplace
- purchases/billing
- premium
- notifications fallback

Unsafe schemes such as `javascript:`, `data:`, and `file:` are rejected in native routing.

## Production evidence

Not proven in this shell:

- live production push delivery to a physical iPhone;
- APNs entitlement/provider acceptance;
- production migration/dry-run results for all existing users;
- two-user notification delivery timing;
- actual server delivery logs from Railway/production;
- physical iPhone badge count after a real backend notification;
- App Store/TestFlight push environment verification.

Those require authenticated production/device access and real push delivery logs.

## Automated audit

Added:

- `scripts/pulsesoc_native_alert_activation_audit.py`

The script writes:

- `reports/pulsesoc_native_alert_activation_audit.json`

The audit checks route reuse, backend default provisioning, push registration/cleanup, realtime sync, deep-link coverage, activity coverage, canonical badge helpers, and absence of native-only alert backend routes/storage.

## Risk remaining

- The backend still contains both legacy pulse notifications and the newer notification OS. The native client reads the combined route, which is correct, but production logs should be monitored for duplicate delivery jobs.
- Push delivery cannot be claimed complete until a real device receives a notification from production.
- The default-on migration should be run with dry-run/reporting before broad production execution.
