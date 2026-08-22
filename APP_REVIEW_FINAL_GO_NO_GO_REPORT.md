# PulseSoc — App Review Finalization Super Mission — Final GO/NO-GO Report

Date: 2026-08-19
Branch: `codex/app-review-final-readiness` (created off clean `main`)
Start SHA (main): `fa4cce7b73bcdf75e7236ea9c849064637af67bf`
End SHA (commit): `326b6fa6875a01221e288d2fbc342ed16b218599`
Commit message: `fix(app-review): complete final production readiness hardening`
Scope of commit: 43 files, +3,643 / −406
Push status: **BLOCKED** — sandbox cannot reach github.com over SSH (`socat E CONNECT github.com:22: Forbidden`). Owner must run: `git push origin codex/app-review-final-readiness`
Companion document: `APP_REVIEW_READINESS_REPORT.md` (Phase-1 audit with full evidence and the device test plan)

## Verdict: NO-GO for immediate submission — CONDITIONAL GO once the external checklist below is executed

Every repository-controllable defect across the 11 items is fixed, tested, and committed. What remains is exclusively evidence and infrastructure that cannot be produced from this environment: physical-device journeys, real email inbox verification, StoreKit sandbox purchases, Railway service/env changes, a production deploy, and the git push itself. Per the agreed policy, device-dependent items are capped at PARTIAL until that evidence exists.

## Deviation from mission brief

The mission named branch `codex/agora-rtc-migration`; that branch was already merged to main (`7519bb14`) before the mission started, and the working tree was clean `main`. Work was done on a fresh branch `codex/app-review-final-readiness` instead. Additionally, the sandbox VM was reset mid-mission, which removed installed Python dependencies (no PyPI access to reinstall); consequences are noted per item below — all bot-importing test suites passed in full before the reset, and everything re-runnable afterward was re-run.

## Per-item matrix

