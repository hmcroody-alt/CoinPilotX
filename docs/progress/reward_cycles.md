# Reward cycles

`services/business_os/progress/milestones.py` turns a qualified count into
milestone awards and cash cycles. It does not move money.

## Two independent locks against double payment

Thirty qualified referrals must produce exactly one $30, no matter how many
times a hook fires, how many requests race, or how many times a reconciliation
sweep runs.

1. **`progress_reward_cycles` is UNIQUE on `(campaign_id, user_id,
   cycle_index)`.** Cycle 1 exists once, cycle 2 exists once.
2. **The rewards engine is UNIQUE on `reward_events.event_key`.** The key is
   deterministic — `reward_event_key(campaign_id, user_id, cycle_index)` — so a
   replay computes the same key and the database rejects it.

A replay has to defeat both locks in two different tables to pay twice. Neither
lock is application logic that a concurrent request can interleave past.

## The cycle arithmetic

`Campaign.cycles_earned(qualified_count)` is `n // reward_interval` and nothing
more: 29 → 0, 30 → 1, 59 → 1, 60 → 2. `sync_reward_cycles` then writes cycles
1..n, each exactly once. Because the write is idempotent per cycle index, the
arithmetic alone can never cause an overpayment even if it is called on every
request.

Progress toward the *next* reward (`next_cycle_progress`) is `n %
reward_interval` — it resets after each cycle. The ladder ends at 30; the cycle
does not.

## Money is canonical

Cash is granted through `services/business_os/rewards/engine.py::grant_reward`.
Progress OS supplies an event key, a user, an amount and evidence; the rewards
engine owns state, currency and payout. Cash always lands `pending` — that is
the normal resting state, not an error, and the UI must not style it as one.

`reward_summary` reads live status back from the rewards engine per event key
rather than trusting its own cached `status` column, so a hold or release
applied in the rewards console shows up in Progress without a second sync.

There is no second ledger, and Progress OS has no payout path of its own.

## Milestones

`progress_milestone_awards` is UNIQUE on `(campaign_id, user_id,
milestone_key)`: a milestone is awarded once, forever, per campaign.

Award kinds map onto existing canonical grant paths — a badge write, or an
entitlement grant — and never invent a new benefit surface:

* `recognition` — badge only
* `creator_perk` — badge + entitlement (`premium.profile.customization`)
* `live_priority` — recognition standing; not a Live grant
* `live_access` — the actual Live Creator unlock at 30

Revocation is soft (`revoked_at`, `revoked_reason`), so the evidence of what
happened survives the withdrawal. See `admin_review.md`.

## Client authority: none

The app never computes a qualified count, a reward amount, Live eligibility or
badge eligibility. `mobile-native/src/api/progress.ts` performs exactly two
arithmetic operations: dividing cents by 100 for the currency formatter, and
clamping a server percent into a bar width. A client that could add up referrals
would eventually disagree with the server, and the member would believe the
number that is wrong — the one on their own screen.
