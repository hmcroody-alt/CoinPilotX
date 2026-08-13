# Sentinel Independent Verification (Stage 16)

Module: `services/sentinel/verification.py`.

## Doctrine: no self-declared success (SC4)

An executor claiming "it worked" is evidence of nothing. Sentinel encodes
this structurally:

- `runbooks.execute` success status is `EXECUTED_UNVERIFIED` — the word
  UNVERIFIED is in the status on purpose; dashboards and reports must not
  present it as done.
- `verification.verify_execution(execution_id, verifier_id, …)` refuses to
  run when `verifier_id == executor_id` (`VerificationError`). Identity
  separation is checked against the persisted execution row, not caller
  claims.
- The spec's `verifier` callable re-derives the outcome from
  `(params, result)`. If the executor lied (returned success without doing
  the work), verification lands `VERIFICATION_FAILED` — regression-tested
  with a deliberately lying executor.

## State flow

```
RUNNING → EXECUTED_UNVERIFIED → COMPLETED          (verifier passed)
                              → VERIFICATION_FAILED (verifier failed)
        → FAILED               (executor raised)
```

Only `EXECUTED_UNVERIFIED` rows are verifiable; verifying anything else is
an error (SC15). Every verification outcome appends to the evidence chain
(SC5) with both identities recorded (SC12).

## The same principle elsewhere

- Incident recovery: `RECOVERY_VERIFIED` requires `verified_by` ≠ the
  transition actor (incidents.py).
- Self-health: `observability.self_health` runs independent probes rather
  than reporting cached success flags.
- External signals: adapters mark everything `verified: False` until a
  platform-internal check corroborates (SC9).

Verifier actors are ordinary registered service identities (e.g.
`sentinel.verifier`) with no elevated authority — verification is a
*different* pair of eyes, not a *privileged* one.
