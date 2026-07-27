# Business OS — Stage 6 Performance — Completion Report

**Branch:** `release/undx-nexus-core-v4`
**Date:** 2026-07-25
**Scope (§15):** stand up the **seventh and final Stage 6 vertical** — an **informational-only**, **deterministic** metric-summary engine. An append-only log records *samples* (a numeric `value` for a `metric_key`, in an optional `window` bucket); an append-only log records *targets* (warn/breach thresholds with a `direction` — `lower_is_better` | `higher_is_better` — and a `compare_stat`). The engine computes a rebuildable, per-org projection of **summaries** — for every (metric_key, window) cell it rolls the samples up (count / min / max / mean / p50 / p95) and labels the cell `ok` / `warn` / `breach` / `none` against the newest active target. Gated behind `BUSINESS_OS_PERFORMANCE`.
**Hard boundary:** **nothing renders, alerts, pages, or scales.** A summary is a *reporting label* — a quantity describing how a metric *is doing* against a declared target — not an alarm, an autoscaler input, or a paging signal. Nothing here fires a notification, mutates infrastructure, or takes any side effect. Whether a `breach` ever triggers an action is a separate, separately-reviewed integration on top of the product's real alerting path.
**Pattern:** strangler — a new canonical `business_os_perf_*` surface is built beside any existing metrics/monitoring handling; nothing legacy is read or written. The vertical mirrors the attribution / recommendations / merchant-automation / creator-commerce / governed-UNDX / localization modules' discipline (append-only truth + rebuildable projection, idempotent ingest, dark-404 gating, curated error codes) exactly.

---

## 1. Status summary

| Part | Deliverable | Status |
|------|-------------|--------|
| 25 | Canonical `business_os_perf_*` schema (append-only sample + target logs, rebuildable summary projection, audit) | **PASS** |
| 26 | Deterministic rollup engine (count/min/max/mean/p50/p95; status by direction + compare_stat; newest active target wins; status rollup; idempotent recompute) | **PASS** |
| 27 | Framework-agnostic API controllers + 6 thin `bot.py` routes | **PASS** |
| 28 | Full test matrix + advertising / localization / UNDX / creator / merchant / recommendations / attribution / crypto / entitlement / marketplace / payments / IAP regression + this report | **PASS** |

---

## 2. What was built

Three modules under `services/business_os/performance/` (plus six thin `bot.py` routes):

- `schema.py` — idempotent `ensure_schema()` for the canonical surface: `business_os_perf_samples` (append-only measurement log: a `value` for a `metric_key`, optional `window`, optional `unit`/`captured_at`), `business_os_perf_targets` (append-only target log: `direction` with a `CHECK IN ('lower_is_better','higher_is_better')`, `compare_stat`, `warn_threshold`, `breach_threshold`, `active` toggle), `business_os_perf_summaries` (the rebuildable per-(org, metric_key, window) projection), and `business_os_perf_audit`. Text UUID PKs, SQLite/Postgres portable. A UNIQUE `(source, external_ref)` on both input logs makes external-feed ingest idempotent (NULL ref — manual entries — is exempt); a UNIQUE `(org_id, metric_key, window)` makes a summary row exactly-once. `window` is `NOT NULL DEFAULT ''` so the summary key never collides under SQLite distinct-NULL semantics.
- `engine.py` — the rollup core. `record_sample` / `record_target` append immutable rows and no-op on a replayed `(source, external_ref)`. Sample values are parsed as finite floats (non-numeric, `inf`, `NaN`, `None` are curated rejects); target `direction` and `compare_stat` are validated and at least one threshold is required. `summarize_org(org_id)` groups samples into (metric_key, window) cells, computes count/min/max/mean/p50/p95 (percentiles by linear interpolation, `pos = (q/100)*(n-1)`, rounded to 4 decimals), and for each cell labels it against the **newest active** target for that metric: for `lower_is_better`, `breach` if the chosen stat ≥ `breach_threshold`, `warn` if ≥ `warn_threshold`, else `ok`; `higher_is_better` inverts the comparisons; an untargeted cell is `none`. The `compare_stat` selects which rollup field drives the label (a `p95` target catches a tail breach a healthy `mean` would hide). Ordering is a strict tie-break — `_STATUS_ORDER` (`breach` < `warn` < `ok` < `none`, surfacing problems first), then `metric_key` ascending, then `window` ascending — so the output is fully reproducible, and a `status_rollup` counts cells by status. Re-evaluation is a deterministic DELETE-then-INSERT replace; the summary table is a projection, always rebuildable from the two logs.
- `api.py` — framework-agnostic controllers returning `(status, body)` tuples with an `ok` bool; **dark 404** when `BUSINESS_OS_PERFORMANCE` is off; curated error codes only (`missing_fields`, `missing_payload`, `invalid_sample`, `invalid_target`, `invalid_request`) — never an internal exception string. The summaries read is **compute-on-read** (it evaluates the org once if the projection is empty and returns the status rollup). Sample/target ingest and summarize are operator entry points; targets/samples reports are read-only.
- `bot.py` — 6 thin authenticated routes (`POST`/`GET .../perf/samples`, `POST`/`GET .../perf/targets`, `GET .../perf/summaries`, `POST .../perf/summarize`) that reuse the existing `pulse_ads_api_user_required` / `pulse_ads_verify_write` / `pulse_ads_json_payload` / `_bo_ad_reply` plumbing and lazily import the controller. Dark 404 when the flag is off.

