# Sentinel Roadmap

V1 (this foundation) is Phase 1. Every later phase EXTENDS these
contracts; nothing replaces them. There is no V2 — there is only more of
V1's constitution applied to more surface.

## Phase 1 — Foundation (SHIPPED)

Constitution, classification, identity/authority, risk budgets, kill
switches, event envelope + store, evidence chain, incident engine,
deterministic correlation (CR1–CR5), graph edges, journeys, read-only
invariants, provider health + breaker contract, runbook pipeline with
independent verification (3 read-only runbooks), UNDX structured
interface, injection heuristics, security-center bridge, self-metrics,
unwired read-only API, regression suite (102 tests).

## Phase 2 — Live observation (wire the senses)

- Schedule `security_center_bridge.sync_security_events` from an existing
  worker loop under `SENTINEL_INGEST_ENABLED`.
- Emit AUTH/PAYMENT/DEPLOYMENT/WORKER events from the highest-value
  existing code paths (login, checkout webhook, deploy hook, worker
  heartbeats) — thin `events.ingest` calls only.
- Run correlation rules + invariants on a worker cadence.
- Shared (DB-backed) BudgetTracker — prerequisite for ANY mutating
  runbook in multi-worker deployments.
- Ops doc: enable/disable drill for every kill switch on Railway.

## Phase 3 — Operator surface

- Wire `init_sentinel(app)` behind an explicit env opt-in; admin-only.
- Minimal incident console (list, detail, transition with notes).
- In-band human approvals: approval records in `sentinel_evidence`
  instead of trusting env access alone (closes residual risk #1).

## Phase 4 — First reversible automations

- 2–3 narrowly scoped ACT_REVERSIBLE runbooks (e.g. require-reauth flag,
  provider health snapshot on alert), each with independent verifiers,
  tight budgets, and per-runbook switches. No financial mutations — SC6
  holds permanently.
- Containment proposals attached to incidents (still human-approved).

## Phase 5 — Detection depth

- More correlation rules from observed incident patterns (still
  deterministic; SC8 unchanged).
- Journey-based synthetic checks (read-only probes of the 6 journeys).
- Graph-assisted clustering surfaced for human review only.

## Phase 6 — External signal

- First real adapter (IP/device reputation) under the Stage 26 contract:
  capped severity, verified:False, MINIMIZE outbound.
- Breaker contract wired around adapter I/O.

## Phase 7 — Evidence hardening

- Periodic chain checkpoints anchored outside the primary DB (object
  storage write-once) — closes residual risk #4.
- Retention/compaction policy for `sentinel_events` (classification-aware).

## Phase 8 — Scale decisions (evidence-gated)

- Revisit Stage 25 ONLY if volume data demands it (partitioning first,
  dedicated stores last). Any storage change preserves the envelope,
  the chain, and the constitution.

## Standing non-goals

Full SIEM/XDR/SOAR suites, ML verdict models, autonomous financial
actions, punishment automation, second security centers, and anything
that gives a model authority. These are not "later" — they are out.
