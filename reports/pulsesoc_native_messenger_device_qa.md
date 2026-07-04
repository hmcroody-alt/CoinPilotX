# PulseSoc Native Messenger Device QA + Hardening

Date: 2026-07-04

## Scope

This pass hardens the existing native Messenger foundation. Do not add another major feature. No major feature was added in this pass, and it does not change production WebView paths, web templates, backend business logic, database behavior, authorization rules, moderation rules, notification fanout, or media pipeline behavior.

The native Messenger app remains a client for the existing PulseSoc platform. Server APIs remain authoritative for auth, conversation membership, permissions, validation, message persistence, receipts, typing/presence payloads, media validation, uploads, moderation, and notifications.

## Device Tooling Status

Real simulator/device QA could not be completed in this environment.

- iOS simulator: blocked. `xcrun simctl` returned `unable to find utility "simctl", not a developer tool or in PATH`.
- Android device/simulator: blocked. `adb` returned `command not found`.

No device-only item is marked as passed. The checks below are recorded as source/runtime hardening evidence until a real iOS or Android target is available.

## QA Matrix

| Area | Status | Evidence |
| --- | --- | --- |
| Long conversation scrolling performance | Source-hardened, not device-verified | Chat `FlatList` uses `initialNumToRender`, `maxToRenderPerBatch`, `windowSize`, `updateCellsBatchingPeriod`, `removeClippedSubviews`, and memoized reversed data. |
| Conversation search | Source-verified, not device-verified | Uses `GET /api/pulse/messages/search`; no duplicate search backend logic. |
| Pull-to-refresh | Source-verified, not device-verified | Conversation list and chat detail use native `RefreshControl`. |
| Offline cache restore | Hardened, not device-verified | Conversation and message cache parse failures now clear corrupt cache and return empty state instead of crashing. |
| Send message | Source-verified, not device-verified | Uses `POST /api/pulse/messages/<conversation_id>/send` with optimistic local state and server reconciliation. |
| Failed-send retry | Source-verified, not device-verified | Failed local messages render retry action and resend through the same server endpoint. |
| Read receipts / seen calls | Source-verified, not device-verified | Uses `POST /api/pulse/messages/<conversation_id>/seen` after load/sync. |
| Typing indicator | Source-verified, not device-verified | Uses `POST /api/pulse/messages/<conversation_id>/typing`; clears typing on timer and unmount. |
| Sync polling | Hardened, not device-verified | Sync polling skips inactive/background app state and resumes sync on foreground. |
| Push deep link into conversation | Source-verified, not device-verified | Linking supports `pulsesoc://pulse/messages/:conversationId` and `https://pulsesoc.com/pulse/messages/:conversationId`. |
| Image picker upload | Source-verified, not device-verified | Uses Expo image picker and existing `POST /api/pulse/messages/media/upload`. |
| File picker upload | Source-verified, not device-verified | Uses Expo document picker and existing `POST /api/pulse/messages/media/upload`. |
| Voice recording upload | Source-verified, not device-verified | Uses Expo AV recording and existing `POST /api/pulse/messages/media/upload` with `voice=true`. |
| Permission denied states | Source-verified, not device-verified | Photo and microphone denial paths show native alerts and do not call upload. |
| Large attachments | Server-authoritative, not device-verified | Native client sends through existing media upload route; server/media service remains responsible for size/type enforcement. |
| Upload failure handling | Hardened, not device-verified | Upload failures show native alert, do not fake success, and upload buttons are disabled while an upload is in flight. |
| App foreground/background recovery | Hardened, not device-verified | AppState listener resumes sync or load on return to active state. |

## Hardening Changes

- Added corrupt-cache safe handling to `loadCachedConversations()` and `loadCachedMessages()`.
- Added foreground/background recovery using React Native `AppState`.
- Prevented duplicate attachment uploads while an upload is already in flight.
- Added long-thread `FlatList` performance settings and memoized visible message order.
- Preserved the reuse-first route contract through `mobile-native/src/api/messenger.ts`.

## Remaining Device QA

These must still be verified on real hardware or an available simulator before Messenger is considered production-ready:

- iOS and Android long-thread scroll frame rate.
- Real login/session plus conversation open on device.
- Search and pull-to-refresh with authenticated production data.
- Offline cache restore after force-close and network loss.
- Text send, failure retry, seen receipts, typing, and sync polling against a live account.
- Push notification tap into the correct conversation.
- Photo permission denied/accepted states and image upload.
- File picker selection, large attachment rejection, and upload failure handling.
- Microphone denied/accepted states and voice upload.
- Foreground/background recovery while messages arrive.

## Current Release Position

Native Messenger is harder and safer than the previous milestone, but it is still not cleared to replace WebView Messenger. The blocker is device access, not source readiness.
