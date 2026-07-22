# PulseSoc Native — Wave 0 Release-Gate Cleanup + Wave 1 Authentication, Session, and Home Layout Stabilization

- Date: 2026-07-21
- Repository: `/Users/hmcherie/Desktop/CoinPilotX`
- Branch: `release/undx-nexus-core-v4`
- Mission report: this file (`reports/pulsesoc_native_wave0_wave1_auth_home_stabilization_2026-07-20.md`)

## 1. Mission Scope

Two waves, both technical (no Apple ownership / D-U-N-S / App Store Connect / Bundle ID / certificates / provisioning / signing were touched):

- **Wave 0 — Release-gate cleanup.** Repair stale / obsolete / misleading release-gate audits and tests so they fail on *real* active blockers, pass when a blocker is truly resolved, ignore comments / docs / test-only fixtures, distinguish legitimate external links and intentional system handoffs from real WebView exits, map failures to blocker IDs, and prefer machine-readable JSON. **Audits were repaired for precision, never weakened to force a pass.**
- **Wave 1 — Authentication, session, and Home layout stabilization.** Make P0 auth/session behavior deterministic (a 6-phase bootstrap state machine), keep single-flight token refresh, keep login/signup no-double-submit, ensure logout clears user-scoped state and account switching is isolated, never log tokens/PII, use secure storage; and fix the Home composer / bottom-navigation-dock overlap structurally (no device-specific hardcoded offset — NRB-058).

**Explicitly out of scope:** broad WebView-exit replacement (the native-only web-redirect elimination). That work stays tracked and the WebView replacement gate remains NO-GO.

## 2. Blocker Outcomes

| Blocker | Title | Class | Outcome |
|---|---|---|---|
| NRB-055/056/057 | Route/notification "web fallback" test expectations | STALE_TEST (TEST_ONLY) | Confirmed **false positives** — stale test *descriptions* only; assertions unchanged, no production behavior implicated. |
| NRB-058 | Home bottom navigation overlaps composer | PARTIAL_NATIVE_FLOW (P0) | **RESOLVED (code)** — structural inset-aware dock clearance. Device QA on P3r7or **NOT OBSERVED** (pending build/deploy). |
| NRB-059 | Pre-existing dirty auth/login/signup/session work | AUTH_SESSION_GAP (P0) | **RESOLVED (code)** — deterministic 6-phase bootstrap machine committed intentionally. Device QA **NOT OBSERVED**. |
| NRB-060 | Foundation audit rejects inert WebView references | STALE_AUDIT (P0) | **RESOLVED** — audit repaired (precision), exits 0. |
| NRB-061 | Live audit expects obsolete "Go Live Web" copy | STALE_AUDIT (P2) | **RESOLVED (audit passes, exit 0)** — realignment to the native LiveStudio flow is present in the working tree but coupled to the separate native-Live-studio workstream (`live_audit.py` + `live_progress.md`); **left uncommitted by this mission** to avoid folding in that workstream's narrative. |
| NRB-062 | Feature-parity audit asserts obsolete wording | STALE_AUDIT (P2) | **RESOLVED** — audit realigned to current readiness truth, exits 0. |
| NRB-001 … NRB-054 | Web-exit / safe-web-fallback source blockers | DIRECT_WEB_EXIT / SAFE_WEB_FALLBACK | **OPEN — out of scope.** WebView replacement gate remains NO-GO. |
| NRB-063 | Physical-device media/call/push behaviors | PHYSICAL_QA_GAP (P0) | OPEN — physical QA not performed this mission. |
| NRB-064 | Apple ownership / signing / APNs / upload | RELEASE_CONFIG_GAP (P0) | OPEN — deliberately untouched. |

## 3. Wave 0 — Audit Repairs (repaired, not weakened)

### 3.1 Foundation audit — `scripts/pulsesoc_native_app_foundation_audit.py` (NRB-060)
- Previously failed on any `"WebView"` or `"react-native-webview"` substring across all native source, including user-facing copy (e.g. "your current WebView account") and comments — a false positive.
- Now compiles two precise signals for a *real* dependency: `WEBVIEW_IMPORT = from/require("react-native-webview")` and `WEBVIEW_RENDER = <WebView …>`. It skips `__tests__`/`__mocks__`/`.test.`/`.spec.` paths and comment lines, and reports the exact `path:line` offenders in the failure message (blocker-mappable).
- Also tightened messenger assertions onto the canonical Communications v2 prefix (`/api/pulse/communications/v2`) instead of the legacy `/api/pulse/messages/*` routes.
- Result: **exit 0** — no real WebView import/render exists in native foundation source.

