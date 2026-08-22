# PulseSoc — App Review Readiness Report

**Date:** 2026-08-19 · **Branch:** `codex/emergency-live-audio-recovery` (dirty)
**Method:** parallel deep code audit (8 agents) + local DB inspection + one live read-only production check.
**Grading rule:** items whose proof requires a real device, real inbox, StoreKit sandbox, or production data are capped at PARTIAL even when code is correct. PASS requires journey evidence.

## Master gate verdict: DO NOT SUBMIT

| # | Item | Status | One-line reason |
|---|------|--------|-----------------|
| 1 | Persistent crypto alerts | **PARTIAL** | Engine is correct, but `alert_worker` is not in the Procfile — evaluator likely never runs in prod |
| 2 | Calls survive navigation / authoritative end | **FAIL** | Navigating away unmounts the Agora engine and drops the call; "Minimize" is a disguised hang-up |
| 3 | Referral links → App Store | **FAIL** | Live-confirmed: iOS gets the web landing page; zero UA branching, zero deferred attribution |
| 4 | QA account removal/hiding | **FAIL** | No test-account flag exists; search/suggestions/feed have no filter; 1,331/1,357 local users are synthetic |
| 5 | Groups + Rooms + messaging | **PARTIAL** | Full backend + matched mobile endpoints verified; needs two-device QA; minor gaps |
| 6 | Face ID | **PARTIAL** | Durable and honest, but delete-account leaves a live biometric-bound token on device |
| 7 | Support ticket email + reference | **FAIL** | No user confirmation email exists at all; no PS-XXXX reference format; only internal support@ email |
| 8 | Password reset email | **PARTIAL** | Correct token design; mobile recover endpoint has NO rate limit; web sessions survive reset |
| 9 | Appeals | **FAIL** | Strike appeals show a submit button with no endpoint; verification-appeal ledger frozen at "submitted" |
| 10 | Device classification | **FAIL** | 5 independent naive classifiers; the native app's UA matches none → classified desktop |
| 11 | Canonical FREE/PREMIUM | **BLOCKED** | Canonical resolver exists but default flag = legacy; under default, an Apple IAP purchase unlocks nothing |

---

## 1. Persistent crypto alerts — PARTIAL

**What's right** (`services/alert_engine.py`):
alerts stay `active` after firing — `trigger_alert` only bumps `last_triggered_at`/`trigger_count` (1330–1337); latest state stored (`condition_state`, `last_observed_value`, 186–189); edge-triggered armed/latched state machine dedups (1141–1234) with atomic claim (1082–1119) and unique `trigger_key` idempotency (238–245); re-arms on condition-false → new crossings re-notify (1182–1192); history in `alert_events` + `/api/alerts/events` (bot.py:35916) and `/api/crypto/alerts/<id>/history` (bot.py:7246); pause/resume/delete (785–809; routes bot.py:35866–35906); all state in DB → survives restart; storm protection = latch + 900s cooldown + quiet hours + throttled delivery log.

**Defects**
1. **CRITICAL:** the only automatic caller of `evaluate_all_active_alerts` is `alert_worker.py:46`, and the Procfile runs only `web`, `undx_worker`, `email_worker`, `ads_worker`. The admin page itself says prod "should run `python alert_worker.py` as a separate Railway worker" (bot.py:26537). If Railway has no such service, no alert ever fires.
2. Edge-trigger test suite (`tests/test_crypto_alert_edge_trigger.py`) is not wired into CI or the protection suite.
3. Duplicate parallel subsystem `services/business_os/crypto/alerts.py` (routes bot.py:24785–24813) — divergence risk.

**To close:** confirm/create the Railway `alert_worker` service (check `worker_heartbeats` freshness), then a live two-crossing test on device (fire → dip → fire again; identical repeated price must not re-notify).

## 2. Call lifecycle — FAIL

Native calls use **Agora** (react-native-agora 4.6.2), not LiveKit.