| # | Item | Status | What was fixed | Evidence | Remaining for PASS |
|---|------|--------|----------------|----------|--------------------|
| 1 | Persistent crypto alerts | **PARTIAL** | `alert_worker` added to `Procfile` (persistence, dedup, history, pause/delete already verified sound in audit) | Audit trail in readiness report | Owner creates the `alert_worker` service on Railway; one live trigger→re-trigger journey |
| 2 | Calls survive navigation; hangup ends both sides | **PARTIAL** (cap) | Engine ownership moved from screen-mounted hook to module-scoped `src/calls/callSessionStore.ts`; release only on explicit hangup / terminal status / 404–410 poll miss; `onUserOffline` + connection-failure → immediate deduped status re-fetch; `MinimizedCallBanner` + AppNavigator mount; CallScreen is a thin consumer; Minimize only navigates | jest 6 suites / 43 tests pass (incl. 5 new store tests); tsc clean; audio gate exit 0 with declaration accepted | Two-device physical test (navigate away/back both sides, one-party hangup, CallKit controls) |
| 3 | Referral links → App Store + deferred attribution | **PARTIAL** | `referral_redirect` rewritten: iOS UA → validated App Store URL (env `PULSESOC_APP_STORE_URL`, fallback id6777591572); `referral_deferred_claims` table + claim endpoint; mobile `claimReferralAttributionOnce` (persisted once-flag, no clipboard) hooked into signed-in App effect | 24 backend tests re-verified in this session; live pre-fix defect was browser-confirmed | Deploy to production, then re-verify `pulsesoc.com/r/<code>` on a real iPhone lands on the App Store and attribution claims post-install |
| 4 | QA/test accounts hidden from production | **PARTIAL** | `hidden_from_discovery` column; canonical `services/discovery_visibility.py` predicate applied in search, user search, suggested-people; classifier `scripts/qa_account_classification.py` (dry-run default) | 9 visibility/appeals tests passed pre-reset; local dry run: 1,124 matches (1,050 HIDE_FROM_DISCOVERY, 74 INTERNAL_ONLY with financial rows — deliberately not auto-hidden) | Run classifier against prod Postgres, human-review the 74 INTERNAL_ONLY, then `--apply-hide` |
| 5 | Groups + Rooms + Messages | **PARTIAL** (strongest item) | No fixes required — audit found a functional core, not decorative shells | Audit evidence in readiness report | Device QA journey (create group, room join, send/receive across two accounts) |
| 6 | Face ID durable + secure | **PARTIAL** (cap) | Already secure (no biometric material stored; SecureStore `AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY`); added `signOut({ clearBiometrics: true })` on account deletion so stale biometric login can't outlive the account | tsc + session tests (33/33 pre-reset) | Physical iPhone Face ID journey incl. reboot-persistence |
| 7 | Support ticket email + reference | **PARTIAL** | `PS-<year>-<8 hex>` reference generated, stored (with unique index), returned by `/api/support/ticket` and shown in `/support` + mobile Help screen; branded user confirmation email with same reference, idempotency key, never-raises send | 8 tests passed pre-reset (format, persistence, same ref in API + patched email send); i18n keys added in 11 locales | One real ticket → confirm email arrives in a real inbox with matching reference |
| 8 | Password reset email delivers | **PARTIAL** | Plaintext-token fallback removed (HMAC-SHA256 hash-only match); both mobile recover endpoints added to `basic_abuse_guard` (6/300s) on top of the existing 5/600s core limit; enumeration-resistant responses retained; 1h expiry + single-use retained | Tests passed pre-reset (hash-only enforcement, 7th POST → 429) | Real inbox delivery check via Brevo in prod. Scoped follow-up: web sessions are not invalidated after reset (session stores only `account_user_id`; fixing requires a session-versioning change to global auth — out of mission scope) |
| 9 | Appeals complete | **PARTIAL** | Dead duplicate verification-appeal route deleted; admin decide endpoint added (`/admin/verification/appeals/<id>/decide`) with status sync; full strike-appeal lifecycle added: user POST (returns `SA-…` reference), admin list/decide, `account_strike_appeals` table; mobile `AccountHealthAppealsScreen` wired | Appeals tests passed pre-reset | Small backend follow-up: expose `latest_strike_id` in account-health metrics so the mobile screen can target the exact strike; one end-to-end appeal journey in prod |
| 10 | Device classification | **PASS** (repo-verifiable) | Canonical `services/device_classification.py`; all call sites rewired (`parse_device`, visitor meta, presence labels, notification service); native app sends `X-PulseSoc-Platform` + `PulseSocNativeApp/<v>` UA; UNKNOWN never coerced to desktop | 24/24 tests re-verified in this session post-commit | — (optional: spot-check labels in prod dashboards after deploy) |
| 11 | Canonical FREE/PREMIUM entitlement | **PARTIAL** | Read-bridge: legacy `has_entitlement()` now consults canonical Apple/Google grants live (expiry/grace/suspension respected) — closes the "Apple buyer gets nothing on legacy surfaces" gap; expired premium now rejected in `pro_access` + identity engine (3-day grace); owner bypass moved from display-name/hardcoded-email to `PULSESOC_OWNER_USER_IDS` env allowlist | Convergence suite 8 + regression suites 15/27/12/11 passed pre-reset | Railway env verification (see blockers — `PULSESOC_OWNER_USER_IDS` is critical); StoreKit sandbox purchase + expiry propagation test |

## Verification record (this session, post-VM-reset)

Run and passed after commit `326b6fa6`: `python3 -m py_compile` over bot.py + all touched services/scripts; `npx tsc --noEmit` (exit 0); call jest suites 6/43; realtime-audio critical suite 11 suites/191 tests; full realtime-audio suite 310 tests; native architecture 22 tests; backend architecture 19 tests (`tests.protection.test_realtime_audio_architecture`); i18n catalog validation (11 locales OK); hardcoded-string check — no new violations (HelpSettingsScreen 40→38 vs main baseline, AccountHealthAppealsScreen 20→20, new call files 0); `git diff --check` clean; zero livestream/LiveKit lines in the entire diff; audio change gate `--base main --head HEAD` exit 0, "Declaration accepted".

