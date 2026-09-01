# Call Significance Decision

## Decision

**SCORED: `call_missed` / `missed_call` (weight 5) and `call_declined`
(weight 1).**
**NOT SCORED: `call_started`, `call_accepted`, `call_ended`, `incoming_call`.**
**DEFERRED: `call_expired`.**

Missed calls are counted once per call, deduplicated across two type spellings.
`SEND_THRESHOLD` is unchanged at 10.

## Why outcomes and not lifecycle

A briefing exists to tell someone what they missed. A call you started,
accepted, or ended is one you were present for — reporting it back is telling
the user what they already know. A missed call is the exact inverse: the one
call outcome the user was by definition not there for.

The volume evidence makes this more than a philosophical point. Lifetime
`pulse_notifications` counts:

| Type | Rows | Users | Scored |
|---|---:|---:|---|
| `call_ended` | 1420 | 11 | no |
| `call_started` | 634 | 11 | no |
| `call_accepted` | 436 | 6 | no |
| `incoming_call` | 374 | 12 | no |
| `call_declined` | 72 | 7 | **yes, weight 1** |
| `call_missed` | 46 | 6 | **yes, weight 5** |
| `missed_call` | 27 | 5 | **yes — same event as above** |
| `call_expired` | 13 | 6 | deferred |

The owner (user 1) alone accumulated 764 lifecycle rows in 30 days. Scoring
lifecycle would hand any active caller a permanently above-threshold score and
degrade the briefing into a fixed 6-hourly send — the precise failure this
work exists to prevent.

`incoming_call` is lifecycle for a second reason: it fires on ring, before the
outcome is known. Every missed call already produces a `call_missed` row, so
scoring `incoming_call` would count the same miss twice.

## The dual-write trap

Production writes **both** `call_missed` and `missed_call` for the same missed
call on the modern path. Users 4, 20, 21 and 36 hold exactly equal counts of
the two spellings, and the rows pair off to the same second. `missed_call` is
the older writer (first seen 2026-07-03, `Z` timestamps); `call_missed` is the
newer one (2026-07-06, `+00:00` timestamps). Both are live.

Routing both through the normal `GROUP BY type` bucket loop would have scored
every miss twice — silently halving a threshold chosen so that exactly two
misses send. One missed call would have triggered a briefing for anyone on the
dual-write path.

`_collect_missed_calls` therefore counts `COUNT(DISTINCT SUBSTR(created_at,
1, 19))` across both spellings. Collapsing on the second is exact rather than
approximate: `max(call_missed, missed_call)` would also fix the paired case,
but user 1 holds 20 and 4 — not a clean pairing — and two genuinely distinct
misses filed under different spellings must still count as two.

This is a workaround for a real upstream defect. **The two writers should be
unified to one type**, tracked separately; until then the dedupe is load-bearing
and its tests must not be relaxed.

## Weight sizing

Against the existing scale (`reaction` 1, `community_event` 2, `comment` 3,
`new_follower` 3, `friend_request` 5, `mention` 5, `unread_message` 8,
`marketplace_order` 10, `security_alert` 50) and `SEND_THRESHOLD` 10:

**`missed_call` = 5.** Deliberately below threshold. One missed call is worth
recording and not worth waking someone for; two are (2 × 5 = 10, exactly the
threshold). That places it level with `mention` and `friend_request` — the
"someone wanted you specifically" tier — which is the honest position for it.
This is the "low-to-moderate" band, not a trigger.

**`declined_call` = 1.** A tiebreaker, never a trigger. A decline is frequently
the user's own action replayed back at them, making it the weakest signal in
the table. The sizing is evidence-led: production's only observed decline burst
(2026-07-18, six declines each for five different users within a day, almost
certainly a system artifact rather than social activity) scores 6 and stays
correctly silent. At weight 2 the same artifact would have scored 12 and sent
five people a briefing about nothing. Declines still compose — six declines
plus one missed call is 11, which sends.

## Dedupe fingerprint

`missed_calls` joins `fact_fingerprint`'s `net_counts`; `declined_calls` does
not. Two misses can carry a briefing alone, so a window whose only change is a
new missed call must not hash identically to the previous one and get dropped
as a duplicate. A decline at weight 1 can never reach the threshold unaided, so
it has nothing to say that would justify re-sending.

## Measured impact

Simulated against production before merge:

- **6h window: zero users affected.** **24h window: zero users affected.**
  No briefing that would not already have been generated is manufactured by
  this change.
- **30d window: 3 users hold any missed/declined signal at all** — user 1
  (13 missed, 2 declined → 67), user 4 (12 missed, 2 declined → 62), user 36
  (1 missed, 0 declined → 5, correctly below threshold).

The single-miss user staying silent is the sizing working as designed.

## Deferred: `call_expired`

A ring timeout is arguably a missed call, and at 13 lifetime rows the volume
risk is negligible. It is left unscored anyway, for two reasons: it was not in
the agreed scope, and its semantics are unsettled — it is not established
whether the callee ever saw the ring, which is the whole basis for scoring
misses. Revisit as its own decision with its own evidence, not as a silent
widening of this one.

## Not changed

`SEND_THRESHOLD`, every pre-existing weight, dedupe behaviour for non-call
signals, quiet hours, and the delivery path.