**(a) Survives navigation — FAIL.** The engine lives in a hook-local ref inside the call screen (`mobile-native/src/calls/useAgoraCallRoom.ts:43–45`); unmount cleanup calls `disconnect("unmounted")` → `leaveChannel()` + `engine.release()` (:47–58, :164). No global call store, no floating banner, no PiP. The "Minimize call" button (`CallScreen.tsx:365`, handler :331–334) does `navigation.goBack()`, which unmounts the screen and **drops media while the backend still shows the call connected** — a ghost-call generator.

**(b) Authoritative termination — PARTIAL.** Backend state machine is solid: `FINAL_STATUSES` + `end_call` + sync events (`services/pulsesoc_communications_engine.py:124–131, 1307–1320, 652+`), 45s ring timeout, stale-active reaping (:881, :1349–1351). Remote auto-exit works — terminal status auto-disconnects media, ends CallKit, shows terminal UI, no second hang-up (`CallScreen.tsx:180–232`). But it's **polling at 4.2s** (:236–243), gated on `appState === "active"` — a backgrounded callee learns nothing until foregrounded; Agora `onUserOffline` isn't used as a fast end signal.

**Fix shape:** hoist room ownership from the screen into a global call store/service (lift the existing hook — do NOT duplicate it; ownership-arbitration policy forbids a second audio singleton), add a minimized-call banner, and use `onUserOffline`/data message as fast-path end signal.
**Protected paths a fix touches:** `CallScreen.tsx`, `calls/useNativeCallRoom.ts`, `calls/callSignalMedia.ts`, `calls/callKitBridge.ts`, `api/calls.ts` are in `config/realtime-audio-protected-paths.json` — follow `docs/realtime_audio_change_policy.md` and run `scripts/realtime_audio_change_gate.py` locally. Livestream (`src/live/`) untouched.
**Device tests:** minimize → browse Feed/profiles/Messages → return; A ends → B latency foreground + backgrounded; kill A's app mid-call; CallKit lock-screen end; audio route release.

## 3. Referral links — FAIL (live-confirmed)

`referral_redirect` (bot.py:13458–13483) 302s **every** visitor to `/?ref=<code>&utm_...` — no user-agent branch, no `apps.apple.com` URL anywhere in the repo, no `apple-itunes-app` Smart App Banner meta. Live check today: `https://pulsesoc.com/r/apptest` → `https://pulsesoc.com/?ref=apptest&utm_source=referral&utm_medium=share&utm_campaign=user_referral`.

Attribution is web-cookie-only (`capture_referral_and_run_trial_maintenance` bot.py:2545–2548 → session; consumed at web signup bot.py:13315). The mobile app has **no deferred-attribution pickup** (no clipboard read / first-launch referral call in `mobile-native/src`). If you added an App Store redirect today, the code would be lost — Apple strips URL params.

Privacy is fine: codes are random `cpx+token_urlsafe(8)` (bot.py:4550), not identity-derived.

**To close:** (a) UA-branch iPhone/iPad → App Store listing URL; (b) deferred attribution — write code to clipboard (or server-side IP/UA short-window match) before redirect, and add first-launch pickup posting to a new `/api/mobile/referral/claim`; (c) verify on a real iPhone: tap link → App Store → install → open → referral attributed.

## 4. QA account exposure — FAIL

No marking mechanism exists: `users` has no `is_test`/`qa`/`hidden` column (verified via PRAGMA locally); `account_status` only gates login (bot.py:3741, 10942) and appears in **no** discovery SQL.

Unfiltered surfaces: creator search `/api/pulse/search` selects from `users` with no filter at all (bot.py:39313–39324); Suggested People has no status filter (bot.py:44139–44151); post/comment search filters only content flags, never author status (bot.py:39261–39298).

Local DB scale of the problem: 1,357 users, **1,331 match test patterns** (`smoke*`, `*_audit_*`, `phase2tester`, emails @example.com/.test), 892 posts authored by example.* users.

