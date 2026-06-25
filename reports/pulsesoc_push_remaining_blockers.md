# PulseSoc Push Remaining Blockers

Date: 2026-06-25

## Remaining Blockers

1. Real-device iPhone and Android lock-screen proof is still required.
2. Production Railway variables must be verified in the live service runtime without exposing values.
3. If credential variables were recently added or changed, the relevant Railway services need a clean restart/redeploy.
4. PWA push requires live VAPID variables and a browser/device combination that supports web push.
5. Firebase APNs and VAPID settings should be verified if direct FCM/Web Push fallback is required outside the Expo primary path.

## What Is Fixed

- Chat message push payloads now identify as `chat_message`.
- Chat payloads use the dedicated Android messages channel.
- Expo payloads include sound, high priority, badge, deep link, HTTPS fallback, conversation id, and message id.
- Native registration sends platform/app metadata.
- User-facing push diagnostics expose only safe hashes and active-device metadata.
- Audits cover token registration, queued delivery, provider payloads, deep links, PWA service worker wiring, muted policy, private previews, and invalid token cleanup.

## What Is Not Claimed

This change does not claim that Apple/Google delivered a real notification to a physical lock screen. That final proof must come from device QA after deployment.
