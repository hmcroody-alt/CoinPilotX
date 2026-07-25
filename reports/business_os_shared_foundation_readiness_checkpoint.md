# Business OS — Shared Entitlement Foundation: Implementation-Readiness Checkpoint

Status: **READY** for downstream modules (Advertising, Marketplace). This is the frozen
contract new Business OS verticals build against. Verified against
`services/business_os/entitlements/{facade,service}.py` on the current working tree.

## 1. Final facade interface (stable — build against this)

`from services.business_os.entitlements import facade`

- `facade.check(subject_id, key, *, subject_type="user", context=None) -> bool`
  — the yes/no gate. Use at every authorization decision.
- `facade.explain(subject_id, key, *, subject_type="user", context=None) -> dict`
  — same decision with reasons (fields in §4). Use in tests, audit, admin views.
- `facade.shadow_compare(subject_id, key, *, subject_type="user", context=None) -> dict`
  — legacy-vs-canonical diff, records disagreement; never changes served access.
- `facade.account_hold(subject_id, context=None) -> {on_hold, account_status, access_enabled, reason}`
  — the shared suspension authority. Reused, never re-implemented per module.
- `facade.get_mode() -> "off" | "shadow" | "canonical"` (from `BUSINESS_OS_ENTITLEMENTS`).

Grant/lifecycle (server-authoritative, via `service`): `grant_entitlement`,
`revoke_entitlement`, `suspend_entitlement`, `has_entitlement`, `get_entitlements`,
`get_entitlement_limits`, `sync_subscription_entitlements`, `reconcile_entitlements`,
`ensure_schema`. New modules call these; they do **not** write entitlement tables directly.

`context` carries fresh account state `{"account_status", "access_enabled"}` so gates never
rely on a stale cached user. Omit it and the facade reads `users` by id.

## 2. Capability-key naming convention

Dotted, lowercase, hierarchical: **`domain.area.capability`**.
Existing catalog (in `_LEGACY_READERS`): `premium.profile.customization`,
`premium.identity.effects`, `premium.media.higher_quality`, `premium.undx.advanced`.

New verticals follow the same shape, e.g. `advertising.campaign.create`,
`advertising.analytics.advanced`, `marketplace.listing.publish` — matching the seed
catalog in `entitlements/schema.py` (`_SEED_CATALOG`). One key = one capability. A key resolves canonically if a
grant row exists; otherwise the optional legacy reader decides (fallback), else denied.
Register a legacy reader only when a pre-existing legacy signal must be honored during
migration; greenfield capabilities (Advertising) need none and are canonical-only.

## 3. Decision precedence (single rule, all keys)

1. **Account hold / suspension** (restriction) — overrides everything, incl. paid grants.
2. **Explicit revocation / expiry** of the grant (handled in grant resolution).
3. **Active canonical grant** → allow.
4. **Legacy fallback** when canonical is silent (no grant rows for the key at all).
5. **Default deny.**
Under flag `off`: legacy-only, no hold overlay — byte-for-byte pre-existing behavior.

## 4. Explainable decision fields (`explain` return)

`allowed, flag_mode, decision_source, mode, account_hold, account_status, reason,
legacy, canonical_grant`.
`decision_source` ∈ `{legacy, account_hold, canonical_grant, legacy_fallback}`;
`reason` carries the hold cause (e.g. `account_suspended`, `account_access_disabled`).

## 5. Legacy fallback behavior

Per key: if no canonical grant rows exist, the registered legacy reader (if any) supplies
the answer; capabilities with no reader default deny under canonical. `off` mode always
serves legacy only. This is the strangler seam — new capabilities start canonical-only and
never depend on legacy.

## 6. Shadow comparison behavior

`shadow` serves the **legacy** answer unchanged but computes what canonical *would* decide
(including the hold override) and records disagreements to `business_os_ent_audit`
(`action='shadow_diff'`). Used to prove a capability before flipping it to `canonical`.

## 7. Required tests for any new capability

Each new key ships a standalone-runnable suite (no pytest dependency) asserting:
active-eligible allowed; non-holder denied; **account-hold overrides grant**
(suspended/banned/restricted/`access_enabled=0`); expired grant denied; revoked grant
denied; **flag `off` = legacy unchanged**; flag `canonical` = precedence enforced;
shadow records the diff; repeated checks idempotent (reads create no grant rows);
capability isolation (a grant for another key does not leak). Byte-compile + direct
verification of the real server-side caller.

## 8. Six inputs kept SEPARATE (never merged into one record)

| Input | Authority / source | Not this |
|---|---|---|
| Commercial entitlement | `service.has_entitlement` / grant rows (source=stripe/apple/google/admin) | account state |
| Account hold / suspension | `facade.account_hold` (`account_status`,`access_enabled`) | a grant |
| Role / admin permission | existing bot.py RBAC (admin routes) | a commercial grant |
| Merchant / advertiser approval | dedicated approval-state record per vertical (e.g. advertiser status) | premium/commercial entitlement |
| Feature rollout | `BUSINESS_OS_ENTITLEMENTS` + per-capability rollout flag | user's entitlement |
| Usage allowance | `service.get_entitlement_limits` + atomic consumption | boolean entitlement |

A gate composes these; it never collapses them into a single generic flag.

## 9. Known limitations deferred to later phases

- Commits are owner-side only (sandbox `.git` is read-only).
- bot.py wrappers verified by byte-compile + inspection, not in-process import (hermetic
  sandbox lacks stripe/flask/telegram).
- Mobile-native parallel Premium checks: separate client surface — **later work**.
- Dedicated mobile-cache review and security review: **later work only**, not in scope now.
- No facade redesign planned; change only if a downstream module exposes a direct blocker.
