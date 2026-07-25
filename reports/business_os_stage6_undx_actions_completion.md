# Business OS — Stage 6 Governed UNDX Business Actions — Completion Report

**Branch:** `release/undx-nexus-core-v4`
**Date:** 2026-07-25
**Scope (§15):** stand up the **fifth Stage 6 vertical** — an **informational-only**, **deterministic** governance decision engine for proposed UNDX business actions. An org declares governance *policies* (an `action_type` — or the `*` wildcard — maps to an `effect` of `allow` / `deny` / `require_approval`, with an optional `max_risk` ceiling and a `priority`); an append-only log records proposed *action requests* (an actor proposed an action of some type against a subject, at a declared risk); the engine computes a rebuildable, per-org projection of governance **decisions** by resolving the highest-priority active policy that matches each request. Gated behind `BUSINESS_OS_UNDX_ACTIONS`.
**Hard boundary:** **no action executes.** A decision is a *governance label* — a reporting quantity summarizing what governance *would* permit — not an instruction. Nothing here runs a tool, sends a message, posts content, moves money, or takes any side effect. Whether an `allow` decision is ever acted on is a separate, separately-reviewed integration on top of the product's real action systems.
**Pattern:** strangler — a new canonical `business_os_undx_*` surface is built beside the existing UNDX policy/operator surfaces (`services/undx_policy.py`, `services/undx_operator.py`); nothing legacy is read or written. The vertical mirrors the attribution / recommendations / merchant-automation / creator-commerce modules' discipline (append-only truth + rebuildable projection, idempotent ingest, dark-404 gating, curated error codes) exactly, and aligns its risk vocabulary (`read_only` < `low` < `medium` < `high`) with the existing UNDX tool registry.

---

## 1. Status summary

| Part | Deliverable | Status |
|------|-------------|--------|
| 17 | Canonical `business_os_undx_*` schema (append-only policy + action-request logs, rebuildable decision projection, audit) | **PASS** |
| 18 | Deterministic governance engine (exact-over-wildcard match; priority tie-break; risk-ceiling escalation; safe default; idempotent re-evaluate) | **PASS** |
| 19 | Framework-agnostic API controllers + 6 thin `bot.py` routes | **PASS** |
| 20 | Full test matrix + creator / merchant / recommendations / attribution / crypto / entitlement / marketplace / payments / advertising / IAP regression + this report | **PASS** |

---

## 2. What was built

Three modules under `services/business_os/undx_actions/` (plus six thin `bot.py` routes):

- `schema.py` — idempotent `ensure_schema()` for the canonical surface: `business_os_undx_policies` (append-only policy defs: `action_type` or `*`, `effect`, optional `max_risk` ceiling, `active` toggle, `priority`), `business_os_undx_action_requests` (append-only fact log; an actor proposed an action of some type against a subject at a declared `risk`), `business_os_undx_decisions` (the rebuildable per-(org, request) decision projection), and `business_os_undx_audit`. Text UUID PKs, SQLite/Postgres portable. An `effect` CHECK constrains the enum (`allow/deny/require_approval`); a UNIQUE `(source, external_ref)` on both input logs makes external-feed ingest idempotent (NULL ref — manual entries — is exempt); a UNIQUE `(request_id)` makes a decision row exactly-once.
- `engine.py` — the governance core. `record_policy` / `record_action_request` append immutable rows and no-op on a replayed `(source, external_ref)`. `evaluate_org(org_id)` reads every **active** policy and, for each request, resolves the governing policy — an exact `action_type` match beats the `*` wildcard; among equal specificity, higher `priority` wins, then `policy_id` ascending — and applies its `effect`. A policy's optional `max_risk` ceiling escalates an otherwise-`allow` decision to `require_approval` when the request's declared risk exceeds the ceiling. When no policy matches, the default effect is `require_approval` (safe governance default — never a silent allow). Ordering is a strict tie-break — effect (`deny` < `require_approval` < `allow`), then `action_type` ascending, then `request_id` ascending — so the output is fully reproducible. Re-evaluation is a deterministic replace; the decision table is a projection, always rebuildable from the two logs.
- `api.py` — framework-agnostic controllers returning `(status, body)` tuples with an `ok` bool; **dark 404** when `BUSINESS_OS_UNDX_ACTIONS` is off; curated error codes only (`missing_fields`, `missing_payload`, `invalid_policy`, `invalid_request`) — never an internal exception string. The decisions read is **compute-on-read** (it evaluates the org once if the projection is empty). Policy/request ingest and evaluate are operator entry points; policies/requests reports are read-only.
- `bot.py` — 6 thin authenticated routes (`POST`/`GET .../undx/policies`, `POST`/`GET .../undx/requests`, `GET .../undx/decisions`, `POST .../undx/evaluate`) that reuse the existing `pulse_ads_api_user_required` / `pulse_ads_verify_write` / `pulse_ads_json_payload` / `_bo_ad_reply` plumbing and lazily import the controller. Dark 404 when the flag is off.

---

## 3. Correctness integrity (the parts that must be right)

**Evaluation is deterministic.** There is no randomness anywhere. The output is a stable order with an explicit tie-break — effect (`deny` < `require_approval` < `allow`), then `action_type` ascending, then `request_id` ascending — proven by a test that records one policy of each effect and asserts the decisions come back `deny`, `require_approval`, `allow` at ranks 1, 2, 3. Two evaluations of the same inputs yield identical decision lists.

**Specificity beats permissiveness.** An exact `action_type` policy governs a request even when a higher-priority `*` wildcard policy exists — proven by a test where a permissive `* -> allow` at priority 100 loses to a specific `delete_account -> deny` at priority 0.

