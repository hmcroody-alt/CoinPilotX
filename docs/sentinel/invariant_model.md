# Sentinel Invariant Model (Stage 11)

Module: `services/sentinel/invariants.py`. Read-only by construction (SC6).

## Doctrine

An invariant is a statement about platform state that must always hold.
Sentinel **checks** invariants; it never repairs them. A violated invariant
produces (1) a `SENTINEL_SELF/invariant_violation` event and (2) an
idempotent `INVARIANT_VIOLATION` incident. The offending row is left
byte-for-byte untouched — the regression suite asserts a seeded negative
balance is still negative after `run_all`.

## Shipped invariants (V1)

| ID | Statement | Source tables |
|----|-----------|---------------|
| `INV_LEDGER_BALANCED` | Creator ledger debits equal credits | `creator_ledger_entries` |
| `INV_AD_WALLET_NON_NEGATIVE` | No ad wallet balance below zero | `pulse_ad_wallets` |
| `INV_PAYOUT_NON_NEGATIVE` | No negative payout amounts | `seller_payouts` |
| `INV_EVIDENCE_CHAIN` | Sentinel's own hash chain verifies end-to-end | `sentinel_evidence` |

## Three-valued results

Each check returns `OK`, `VIOLATED`, or `SKIPPED`:

- `SKIPPED` when a source table doesn't exist (fresh install, local SQLite,
  partial deployments). Missing data is *absence of evidence*, not a
  violation — invariants must never manufacture incidents out of
  environment differences (SC15 applied honestly).
- `VIOLATED` opens the incident with a key derived from the invariant id
  and day bucket, so repeated runs don't spam duplicate incidents.

## Extension rules

New invariants must: read only (SELECT), return the three-valued status,
tolerate missing tables as SKIPPED, and never join user PII into their
detail payloads (SC7). Financial invariants are the priority — the point
of this engine is catching money-state corruption early, not enforcing
style.
