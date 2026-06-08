# Pulse Mobile Production Parity Audit

Date: 2026-06-07

## Production API Configuration

- Mobile production build profile: `EXPO_PUBLIC_PULSE_API_BASE_URL=https://pulsesoc.com`
- Expo app extra config: `apiBaseUrl=https://pulsesoc.com`
- Runtime mobile config defaults: `https://pulsesoc.com`
- Railway production variable check:
  - `EXPO_PUBLIC_PULSE_API_BASE_URL=https://pulsesoc.com`
  - `NEXT_PUBLIC_API_URL` is not set.
  - `API_URL` is not set.
  - `APP_URL` is not set.

Railway was missing `EXPO_PUBLIC_PULSE_API_BASE_URL`; it was set to the required production value.

## Production Mobile Screen Parity

| Screen | Production web baseline | Mobile status after this pass | Remaining gap |
| --- | --- | --- | --- |
| Feed | `/pulse` production feed, media cards, reactions, comments, shares, reposts | Uses `/api/pulse/feed`, real post cards, real media rendering, pull refresh, no mock data | Native composer/status tray still has less depth than web |
| Reels | `/pulse/reels` full-screen Mux-backed playback | Uses `/api/pulse/reels/feed`, Mux HLS resolver, full-screen native video, no forced mute default | Native comments/reaction sheets are lighter than web overlays |
| Videos | `/pulse/videos` production video grid/feed | Uses `/api/pulse/videos`, Mux/R2 playback, real processing/failure states | Native video detail route is simpler than web |
| Messages | `/pulse/messages-v2` Communications V2 | Rewired to `/api/pulse/communications/v2/*`, loads conversations first, selected thread second, sends through V2, shows presence/read/typing labels | Native attachment and voice actions are present but still need full native upload/recording implementation |
| Notifications | `/pulse/notifications` production inbox | Uses `/api/pulse/notifications?limit=80`, mark read, delete, quick replies, push registration | Realtime live badge parity depends on native push/session delivery in an authenticated device build |
| Profile | `/pulse/profile` production account/profile | Uses `/api/pulse/profile/me`, renders real user fields, no John Doe placeholder | Native avatar/cover edit flow is not full web parity |
| Premium | `/pulse/premium` production premium hub | Uses `/api/account/status`, opens real production Portfolio, Intelligence, Alerts, Saved, Creator Studio, Security, Premium Room routes | Native billing and feature modules open production web routes rather than fully embedded native screens |

## Placeholder and Debug Removal

- The app entry now uses the production navigator instead of the retired demo shell.
- The old `src/App.tsx` demo entry now re-exports the production root app.
- Main tab wrappers for Messages and Notifications point to real production screens.
- Settings, Marketplace, and UNDX placeholders were replaced with production API or production route handoffs.
- Premium shortcuts now open live PulseSoc routes.

## Media Rendering

- Feed videos use the shared feed media renderer.
- Reels and Videos use Mux-aware playback URL resolution.
- Mux HLS URLs resolve through `https://stream.mux.com/<playback_id>.m3u8`.
- Reels no longer force muted startup.

## Realtime and Push

- Native push registration uses Expo notifications and `/api/push/subscribe`.
- Notification deep links are wired.
- Communications V2 read receipts and typing heartbeat calls are wired.
- Full in-app realtime parity still depends on authenticated device QA against production push/session behavior.

## Screenshot QA

Before screenshots were not available from the repository state after prior mobile rewrites. After screenshots still require an authenticated iOS and Android build or simulator session because the production app correctly uses real backend APIs and does not load mock data. The source audit and typecheck passed; real-device visual screenshots should be captured with a production test account for Feed, Reels, Videos, Messages, Notifications, Profile, and Premium.

## Validation

- TypeScript parse/typecheck passed.
- Feed audit passed.
- Notifications/Communications audit passed.
- Android UI quality audit passed.
- Production parity audit passed after tightening the forced-mute guard.

