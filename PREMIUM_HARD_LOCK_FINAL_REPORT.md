# PulseSoc Premium Entitlement Hard-Lock — Final Report

**Mission:** P0 monetization — premium tile contents accessible only to active premium/trial members
**Commit:** `c9377982` on `main` (34 files, +1365/−45, explicit staging only)
**Date:** 2026-09-04
**Verdict:** **PARTIAL — all code, tests and static gates PASS; Stage 39 physical-iPhone QA pending (user-run, script below)**

---

## Verification matrix

| # | Requirement | Result | Evidence |
|---|-------------|--------|----------|
| 1 | One server authority (canonical tier resolver) | **PASS** | `GET /api/private-office/entitlement` → TierAnswer; mobile `parseTierAnswer` requires `ok && resolver_state=="ok"`, else UNKNOWN_TIER. `noClientTierInference` jest rules A/B pass. |
| 2 | 7-day trial, genuinely new users only | **PASS** | `trial.start_trial_if_eligible` refuses unless caller asserts `is_new_signup`; only the signup handler does. Test: `ProspectiveOnlyAndInputValidation` (3/3). |
| 3 | Trial idempotent, server clock, replay never extends | **PASS** | `TrialLifecycleEndToEnd`: period_end bounded by server `now+7d`; replay → `already_used`, grant rows byte-identical. |
| 4 | Durable abuse prevention, no fingerprinting | **PASS** | `has_ever_had_trial` keys on the account's grant rows — any row, any status (revoked included) → permanently ineligible. Tested incl. post-revocation. |
| 5 | "Canceled" keeps access until period end | **PASS** | Canonical grants carry `expires_at`; every read compares to the clock. Existing `test_crypto_premium_gate` + tier resolver suites unchanged and green. |
| 6 | Tier inheritance FREE⊂PREMIUM⊂PRIVATE⊂PRIVATE_OFFICE | **PASS** | `tierSatisfies` on mobile; `PremiumFeatureGate` PRIVATE_OFFICE-inherits test green. |
| 7 | Per-feature hard locks (not tile hiding) | **PASS** | Screen-wrap gate on Market Pulse, Watchlists, Alerts, Asset Detail, Intelligence Center; locked branch never mounts the body (zero feature requests) — rendered-assertion tests. |
| 8 | API hard locks | **PASS** | ~20 bot.py route gates through the one `crypto_premium_gate` (+`CAP_CRYPTO_INTELLIGENCE`); deny-closed + payload-shape suites green (57/57). Flask-dependent route tests run in CI (no flask in sandbox). |
| 9 | Deep-link hard locks | **PASS** | Deep links resolve to the same exported wrapped screens; navigator untouched. |
| 10 | Cached-screen reconcile on foreground/403 | **PASS** | AppState "active" → `loadCanonicalTier()`; `reconcilePremiumRequired` on API denial; Portfolio/AlertHistory wired. |
| 11 | Bounded offline behavior — never render "you are on Free" on resolver failure | **PASS** | UNKNOWN_TIER → "Membership check unavailable" + retry, upsell suppressed. Rendered-assertion test green. |
| 12 | Truthful trial UX (6d23h → "6 days", never "7") | **PASS** | `trialDaysLeft` floors; unit + rendered tests (6d23h→6, 7d→7, 5h→0, expired→null). |
| 13 | No data deletion on expiry | **PASS** | Expired grant rows persist (asserted); alert rules kept `active`; briefings history/preferences readable ("keep read of history"). |
| 14 | Alert delivery stops on expiry, rules stay | **PASS** | `evaluate_alert_rule` delivery-time gate for EVERY rule type → `skipped/premium_required`, rule untouched, resumes on renewal. Engine + delivery-stop suites green. Briefings gated pre-CLAIM (`premium_required`), fail-closed. |
| 15 | Restore purchase updates server entitlement | **PASS** | `resetCanonicalTier()+loadCanonicalTier()` on purchase/restore; server restore path unchanged (canonical provider grants). Restore-idempotency test green (5× restore → 1 grant). |
| 16 | Observability | **PASS** | `premium_trial_started` product event at grant; `_mark_checked` status message on paused delivery; `advanced_state.last_status=premium_required` preserved. |
| 17 | Reuse existing schema | **PASS** | Zero new tables. Trial rides `pulse_premium_trial` catalog plan via existing `sync_subscription_entitlements`. |
| 18 | BUSINESS_OS_ENTITLEMENTS flag honored | **PASS** | Legacy trial statuses time-bounded by `_trial_window_open` (fails closed on unparseable dates) so flag-off mode can't leak an unbounded trial. |
| 19 | Git safety (Stage 40) | **PASS** | Explicit staging only; bot.py staged hunk-by-hunk (14 premium hunks; live/messenger/private-office-security hunks left untouched); i18n catalogs staged at key level (only `premium.gate`); no `add -A`/reset/clean/force-push. Stale `.git/*.lock` files (sandbox cannot unlink) cleared by rename. |
| 20 | Physical iPhone QA (Stage 39) | **PENDING** | User-run — script below. |

**Test totals this mission:** backend 57 (crypto_premium) + 13 (trial) + 7 (delivery stop) + 57 (business_os entitlements) + tier-resolver & kill-switch suites; mobile 62 jest (entitlements + alert suites) + full prior shards; tsc clean; i18n 11 locales OK; audio gate: no protected path touched.

---

## Stage 39 — Device QA script (run on your iPhone, dev build)

Users: **A** new signup (fresh email) · **B** active premium (Stripe) · **C** trial day ~6 · **D** expired trial · **E** canceled-not-yet-ended · **F** free (never trialed) · **G** restored purchase · **H** signed out→in as F on B's device · **I** airplane-mode B.

1. **A:** Sign up fresh → Premium tile features open; banner shows "6 days left in your Premium trial" (not 7). Sign out/in → still trial, same end date.
2. **A replay:** Delete app, reinstall, sign in as A → no second trial, same end date.
3. **B:** Market Pulse, Watchlists, Alerts, Asset Detail, Intelligence Center all open. Create an alert → fires.
4. **D:** Every premium screen shows the upsell ("See Premium plans"); tapping a premium deep link (`pulsesoc://` to Market Pulse/Alerts) shows the same lock, no content flash. Alert rules still listed in history (read-only), no deliveries.
5. **F:** Same locks as D. Portfolio history readable; adding past free ceiling → upsell.
6. **E:** Everything still open (access until period end).
7. **G:** Restore Purchases on a re-signed-in premium account → features unlock without app restart.
8. **H:** On a device where B was cached: sign out, sign in as F → premium screens LOCKED immediately (no stale-cache leak).
9. **I:** Airplane mode on B, open a premium screen → "Membership check unavailable" + Retry — never "you are on Free". Reconnect, foreground → content returns without restart.
10. Background the app on a locked account, upgrade via Stripe on web, foreground → screen unlocks on its own (foreground reconcile).

Report each numbered item PASS/FAIL and I'll issue the FINAL verdict.

---

## Not gated (deliberate, per confirmed product decisions)

Portfolio reads, Briefings reads, CryptoAlertHistory (own reactive gate), UNDX screens (premium UNDX capabilities are server-authorized per call), PrivateOfficeScreen (own tier system).
