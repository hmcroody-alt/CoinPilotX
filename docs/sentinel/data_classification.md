# Sentinel Data Classification (Stage 3)

Module: `services/sentinel/classification.py`.

## The 5 levels

| Level | Value | Examples |
|-------|-------|----------|
| PUBLIC | 0 | public profile name, public post id |
| INTERNAL | 1 | internal ids, provider names, event counts |
| CONFIDENTIAL | 2 | device fingerprints, IPs, session metadata |
| SENSITIVE | 3 | email, phone, pulse_id, financial amounts per user |
| HIGHLY_RESTRICTED | 4 | passwords, tokens, keys — must never be stored at all |

`classify_field(name)` maps field names to levels; unknown fields default
to SENSITIVE (fail closed, SC15). Redacted values are replaced by the
marker `[REDACTED:sentinel]`.

## Ceilings in force

| Surface | Ceiling | Why |
|---------|---------|-----|
| `events.ingest` payloads | CONFIDENTIAL | secrets/PII never reach `sentinel_events` (SC7) |
| `undx_interface.read` | INTERNAL | the model sees less than the database holds (SC2+SC7) |
| `adapters.outbound_filter` | INTERNAL, allowlist-based | external sharing is MINIMIZE (Stage 27) |
| evidence chain bodies | inherit event redaction | chain is immutable, so pre-redaction is the only redaction (SC5) |

## MINIMIZE (external sharing)

`external_share_allowed(field)` permits only fields at INTERNAL or below,
and `outbound_filter` additionally drops unknown fields entirely — the
default answer to "can we share this?" is no. Emails, passwords,
pulse_ids, and unrecognized fields are all stripped before anything leaves
the platform (regression-tested).

## Order of operations matters

Redaction happens **before** persistence, not at read time. A read-time
redactor can be bypassed by a new read path; a write-time redactor means
the sensitive value simply does not exist downstream. `undx_interface`
then applies a *second*, stricter pass at read time — defense in depth.
