# Business OS — Stage 6 Localization — Completion Report

**Branch:** `release/undx-nexus-core-v4`
**Date:** 2026-07-25
**Scope (§15):** stand up the **sixth Stage 6 vertical** — an **informational-only**, **deterministic** string-resolution engine. An org declares *locales* (a `locale` tag, one flagged `is_default`, an optional explicit `fallback_locale`, an `active` toggle); an append-only log records *translation strings* (a `value` for a `string_key` in a `locale`); the engine computes a rebuildable, per-org projection of **resolutions** — for every (locale, string_key) cell it walks a fixed fallback chain and records which value would surface and why. Gated behind `BUSINESS_OS_LOCALIZATION`.
**Hard boundary:** **nothing renders.** A resolution is a *reporting label* — a quantity summarizing which string *would* resolve for a locale — not a rendered UI string. Nothing here ships a translation to a client, mutates product copy, or takes any side effect. Whether a resolved value is ever displayed is a separate, separately-reviewed integration on top of the product's real rendering path.
**Pattern:** strangler — a new canonical `business_os_l10n_*` surface is built beside any existing copy/i18n handling; nothing legacy is read or written. The vertical mirrors the attribution / recommendations / merchant-automation / creator-commerce / governed-UNDX modules' discipline (append-only truth + rebuildable projection, idempotent ingest, dark-404 gating, curated error codes) exactly.

---

## 1. Status summary

| Part | Deliverable | Status |
|------|-------------|--------|
| 21 | Canonical `business_os_l10n_*` schema (append-only locale + string logs, rebuildable resolution projection, audit) | **PASS** |
| 22 | Deterministic resolution engine (exact → explicit fallback → language base → org default → missing; newest value wins; coverage rollup; idempotent recompute) | **PASS** |
| 23 | Framework-agnostic API controllers + 6 thin `bot.py` routes | **PASS** |
| 24 | Full test matrix + advertising / UNDX / creator / merchant / recommendations / attribution / crypto / entitlement / marketplace / payments / IAP regression + this report | **PASS** |

---

## 2. What was built

Three modules under `services/business_os/localization/` (plus six thin `bot.py` routes):

- `schema.py` — idempotent `ensure_schema()` for the canonical surface: `business_os_l10n_locales` (append-only locale decls: `locale`, `is_default`, optional `fallback_locale`, `active` toggle), `business_os_l10n_strings` (append-only fact log: a `value` for a `string_key` in a `locale`), `business_os_l10n_resolutions` (the rebuildable per-(org, locale, string_key) projection), and `business_os_l10n_audit`. Text UUID PKs, SQLite/Postgres portable. A UNIQUE `(source, external_ref)` on both input logs makes external-feed ingest idempotent (NULL ref — manual entries — is exempt); a UNIQUE `(org_id, locale, string_key)` makes a resolution row exactly-once.
- `engine.py` — the resolution core. `record_locale` / `record_string` append immutable rows and no-op on a replayed `(source, external_ref)`. Locale tags are canonicalized (`EN_us` → `en-us`, `_`→`-`, lowercased) so equivalent tags resolve identically. `resolve_org(org_id)` builds a value map picking the **newest** recorded value per (key, locale) cell, then for every (locale, string_key) resolves via a fixed fallback chain: **exact** locale match → the locale's **explicit** `fallback_locale` → the **language base** (`en-US` → `en`) → the **org default** locale → **missing**. Each resolution records `value`, `resolved_from`, and `match_type`. Ordering is a strict tie-break — `match_type` rank (`missing` < `default` < `base` < `fallback` < `exact`, surfacing gaps first), then `locale` ascending, then `string_key` ascending — so the output is fully reproducible. A per-locale **coverage** rollup reports total / resolved / missing / `coverage_pct`. Re-evaluation is a deterministic DELETE-then-INSERT replace; the resolution table is a projection, always rebuildable from the two logs.
- `api.py` — framework-agnostic controllers returning `(status, body)` tuples with an `ok` bool; **dark 404** when `BUSINESS_OS_LOCALIZATION` is off; curated error codes only (`missing_fields`, `missing_payload`, `invalid_locale`, `invalid_string`, `invalid_request`) — never an internal exception string. The resolutions read is **compute-on-read** (it evaluates the org once if the projection is empty and returns the coverage rollup). Locale/string ingest and resolve are operator entry points; locales/strings reports are read-only.
- `bot.py` — 6 thin authenticated routes (`POST`/`GET .../l10n/locales`, `POST`/`GET .../l10n/strings`, `GET .../l10n/resolutions`, `POST .../l10n/resolve`) that reuse the existing `pulse_ads_api_user_required` / `pulse_ads_verify_write` / `pulse_ads_json_payload` / `_bo_ad_reply` plumbing and lazily import the controller. Dark 404 when the flag is off.