**To close:** (1) run the same pattern query against Railway Postgres to find real contamination (BLOCKED locally — no prod access); (2) add `is_test`/`hidden_from_discovery` flag + filters at the four query sites; (3) classify prod QA accounts DELETE / DEACTIVATE / HIDE / RETAIN-INTERNAL — do not destroy accounts holding financial/audit history or App Review test credentials (Apple's reviewer demo account must RETAIN).

## 5. Groups + Rooms + messaging — PARTIAL (strongest item)

Not a decorative shell. Backend verified end to end (bot.py): group create 88056 (real inserts, owner roles, rate limit), public/private/invite-only join 88136, invites 88308/88346 + invite links 88277, leave 88190, group↔conversation chat 88228, message persistence via `pulse_send_conversation_message` (43119, membership-checked, idempotent by `client_message_id`), scoped retrieval 84652; rooms create/join/archive/delete/leave 84226–84354; roles/ban/moderation 88425–89206; admin search + lifecycle + audit 91133–91174. **Every mobile screen API call was matched to an existing backend route** (groups.ts, messenger.ts → `/api/pulse/communications/v2/...` in `pulse_communications_v2/routes.py:938/1001`).

**Gaps:** (1) the whole v2 messenger registers via a try/except route pack (bot.py:1221–1243) — if it fails to import, all chat 404s while groups stay up; check `/health/routes` on every deploy. (2) No room-invite endpoint after creation, and create-time invitees get no notification. (3) Mobile realtime is polling + push, no websocket — delivery latency = poll interval. (4) Existing tests are source-string scans, not behavioral.

**To close (device):** two-account journey — private group, invite, join, exchange messages, kill/relaunch (persistence), backgrounded push delivery, promote moderator, ban, archive, delete; repeat for a private room; admin delete via `/api/admin/community`.

## 6. Face ID — PARTIAL

**Right:** enrollment = userId + snapshotted refresh token in SecureStore (`AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY`, `sessionStore.ts:9–17`, `biometricAuth.ts:13–16`); sign-out deliberately preserves the biometric-bound token so no re-enrollment (`auth.ts:244–277`); unlock re-snapshots rotated tokens (:167–175); **zero biometric data stored anywhere**, backend has no biometric endpoints; usage string ("unlock your saved sign-in securely", app.json:27 / Info.plist:58–59) makes no face-storage claim.

**Defects:**
1. **Delete account** (`DataPrivacySettingsScreen.tsx:166–178`) calls plain `signOut()` → takes the keep-biometric branch → a live refresh token stays on-device, bound to Face ID, for an account pending deletion. Fix: that path must call `disableBiometricLogin()` + full credential clear. Related: no executor found for pending `pulse_account_data_requests` deletion rows — verify deletion actually executes and revokes tokens.
2. No `kSecAccessControl`/`requireAuthentication` — the token is keychain-readable without biometry; the gate is an app-level `authenticateAsync` prompt (`biometricAuth.ts:133`). Acceptable pattern, but note it; `disableDeviceFallback:false` means passcode is accepted.
3. Keychain write failures are silently swallowed outside QA (`sessionStore.ts:180–190`) — enrollment can silently not persist.

**Device tests:** real device only (simulator lacks keychain entitlements): enroll → force-quit → relaunch → unlock; logout → biometric login; reinstall behavior (keychain survives — confirm that's intended policy); biometric lockout + passcode fallback.

## 7. Support ticket email — FAIL

Endpoints exist and persist honestly: `/support` (bot.py:1448), `/api/support/ticket` (bot.py:1516), mobile uses the same API (`support.ts:71`); tickets → `support_tickets` (bot.py:1483/1491).

**Missing entirely:** no `PS-2026-XXXXXXXX` reference format anywhere (reference = raw autoincrement id); **the submitting user receives no email at all** — the only email is internal to support@pulsesoc.com (bot.py:1496–1504, 1556–1564), and its result isn't checked.

**To close:** add reference generator + column (e.g. `PS-{year}-{8 hex}`), return it in the API response and mobile confirmation, send a branded user-facing confirmation email carrying the SAME reference via the existing Brevo path with honest failure handling, then inbox-verify.

## 8. Password reset email — PARTIAL

**Right:** both entry points real — web `/forgot-password` (bot.py:12106) and mobile `POST /api/mobile/auth/recover` (bot.py:6362, wired from `AccountRecoveryScreen.tsx`) → `safe_password_reset_request` (bot.py:5650). Token: `secrets.token_urlsafe(32)` (5573), stored **hashed** (HMAC-SHA256, 5612–5615); 1h expiry (5587); single-use + revoke-all-on-success (4417–4418, 6395); enumeration-resistant generic response (5653, 6369); branded email via synchronous Brevo (99634–99659) with failed sends queued to email_worker; audit events (5690–5691) + password-changed security email (6423).

**Defects:** (1) **`/api/mobile/auth/recover` has no rate limit** — not in the protected dict (bot.py:2558–2567); web is 6/300s but the bucket is in-memory per-worker. (2) **Web Flask sessions are not invalidated after reset** (mobile sessions are, 4392–4399); `revoke_mobile_refresh_token` not called on reset. (3) Legacy plaintext-token fallback lookup remains (5643–5646) — remove.

**To close:** fix the three defects, then live inbox test: request → email arrives → link opens on pulsesoc.com (`PUBLIC_APP_BASE_URL` env-dependent) → reset succeeds → token rejected on reuse → old web session behavior per policy.

## 9. Appeals — FAIL

**Working end-to-end:** ads appeals (submit bot.py:18733/19035 → `pulse_ad_appeals`, admin decide bot.py:19966, real statuses in `AdsPolicyCenterScreen.tsx`); business-OS advertising/marketplace appeals (submit 22612/24438, admin resolve + audit 22778–22813/24625–24654).

**Broken:**
1. **Strike appeals are a dead entry point:** `account_strikes.appeal_status` is inserted as `'available'` and shown to users (bot.py:10107), but no endpoint exists to submit one (zero `UPDATE account_strikes` anywhere); mobile `AccountHealthAppealsScreen.tsx:70–75` always hard-fails (`supported: false`, accountHealth.ts:199–207). A visible appeal button that can never submit — exactly what this checklist item forbids.
2. **Verification-appeal ledger frozen:** `verification_appeals` rows never leave `submitted` — `reviewed_by/reviewed_at/decision_reason` are never written; admin page `/admin/verification/appeals` (bot.py:13834–13851) is read-only. Stale status shown forever.
3. **Duplicate route:** `/api/dashboard/account/verification/appeal` registered twice with different handlers/storage (bot.py:6915 vs 10205) — one is unreachable; verify which wins in prod.

**To close:** either wire strike appeals to a real endpoint + admin decision path, or remove the entry point before submission; make verification-appeal decisions update the ledger; delete the duplicate route.

## 10. Device classification — FAIL

**Five independent naive classifiers**, none reading `sec-ch-ua`:
`parse_device` (bot.py:13035–13053, feeds analytics + ads), `visitor_user_agent_meta` (13064–13070, feeds **login security emails** via 4878–4884), `presence_device_label` (3306–3314), `native_app_request_context` (4734–4738), `notification_service.py:1528–1530/2186` (push).

**Root causes of mobile→desktop:** (a) the RN app's default iOS fetch UA (`PulseSoc/<build> CFNetwork/... Darwin/...`) contains none of the matched tokens → every API call classifies as **desktop**; (b) `native_app_request_context` looks for `"PulseSocNativeApp/"` in the UA but **the app never sets that UA** (no hit in `mobile-native/`, `pulseApi.ts` sets only Authorization) → `is_native` is always false, which also silently disables iOS-only gating like IAP enforcement (bot.py:4741–4754); (c) iPadOS Safari sends a Macintosh UA → desktop.

Device type vs trusted device are correctly separate concepts (`security_devices.trusted`, bot.py:10450–10461) — the symptom users see is security emails describing app logins as "desktop".

**To close:** one canonical classifier (MOBILE/TABLET/DESKTOP/UNKNOWN — return UNKNOWN on weak evidence, don't guess) used by all five call sites; set a real UA (`PulseSocNativeApp/<build> ...`) in `pulseApi.ts` so native detection works; test matrix: iPhone, iPad (Macintosh UA), Android, desktop, simulator, unknown UA.

## 11. Canonical FREE/PREMIUM — BLOCKED (on prod env verification; FAIL if defaults are live)

A real canonical resolver exists: `services/business_os/entitlements/premium.py` over `business_os_ent_grants`, fronted by `facade.py` with `BUSINESS_OS_ENTITLEMENTS` = off | shadow | canonical — **default off = legacy authoritative** (facade.py:52–59).

**Fragmentation:** ~148 raw premium-column reads in bot.py + 55 in services vs ~13 resolver calls. Worst offenders: inline boolean from `lifetime_premium`/`premium_glow_manual_grant` (bot.py:76046); direct `subscription_status` checks (51975, 27061, 4555); `pro_access.py` columns (11036–11064); feed engine (8 hits); and `premium_identity_engine.py:31–43` grants permanent premium on a **display-name match ("Roody Cherie") — spoofable, remove this**.

**The critical split:** Stripe webhook (bot.py:98060) writes legacy tables + users columns, then projects to canonical. Apple IAP verify (bot.py:~18156 → `iap_apple.apply_verified_subscription_transaction`) writes **canonical only**. So with `BUSINESS_OS_ENTITLEMENTS=off`, **an App Store purchase grants an entitlement nothing reads — the buyer sees no premium.** That alone is an App Review rejection. Also requires `BUSINESS_OS_IAP=on` + `APPLE_ROOT_CA_CERTS` (endpoint 503s without anchors; a prior report flags them MISSING in prod).

**Expiry:** canonical/legacy check `expires_at` at read time (good), but `has_active_premium` trusts `subscription_status='active'` with no expiry cross-check — a missed webhook leaves stale premium on every column-reading surface.

**Mobile caching is safe:** cache is declared display-only; purchase flow re-reads `/api/premium/status` after verification — no restart needed.

**To close:** verify Railway env (`BUSINESS_OS_ENTITLEMENTS`, `BUSINESS_OS_IAP`, `APPLE_ROOT_CA_CERTS`); cut over to `canonical`; migrate the raw column reads to the resolver (start with feed, IAP-adjacent, and settings surfaces); remove the display-name owner grant. Then StoreKit sandbox: purchase → premium visible app-wide without restart; restore; expiry/refund via ASSN v2 webhook propagates.

---

# Priority fix order

1. **Entitlements env cutover + IAP convergence (11)** — purchase-doesn't-unlock is a guaranteed App Review rejection.
2. **Call ownership hoist + minimize banner (2)** — reviewer-visible core defect; touches audio-protected paths, budget review time.
3. **Referral App Store redirect + deferred attribution (3)** — small backend change + small app change.
4. **Support ticket user email + reference (7)** — contained, uses existing Brevo path.
5. **Device classifier consolidation + app UA (10)** — one function + one header; also un-breaks native-app detection/IAP gating.
6. **Appeals: fix or remove strike entry point; ledger updates (9).**
7. **QA account flag + discovery filters + prod audit (4).**
8. **Password reset: mobile rate limit, web session invalidation, drop plaintext fallback (8).**
9. **Face ID: delete-account credential clear; verify deletion executor (6).**
10. **Crypto alerts: add Railway alert_worker service; wire tests into CI (1).**
11. **Groups/rooms: room-invite endpoint + notification; behavioral tests (5).**

# Device / production verification plan (required for final PASS)

**Infrastructure (Railway dashboard):** confirm `alert_worker` service exists and heartbeats; read `BUSINESS_OS_ENTITLEMENTS`, `BUSINESS_OS_IAP`, `APPLE_ROOT_CA_CERTS`, `PUBLIC_APP_BASE_URL`; check `/health/routes` for the communications-v2 route pack after each deploy.

**Production data (read-only Postgres):** run the QA-pattern user query (`smoke%`, `%_audit_%`, `%@example.%`) to size real contamination before classifying accounts.

**Two-device tests:** call minimize → browse → return; A-ends → B auto-exit (foreground + backgrounded, measure latency); kill-app mid-call; group/room full journey incl. relaunch persistence and backgrounded push.

**Single-device tests:** referral link tap → App Store → install → open → attribution recorded; Face ID enroll/relaunch/reinstall/lockout; password reset + support ticket real-inbox delivery (check the reference matches); crypto alert double-crossing (fire, re-arm, fire again, no dup storm); device-type matrix (iPhone/iPad/Android/desktop/unknown UA) checking the security email wording; StoreKit sandbox purchase/restore/expiry.

**Evidence rule:** each item flips to PASS only with the journey artifact (screenshot, inbox message, webhook log, or DB row) — not on code inspection.