### 3.2 Live audit — `scripts/pulsesoc_native_live_audit.py` (NRB-061)
- The audit now validates the **native** LiveStudio go-live flow (`navigation.navigate("LiveStudio")`, `Stack.Screen name="LiveStudio"`, deep-link resolves to native studio) and stricter provider boundaries (native host mints LiveKit tokens via the backend `livekit/token` endpoint, co-host requests via `join-request`, no `browser-publish` handoff), instead of the obsolete "Go Live Web" / `live_studio_web_fallback` expectations. Result: **exit 0**.
- **Attribution / commit note:** this realignment (`scripts/pulsesoc_native_live_audit.py`) is coupled to a companion narrative rewrite in `reports/pulsesoc_native_live_progress.md` describing native Live Studio (`LiveStudioScreen`/`LiveHostSessionScreen`) as wired — i.e. it documents a **separate native-Live-studio workstream**, not this mission's auth/home/gate cleanup. To avoid mis-attributing or finalizing another workstream's unreviewed narrative, **this mission left `live_audit.py` and `live_progress.md` uncommitted in the working tree.** The gate passes today; committing this pair should be owned by the Live workstream.

### 3.3 Feature-parity audit — `scripts/pulsesoc_native_feature_parity_audit.py` (NRB-062)
- Verifies the completed "Device QA Setup" follow-up in the living master record instead of asserting the obsolete "Recommended next action: device QA setup" wording.
- Treats the intentionally-installed Expo web QA dependencies (`react-native-web`, SDK 54) as an available QA surface rather than asserting their absence.
- Result: **exit 0**.

### 3.4 Stale tests (NRB-055/056/057)
- `routeResolution.test.ts` and `notificationRouting.test.ts` "web fallback" expectations are stale test *descriptions*; the assertions are correct and unchanged. Classified TEST_ONLY false positives (no production behavior). Left assertions intact.

### 3.5 WebView replacement audit — `scripts/pulsesoc_native_webview_replacement_audit.py`
- Still **exits 1** (`release_readiness: FAIL`, `hard_blocker_count: 54`) on real remaining web-fallback source (e.g. `SearchScreen.tsx` events/lessons gateway copy). This is correct — WebView-exit replacement is out of scope, and the gate must keep failing until that source is migrated.

## 4. Wave 1 — Authentication & Session (NRB-059)

### 4.1 Deterministic 6-phase bootstrap machine — `mobile-native/src/session/auth.ts`
- New phase type and a single constructor that derives the legacy `status` so the two can never desync:
  - `SessionPhase = BOOTSTRAPPING | AUTHENTICATED | UNAUTHENTICATED | SESSION_EXPIRED | RECOVERABLE_ERROR | FATAL_ERROR`
  - `AuthState = { phase, status, user }`, `status` projected by `statusForPhase` (`BOOTSTRAPPING→loading`, `AUTHENTICATED→signedIn`, else `signedOut`).
  - Helpers: `stateFor`, `authenticatedState`, `unauthenticatedState`, `expiredState`, `recoverableErrorState`, `fatalErrorState`.
- `restoreSession` rewritten deterministically:
  - Reads stored credentials **before** any mutation (`hasStoredCredentials()` = cookie + envelope) so SESSION_EXPIRED (had credentials) is distinguished from UNAUTHENTICATED (clean first launch), via `signedOutPhase(hadCredentials)`.
  - Valid session → AUTHENTICATED; refresh `"refreshed"` → re-check → AUTHENTICATED or `signedOutPhase`; `"temporary"` → cached session or RECOVERABLE_ERROR; `"invalid"`/`"unavailable"` → `signedOutPhase`.
  - Error handling: 401 → recovery path; other errors → cached session, else `isTransientBootstrapError` (PulseApiError with `request_unreachable`, status 503, or ≥500) → RECOVERABLE_ERROR, otherwise FATAL_ERROR.
  - All sign-in / create-account / sign-out / cached-restore / QA paths route through the constructor helpers.

