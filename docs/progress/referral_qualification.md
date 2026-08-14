# Referral qualification

`services/business_os/progress/qualification.py` decides what a referral is
worth. It replaces counting with earning.

## States

```
ATTRIBUTED → SIGNED_UP → PROFILE_COMPLETED → POSTED_DAY_1 → QUALIFIED
                                                   ↓
                                            REVIEW_REQUIRED  (reversible)
                                                   ↓
                                             DISQUALIFIED    (confirmed facts only)
```

`COUNTING_STATES = (QUALIFIED,)`. Everything else — including
`REVIEW_REQUIRED` — is worth exactly zero toward the ladder until it resolves.

`TERMINAL = (DISQUALIFIED, EXPIRED)`. The evaluator never walks a terminal state
back into progress on its own; only an admin `/restore` reopens it.

## Sources of truth

| Input | Read from | Notes |
|---|---|---|
| attribution | `users.referred_by`, `referral_conversions` | canonical, unchanged |
| profile complete | `users.onboarding_complete` | the existing definition |
| posting days | `pulse_posts.created_at` | UTC day buckets |
| standing | `users.account_status`, `access_enabled` | confirmed decisions only |

Nothing trusts a client claim. There is no code path by which a request body
sets a state.

## Posting days

A "posting day" is a distinct UTC calendar day on which the referred person
published qualifying content. Two rules do the work:

**UTC bucketing.** Using the viewer's local timezone would make the same two
posts qualify or not depending on who is looking, and would hand a user a
trivial way to manufacture a second day by changing device timezone around
midnight.

**Reposts excluded.** A repost is a single tap with no authored content, which
makes it the cheapest possible way to fake activity — a script can manufacture a
posting day for a hundred accounts in a minute. Original posts and reels count;
so does going live, because that is *harder* than a text post, not easier, and
excluding it would penalise creators who mainly broadcast.

`_QUALIFYING_POST_QUERIES` has a fallback for deployments predating the repost
columns. Degrading to "count everything" is the right failure direction: being
slightly too generous during a partial rollout is recoverable, whereas reporting
zero days would strip qualifications from people who earned them.

Days are recorded append-only in `progress_posting_days`, UNIQUE on
`(campaign_id, user_id, day_key)`. Consequences, both deliberate:

* Five posts on Monday are worth exactly one posting day.
* Deleting the post later does not retroactively strip a day that was genuinely
  earned.

## Standing

`_standing()` is deliberately narrow. Only states that are already *confirmed
decisions about the account* disqualify: deletion, and a suspension or ban an
operator or the safety system actually applied. Open reports, suspicion and risk
scores are not consulted here — they route to review.

## Idempotency

`evaluate(referred_user_id)` recomputes from source tables every time. It is
safe to call from any event — signup, profile save, post create, account status
change — as well as from the bounded reconciliation sweep. It never trusts its
own previous output, so a corrupted or hand-edited state row self-heals on the
next event.

`progress_referral_qualifications` is UNIQUE on `(campaign_id,
referred_user_id)`: one referred person can be claimed by exactly one referrer
per campaign. Two referrers racing to claim the same signup is resolved by the
database, not by whoever wrote last.

## Event hooks

`bridge.py` exposes the seam the monolith calls:

| Hook | Fired from |
|---|---|
| `on_referral_signup` | referral signup recording |
| `on_post_created` | `services/pulse_feed_engine.py` after a post is created |
| `on_profile_completed` | onboarding completion |
| `on_account_status_changed` | account status changes |

Each hook re-evaluates and then resyncs the referrer's milestones and reward
cycles. Hooks are best-effort; `admin/business-os/progress/reconcile` is the
bounded safety net for anything a hook missed.

## What the user sees

`checklist(referred_user_id)` returns stable keys plus an English fallback
label, never a Pulse ID and never a risk signal. The app localizes from the key.
The referral list identifies each referral by `ref` — an opaque, viewer-bound
token — so no internal identifier for a referred user ever reaches a client.
