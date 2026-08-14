# Founding Member Challenge

The first campaign running on Progress OS. It asks a member to bring thirty
people to PulseSoc who actually stay, and it pays for that — repeatedly.

## The ladder

| Qualified referrals | Milestone | What it unlocks | Repeats? |
|---|---|---|---|
| 5 | Early Supporter | Recognition badge | No |
| 10 | Creator Profile Perk | `premium.profile.customization` entitlement | No |
| 20 | Priority Creator Standing | Priority standing toward Live | No |
| 30 | Founding Member | Live Creator eligibility, permanent badge, first $30 | Badge + Live: no |
| every further 30 | — | Another $30 | Yes, forever |

Definitions live in `services/business_os/progress/campaign.py` as
`FOUNDING_MEMBER_CHALLENGE_V1`.

### Why 20 is "priority standing" and not partial Live

`services/privilege_engine.py` models Live access as a single boolean. There is
no honest way to grant half of it. Rather than invent a second Live tier the
media stack knows nothing about, 20 is recognition that support and admin can
act on, and 30 is the real unlock. Advertising 20 as partial Live access would
be a promise no gate could keep.

## What "qualified" means

A referral is worth zero until **all** of these are true, and each is read from
a server-owned source at evaluation time:

1. The person signed up through the referrer's link (`users.referred_by` /
   `referral_conversions` — attribution is unchanged and still canonical).
2. They completed their profile (`users.onboarding_complete` — the existing
   onboarding definition, not a second one invented here).
3. They posted on **two separate UTC calendar days** (`pulse_posts`, reposts
   excluded).
4. Their account is in good standing (`users.account_status`).
5. No unresolved risk hold (see `referral_fraud_controls.md`).

Signups are not progress. The full state machine is in
`referral_qualification.md`.

## The repeatable reward

`Campaign.cycles_earned` is integer division and nothing else: 29 → 0, 30 → 1,
59 → 1, 60 → 2. The engine then pays cycles 1..n exactly once each, so the
arithmetic can never by itself cause a double payment. Cash lands `pending` in
the canonical rewards engine — Progress OS does not move money. See
`reward_cycles.md`.

Milestones are one-time per user per campaign, enforced by a UNIQUE index. Only
the cash repeats.

## Campaign versioning

A campaign is the rules of the game, and the rules are a promise. Someone who
started under a 30-referral target must not silently wake up needing 40.
Changing the rules means publishing a **new version**, not editing the old one.
Every qualification, milestone award and reward cycle records the
`campaign_version` it was decided under, so a historical decision stays
explainable after the rules move on.

What a campaign may **not** configure: that posting days are distinct, that a
referred user has exactly one referrer, or that a reward cycle pays once. Those
live in the engine, because they are what makes the numbers mean anything.

## The pre-existing defect this closes

Before Progress OS, `record_referral_signup` wrote `referral_conversions` with
`counted=1` **at signup time**. `pulse_referral_status_for_user` counted exactly
those rows, and `privilege_engine.get_user_privileges` unlocked Live at
`referral_count >= 30`. Composed: **thirty bare signups unlocked Live Creator**
— no profile, no post, no person.

`services/business_os/progress/bridge.py::qualified_referral_count` replaces
that arithmetic at its source. Attribution stays canonical; only the decision
about whether an attributed signup *counts* moved.

Nobody loses access. `grandfather_legacy_live_access` runs once per user,
lazily: a user with no explicit `livestream_access` row and ≥30 legacy counted
signups gets a real row stamped `approved_by=0` with a legacy reason. That is
strictly more honest than the status quo — it converts a privilege that was
being silently recomputed from a number into an explicit, dated grant — and it
means the new rule governs only new progress. A user who already has any access
row, including a *suspended* one, is left completely alone.

If Progress OS is unavailable, `qualified_referral_count` returns 0 rather than
falling back to the legacy count: a fallback would quietly reopen the hole
precisely when the system is least healthy. Existing creators are unaffected,
because their access lives in the explicit row, not in the count.
