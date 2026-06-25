# PulseSoc Notification Pipeline Audit

Date: 2026-06-25

## Scope

This audit traced the PulseSoc notification path from native token registration through in-app notification creation, durable push job queueing, Expo/WebPush provider dispatch, provider response logging, receipt cleanup, and deep-link routing.

## Current Pipeline

- Mobile native registration lives in `mobile/pulse-react-native/services/push.ts` and the WebView bridge in `mobile/pulse-react-native/App.tsx`.
- Native builds use Expo push tokens with the EAS project id from `mobile/pulse-react-native/app.json`.
- Browser/PWA push registration uses `/api/push/public-key`, `/api/push/subscribe`, `/api/push/unsubscribe`, and `/static/service-worker.js`.
- Device tokens are mirrored into `user_device_tokens`; active delivery also supports `push_subscriptions`.
- In-app notification records are created by `services/notification_service.py`.
- Durable push jobs are queued and delivered by `services/push_service.py`.
- The Command Center Worker heartbeat drains pending push jobs and reconciles Expo receipts.

## Root Cause Fixed

Chat notification events were queued, but native push metadata was ambiguous:

- `data.type` could remain the generic value `message`.
- `push_type` was `chat_message`.
- Android could fall back to the generic/default channel instead of the high-priority messages channel.

That mismatch made chat delivery look partially wired while device routing, channel behavior, and admin diagnostics could disagree.

## Fixes Applied

- Chat push payloads now use `data.type=chat_message` and `data.push_type=chat_message`.
- The original in-app notification type remains available as `notification_type`.
- Chat push jobs carry `channelId=pulse-messages-v2` unless explicitly overridden.
- Expo push defaults chat payloads to the dedicated high-importance Android messages channel.
- Native token registration includes platform and app version metadata.
- `/api/push/devices` lists the current user's active push devices using hashes only; full tokens are never exposed.

## Security Notes

- Full push tokens are not returned by diagnostics.
- Internal worker tokens are not exposed to browser/mobile code.
- Push logs use token hashes and safe provider summaries.
- Muted conversations suppress noisy push while preserving unread state.
- Private-preview mode uses generic lock-screen copy.
- Invalid Expo tokens are deactivated after provider errors.

## Validation

Passed locally:

- `scripts/push_delivery_runtime_audit.py`
- `scripts/messenger_push_runtime_audit.py`
- `scripts/push_provider_configuration_audit.py`
- `scripts/device_token_registration_audit.py`
- `scripts/mobile_push_deeplink_audit.py`
- `scripts/chat_notification_delivery_audit.py`
- `scripts/push_notification_delivery_audit.py`
- `scripts/pwa_push_audit.py`

The credentials readiness audit was run with local missing-runtime-env allowed; production secrets must be verified in Railway/Expo/Firebase without printing values.
