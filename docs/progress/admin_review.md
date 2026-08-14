# Admin review

`services/business_os/progress/admin_api.py`, exposed under
`/admin/business-os/progress/*` in `bot.py`. Every route is `require_owner_api()`
and every mutation writes both a Progress audit row and `log_admin_audit`.

## Routes

| Method | Path | Purpose |
|---|---|---|
| GET | `/review-queue` | Referrals awaiting a human decision, oldest first |
| GET | `/referral/<referred_user_id>` | Full evidence for one referral |
| GET | `/referrer/<user_id>` | One referrer's counts, milestones, reward cycles |
| POST | `/referral/<id>/approve` | Clear a review hold |
| POST | `/referral/<id>/reject` | Record a confirmed abuse finding |
| POST | `/referral/<id>/restore` | Undo a rejection |
| POST | `/reward/<user_id>/<cycle_index>/hold` | Pause an earned reward |
| POST | `/reward/<user_id>/<cycle_index>/release` | Lift a hold back to pending |
| POST | `/milestone/<user_id>/revoke` | Withdraw a fraudulently obtained award |
| POST | `/reconcile` | Bounded sweep for anything the hooks missed |

A `reason` is required on every mutating call. `_require(actor, reason)` refuses
the action without one — an unexplained reversal of someone's earnings is not
an auditable decision.

## What approval is not

**Approve does not assert qualification.** It clears the review hold and then
re-runs `evaluate()`, which re-derives state from the source tables. If the
person still has not posted on a second day, approving does not make them
qualified. The admin's authority is over the *hold*, not over the facts.

Symmetrically, **release is not approve**. Releasing a held reward returns it to
`pending`; whether it is ever disbursed remains the rewards engine's decision in
the rewards console. Progress OS can pause money and un-pause it. It cannot pay
it and it cannot deny it.

## Reversibility

* `reject` → `restore`. Restore does not re-grant anything; it clears the
  terminal state and lets the state machine re-derive from current facts.
* `hold` → `release`.
* `revoke_milestone` is a **soft** revoke: `revoked_at` and `revoked_reason` are
  set and the row stays. The evidence of what was earned, when, and at what
  count survives the withdrawal — which is what makes an appeal answerable.

Reward holds mirror to the canonical rewards engine via
`set_fraud_state(reward_id, ...)` so the money system and Progress agree without
Progress owning the money.

## Reconciliation

Event hooks are best-effort by design: a lost hook must never fail the user
action that triggered it. `reconcile(limit=500)` is the bounded safety net —
re-evaluate stale qualifications, resync milestones and reward cycles. It is
idempotent (every write it performs is protected by a UNIQUE index) and bounded,
so it can be run freely without risk of a double grant or an unbounded scan.
