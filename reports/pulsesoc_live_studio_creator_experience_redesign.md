# PulseSoc Live Studio Creator Experience Redesign

Date: 2026-06-13

## Scope
Redesigned the desktop PulseSoc Live Studio from a technical streaming dashboard into a creator broadcast center. Backend LiveKit/Mux transport behavior was intentionally preserved except for UI status presentation and secret handling.

## Files changed
- `bot.py`
- `static/css/pulse_live_studio.css`
- `static/js/pulse_live_studio_runtime.js`
- `scripts/live_studio_audit.py`
- `reports/pulsesoc_live_studio_creator_experience_redesign.md`

## UI architecture
- Added a premium Studio shell with left navigation: Studio, Chat, Analytics, Revenue, Settings, Guests, Exit Studio.
- Promoted the live camera preview into the hero surface with badges, muted local preview, and floating reactions.
- Moved chat into the right panel with viewer count, top-supporter row, realtime chat feed, emoji reactions, and composer.
- Added bottom creator controls for Camera, Mic, Speaker Off, Screen Share, Flip Camera, Effects, Settings, and End Stream.
- Added compact audience/creator cards below the preview for Audience Overview, Top Supporters, Recent Reactions, and Stream Goals.
- Added disabled quick actions for Invite Guest, Go Live Alert, Clip Highlight, and Poll Audience with Coming soon tooltips.

## Security changes
- RTMP ingest URL, stream key, LiveKit room, Mux playback URL, and raw bridge details are hidden from the default Studio view.
- Technical streaming information now lives inside the Advanced Streaming drawer only.
- Advanced Streaming is hidden with `hidden`, `aria-hidden=true`, and `display:none` until Settings is opened.
- Stream key remains masked by default and requires an explicit Reveal action.
- Copy actions remain host-only within Studio; public viewer pages still do not receive ingest credentials.

## Echo prevention
- The host camera preview remains local and muted by default.
- Preview video is rendered with `autoplay`, `muted`, `playsinline`, `webkit-playsinline`, and `volume="0"`.
- The main Studio UI uses a disabled `Speaker Off` control to make the no-audio-preview policy explicit.
- The runtime continues enforcing muted host playback for host viewer surfaces.

## QA results
- Python compile passed with the app virtualenv.
- JavaScript parse passed for `static/js/pulse_live_studio_runtime.js`.
- `scripts/live_studio_audit.py` passed and verifies:
  - Studio route loads.
  - Premium creator shell and left nav render.
  - Hero preview renders.
  - Old R/camera-ready overlay is absent.
  - Advanced Streaming is hidden by default.
  - Stream key is masked behind Reveal.
  - Technical Stream Health Center is removed from default view.
  - Bottom controls, live chat, and floating reactions render.
- Browser attempt reached the local Studio route, but the in-app browser service worker served a cached offline shell after reconnecting. `/reset-pwa` could not clear it from the restricted read-only browser context. Static route/audit validation was used as the reliable QA path for this turn.

## Before vs after
Before: default Studio exposed Mux playback URL, RTMP ingest URL, stream key, LiveKit room, bridge status, bitrate/FPS internals, and a technical health center.

After: default Studio shows a creator broadcast center with preview, chat, reactions, engagement metrics, controls, and friendly stream health. Infrastructure details are only available from Settings > Advanced Streaming.
