# Sentinel Evidence Chain (Stage 17, verified in Mission 2)

Module: `services/sentinel/evidence.py`. Table: `sentinel_evidence`.

## Append-only sha256 hash chain

Each record's hash covers its canonical body **plus the previous
record's hash**:

```
hash_n = sha256(prev_hash | seq | kind | actor_id | created_at | canonical_body)
hash_1 chains from GENESIS_HASH = "0" * 64
```

Any retroactive edit, deletion, or reordering breaks every subsequent
link (SC5 — never alter or hide security evidence). The module
deliberately exposes **no update or delete function**; the only writers
are `append()` and nothing else.

## Redaction before hashing

Bodies pass `classification.redact(body, CONFIDENTIAL)` **before**
canonicalisation and hashing (SC9). Secrets never enter the chain, so
there is never a "we must rewrite history to scrub a leak" scenario —
the second net for value-smuggled secrets is
`INV_NO_SECRETS_IN_SENTINEL`, which scans storage and opens an incident
rather than mutating evidence.

## Redaction ceilings

`classification` masks values of keys matching forbidden substrings
(secrets/tokens/keys → HIGHLY_RESTRICTED) and internal id fields
(including `pulse_id` → SENSITIVE) with the marker `[REDACTED:sentinel]`.
The same ceilings apply to event payloads at ingest and to API
responses — one redaction policy, applied at every boundary.

## Verification is independent, not self-certified

`verify_chain()` recomputes **every** link from genesis and reports
`{ok, records, broken_at}`. It is run by `observability.self_health()`
as a live probe on each health read — the chain's integrity is
re-proven, not remembered (SC4 applied to ourselves).

## Provenance

Every record persists `deployment_sha` and `policy_version` (NOT NULL):
evidence is traceable to the exact code and constitution version that
wrote it. Every incident transition also writes an evidence record, so
the incident history and the hash chain corroborate each other.
