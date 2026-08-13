# Sentinel Operations: Kill Switches, Containment, Self-Metrics, API
(Stages 21, 23, 24, 28, 29 — updated in Mission 2)

## Kill switches (Stage 23)

Module: `services/sentinel/killswitches.py`. All env-driven, namespace
`SENTINEL_*`, mirroring the UNDX convention. Truthy = one of
`1/true/yes/on/enabled`.

Precedence (highest first):

| Switch | Default | Effect |
|--------|---------|--------|
| `SENTINEL_EMERGENCY_KILL_SWITCH` | off | ON kills EVERYTHING — automation and ingest |
| `SENTINEL_AUTOMATION_ENABLED` | **OFF** | master gate for all automation (SC3) |
| `SENTINEL_<DOMAIN>_AUTOMATION_ENABLED` | **OFF** | per authority dimension; useless without master |
| `SENTINEL_RUNBOOK_<NAME>_ENABLED` | **OFF** | per runbook; useless without domain+master |
| `SENTINEL_INGEST_ENABLED` | ON | observation only; emergency still kills it |

To run one runbook you must flip three switches ON, deliberately. To stop
everything you flip one. `switch_state()` reports the whole matrix for
the API/health surface.

## Failure containment (Stage 24)

Sentinel must never take the platform down with it:

- `observability.record` swallows storage errors and returns False — a
  broken metrics table cannot crash a request path.
- `runbooks.execute` returns DENIED dicts; it does not raise into callers.
- Invariants treat missing tables as SKIPPED, exceptions as skip-not-crash.
- The bridge tolerates absent source tables.
- api.py fails closed to 503 under the emergency switch.
- Nothing in sentinel is imported by bot.py, so a sentinel bug cannot
  break boot (registration isolation by construction, not by try/except).

## Self-metrics & self-health (Stage 28, extended Mission 2)

Module: `services/sentinel/observability.py`. Table: `sentinel_metrics`.
Closed metric-name registry (unknown names refused, returning False —
no unbounded cardinality). `summary(hours)` aggregates counts;
`self_health()` runs *independent probes* — store round-trip and a full
evidence-chain verification — rather than reporting cached flags (SC4
applied to ourselves).

Self-health reports a **maturity** grade Sentinel applies to itself:
`CONFIGURED` (code present, nothing proven) → `FUNCTIONAL` (probes pass
now) → `RECENTLY_PROVEN` (probes pass AND real events were ingested in
the last 24h). A failed probe never upgrades maturity.

`owner_summary()` is the one-glance owner contract: `overall_status`
(`ok` / `attention` / `critical` / `sentinel_impaired`), open and
critical incident counts, `owner_action_required_count`, per-domain
statuses (security, providers, workers, deployment), stale signal
count, and the deployment SHA. Absence of signal reports **unknown**,
never ok — no data is not good news.

## Deployment identity

`store.deployment_sha()` resolves the running SHA from env, precedence:
`RAILWAY_GIT_COMMIT_SHA` → `GIT_COMMIT` → `SOURCE_VERSION` →
`COMMIT_SHA` → `RAILWAY_GIT_COMMIT`, else `"unknown"`. This is the same
precedence bot.py's health surface uses — the two must agree or the
deployment-mismatch rule would fire against ourselves. Every event,
incident, transition, evidence record, and health snapshot row persists
the SHA (NOT NULL).

## Read-only API contract (Stage 29, Mission 2 mount)

Module: `services/sentinel/api.py`. Flask Blueprint `sentinel_bp`,
prefix **`/api/admin/sentinel`** (the path itself states the privilege
level), **deliberately NOT registered in bot.py**. Wiring it is an
explicit future opt-in: `init_sentinel(app)`.

- GET-only: `/health`, `/summary` (owner summary), `/switches`,
  `/events`, `/incidents`, `/incidents/<key>`, `/providers`,
  `/metrics`. The regression suite asserts the module contains no
  POST/PUT/DELETE/PATCH surface.
- `before_request`: admin session required (fail closed to 403);
  emergency kill → 503.
- Responses reuse the same redaction ceilings as internal reads.

Rationale for staying unwired in V1: bot.py is protected by a
diff-content gate, is being modified by concurrent workstreams, and the
API adds no V1 value until there is an operator UI. Zero exposure is the
correct default (SC3 in spirit).

## Hardening baseline (Stage 21)

Verified by `tests/sentinel/test_ethical_regression.py`, which is a
static self-inspection (non-destructive — no live attacks, no fuzzing of
production): no super-key strings, no hardcoded secrets, no
subprocess/os.system/eval/exec, no forbidden runbooks, read-only API, no
financial-table writes, only `sentinel_*` write targets, no protected
audio-stack references.
