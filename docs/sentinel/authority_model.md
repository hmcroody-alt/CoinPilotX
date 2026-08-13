# Sentinel Authority Model (Stages 4–5)

Modules: `services/sentinel/identity.py`, `services/sentinel/authority.py`.

## Identity (Stage 4)

Every actor is a registered `Actor(actor_id, kind, trust_tier)`.

Kinds and structural tier ceilings (`MAX_TIER_BY_KIND`, enforced at
construction — violation is a hard error, not a clamp):

| kind | max tier | examples |
|------|----------|----------|
| `human` | OWNER | operators; only humans can approve high-risk actions |
| `service` | OPERATIONAL | `sentinel.ingest`, `sentinel.correlator`, `sentinel.verifier` |
| `model` | ADVISORY | `undx.model` — permanently advisory (SC2) |
| `external` | ADVISORY | vendor adapters |

Unknown actor ids resolve to an UNTRUSTED external identity (SC15).
There is **no super key** — no credential grants blanket authority (SC11);
the regression suite bans the very string from the codebase.

## Authority (Stage 5)

Five independent dimensions × five levels. A grant is per-dimension;
authority in one dimension implies nothing in another.

Dimensions: `OPERATIONAL`, `SECURITY`, `FINANCIAL`, `PRIVACY`, `COMPLIANCE`.

Levels (`AuthorityLevel`):

| level | value | meaning |
|-------|-------|---------|
| READ | 0 | observe |
| SUGGEST | 1 | propose; ceiling for all models (SC2) |
| ACT_REVERSIBLE | 2 | bounded, undoable automation |
| ACT_SENSITIVE | 3 | requires `human_approved=True` at check time |
| OWNER_ONLY | 4 | never satisfiable by automation; cannot be a runbook level |

`authority.check(actor, grant, dimension, required, human_approved=False)`
returns an `AuthorityDecision(allowed, reason, rule_ids, policy_version)`.
Denials always cite constitution rule IDs. `check_all` with an empty
requirement set denies (fail closed, SC15).

## Decision order

1. Unknown dimension/level → deny (SC15)
2. Actor kind is model → capped at SUGGEST regardless of grant (SC2)
3. Grant level < required → deny
4. Required ≥ ACT_SENSITIVE and not `human_approved` → deny (SC3)
5. Required = OWNER_ONLY and actor is not a human at OWNER tier → deny

Authority is checked at the moment of action (runbook execution), not
cached — a revoked switch or grant takes effect immediately.