**Priority breaks ties within equal specificity.** Two `post` policies (`allow` priority 1, `deny` priority 9) resolve to `deny` — proven by a focused test.

**Risk ceilings escalate, never silently allow.** A `send -> allow` policy with `max_risk = medium` allows a low-risk request but escalates a high-risk request to `require_approval` — proven by a test asserting both outcomes from the same policy.

**No match means human approval.** A request whose `action_type` matches no active policy resolves to `require_approval` with a NULL matched policy — proven by a test. An `active = false` policy is skipped even when it would match, falling through to the same safe default — proven by a separate test.

**The logs are the authority; decisions are a projection.** Policies and requests are immutable rows; the decision list is recomputed deterministically from them and is always rebuildable. Re-evaluation is a replace, so re-running after a crash yields identical rows — proven by a test that evaluates twice, asserts equality, and asserts exactly one row per `request_id` (no duplication).

**Ingest is idempotent; bad input is curated.** A feed replaying the same `(source, external_ref)` on either log returns the existing row and creates no duplicate (NULL refs exempt). An unknown `effect`, `risk`, or `max_risk` raises the module's curated `UndxActionsError`; the controller maps these to `invalid_policy` / `invalid_request` — never an internal exception string.

**No action is taken.** A decision only records a governance *label* — proven by a test asserting that after evaluation the only `business_os_undx_*` tables that exist are the four canonical ones; nothing runs, sends, posts, or moves money.

---

## 4. Test evidence (all standalone, no pytest, exit 0)

**Governed UNDX actions — 26/26**

| Suite | Result |
|-------|--------|
| `test_undx_schema.py` (tables, idempotency, effect CHECK, policy+request dedupe indexes + NULL-exempt, decision key, legacy untouched) | 7/7 |
| `test_undx_engine.py` (ingest+dedupe, curated bad enums, exact-beats-wildcard, priority tie-break, default approval, risk-ceiling escalation, inactive skipped, deterministic ordering, idempotent re-evaluate, no side effects) | 10/10 |
| `test_undx_api.py` (controller contract: dark, validation, compute-on-read decisions, policies/requests reports, evaluate) | 9/9 |

**Regression — no breakage introduced (all green)**

| Group | Suites | Tests |
|-------|--------|-------|
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

**Total: 522 tests, 0 failures** (496 prior regression unchanged + 26 new governed UNDX actions). `python -m py_compile bot.py` → **COMPILE OK**. The 6 new UNDX routes have unique endpoint function names (`api_business_os_undx_record_policy`, `_record_request`, `_policies_report`, `_requests_report`, `_decisions_report`, `_evaluate`); the GET/POST pairs on `/undx/policies` and `/undx/requests` are method-distinguished exactly as the existing attribution/recommendations/merchant/creator/marketplace routes are.

---

## 5. Honest limitations

- **A decision is not an execution.** These decisions are a governance projection, not a queued job. Wiring an `allow` decision into an actual tool call, message send, or content post would be a separate, separately-reviewed integration on top of the product's real UNDX action systems (`services/undx_operator.py` and the authenticated production tool registry). This vertical deliberately stops at the label.
- **Policies are single-`action_type` threshold rules, not a workflow language.** A policy is one `action_type -> effect` mapping with an optional risk ceiling and a priority. There is no compound boolean logic (AND/OR across attributes), no per-actor or per-subject scoping beyond `action_type`, no time-window or rate logic. Those are a larger, separate effort; the current engine is a transparent per-action governance lens.
- **Requests and policies are caller-supplied.** The engine governs whatever policies and requests are recorded; wiring the product's real proposed-action stream (agent tool proposals, operator-initiated business actions) into this log as durable feeds keyed by `external_ref` is the remaining production integration step. The idempotent `(source, external_ref)` design exists for exactly that.
- **Risk is a declared four-level enum.** `read_only` < `low` < `medium` < `high` is compared verbatim against a policy ceiling; the engine assigns no risk itself and applies no hidden weighting. Deriving risk from action semantics (as the existing tool registry does per-route) is a caller/integration decision, not baked in here.
- **The safe default is `require_approval`, not `deny`.** An unmatched or inactive-only action surfaces as needing human approval rather than a hard block, so governance is fail-safe (a human decides) rather than fail-closed. An org wanting default-deny declares a `* -> deny` policy explicitly.
- **`bot.py` is not importable in the sandbox** (missing stripe/flask/telegram, no PyPI). The 6 new routes are verified structurally via `py_compile` and endpoint-name/duplicate scanning; the controller logic they call is fully unit-tested outside Flask. Runtime route verification remains an owner-side step.

---

## 6. Reversibility

With `BUSINESS_OS_UNDX_ACTIONS` unset the entire surface is inert: all six routes return a dark 404 and the controllers short-circuit — proven by `test_dark_when_disabled`. The governed-actions modules are additive; no legacy table is read or written (proven by `test_legacy_untouched`, which asserts only the four `business_os_undx_*` tables were created). Rolling back is flag-off; nothing to migrate down.

---

## 7. Stage 6 remaining verticals (roadmap)

Attribution (Part 1), Recommendations (Part 2), Merchant Automation (Part 3), Creator Commerce (Part 4), and Governed UNDX Business Actions (this slice) are now delivered. Still to come per §15: **localization** and **performance** — each to follow the same strangler pattern (canonical `business_os_*` tables, dedicated flag, thin `bot.py` adapters, standalone tests, full regression, completion report).