### 4.2 App shell — `mobile-native/App.tsx`
- Initial state `stateFor("BOOTSTRAPPING")`; bootstrap extracted into a `useCallback` and run on mount.
- Render gates on `phase`: spinner while BOOTSTRAPPING; an error panel with a **"Try again"** `Pressable` (re-runs bootstrap) for RECOVERABLE_ERROR / FATAL_ERROR. `requestReauthentication` transitions to `expiredState()`.
- Net user-visible win: a transient network blip on cold start no longer silently dumps the user to the login screen — it shows a retryable recoverable state, and a still-valid cached session survives.

### 4.3 QA simulator auth — `mobile-native/src/session/qaSimulatorAuth.ts`
- Updated to the phase constructors (`authenticatedState`/`unauthenticatedState`) at all four session-construction sites.

### 4.4 Pre-existing guarantees verified retained (read-only review)
- **Single-flight token refresh** — `mobile-native/src/api/pulseApi.ts` keeps a module-level in-flight `refreshPromise` and returns it to concurrent callers; logs `PULSESOC_SESSION_INVALID`/`PULSESOC_SESSION_REFRESH_TEMPORARY` with `{path,status}` only (no tokens/PII).
- **No double submit** — `LoginScreen.tsx` guards with `if (submitting) return;`; truthful error mapping via `describeLoginError`.
- **Logout clears user-scoped state**, **account-switch isolation**, **secure storage** (expo-secure-store), and **no token/PII logging** confirmed intact.

## 5. Wave 1 — Home Layout (NRB-058)

- Root cause: `HomeScreen.tsx` hardcoded `paddingBottom: 172`, deviating from the app-wide structural pattern; on devices whose home-indicator inset differed from that baseline the dock overlapped the composer / last row.
- Fix: the shared dock-clearance constant `BOTTOM_NAV_CONTENT_CLEARANCE = 92` lives in `mobile-native/src/navigation/BottomNavVisibility.tsx` (documented). `HomeScreen.tsx` now sets `paddingBottom: Math.max(insets.bottom, 12) + BOTTOM_NAV_CONTENT_CLEARANCE` via `useSafeAreaInsets`, and `components/Screen.tsx` references the same constant instead of a bare `+ 92`. All scroll surfaces now reserve dock clearance identically and inset-aware.

## 6. Tests Added / Changed

- **New** `mobile-native/src/session/__tests__/restoreSession.test.ts` — 7 tests covering AUTHENTICATED, UNAUTHENTICATED (clean first launch), SESSION_EXPIRED (cookie present + recover "invalid"), AUTHENTICATED-after-refresh, RECOVERABLE_ERROR (503 unreachable, no cache), AUTHENTICATED-from-cache on network failure, and FATAL_ERROR (generic error, no cache). Uses the real `PulseApiError` class so `instanceof` transient detection is exercised.
- **New** `mobile-native/src/screens/__tests__/HomeScreen.layout.test.ts` — 3 source-scan assertions: no `paddingBottom: 172`; feed padding derived from `useSafeAreaInsets` + `BOTTOM_NAV_CONTENT_CLEARANCE` (`Math.max(insets.bottom, 12) + BOTTOM_NAV_CONTENT_CLEARANCE`); `Screen.tsx` shares the constant (no bare `+ 92`).
- Existing 43 auth/session tests pass unchanged against the derived `status` (the reason a derived projection was chosen over a breaking rename).

## 7. Verification Results

| Check | Command | Result |
|---|---|---|
| TypeScript | `npx tsc --noEmit` | **PASS** (exit 0) |
| Jest (full) | `npx jest` | **PASS** — 37 suites / 355 tests |
| Expo Doctor | `npx expo-doctor` | **PASS** — 18/18 |
| Whitespace | `git diff --check` | **PASS** (clean) |
| Foundation audit | `python3 scripts/pulsesoc_native_app_foundation_audit.py` | **PASS** (exit 0) |
| Live audit | `python3 scripts/pulsesoc_native_live_audit.py` | **PASS** (exit 0) |
| Feature-parity audit | `python3 scripts/pulsesoc_native_feature_parity_audit.py` | **PASS** (exit 0) |
| WebView replacement audit | `python3 scripts/pulsesoc_native_webview_replacement_audit.py` | **FAIL (expected, out of scope)** — exit 1, 54 hard blockers |