---

## 3. Correctness integrity (the parts that must be right)

**Rollup is deterministic.** There is no randomness anywhere. Statistics are computed with a fixed method: count/min/max/mean, p50 and p95 by linear interpolation over the sorted values, each rounded to 4 decimals. `[10, 20, 30]` yields mean 20.0, p50 20.0, p95 29.0 (`20 + (30−20)·0.9`); `[100, 100, 100, 100, 1000]` yields mean 280.0 and p95 820.0 (`100 + (1000−100)·0.8`) — each proven by a focused test. Two evaluations of the same inputs yield identical summary lists.

**Status honors direction and the chosen stat.** `lower_is_better` labels a cell `ok` / `warn` / `breach` as the compare stat crosses the warn then breach thresholds upward; `higher_is_better` inverts it — both proven by dedicated tests. The `compare_stat` selects the field: a `p95` target on `[100,100,100,100,1000]` reports `breach` (p95 820 ≥ 500) even though the `mean` (280) alone would be `ok` — proven by `test_compare_stat_selects_field`.

**Windows are separate cells.** Samples in different `window` buckets roll up independently — proven by a test asserting two windows keep separate means and counts.

**Untargeted metrics surface as `none`, never a false pass.** A cell with no active target resolves `none` with a NULL `target_stat` — proven by `test_no_target_is_none` and by the rollup stats test.

**Newest active target wins.** A stricter target recorded after an initial one drives the label — a mean of 100 becomes `warn` under a newer `warn_threshold=50` instead of `ok` — proven by `test_newest_target_wins`.

**Ordering and rollup are correct.** Summaries come back non-decreasing by `_STATUS_ORDER` (`breach` first, `none` last), then metric then window, with contiguous ranks `1..n`; the `status_rollup` counts each status exactly — both proven by `test_deterministic_ordering` and `test_status_rollup`.

**The logs are the authority; summaries are a projection.** Samples and targets are immutable rows; the summary list is recomputed deterministically and is always rebuildable. Re-evaluation is a replace, so re-running after a crash yields identical rows — proven by `test_recompute_idempotent_replace` (resolve twice, assert equality, assert exactly one row per cell).

**Ingest is idempotent; bad input is curated.** A feed replaying the same `(source, external_ref)` on either log returns the existing row and creates no duplicate (NULL refs exempt). A non-numeric / non-finite / empty sample, a bad `direction` / `compare_stat`, or a target with no thresholds raises the module's curated `PerformanceError`; the controller maps these to `invalid_sample` / `invalid_target` — never an internal exception string.

**Nothing renders, alerts, or scales.** A summary only records a label — proven by `test_no_side_effects` asserting that after evaluation the only `business_os_perf_*` tables that exist are the four canonical ones; nothing fires a notification, mutates infrastructure, or takes a side effect.

---

## 4. Test evidence (all standalone, no pytest, exit 0)

**Performance — 30/30**

| Suite | Result |
|-------|--------|
| `test_perf_schema.py` (tables, idempotency, sample+target `(source, external_ref)` dedupe + NULL-exempt, direction CHECK enforced, summary key exactly-once, legacy untouched) | 7/7 |
| `test_perf_engine.py` (dedupe, curated bad input, rollup stats, windows separate, status lower/higher-is-better, compare_stat selects field, no-target→none, newest wins, deterministic ordering, status rollup, idempotent replace, no side effects) | 13/13 |
| `test_perf_api.py` (controller contract: dark, validation, compute-on-read summaries + status rollup, targets/samples reports, summarize) | 10/10 |

**Regression — no breakage introduced (all green)**

