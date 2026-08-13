# Sentinel V1 — Architecture

Sentinel is PulseSoc's canonical autonomous-operations and cyber-defense
foundation. It is the FIRST and ONLY such system — there is no V2, no parallel
implementation, and any future capability extends this foundation.

## Position in the platform

```
bot.py (Flask monolith)          workers (undx_worker, email_worker, …)
        │                                   │
        │  (bridge reads existing tables)   │
        ▼                                   ▼
┌─────────────────────────────────────────────────────┐
│ services/sentinel/            (24 modules)          │
│                                                     │
│  constitution ── the 15 rules everything cites      │
│  classification ─ 5-level data classification       │
│  identity ─────── actors, trust tiers, no super key │
│  authority ────── 5 dimensions × 5 levels           │
│  risk ─────────── bounded budgets (SC14)            │
│  killswitches ─── env-driven, default OFF           │
│  store ────────── 8 sentinel_* tables, existing DB  │
│  events ───────── canonical envelope, 15 categories │
│  evidence ─────── append-only sha256 hash chain     │
│  incidents ────── 11-state lifecycle engine         │
│  correlation ──── deterministic multi-signal rules  │
│  graph ────────── relational edges (no graph DB)    │
│  journeys ─────── 6 canonical user/system journeys  │
│  invariants ───── read-only financial checks        │
│  providers ────── provider→capability→status health │
│  runbooks ─────── governed, registered actions only │
│  verification ─── independent post-action checks    │
│  ai_security ──── injection heuristics (honest)     │
│  undx_interface ─ structured read/analyze for UNDX  │
│  adapters ─────── external signal normalization     │
│  security_center_bridge ─ ingests existing events   │
│  observability ── self-metrics + self-health        │
│  api ──────────── read-only Blueprint (NOT wired)   │
└─────────────────────────────────────────────────────┘
        │
        ▼
   existing database (SQLite locally, PostgreSQL in prod)
   via services/db.py — no Kafka, no Neo4j, no ClickHouse, no Elasticsearch
```

## Core doctrine

1. **UNDX is intelligence, not root.** Model output enters Sentinel only
   through `undx_interface.submit_analysis`, always recorded at severity
   `info` with authority `ADVISORY` (SC2). Models never mutate state,
   never assign severity, never approve actions.
2. **Foundation over features.** V1 has no SIEM, no XDR, no SOAR pipelines,
   no ML detection, no dashboards. It has the contracts those things will
   be forced to obey.
3. **Deny by default.** Automation is OFF until three env switches agree
   (master → domain → runbook), and the emergency kill switch overrides
   everything including ingest.
4. **Observe, never correct.** Invariant violations open incidents; they
   never write to financial tables. Circuit breakers are a contract, not
   an enforcement layer.
5. **Nothing self-certifies.** Runbook success is `EXECUTED_UNVERIFIED`
   until a *different* actor verifies it (SC4). Incident recovery requires
   an independent verifier.

## Write surface

Sentinel writes exclusively to tables prefixed `sentinel_` (enforced by
`tests/sentinel/test_ethical_regression.py`). It reads platform tables
(security_events, financial tables for invariants) but never mutates them.

## Storage decision (Stage 25)

Sentinel uses the existing database through `services/db.py`
(CompatConnection: `?` placeholders, portable schema DDL). Rationale: one
operational surface, one backup story, transactional co-location with the
data it observes, zero new infrastructure to secure. Dedicated stores are a
Phase 4+ consideration and only with volume evidence.

## Module dependency rule

Lower layers never import higher ones: constitution/classification are
leaf modules; store depends only on services/db; events depends on
classification+killswitches+store; everything else composes those.
`api.py` is the outermost layer and is intentionally not imported by
anything (including bot.py).