---

## 3. Correctness integrity (the parts that must be right)

**Resolution is deterministic.** There is no randomness anywhere. The output is a stable order with an explicit tie-break — `match_type` rank (`missing` < `default` < `base` < `fallback` < `exact`), then `locale` ascending, then `string_key` ascending — proven by a test that asserts the decisions come back non-decreasing by rank, `missing` first and `exact` last, with contiguous ranks `1..n`. Two evaluations of the same inputs yield identical resolution lists.

**The fallback chain is fixed and labeled.** A `fr` request for a key that has an `fr` value resolves `exact`; a key that has only the `en` default value resolves `default` with `resolved_from = en`. An `en-US` locale with no own value falls back to the `en` **base** language. A locale with an explicit `fallback_locale = fr` resolves through `fr` (`fallback`) and that **beats** the org default — each arm proven by a focused test.

**Missing surfaces, never guesses.** A (locale, string_key) with no value anywhere in its chain resolves `missing` with a NULL value — proven by a test where the org default itself lacks the key and a third locale has nothing in its chain.

**Newest value wins.** Two recorded values for the same (key, locale) resolve to the later-recorded one — proven by a test that records a correction after a strict time delta and asserts the correction surfaces.

**Coverage is a correct rollup.** A locale with two resolved cells out of two reports 100%; a locale with one resolved and one missing reports 50% — proven by a test asserting exact resolved/missing counts and `coverage_pct`.

**The logs are the authority; resolutions are a projection.** Locales and strings are immutable rows; the resolution list is recomputed deterministically and is always rebuildable. Re-evaluation is a replace, so re-running after a crash yields identical rows — proven by a test that resolves twice, asserts equality, and asserts exactly one row per (locale, string_key) (no duplication).

**Ingest is idempotent; bad input is curated.** A feed replaying the same `(source, external_ref)` on either log returns the existing row and creates no duplicate (NULL refs exempt). An empty locale, key, or value raises the module's curated `LocalizationError`; the controller maps these to `invalid_locale` / `invalid_string` — never an internal exception string.

**Nothing renders.** A resolution only records a label — proven by a test asserting that after evaluation the only `business_os_l10n_*` tables that exist are the four canonical ones; nothing ships a string, mutates copy, or takes a side effect.

---

## 4. Test evidence (all standalone, no pytest, exit 0)

**Localization — 27/27**

| Suite | Result |
|-------|--------|
| `test_l10n_schema.py` (tables, idempotency, locale+string `(source, external_ref)` dedupe + NULL-exempt, resolution key exactly-once, legacy untouched) | 6/6 |
| `test_l10n_engine.py` (dedupe, curated bad input, locale normalization, exact+default fallback, base-language fallback, explicit-fallback-beats-default, missing surfaces, newest wins, deterministic ordering, coverage rollup, idempotent replace, no side effects) | 12/12 |
| `test_l10n_api.py` (controller contract: dark, validation, compute-on-read resolutions + coverage, locales/strings reports, resolve) | 9/9 |

