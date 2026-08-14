# Sentinel Mission 5 — Financial Account Takeover (FAT) Detection

Module: `services/sentinel/financial_sequences.py`. Detects the *sequence*
that turns an identity compromise into a financial drain — because ATO is a
chain, not a single event.

## FAT sequence chains

FAT chains correlate Mission 3 identity events with Mission 5 financial
events by subject: the sequences engine groups on subject_id, so identity
events for user `901` correlate with financial events for `SELLER:901`.

Representative chains:

- **FAT1 (suspected drain setup)**: credential reset → new login →
  payout requested, inside the chain window. Fires
  FINANCIAL_ACCOUNT_TAKEOVER_SUSPECTED (idempotent — re-running detection
  does not duplicate the incident).
- **FAT2 (partial chain)**: identity compromise signals *without* the
  financial step yet. Fires with "PARTIAL" in the title — an early warning
  explicitly labeled incomplete, not a stronger accusation.

All chain outputs are SUSPECTED. A chain firing means "this ordering of
events deserves owner review", never "this account was taken over."

## False-positive posture (tested)

- A traveler logging in from an unusual device, alone, fires nothing —
  single identity signals without a financial follow-through are not FAT.
- A user who legitimately recovers their account and gets a new phone,
  without a payout request, produces zero incidents.
- Detection is gated by `SENTINEL_FINANCIAL_DETECTION_ENABLED`; switch off
  → empty results, no silent background judgment.

## What firing does NOT do

No session is revoked, no payout is held, no wallet is frozen. FAT
detection opens a SUSPECTED incident and stops. The capabilities to act do
not exist in the package (see financial_mutation_lock and
payout_security.md).
