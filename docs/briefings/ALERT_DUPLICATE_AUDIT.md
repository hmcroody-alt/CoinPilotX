# Crypto Alert Duplicate Audit

Audit of the `crypto_alert_triggered` backlog before any cleanup. Run against
production Postgres on 2026-08-31.

## Verdict

**The loop is dead, the fix is confirmed working in production, and nothing
should be deleted.** The recommended remediation is a reversible
mark-as-read scoped to loop-era rows, not a delete.

## Scale and split

4,990 `crypto_alert_triggered` rows across 3 users. The 2026-07-28 boundary
splits them cleanly:

| Era | Rows | Days | Range |
|---|---:|---:|---|
| Loop era (`< 2026-07-28`) | 4,798 | 27 | 2026-07-01 → 2026-07-27 |
| Post-loop (`>= 2026-07-28`) | 192 | 12 | 2026-08-02 → 2026-08-31 |

97% of the backlog predates the fix. Per user, loop era:

| User | Rows | Unread |
|---:|---:|---:|
| 1 (owner) | 4,780 | 4,776 |
| 19 | 18 | 18 |
| 34 | 0 | 0 |

## The loop has a signature

Daily volume for user 1 sat on a flat plateau of **95–96 rows/day** for most of
July — exactly 4 per hour, the alert worker's 15-minute cadence. That is the
fingerprint of a rule that never latched: every sweep re-fired it. The two
opening days were worse still (1,167 and 1,399) before settling onto the
worker's rhythm.

The plateau ends at 2026-07-27 and never returns.

## The fix is verified end-to-end in production

Post-loop, `pulse_notifications` rows and `alert_events` rows match **exactly
1:1 on every single day**:

```
2026-08-02   1/1     2026-08-24  56/56     2026-08-28  12/12
2026-08-04   1/1     2026-08-25  14/14     2026-08-30   2/2
2026-08-06   2/2     2026-08-26  10/10     2026-08-31  21/21
2026-08-22   6/6     2026-08-27  21/21
```

Twelve days, twelve exact matches. One evaluation → one event → one
notification. Deterministic trigger identity holds: among rows carrying a
`trigger_key`, **every key is distinct** — there is not one duplicated key in
production.

> A false alarm worth recording: `alert_events` reports 64,806 rows against
> only 191 distinct `trigger_key` values, which looks catastrophic. It is an
> artifact — 64,615 of those rows have `trigger_key IS NULL` (they predate the
> identity work) and `COUNT(DISTINCT)` skips NULLs. The 191 keyed rows are 191
> distinct keys.

## Was the user actually spammed?

**No.** This is the single most important finding for sizing the remediation.

Loop-era push delivery outcomes, all channels, all notification types:

| Status | Rows |
|---|---:|
| `not_configured` | 44,139 |
| `queued` | 2,567 |
| `sent` | **14** |
| `failed` | 10 |
| `skipped` | 3 |

Fourteen pushes were sent in the entire loop era, across every notification
type in the system. Email and SMS were `not_configured` too (44,505 / 3,297).
The blast was contained to in-app rows because no outbound channel was
configured at the time — the users' phones stayed quiet. The damage is a
**badge and a feed**, not a notification storm.

## Actual user-visible harm

For the owner:

- 7,440 total unread notifications
- 4,915 of them `crypto_alert_triggered`
- **4,776 are dead loop-era rows — 64% of the entire unread badge**

That is the harm worth fixing: an unread counter dominated by noise, which
makes the badge useless as a signal.

## Briefings are not contaminated

The newest loop-era row is **2026-07-27**, five weeks old. Every briefing
window is rolling (6h / 24h / 30d), so loop-era rows fall outside all of them
and cannot inflate any current briefing.

Independently, briefings no longer count raw notifications at all — they read
`alert_events` grouped into latch episodes (see the sibling change). Even if a
loop recurred, one rule firing 21 times would score once, not 21 times.

## Recommended remediation

**Do not delete.** These rows are the evidence that the loop existed and the
only record of its shape; a future regression is diagnosed by comparing
against them.

Proposed instead:

1. **Mark loop-era rows read.** `UPDATE pulse_notifications SET is_read=1`
   where `type='crypto_alert_triggered' AND created_at < '2026-07-28' AND
   COALESCE(is_read,0)=0`. Scope: **4,794 rows across users 1 and 19.** This
   restores the badge, preserves every row, and is trivially reversible
   because the affected id set is recorded before the write.
2. **Leave the 192 post-loop rows untouched.** They are correct, they are 1:1
   with real events, and they are what the user should still see.
3. **Snapshot before writing** — dump the affected ids and their prior
   `is_read` value to a file so the operation can be undone exactly.

This is a presentation-layer repair. It changes what the user sees, not what
happened.

## Not done here

Deletion of any row; any change to `alert_events`; any backfill of the 64,615
NULL `trigger_key` rows (they predate the identity scheme and inventing keys
for them would be guesswork, which the mission explicitly forbids).