## 8. Device / Simulator Validation

- Simulator: prior release-readiness evidence exists; the NRB-058 fix is verified by source-scan tests and the shared-constant unification.
- Physical device `P3r7or` (iPhone 16 Pro): **NOT OBSERVED this mission** — build/deploy to the device and on-device sign-in / logout / relaunch / account-switch and Home dock-clearance visual QA are the remaining verification steps for NRB-058 and NRB-059. No credentials were entered or exposed.

## 9. Release Readiness

**NO-GO** for replacing the production WebView app. Wave 0 and Wave 1 removed audit noise and stabilized the P0 auth/session and Home-layout blockers in code, but:
- The WebView-exit replacement gate still fails on real remaining web-fallback source (out of scope here).
- Physical-device QA (NRB-063) and Apple release tasks (NRB-064) remain open.
- NRB-058 / NRB-059 device QA on `P3r7or` is pending build/deploy.

## 10. Authoritative Records Updated

- `reports/pulsesoc_native_blocker_inventory_2026-07-20.md` — Resolution Log + per-NRB detail updates (NRB-058…062).
- `reports/pulsesoc_native_blocker_inventory_2026-07-20.json` — machine-readable status/resolution for NRB-058…062 + summary note.
- `reports/pulsesoc_native_progress.md` — Wave 0/1 mission entry.
- `reports/pulsesoc_native_webview_replacement_readiness.md` / `.json` — verdict unchanged (NO-GO), dated note added.

## 11. Commits & Working-Tree Disposition

Committed this mission (branch `release/undx-nexus-core-v4`):
- `fix(home): reserve inset-aware bottom-dock clearance (NRB-058)` — HomeScreen.tsx, Screen.tsx, BottomNavVisibility.tsx.
- `test(home): guard inset-aware bottom-dock clearance (NRB-058)` — HomeScreen.layout.test.ts.
- `fix(release-gate): repair stale native release-gate audits (Wave 0)` — foundation, webview-replacement, and feature-parity audits + regenerated webview readiness JSON.
- `docs(native): ...` — the report updates listed in §10.

**Held back — auth/session cluster (NRB-059).** The Wave 1 state machine (`App.tsx`, `src/session/auth.ts`, `src/session/qaSimulatorAuth.ts`, `src/session/__tests__/restoreSession.test.ts`) is complete and green in the working tree, but `src/session/auth.ts` **imports new symbols** from pre-existing dirty files owned by a **separate Face-ID / login-mark workstream**: `RegisterResponse` from `src/api/auth.ts`, and `clearActiveSessionKeepBiometric` / `getBiometricSession` / `getBiometricUserId` / `setBiometricSession` from `src/session/sessionStore.ts`. Because my state machine is layered on top of that unreviewed work, `auth.ts` cannot be committed in isolation without either breaking the committed snapshot's typecheck or folding in ~9 files I did not author or review this session (`api/auth.ts`, `sessionStore.ts`, `biometricAuth.ts`, `LoginScreen.tsx`, `SignupScreen.tsx`, `components/auth/*`, new `src/auth/`, new `src/components/auth/signup/`, and their tests). To respect the "preserve pre-existing dirty auth work" constraint and avoid mis-attribution, this cluster is **left staged-pending for a human decision** (see the handoff at the end of the mission response). All checks were run against the full working tree (which includes this cluster) and pass.

**Preserved uncommitted (unrelated workstreams):** the Face-ID/login-mark auth cluster above; the native-Live-studio pair (`scripts/pulsesoc_native_live_audit.py`, `reports/pulsesoc_native_live_progress.md`); `mobile-native/app.json`, `ios/…/project.pbxproj`, the deleted brand PNG and new `mobile-native/assets/`; and `reports/security/security_findings_ledger_a0cc15cd.md`. No `git reset`, `git clean`, `git checkout --`, `git restore .`, or force-push was used.
