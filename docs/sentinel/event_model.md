# Sentinel Event Model — SentinelEventV1 (Stage 2, rebuilt in Mission 2)

Module: `services/sentinel/events.py`. Table: `sentinel_events`.
`event_version = "1"`. One envelope for every domain; every Mission 2
field has a safe default so old call sites keep working.

## Canonical envelope

| Field | Meaning |
|-------|---------|
| `category` | One of 15 canonical categories. Unknown → `EventRejected` (SC15). |
| `event_type` | Snake_case machine name (e.g. `login_failed`). |
| `severity` | `info` / `low` / `medium` / `high` / `critical`. |
| `actor_id` | Registered identity that produced the observation (SC12). |
| `actor_type` | Closed vocabulary from `entities.ACTOR_TYPES`. Derived from actor-id prefix (`undx.`→UNDX_AGENT, `sentinel.`/`service.`→SERVICE, `worker.`→WORKER, `runbook.`→RUNBOOK, else SYSTEM) when omitted; unknown value → rejected. |
| `source` | Emitting component (`test`, `bridge:security_events`, adapter id). |
| `source_system` / `source_component` / `source_event_id` | Provenance: which system, which piece of it, and its native id for tracebacks. |
| `source_trust` | One of the 7 trust grades (see `source_trust.md`). Unknown grade → rejected. |
| `confidence` | Optional float. Defaults to the trust grade's ceiling; an explicit value above the ceiling is **rejected**, not clamped (SC4). |
| `environment` | `production` / `development` / `""`. |
| `subject_type` / `subject_id` | What the event is about (`user`/`42`). |
| `resource_type` / `resource_ref` | Typed resource the event touches. |
| `session_ref` / `device_ref` / `network_ref` | Typed refs (`entities.make_ref`, e.g. `network:abc123`). Malformed ref → rejected. |
| `occurred_at` | UTC `YYYY-MM-DD HH:MM:SS`, when it happened at the source. |
| `received_at` | Set by `ingest()`, when Sentinel saw it. The gap between the two is measurable skew, never hidden. |
| `expires_at` | After this, the observation is STALE and cannot read as fresh truth. |
| `operational/security/financial/privacy/compliance_impact` | Five independent impact dimensions, each `none`–`critical`. Unknown level → rejected. |
| `correlation_keys` | Tuple of scalar keys correlation rules may group on. |
| `evidence_refs` | Tuple of evidence-chain record ids supporting this event. |
| `policy_context` | Dict; which policy/rule version produced the observation. Non-dict → rejected. |
| `payload` | Dict, redacted to CONFIDENTIAL ceiling **before** persistence (SC7). Non-dict → rejected. |
| `dedupe_key` | UNIQUE. Explicit, or deterministic sha256 of (source, category, type, subject, occurred_at). |

Rows also persist `deployment_sha` and `policy_version` (NOT NULL): every
event is traceable to the code and policy that emitted it.

## The 15 categories

`AUTH`, `SESSION`, `ADMIN`, `PRIVACY`, `SECURITY`, `PAYMENT`, `LEDGER`,
`SETTLEMENT`, `PAYOUT`, `ADVERTISING`, `PROVIDER`, `DEPLOYMENT`, `WORKER`,
`UNDX`, `SENTINEL_SELF`. A new category is a schema-visible decision, not
an ad-hoc string.

## Trust–confidence coupling (Mission 2)

Confidence ceilings by trust grade: AUTHORITATIVE/MEASURED 1.0,
DERIVED 0.8, CONFIGURED 0.4, STALE 0.3, SIMULATED 0.2, UNKNOWN 0.1.
"The API key is set" (CONFIGURED) can never carry the confidence of "the
webhook round-trip succeeded" (MEASURED). Violations are rejected at
construction — an event that lies about its certainty never exists.

## Ingestion guarantees

1. **Kill-switchable**: `killswitches.ingest_enabled()` first; the
   emergency switch stops even ingestion.
2. **Idempotent**: duplicate `dedupe_key` is a silent no-op returning
   `False` — replays and bridge re-syncs are safe.
3. **Redacted at the door**: `classification.redact(payload, CONFIDENTIAL)`
   runs before the INSERT. Key-named secrets are masked here; value-smuggled
   secrets are caught by `INV_NO_SECRETS_IN_SENTINEL` (layered defense).
4. **Validated, fail closed**: category, severity, trust grade, actor type,
   impact levels, typed refs, and dict-ness of payload/policy_context are
   all enforced in `Event.__post_init__`; anything malformed raises
   `EventRejected`.

## What events are NOT

Events are evidence of observation, not verdicts. No event, by itself,
triggers enforcement (SC8). Correlation rules and invariants read events;
humans and deterministic code decide what they mean.
