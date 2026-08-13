# Sentinel External Integrations (Stages 12–13, 26–27)

## Provider health (Stage 12)

Module: `services/sentinel/providers.py`. Table:
`sentinel_provider_capabilities`.

Model: provider → capability → status. Statuses: `up`, `degraded`,
`down`, `unknown`. An **unrecorded capability is `unknown`, never `up`**
— absence of bad news is not good news (SC15). Known capabilities cover
stripe (checkout/webhooks/payouts/refunds), rtc_media (rooms/egress —
neutral key; sentinel never names the protected audio stack, SC13), mux,
brevo, r2, fcm, apns.

`record_status` validates status values and upserts; `health_table`
returns the current matrix. V1 records health passively — no active
probing of vendors.

## Circuit-breaker contract (Stage 13)

`providers.CircuitBreaker` is a **contract, not an enforcement layer**:
closed → open after `failure_threshold` failures → half_open after
`recovery_timeout_seconds` → closed on success. Constructor rejects
non-positive thresholds (SC14). V1 wires it into nothing; it exists so
future adapter I/O has one blessed pattern instead of five ad-hoc ones.

## Adapter contract (Stage 26)

Module: `services/sentinel/adapters.py`.

An `AdapterSpec` declares vendor, purpose, and the exact signal types it
may emit. `normalize_signal` enforces:

- undeclared signal type → ValueError (SC15)
- severity from outside is **capped at `medium`** no matter what the
  vendor claims (SC9)
- `payload.verified = False` always — external claims are unverified
  until platform-internal corroboration
- doctrine string `signal_is_not_guilt` travels with the event

External data can *inform* correlation; it can never convict on its own
(SC8 requires multiple signals anyway).

## Outbound sharing = MINIMIZE (Stage 27)

`adapters.outbound_filter` is the single choke point for data leaving the
platform. Policy: allowlist by classification — only fields at INTERNAL
or below pass (`classification.external_share_allowed`), and unknown
fields are dropped entirely. Emails, phones, pulse_ids, passwords, and
anything unclassified never leave. There is no "share everything with
vendor X" mode and none should be added without a constitution change
(SC7).
