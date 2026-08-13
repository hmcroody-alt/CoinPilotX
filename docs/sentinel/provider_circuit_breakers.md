# Provider Circuit Breakers & Budgets (Mission 4, Stages 7–9)

## Scope

Circuit breakers here are **external-only**: they protect PulseSoc from
flaky/slow vendors. They never gate internal Sentinel detection — Mission 2/3
pipelines keep running regardless of vendor health.

## Circuit semantics

Per provider (persisted via `save_circuit`, reusing `providers.CircuitBreaker`):

- `closed` → normal; failures count toward the threshold.
- threshold failures → `open`; all requests denied at the policy gate.
- recovery window elapsed → `half_open`; one probe allowed.
- probe success → `closed`; probe failure → `open` again.

**An open circuit is a policy denial whose meaning is UNKNOWN, never SAFE.**
The denial reason names the circuit so triage explanations stay honest.
Open circuits are visible in `/providers/<id>/health` and self-health
(`circuit_breakers_open`).

## Budgets

Every provider has a minute/hour/day request budget declared in its
`ProviderSpec` (e.g. OSV 30/500/2000; KEV 2/4/8 — the catalog is a bulk sync,
not a per-indicator API). Exhaustion is a gate denial, not an exception, and
never triggers retries.

## Cache, negative cache, single-flight (Stage 7)

- Successful responses cache per provider TTL; cache hits bypass transport,
  budget, and circuit entirely.
- Negative results (NOT_AFFECTED, no-record) cache too — "we asked and the
  answer was clean" is evidence with an expiry, and prevents re-querying.
- Single-flight: concurrent identical requests collapse to one outbound call.

## Failure honesty

`record_result(success=False)` degrades the registry row (DEGRADED → FAILED)
and stores the truncated error detail. A provider that has never been called
stays in its configured state — never-called means **unknown, not healthy**
(Stage 32). Missing credentials mean CONFIGURED=false, never FAILED.