**Regression — no breakage introduced (all green)**

| Group | Suites | Tests |
|-------|--------|-------|
| Governed UNDX actions (Stage 6 Part 5) | 3 | 31/31 |
| Creator commerce (Stage 6 Part 4) | 3 | 25/25 |
| Merchant automation (Stage 6 Part 3) | 3 | 28/28 |
| Recommendations (Stage 6 Part 2) | 3 | 27/27 |
| Attribution (Stage 6 Part 1) | 3 | 27/27 |
| Crypto (Stage 5) | 5 | 38/38 |
| Entitlement + premium visibility | 5 | 65/65 |
| Marketplace (Stage 3) | 4 | 29/29 |
| Payments foundation (ledger/webhook, stripe handler) | 2 | 13/13 |
| Advertising (Stage 2) | 22 | 218/218 |
| IAP / Premium (Stage 4) | 3 | 26/26 |

**Total: 554 tests, 0 failures** (527 prior regression unchanged + 27 new localization). `python -m py_compile bot.py` → **COMPILE OK**. The 6 new localization routes have unique endpoint function names (`api_business_os_l10n_record_locale`, `_record_string`, `_locales_report`, `_strings_report`, `_resolutions_report`, `_resolve`); the GET/POST pairs on `/l10n/locales` and `/l10n/strings` are method-distinguished exactly as the existing attribution / recommendations / merchant / creator / UNDX routes are.

---

## 5. Honest limitations

- **A resolution is not a rendered string.** These resolutions are a reporting projection, not a shipped translation. Wiring a resolved value into the product's actual rendering path (feed copy, notifications, native UI) would be a separate, separately-reviewed integration. This vertical deliberately stops at the label.
- **No ICU / plural / gender / interpolation grammar.** A string is an opaque `value` for a `(string_key, locale)`. There is no plural-category selection, gender agreement, number/date formatting, or `{placeholder}` interpolation. Those are a larger, separate effort; the current engine is a transparent per-cell resolution lens.
- **The fallback chain is fixed, not policy-driven.** The order (exact → explicit fallback → language base → org default → missing) is hard-coded. There is no per-org override of the chain shape and no multi-hop transitive fallback beyond the single declared `fallback_locale`.
- **Strings and locales are caller-supplied.** The engine resolves whatever is recorded; wiring the product's real translation source (a TMS export, a strings feed) into this log as durable feeds keyed by `external_ref` is the remaining production integration step. The idempotent `(source, external_ref)` design exists for exactly that.
- **Newest-wins is by `created_at` with a `string_id` tie-break.** Two values recorded within the same clock tick fall back to `string_id` ordering (a UUID), which is not semantically meaningful. In practice corrections arrive at distinct times; a feed needing strict ordering should carry monotonic refs. This is documented in the module.
- **`bot.py` is not importable in the sandbox** (missing stripe/flask/telegram, no PyPI). The 6 new routes are verified structurally via `py_compile` and endpoint-name/duplicate scanning; the controller logic they call is fully unit-tested outside Flask. Runtime route verification remains an owner-side step.

---

## 6. Reversibility

With `BUSINESS_OS_LOCALIZATION` unset the entire surface is inert: all six routes return a dark 404 and the controllers short-circuit — proven by `test_dark_when_disabled`. The localization modules are additive; no legacy table is read or written (proven by `test_legacy_untouched`, which asserts only the four `business_os_l10n_*` tables were created). Rolling back is flag-off; nothing to migrate down.

---

## 7. Stage 6 remaining verticals (roadmap)

Attribution (Part 1), Recommendations (Part 2), Merchant Automation (Part 3), Creator Commerce (Part 4), Governed UNDX Business Actions (Part 5), and Localization (this slice) are now delivered. Still to come per §15: **performance** — the final Stage 6 vertical, to follow the same strangler pattern (canonical `business_os_*` tables, dedicated flag, thin `bot.py` adapters, standalone tests, full regression, completion report).
