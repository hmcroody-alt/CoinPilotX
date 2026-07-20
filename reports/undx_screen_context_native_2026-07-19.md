# UNDX Intelligence — Honest Native Slice: Real Screen-Context Enrichment — Build Report

Date: 2026-07-19
Scope: `mobile-native` — UNDX/PulseAI native chat (`ChatScreen`, assistant conversation `-9001001`)
Verdict: **SHIPPED (honest, verified)** — one real, unit-tested, privacy-safe capability landed.
The remainder of the 45-section mission is **platform/backend-blocked** and documented here, not faked.

## Executive summary

The "UNDX Intelligence Supercharge" mission asks for multimodal understanding, voice, image,
autonomous action-execution, live product knowledge, screen context, and continuous learning.
An inspect-first reconnaissance found that **the action-execution framework already exists and is
already wired** (backend returns `UndxResponseComponent[]` — `confirmation_card`, `progress_card`,
`draft_preview`, `verified_success_card`, `honest_failure_card`, etc. — and native renders them and
confirms via `confirmation_token` against `/api/pulse-ai/actions/confirm`). Rebuilding that would be
duplicative and, worse, would risk faking capability the mission explicitly forbids.

The one place the native client was **lying** was its screen context: every UNDX message hardcoded
`ui_context: { current_route: "Chat" }` regardless of the real device state. That is a small, real,
verifiable gap. This change replaces the lie with an **honest, minimal, privacy-sanitized**
`ui_context` assembled from real signals.

## What shipped (real and verified)

New pure module `src/undx/undxContext.ts` builds a privacy-safe `ui_context` and is fully unit-tested
without a device. The native chat now sends real signals for the UNDX assistant conversation:

- `surface` — always `"undx_chat"`.
- `origin_route` — the route the user came from (read from `navigation.getState()`), sanitized to a
  bare identifier. Any path/query/url/param form is rejected.
- `platform` — `Platform.OS`.
- `app_version` — from `Constants.expoConfig.version` via the new `APP_VERSION` export in `config.ts`.
- `screen_reader_enabled` / `reduce_motion_enabled` — real async `AccessibilityInfo` reads.
- `color_scheme` — `Appearance.getColorScheme()` (only `light`/`dark` forwarded).
- `timezone` — `Intl.DateTimeFormat().resolvedOptions().timeZone`.
- `selected_conversation_id` — the current conversation id.

### Privacy design (mission §19)

- **Allowlist-only assembly**: output is built field-by-field from known-safe signals, never spread
  from raw input.
- **Defense-in-depth redaction**: `sanitizeUiContext` drops any key matching a forbidden pattern
  (token/secret/password/auth/session/body/message/content/email/phone/address/payment/card/ssn/…),
  drops all non-primitive values (objects/arrays/null/undefined/functions), strips control characters,
  and caps every string at 64 chars.
- **No tokens, message bodies, content, captions, or contact info** can reach the wire.

## Files

Created:
- `src/undx/undxContext.ts` — pure builder + sanitizers (`buildUndxUiContext`, `sanitizeUiContext`,
  `sanitizeRouteName`).
- `src/undx/__tests__/undxContext.test.ts` — 13 unit tests (allowlisting, forbidden-key redaction,
  route-name sanitization, non-primitive dropping, control-char stripping, length capping).

Edited:
- `src/api/config.ts` — added `APP_VERSION` export.
- `src/screens/ChatScreen.tsx` — imports the builder; adds `collectUndxUiContext(navigation,
  conversationId)` (gathers the real async signals) and `readOriginRoute`; replaces the hardcoded
  `ui_context: { current_route: "Chat", selected_conversation_id }` in the assistant send path with
  `ui_context: await collectUndxUiContext(...)`.

## Verification

- `npx tsc --noEmit` → clean (exit 0).
- `npx jest` → **14 suites, 161 tests passing** (was 13/148; +1 suite, +13 tests).
- On-device install to physical iPhone **P3r7or** (iPhone 16 Pro) via `expo run:ios --device` →
  Build Succeeded, installed.
- Simulator install to **PulseSoc iPhone 16 Pro** (`expo run:ios --device <sim udid>`) → see status
  below.

## Honest caveat

This is **plumbing**, not a claim of magically smarter UNDX. The richer `ui_context` only improves
assistant behavior to the extent the backend consumes these fields. Native now tells the truth about
where the user is and how their device is configured; the intelligence payoff lands when the server
reads it.

## Backend/platform-blocked (documented, not faked)

The following mission areas require server contracts or native libraries that do not exist for the
native client, and **no fake UI was shipped** for them:

- **Voice / STT** — hardcoded disabled; no speech-to-text library installed. Needs an STT integration
  + a transcription endpoint.
- **Image understanding** — native image sends are rejected server-side (`pulse_ai_text_only`,
  `messenger.ts:712`). Needs a multimodal message contract.
- **Streaming responses** — the message endpoint returns a single JSON payload; no token streaming.
- **Continuous learning / memory** — no learning/memory API surface exists for the native client.

Action-execution is intentionally **not** rebuilt — it already exists and works via the
`UndxResponseComponent` + `confirmation_token` flow.

## Rollback

Delete `src/undx/undxContext.ts` and `src/undx/__tests__/undxContext.test.ts`; revert the
`APP_VERSION` export in `config.ts`; in `ChatScreen.tsx` remove `collectUndxUiContext`/`readOriginRoute`
and restore the prior hardcoded `ui_context`.
