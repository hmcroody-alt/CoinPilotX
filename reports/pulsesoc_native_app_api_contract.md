# PulseSoc Native App API Contract

Date: 2026-07-04

## Objective

Build the parallel PulseSoc native app against the existing Railway/backend APIs and shared production database without disturbing the current live WebView app.

Current production shell remains:

`iOS WebView App -> pulsesoc.com`

Parallel native track becomes:

`PulseSoc Native App -> PulseSoc APIs -> same backend/database/services`

## Contract Rules

- The native app lives under `mobile-native/`.
- The current `mobile/` WebView/hybrid shell and web backend remain the production path until native QA passes.
- Native code must consume existing server-authoritative API behavior instead of duplicating business logic locally.
- Session, permissions, notification registration, media upload, messaging, and calls must degrade explicitly when backend/device capability is unavailable.
- App Store submission is blocked until login, messaging, notifications, reels/status, and native calls pass real-device QA with no major regressions.

## Phase 1 Endpoints

| Capability | Endpoint | Method | Native owner | Status |
| --- | --- | --- | --- | --- |
| Session restore | `/api/mobile/auth/session` | GET | `src/api/auth.ts` | wired |
| Login | `/api/mobile/auth/login` | POST | `src/api/auth.ts` | wired |
| Signup | `/api/mobile/auth/register` | POST | `src/api/auth.ts` | wired |
| Logout | `/api/mobile/auth/logout` | POST | `src/api/auth.ts` | wired |
| Push registration | `/api/push/subscribe` | POST | `src/api/push.ts` | wired |
| Mission Control | `/api/dashboard/mission-control` | GET | `src/api/pulse.ts` | wired |
| Messenger list | `/api/pulse/messages/conversations` | GET | `src/api/pulse.ts` | wired |
| Conversation detail | `/api/pulse/messages/<conversation_id>` | GET | `src/api/pulse.ts` | wired |
| Send message | `/api/pulse/messages/<conversation_id>/send` | POST | `src/api/pulse.ts` | wired |
| Pulse AI chat | `/api/pulse/assistant/chat` | POST | `src/api/pulse.ts` | wired |
| Profile | `/api/pulse/profile/me` | GET | `src/api/pulse.ts` | wired |

## Phase 2 Endpoint Targets

| Capability | Existing API target | Native requirement |
| --- | --- | --- |
| Reels feed | `/api/pulse/reels/feed` | Native full-screen video renderer with smooth scroll and audio policy parity |
| Reel creation | `/api/pulse/reels/create`, `/api/pulse/reels/create-from-camera` | Native camera capture, local compression, upload progress, server processing status |
| Status creation/viewer | Status APIs and camera/media upload APIs | Native viewer, creator, sound policy, upload retry, and content-preserving preview |
| Media upload | `/api/pulse/media/upload`, `/api/pulse/media/mux/direct-upload` | Native image/video picker, camera, compression, direct-upload completion |

## Phase 3 Endpoint Targets

| Capability | Existing API target | Native requirement |
| --- | --- | --- |
| Live rooms | `/api/pulse/live`, `/api/pulse/live-now`, `/api/pulse/live/stream` | Native live discovery and room state |
| LiveKit token | Existing LiveKit token routes in Pulse live/call system | Native LiveKit SDK, camera flip, mic/speaker/Bluetooth controls |
| Calls | PulseSoc call system APIs | Full-screen incoming calls, native ringing, background audio handling, stable reconnect |

## Phase 4 Endpoint Targets

| Capability | Existing API target | Native requirement |
| --- | --- | --- |
| Notifications | `/api/pulse/notifications`, `/api/pulse/notifications/preferences` | Native notification center, deep links, badge sync |
| Intelligence alerts | PulseSoc Intelligence notification/event APIs | Lock-screen-ready alert delivery and native alert detail |
| Growth Center | `/api/pulse/growth` and related ads/growth APIs | Native dashboard surfaces without duplicating ranking/business logic |
| Crypto/market alerts | `/api/crypto/*` and alert APIs | Native alert CRUD, push wiring, market refresh discipline |
| Premium | `/api/premium/status`, `/api/subscriptions/*` | Native status and upgrade routing with existing billing constraints |
| Creator tools | Dashboard/content/media APIs | Native creation tools after Phase 2 media QA |

## Session Model

The foundation uses the existing mobile auth endpoints and stores the backend session cookie in secure native storage. The server remains authoritative for account status, premium state, permissions, and feature access.

## Push Model

The foundation requests native notification permission through Expo Notifications and registers the Expo token with `/api/push/subscribe` using provider `expo` and `device_type` `native`. APNs/FCM credential readiness remains a backend/provider QA requirement before production release.

## Media Model

Native media must use device camera/media APIs and server upload endpoints. The native app should not rely on WebView media controls for Phase 2 surfaces. Compression, upload progress, retry, and processing status must be verified on real devices.

## Call Model

Native calls must use the native LiveKit SDK track. WebView calling surfaces stay live until the native implementation proves incoming call UI, ringing, background audio, reconnect, camera flip, mic, speaker, and Bluetooth behavior on supported devices.

## QA Gates

- Authenticated login/signup/session restore works against the real backend.
- Messenger list, conversation detail, send, read/sync, and push delivery work on real devices.
- Native push permission, registration, notification display, deep link, sound, vibration, and badge behavior pass.
- Reels/status playback and creation are smooth with native media rendering.
- Calls are native and stable through foreground/background transitions.
- No current WebView production path is modified or blocked by the native app work.
