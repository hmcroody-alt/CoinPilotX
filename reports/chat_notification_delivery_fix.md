# PulseSoc Chat Notification Delivery Fix

Date: 2026-06-20

## Scope

Fix chat notification delivery end-to-end for PulseSoc Messenger without committing before real device QA. This report covers the local implementation, local/runtime audits, and remaining phone QA required before merge.

## Exact Root Cause

Multiple issues contributed to silent chat delivery:

1. The legacy Pulse chat send path created notification side effects inside the same open database transaction as the message save. That made the notification/push path vulnerable to transaction timing, cross-connection visibility, and failures that could affect an already-saved message.
2. Several legacy and compatibility chat routes saved messages but did not consistently route through the same notification, push, realtime, and Command Center handoff path.
3. Command Center `message_created` dispatch was not consistently emitted from the canonical legacy Pulse message finalizer.
4. Legacy chat notification dedupe used conversation-like identifiers, which could suppress repeated chat messages in the same conversation. The fix dedupes by `message_id`.
5. Mobile foreground suppression/deep-link parsing did not recognize the current `?conversation=` query key. It only parsed older `conversation_id`/`conversationId` shapes.

## Why Push Showed `not_configured` Despite Variables Existing

There are two different delivery records:

- `notification_delivery_logs`: older/legacy multi-channel evidence. Some rows say `push not_configured` when the legacy Web Push/VAPID path or optional provider path is missing.
- `push_delivery_jobs`: durable Messenger push queue. This is the active chat push path. It can show `sent`, `queued`, `skipped`, `not_configured`, retry, and dead-letter states.

Native mobile delivery currently uses Expo tokens through the Expo push endpoint. APNs/FCM variables on Railway are readiness inputs and may be needed by Expo/build configuration, but the current server-side native sender path is Expo-first. PWA/browser push uses VAPID. Therefore, APNs/FCM variables existing on the Command Center Worker did not by itself prove chat notification delivery.

The admin diagnostics now separate:

- missing active token/subscription
- provider setup skip
- Expo native readiness
- Web Push/VAPID readiness
- APNs readiness
- FCM readiness
- provider response summaries
- Expo receipt checks

No secrets, full tokens, private keys, or database URLs are displayed.

## Files Changed

- `bot.py`
  - Added post-commit `pulse_finalize_message_delivery`.
  - Added legacy bridge `pulse_finalize_legacy_message_delivery`.
  - Wired legacy/compatibility send routes into the post-commit notification path.
  - Added Command Center `message_created` handoff per recipient.
  - Added message-specific push dedupe, badge payload, native deep link, web fallback, mute suppression, and safe trace logging.
  - Added `/admin/system/chat-delivery` alias.
  - Upgraded `/admin/messages-health` with chat push provider diagnostics, provider summaries, Expo receipts, user lookup, and message notification lookup.
- `mobile/pulse-react-native/services/push.ts`
  - Updated conversation ID extraction to support `conversation`, `conversation_id`, and `conversationId`.
- `scripts/chat_notification_delivery_audit.py`
  - Added static coverage for legacy finalizer, message-specific dedupe, Command Center dispatch, mute suppression, and deep links.
- `scripts/push_provider_configuration_audit.py`
  - Added provider readiness audit for Expo/WebPush/APNs/FCM configuration paths without exposing secrets.
- `scripts/device_token_registration_audit.py`
  - Added runtime audit for Expo token registration, token refresh, Web Push registration, provider classification, and revocation.
- `reports/chat_notification_delivery_fix.md`
  - This report.

## Local Validation Performed

Passed:

```text
venv/bin/python -m py_compile bot.py services/notification_service.py services/push_service.py services/command_center_client.py services/command_center_worker/app.py scripts/chat_notification_delivery_audit.py scripts/push_provider_configuration_audit.py scripts/device_token_registration_audit.py
venv/bin/python scripts/chat_notification_delivery_audit.py
venv/bin/python scripts/push_provider_configuration_audit.py
venv/bin/python scripts/device_token_registration_audit.py
venv/bin/python scripts/push_notification_delivery_audit.py
venv/bin/python scripts/push_delivery_queue_audit.py
venv/bin/python scripts/command_center_messaging_core_audit.py
venv/bin/python scripts/command_center_realtime_transport_audit.py
venv/bin/python scripts/messenger_push_runtime_audit.py
venv/bin/python scripts/pulse_messages_two_user_delivery_audit.py
```

