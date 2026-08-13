# Sentinel Operations: Kill Switches, Containment, Self-Metrics, API
(Stages 21, 23, 24, 28, 29)

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

## Self-metrics & self-health (Stage 28)

Module: `services/sentinel/observability.py`. Table: `sentinel_metrics`.
Closed metric-name registry (unknown names refused, returning False —
no unbounded cardinality). `summary(hours)` aggregates counts;
`self_health()` runs *independent probes* — store round-trip and a full
evidence-chain verification — rather than reporting cached flags (SC4
applied to ourselves).

## Read-only API contract (Stage 29)

Module: `services/sentinel/api.py`. Flask Blueprint `sentinel_bp`,
prefix `/api/sentinel`, **deliberately NOT registered in bot.py**.
Wiring it is an explicit future opt-in: `init_sentinel(app)`.

- GET-only: `/health`, `/switches`, `/events`, `/incidents`,
  `/incidents/<key>`, `/providers`, `/metrics`. The regression suite
  asserts the module contains no POST/PUT/DELETE/PATCH surface.
- `before_request`: admin session required; emergency kill → 503.
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