| Group | Suites | Tests |
|-------|--------|-------|
| Localization (Stage 6 Part 6) | 3 | 27/27 |
| Governed UNDX actions (Stage 6 Part 5) | 3 | 31/31 |
| UNDX marketplace workflow | 1 | 5/5 |
| Creator commerce (Stage 6 Part 4) | 3 | 25/25 |
| Merchant automation (Stage 6 Part 3) | 3 | 28/28 |
| Recommendations (Stage 6 Part 2) | 3 | 27/27 |
| Attribution (Stage 6 Part 1) | 3 | 27/27 |
| Crypto (Stage 5) | 5 | 38/38 |
| Entitlement + premium visibility | 5 | 65/65 |
| Marketplace (Stage 3) | 4 | 29/29 |
| Payments foundation (ledger/webhook, stripe handler) | 2 | 13/13 |
| IAP / Premium (Stage 4) | 3 | 26/26 |
| Advertising (Stage 2) | 22 | 218/218 |

**Total: 589 tests, 0 failures** (559 prior regression + 30 new performance). `python -m py_compile bot.py` → **COMPILE OK**. The 6 new performance routes have unique endpoint function names (`api_business_os_perf_record_sample`, `_record_target`, `_targets_report`, `_samples_report`, `_summaries_report`, `_summarize`); the GET/POST pairs on `/perf/samples` and `/perf/targets` are method-distinguished exactly as the existing attribution / recommendations / merchant / creator / UNDX / localization routes are. The 22 advertising suites were re-run in full after the `bot.py` route insertion and hold at 218/218.

---

## 5. Honest limitations

- **A summary is not an alert.** These summaries are a reporting projection, not a monitoring signal. Wiring a `breach` into the product's actual alerting / paging / autoscaling path would be a separate, separately-reviewed integration. This vertical deliberately stops at the label.
- **Percentiles are linear-interpolation, unweighted, over the full sample set.** There is no time-decay, no sliding retention window, no per-sample weighting, and no streaming/approximate percentile (t-digest etc.). Every recorded sample in a cell counts equally forever until the log is trimmed; a caller needing recency should bucket by `window` or feed a pre-windowed stream.
- **The status ladder is fixed, not policy-driven.** Two thresholds (warn, breach) and one `compare_stat` per target; there is no multi-tier severity, no hysteresis / debounce, and no compound conditions across metrics. The order (`breach` < `warn` < `ok` < `none`) is hard-coded.
- **Samples and targets are caller-supplied.** The engine summarizes whatever is recorded; wiring the product's real metrics source (an APM export, a StatsD/Prometheus scrape) into this log as durable feeds keyed by `external_ref` is the remaining production integration step. The idempotent `(source, external_ref)` design exists for exactly that.
- **Newest-target-wins is by `created_at` with a `target_id` tie-break.** Two targets recorded within the same clock tick fall back to `target_id` (a UUID) ordering, which is not semantically meaningful. In practice targets are revised at distinct times; a feed needing strict ordering should carry monotonic refs.
- **`bot.py` is not importable in the sandbox** (missing stripe/flask/telegram, no PyPI). The 6 new routes are verified structurally via `py_compile` and endpoint-name/duplicate scanning; the controller logic they call is fully unit-tested outside Flask. Runtime route verification remains an owner-side step.

---

## 6. Reversibility

With `BUSINESS_OS_PERFORMANCE` unset the entire surface is inert: all six routes return a dark 404 and the controllers short-circuit — proven by `test_dark_when_disabled`. The performance modules are additive; no legacy table is read or written (proven by `test_no_side_effects` / `test_legacy_untouched`, which assert only the four `business_os_perf_*` tables were created). Rolling back is flag-off; nothing to migrate down.

---

## 7. Stage 6 — complete

Attribution (Part 1), Recommendations (Part 2), Merchant Automation (Part 3), Creator Commerce (Part 4), Governed UNDX Business Actions (Part 5), Localization (Part 6), and Performance (this slice, Part 7) are now delivered. **Performance was the final Stage 6 vertical per §15 — Stage 6 is complete in its entirety.** Every vertical followed the same strangler discipline: canonical `business_os_*` tables beside untouched legacy, a dedicated feature flag, append-only truth with a rebuildable projection, idempotent ingest, dark-404 gating, curated error codes, thin `bot.py` adapters, standalone tests, and full cross-vertical regression. The consolidated suite stands at **589 tests, 0 failures**.

*Uncommitted:* all new `services/business_os/performance/*` modules, the three `tests/business_os/test_perf_*.py` suites, the six `bot.py` performance routes, and this report are on the working tree and awaiting an owner-side commit (the sandbox `.git` is read-only).