Not re-runnable after the VM reset (no PyPI network; flask/stripe/agora_token_builder uninstallable): the four bot-importing app-review suites (8/24†/9/8 tests — all passed pre-reset; †the 24 device/referral tests are stdlib-only and WERE re-verified) and `tests/protection/test_agora_token_generation.py` + `test_agora_rtc_provider_contract.py`, which fail here solely because `agora_token_builder` is missing — `services/pulsesoc_communications_engine.py` is byte-identical to main (0 diff lines). Owner should run `python3 -m pytest tests/ -q` once on a machine with dependencies.

## Security defects fixed in this commit

Plaintext password-reset token fallback removed (hash-only matching). Owner premium bypass by display-name "Roody Cherie" and hardcoded email removed — replaced with `PULSESOC_OWNER_USER_IDS` env allowlist. Expired subscriptions no longer grant premium (`pro_access`, `premium_identity_engine` expiry cross-checks). Mobile account-recovery endpoints rate-limited at the abuse guard. Referral redirect target validated against `https://apps.apple.com/` (no open-redirect via env). No security control was weakened anywhere in the diff.

## Protected-system audit

**Livestream: UNTOUCHED.** Zero livestream/LiveKit files or lines changed; the full realtime-audio and live suites pass unchanged.

**Call files changed (item 2 only), all declared in `reports/realtime_audio_change_declaration.md` (gate-accepted):**
- `mobile-native/src/screens/CallScreen.tsx` (protected) — thin store consumer; no AVAudioSession, track, or publication changes
- `mobile-native/src/calls/__tests__/useAgoraCallRoom.test.ts` (protected) — assertions updated for survive-navigation behavior
- `mobile-native/src/calls/callSessionStore.ts` (new) — single module-scoped engine owner (one-audio-singleton preserved)
- `mobile-native/src/calls/useAgoraCallRoom.ts` — reduced to a 38-line binding
- `mobile-native/src/calls/MinimizedCallBanner.tsx` (new) + `src/navigation/AppNavigator.tsx` — return-to-call UI
- `mobile-native/src/calls/__tests__/callSessionStore.test.ts` (new)

Gate-mandated release requirements still outstanding: native build verification (`npx expo prebuild --platform ios` or EAS build) and physical audible validation per `reports/realtime_audio_verified_baseline.md` §7.

## Unrelated defects — documented, deliberately NOT fixed (out of scope)

TrustSafetyScreen.tsx still shows the old ticket wording; duplicate `/api/dashboard/account/verification/request` route; two conflicting `verification_requests` schemas; Postgres-invalid `SELECT DISTINCT` + `ORDER BY` in suggested-people; unreachable duplicate return in arena; Apple IAP buyers get access but no users-column badge writes; ringback tone stops while a ringing call is minimized; hundreds of stale `.fuse_hidden*` files in the repo root.

## External blockers — owner checklist (in order)

1. **Push:** `git push origin codex/app-review-final-readiness` (from your Mac; sandbox SSH is blocked).
2. **Repo hygiene:** `rm .git/index.lock` (a stale lock the sandbox could not delete — it will block your next git write) and optionally `git worktree prune`. The index itself is already synced to the commit.
3. **Railway:** create the `alert_worker` service; set/verify `PULSESOC_OWNER_USER_IDS` (**critical — owner accounts lose premium bypass without it**), `BUSINESS_OS_ENTITLEMENTS`, `BUSINESS_OS_IAP`, `APPLE_ROOT_CA_CERTS`, `APPLE_IAP_ALLOW_SANDBOX=1` (during review), `PULSESOC_APP_STORE_URL`.
4. **Deploy** and live-verify: `pulsesoc.com/r/<code>` on an iPhone → App Store; support ticket → email in a real inbox with matching `PS-…` reference; password reset → email delivers, token single-use.
5. **Prod data:** run `scripts/qa_account_classification.py` (dry-run) against Postgres, review the INTERNAL_ONLY set, then `--apply-hide`.
6. **Device QA:** two-device call test (navigation + one-party hangup + CallKit), physical Face ID incl. reboot, Groups/Rooms/Messages journey, StoreKit sandbox purchase + expiry propagation.
7. **Backend follow-ups:** expose `latest_strike_id` in account-health metrics (mobile strike appeals); run `python3 -m pytest tests/ -q` on a dependency-complete machine; native iOS build + physical audible validation before merging to main.
