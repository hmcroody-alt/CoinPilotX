# Business OS — Stage 2 Advertising MVP Consolidated Completion Report

**Stage:** 2 — Advertising MVP (consolidated sprint, Parts 1–9)
**Flag:** `BUSINESS_OS_ADVERTISING` (dark by default; viewer/advertiser routes 404, admin routes 409 when off)
**Assistant write kill switch:** `BUSINESS_OS_ADVERTISING_ASSISTANT_DISABLE_WRITES`
**Branch:** `release/undx-nexus-core-v4`
**Verification environment:** hermetic sandbox (temp SQLite per suite). `bot.py` is not importable in the sandbox, so its routes/helpers are verified structurally (`py_compile` + AST) and, where possible, behaviourally by executing the real source out of `bot.py`.

Status vocabulary used below is restricted to **PASS / PARTIAL / BLOCKED / NOT TESTED**. Nothing is marked PASS that was not observed green.

---

## 1. Executive status by part

| Part | Scope | Status |
|------|-------|--------|
| 1 | Billing — versioned CPM/CPC pricing, atomic escrow → platform-revenue debit, spend projection, reconciliation | **PASS** |
| 2 | Advertiser reporting + spend (Confirmed / Estimated / Modeled), owner-scoped | **PASS** |
| 3 | Canonical advertising notifications | **PASS** |
| 4 | Governed UNDX Advertising Assistant (confirm-before-consequential + read-after-write verify + kill switch) | **PASS** |
| 5 | Native iOS advertising portal | **PARTIAL** — HTTP API contract PASS; native Expo UI build/run BLOCKED in sandbox |
| 6 | Admin surfaces — billing inspection, fraud signals, spend controls, restrictions, appeals (governed) | **PASS** |
| 7 | Production Feed/Reels placement → canonical delivery integration | **PASS** |
| 8 | Full Stage 2 test matrix + regression | **PASS** |
| 9 | This consolidated completion report | **PASS** |

---

## 2. Test matrix (as observed)

All suites are standalone (`python tests/business_os/<name>.py`, no pytest), print `PASS <name>`, and exit 0/1.

### Stage 2 new surfaces

| Suite | Result |
|-------|--------|
| `test_advertising_billing.py` | PASS 14/14 |
| `test_advertising_reporting.py` | PASS 9/9 |
| `test_advertising_notifications.py` | PASS 6/6 |
| `test_advertising_assistant.py` | PASS 8/8 |
| `test_advertising_admin.py` | PASS 6/6 |
| `test_advertising_stage2_api.py` | PASS 5/5 |
| `test_advertising_feed_integration.py` | PASS 5/5 |

### Stage 1 / slice regression (unchanged, re-run green)

| Suite | Result |
|-------|--------|
| `test_advertising_slice1.py` | PASS 11/11 |
| `test_advertising_slice2_api.py` / `_routes.py` | PASS 8/8 · 6/6 |
| `test_advertising_slice3_api.py` / `_routes.py` | PASS 13/13 · 6/6 |
| `test_advertising_slice4_api.py` / `_routes.py` | PASS 15/15 · 8/8 |
| `test_advertising_slice5_api.py` / `_routes.py` | PASS 15/15 · 9/9 |
| `test_advertising_slice6_api.py` / `_routes.py` | PASS 19/19 · 10/10 |
| `test_advertising_slice7_api.py` | PASS 11/11 |
| `test_advertising_slice7_migration.py` | PASS 7/7 |
| `test_advertising_slice7_delivery.py` | PASS 17/17 |
| `test_advertising_slice7_routes.py` | PASS 10/10 |

### Shared money layer regression (advertising rides the canonical ledger)

| Suite | Result |
|-------|--------|
| `test_ledger_and_webhook_inbox.py` | PASS 6/6 |
| `test_stripe_ledger_handler.py` | PASS 7/7 |

**Total observed green this sprint: 218 advertising assertions + 13 ledger assertions.** `python3 -m py_compile bot.py` clean; no duplicate route endpoints introduced.

---

## 3. What each part delivered

### Part 1 — Billing (PASS)
Versioned, server-authoritative CPM/CPC pricing policies; billing eligibility derived from the immutable impression/click log; an atomic escrow-debit → platform-revenue-credit against the shared double-entry ledger (escrow account `ad_campaign_escrow:<cid>` is not allow-negative, so exhaustion surfaces as a ledger error rather than an overdraft); spend projection and a billing reconciliation pass. A self/advertiser-owned event is never billed.

### Part 2 — Reporting + spend (PASS)
Authoritative advertiser reporting split into **Confirmed / Estimated / Modeled** tiers plus an owner-scoped spend view. Ownership is enforced at the controller boundary: a non-owner read returns 404 (existence is not leaked).

### Part 3 — Notifications (PASS)
Canonical advertising notifications wired through the existing notification path (no new delivery mechanism invented).