Mobile validation passed:

```text
npm run typecheck
npm run audit:notifications
npm run audit:firebase
```

Broader regression audits passed:

```text
venv/bin/python scripts/command_center_service2_worker_audit.py
venv/bin/python scripts/command_center_presence_audit.py
venv/bin/python scripts/command_center_notifications_audit.py
venv/bin/python scripts/command_center_security_audit.py
venv/bin/python scripts/command_center_ai_messaging_audit.py
venv/bin/python scripts/command_center_redis_audit.py
venv/bin/python scripts/notification_health_truth_audit.py
venv/bin/python scripts/pulse_badge_separation_audit.py
venv/bin/python scripts/apple_review_compliance_audit.py
```

Repository hygiene:

```text
git diff --check
```

Route smoke passed with authenticated local sessions:

```text
/pulse/messages
/api/pulse/communications/v2/conversations?limit=10
/api/pulse/notifications/unread-count
/api/push/status
/api/service/health
/admin/messages-health
/admin/system/chat-delivery
```

## Runtime Proof From Local Audits

The runtime Messenger push audit verified:

- message send succeeds
- provider is not called inline during message send
- durable push job is queued
- worker processes queued push job
- only the recipient device token is pushed
- Expo payload includes sound, high priority, interruption level, badge, conversation ID, message ID, sender ID, native deep link, and web fallback
- recently active users are not incorrectly suppressed
- muted conversations suppress noisy push and keep notification state
- private-preview mode uses generic lock-screen copy
- invalid Expo tokens are deactivated
- endpoint unsubscribe revokes active delivery eligibility

The two-user delivery audit verified:

- recipient can load the sent message
- recipient realtime feed includes message events
- typing events arrive
- read events arrive
- reaction path works
- message notification record exists
- in-app delivery record exists
- durable push job exists
- message notification appears in the Messages filter

## Provider Response Summary

Local provider calls were mocked for safety. The audited provider payload shape is correct for Expo native delivery and records safe status into `push_delivery_jobs.provider_response`.

Production still requires real provider confirmation after deploy:

- `PUSH_TRACE stage=provider_request`
- `PUSH_TRACE stage=provider_response`
- `PUSH_TRACE stage=provider_receipt` when Expo receipts are available
- `/admin/system/chat-delivery` recent push attempts and Expo receipt checks

## Security Checks

- No internal Command Center token is exposed to browser code.
- No push tokens are displayed in admin diagnostics.
- Logs use endpoint hashes and trace IDs instead of raw tokens.
- Private previews suppress lock-screen message content.
- Blocked recipients are skipped.
- Muted conversations suppress noisy push while preserving message/unread state.
- Invalid tokens are disabled.
- Provider readiness checks do not print secrets.

## Remaining Required QA Before Commit

Do not commit or push until real device QA passes:

1. Deploy these changes to production or a production-equivalent QA environment.
2. Confirm the deployed commit is active.
3. On `/admin/system/chat-delivery`, verify the recipient has an active Expo token or Web Push subscription.
4. User A sends User B a timestamped message while User B is in the active chat.
5. User B sees the message without refresh and no duplicate native notification.
6. User B stays on Messages inbox; User A sends a message; preview and unread badge update.
7. User B backgrounds the iPhone app; User A sends a message; native notification appears with sound/vibration if OS allows.
8. User B locks iPhone; User A sends a message; lock-screen notification appears and opens the correct conversation.
9. Repeat background and locked tests on Android.
10. Install/open PWA, close the browser, and verify Web Push delivery if browser permissions allow it.
11. Test muted conversation: no noisy push, unread remains.
12. Test invalid/missing token path: admin diagnostics show the exact skip/failure reason.

## Remaining Risks

- Real OS behavior still depends on notification permission, Focus/Do Not Disturb, app install mode, Expo token validity, APNs/FCM project setup, and Android notification channel settings.
- APNs/FCM readiness is visible but server-side native sends are still Expo-first.
- Existing local database audit data may contain synthetic test users/messages from runtime audits.
