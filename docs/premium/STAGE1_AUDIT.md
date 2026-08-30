# Premium Mission — Stage 1 Audit Matrix

Captured: 2026-08-30 · Base: 136106ad (deployed) · Golden rule: no faked Premium value.

## Truth chain (verified by reading code)

| Layer | File | Verdict |
|---|---|---|
| Entitlement resolution | services/business_os/entitlements/premium.py | CANONICAL. resolve() reconciles 4 authorities (legacy tables, ent_grants, users columns, session flag); split-brain reporting; account_hold beats grants. |
| Feature truth | services/business_os/entitlements/readiness.py | CANONICAL. `sellable(key)` is the only advertising authority; unknown keys fail closed to FUTURE. |
| Status API | services/business_os/entitlements/premium_api.py | HONEST. Benefits = sellable ∩ presented; allowances only from real limit grants + ent_usage; subscription via column allowlist; NOT_VERIFICATION disclaimer everywhere. |
| Migration facade | services/business_os/entitlements/facade.py | SOUND. off/shadow/canonical; legacy readers mapped for all 9 presented keys; account-hold precedence single-sourced. |
| StoreKit verify | services/business_os/entitlements/iap_apple.py | REAL. ES256 JWS + x5c chain to injected trust anchor; no skip-verification path; grants nothing on failure. |
| Quota engine | services/business_os/entitlements/usage.py | REAL but UNWIRED for premium: check_and_consume call sites exist only in business_os assistants, none for premium.* capabilities. |
| Native client | mobile-native/src/api/premiumCenter.ts | CANONICAL, fail-closed normalize, display-only cache. Legacy api/premium.ts deprecated — do not build on it. |
| Native screen | mobile-native/src/screens/PremiumCenterScreen.tsx | Honest sections; CommandCenter modules all inert (see gap G1). |

## Benefit matrix

| Key | Readiness | Enforcement (verified call site) | Real counts available |
|---|---|---|---|
| premium.profile.customization | PRODUCTION | bot.py `_profile_customization_allowed` → facade.check (L77256) | theme/aura state in pulse_premium_profiles |
| premium.identity.effects | BETA | bot.py `_identity_effects_allowed` → facade.check (L77177) | effect_key set/unset |
| premium.media.higher_quality | BETA | **No per-key call site found** — enforced only via generic is_premium at media routes; readiness's `enforced_by=facade.check` is aspirational | none (no counter) |
| premium.undx.advanced | BETA | **No per-key call site found** — same; NO credit meter (per readiness note, must not advertise numeric allowance) | none |
| premium.crypto.advanced_alerts | PRODUCTION | alert_engine premium gate L3883-3886; `_is_advanced_rule` L2079 | COUNT of owned advanced rules |
| premium.crypto.portfolio | PRODUCTION | portfolio_service FREE_LIMITS holdings=3, `_limit_check` L83-85 | COUNT(portfolio_items) vs ceiling |
| premium.crypto.intelligence | PRODUCTION | derived read over portfolio | derives from holdings count |
| premium.verification.blue_check.apply | PRODUCTION (apply only) | gated app flow; tests/test_premium_blue_check_gate.py | application state row |
| priority_verification | BLOCKED | — | NEVER SELL. Verification not purchasable. |
| premium.security.timeline | FUTURE | — | Security never paywalled. |
| premium.undx.credits | FUTURE (no meter) | — | must not fake an allowance |

## Gaps (what the mission actually builds)

- **G1 — CommandCenter is hollow.** `COMMAND_MODULES = ["activity","valueRecap","usage","achievements","unlocked","recommended"]` (PremiumCenterScreen.tsx L1234) all render inert NEXT chips because no backend measures them.
- **G2 — No premium usage summary endpoint.** Real counts ARE derivable today: advanced alert rules owned, portfolio holdings vs free ceiling of 3, identity/theme active state, blue-check application state, business_os_ent_usage rows. Nothing else — anything without a backing count is omitted, not estimated.
- **G3 — Unused-benefit discovery.** "recommended" can be derived honestly from G2: a member with 0 advanced rules / ≤3 holdings / no theme set has concrete unused benefits.
- **G4 (observation, do-not-fix here).** readiness.enforced_by for media.higher_quality and undx.advanced points at facade.check but no per-key call site exists; enforcement is via generic premium truthiness. Documented, not changed.

## Build plan (allowlist candidates)

1. `services/business_os/entitlements/usage_summary.py` (new) — server-side real-count aggregation feeding "usage" + "recommended".
2. `premium_api.py` — expose summary in status_center or a sibling endpoint (additive).
3. `bot.py` — one additive route registration (surgical; foreign +7 hunk must survive; stage via explicit `git add bot.py` only).
4. `mobile-native/src/api/premiumCenter.ts` — additive types + fetch.
5. `mobile-native/src/screens/PremiumCenterScreen.tsx` — make "usage" and "recommended" modules live; others stay inert (no backend truth).
6. `docs/premium/*` — this audit + final report.

Not sellable / not built: activity, valueRecap, achievements, unlocked (no measurement exists; faking violates the golden rule).