### Part 4 — Governed UNDX Advertising Assistant (PASS)
Two-phase `plan` / `execute`. A read-only tool runs immediately from `plan`; a consequential tool (`set_budget`, `submit_campaign`, `activate_campaign`, …) mints a confirmation token bound to the exact `(user, tool, canonical params)`. `execute` refuses without the matching token (428 `confirmation_required`), refuses a forged/foreign token (409 `confirmation_mismatch`), and — critically — never reports success from the verb's return value: it re-reads canonical state and reports `verified` from what it observes. Write kill switch disables writes without touching reads. Ownership enforced on both paths (404).

### Part 5 — Native iOS advertising portal (PARTIAL)
The HTTP API contract advertisers/native clients consume is complete and tested (report, spend, assistant tools/plan/execute, appeals). Building and running the Expo/React-Native portal against a live backend is **BLOCKED** in this sandbox (no device/simulator, no live `gunicorn bot:app`). This is an environment limitation, not a missing backend surface.

### Part 6 — Admin surfaces (PASS)
Billing inspection (per-campaign money totals reconciled against escrow), fraud summary (clean vs flagged), and the governed actions: spend halt/lift, advertiser restrict/lift-restriction, and appeal submit/list/resolve. Every governed action requires a non-empty actor (`actor_required`) and a non-empty reason (`reason_required`), writes an append-only audit row, and returns an explicit before/after. A grant on an appeal lifts the restriction in the same governed action; a resolved appeal cannot be resolved twice (409). Restrictions ride the canonical advertiser `suspended` status and appeals ride the append-only `business_os_ad_audit` table — **no new migration was added**, per the "don't create many small foundational slices" directive.

### Part 7 — Production Feed/Reels integration (PASS)
The live `/api/pulse/feed` and `/api/pulse/reels/feed` responses now attach one canonical sponsored placement via a single module-level helper, `_bo_ad_attach_sponsored`. The helper is flag-gated and fully defensive: the organic feed is never mutated when the flag is off, when there is no eligible ad, or when the delivery layer raises. The injected value is the delivery pipeline's already client-safe projection (label only — no ledger ids, private targeting, or advertiser account internals). Because `bot.py` cannot be imported in the sandbox, `test_advertising_feed_integration.py` extracts the helper's **real source out of `bot.py` via AST** and executes it against the canonical delivery service and a delivery-ready hierarchy, proving flag-off no-op, client-safe injection with organic keys intact, reels-placement serving, defensive swallow on a raising delivery layer, and non-dict passthrough.

---

## 4. Files changed this sprint

**New service modules** (`services/business_os/advertising/`)
- `assistant.py` — governed plan/execute assistant (tokens, verification, kill switch).
- `admin.py` — consolidated Part-6 admin governance (billing/fraud/spend/restrictions/appeals).

**Extended**
- `api.py` — Part-2/4/6 thin controllers (report, spend, assistant tools/plan/execute, admin billing/fraud/spend/restrict/appeals). Every handler returns `(int status, dict body)` with an `ok` bool, dark-404 when the flag is off, an unknown-field allowlist (400 `unknown_field`), and only curated `AdvertisingError` messages.
- `bot.py` — 17 new thin route adapters (6 advertiser: report, spend, assistant tools/plan/execute, appeal; 11 admin: billing summary/events, fraud summary/flagged, spend halt/lift, restrict/lift-restriction, appeals list/resolve) plus the `_bo_ad_attach_sponsored` Feed/Reels helper wired into both production feed endpoints. All decision logic stays in the importable controllers; routes remain thin adapters.

**New tests** (`tests/business_os/`)
- `test_advertising_billing.py`, `test_advertising_reporting.py`, `test_advertising_notifications.py`, `test_advertising_assistant.py`, `test_advertising_admin.py`, `test_advertising_stage2_api.py`, `test_advertising_feed_integration.py`.

**New docs**
- `docs/business_os/STAGE2_ADVERTISING_COMPLETION_REPORT.md` (this file).

No legacy `pulse_ads_service` / `pulse_ad_*` table or route was touched; the whole surface stays dark by default.

---

## 5. Honest limitations / not-yet-covered

- **Native portal runtime (Part 5): BLOCKED.** The backend API is done and tested; the Expo UI cannot be built/run here.
- **bot.py under a live server: NOT TESTED.** Route wiring is verified structurally (compile + AST + duplicate-endpoint check) and the Feed/Reels helper is verified behaviourally by executing its real source; a full `gunicorn bot:app` request/response cycle is out of scope for the sandbox.
- **No auction / pacing / ML.** Selection remains deterministic per viewer behind the replaceable strategy interface from slice 7; Stage 2 did not change that.
- **Load/perf and multi-tenant concurrency: NOT TESTED.**

---

## 6. Sprint outcome

Every Stage 2 Advertising MVP part is delivered and observed green except the native iOS UI runtime, which is environmentally BLOCKED (its backend contract is PASS). The advertising surface is complete behind `BUSINESS_OS_ADVERTISING`, moves money only through the shared canonical ledger with governed admin controls, and injects sponsored placements into the live Feed/Reels responses without disturbing the organic feed. Ready to proceed to Stage 3 (Marketplace MVP).
