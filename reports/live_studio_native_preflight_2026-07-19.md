# PulseSoc Native — Next-Generation Live Studio (Pre-Flight) — Build Report

Date: 2026-07-19
Scope: `mobile-native` — native Live Studio entry (`/pulse/live/studio`)
Verdict: **PARTIAL (honest)** — a real, verified native pre-flight shipped; full multi-user
broadcasting is **BLOCKED on backend** and documented, not faked.

## Executive summary

The mission asked for a complete next-generation multi-user live broadcasting platform
(45 sections: go-live, multi-guest, scenes, layouts, moderation host, gifts/polls, stream
health, recording, analytics, monetization, etc.). An architecture audit (Phase 01) confirmed
the native app is a **Live VIEWER client only**: `src/api/live.ts` exposes viewer endpoints,
and host broadcasting was already a plain web fallback
(`Linking.openURL(${PULSE_API_BASE_URL}/pulse/live/studio?context_type=native)`). **No client
endpoints exist** for go-live/publish, guest connectivity, scenes, moderation-as-host, live
polls/gifts issuance, or host-side analytics. Roughly 90% of the mission is therefore
backend-blocked.

Per the mission's §44 DO_NOT rules (no fake buttons, no mock metrics, no faked guest
connectivity, no claiming unverified functionality, document missing endpoints, do not hide
blockers), the honest, shippable deliverable is a **native pre-flight**: real device signals +
a live camera self-preview + local broadcast setup that hands off to the existing production
web studio to actually go live. Nothing is mocked.

## What shipped (real and verified)

New native screen `LiveStudioScreen` reached via `/pulse/live/studio`, driven by a pure,
unit-tested logic module. All signals are real:

- **Device check** — `expo-device` (`Device.isDevice`). Simulator → recommend (never blocks).
- **Camera / Microphone** — `expo-camera` permission hooks; live `CameraView` self-preview when
  granted on a physical device; front/back flip. Denied → blocked with an in-context
  request/open-settings action.
- **Network** — a **real timed reachability probe** (`fetch` to the API base, measured ms,
  6s abort). Classifies excellent/good/degraded/weak/offline. No fabricated bandwidth number
  (no `netinfo`/`expo-network` is installed, so none is claimed).
- **Battery** — `expo-battery` level + Low Power Mode; low battery/LPM → recommend (never blocks).
- **Overall readiness** — blocked > recommend > ready; "Go Live" is disabled while blocked.
- **Setup** — title, description, live type (10 options), audience (4 options), allow-comments
  and record-replay toggles, autosaved locally (`AsyncStorage` via `core/cache`, debounced 400ms).
- **Handoff** — "Go Live" builds a signed-scope URL
  (`/pulse/live/studio?context_type=native&live_type=…&audience=…&comments=…&record=…&title=…`)
  and opens the existing production web studio. Only the user's own broadcast metadata for their
  own studio is passed (privacy-safe).
- An explicit on-screen note states broadcasting itself runs in the production studio — no
  hidden claims.

## Files

Created:
- `src/live/liveStudioReadiness.ts` — pure readiness/draft logic (mappers, normalize, handoff URL).
- `src/screens/LiveStudioScreen.tsx` — the native pre-flight screen.
- `src/live/__tests__/liveStudioReadiness.test.ts` — 16 unit tests.

Edited (navigation wiring):
- `src/navigation/types.ts` — added `LiveStudio` to `RootStackParamList`.
- `src/navigation/AppNavigator.tsx` — import + `<Stack.Screen name="LiveStudio">`.
- `src/navigation/dashboardRouting.ts` — `/pulse/live/studio` now navigates to native
  `LiveStudio` (was web fallback); reclassified from safe-fallback to native path.
- `src/navigation/masterNavigation.ts` — Live Studio status `fallback` → `native`, with an
  honest description of the pre-flight + web-studio handoff.

## Verification

- `npx tsc --noEmit` → clean (exit 0).
- `npx jest` → **13 suites, 148 tests passing** (was 12/132; +1 suite, +16 tests).
- On-device install to physical iPhone **P3r7or** (iPhone 16 Pro) via `expo run:ios --device`.

## Backend-blocked (documented, not faked)

The following mission areas require server endpoints/contracts that do not exist for the native
client and were therefore **not** implemented as native surfaces (no fake UI was shipped for them):
go-live/publish, multi-guest join/leave & WebRTC negotiation, scenes/layout compositor,
screen-share ingest, host-side moderation actions, live polls/gifts issuance & settlement,
real-time viewer/stream-health telemetry, host recording control, and monetization/payout.
Actual broadcasting continues to run in the production web studio via the handoff.

## Recommended backend follow-ups (to unblock native)

Native host broadcasting needs, at minimum: a create-broadcast/go-live endpoint returning
ingest credentials, a guest-session signaling channel, a host stream-health/telemetry stream,
and host moderation/poll/gift issuance endpoints. Once those exist, the pre-flight screen is the
natural home to grow real native broadcasting on top of the already-verified readiness signals.

## Rollback

Revert the four edited navigation files and delete the two new `src/live/*` files plus the new
screen `src/screens/LiveStudioScreen.tsx`.
