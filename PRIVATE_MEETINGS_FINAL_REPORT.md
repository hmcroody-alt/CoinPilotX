# PRIVATE MEETINGS — FINAL REPORT

**Date:** 2026-09-16
**Mission:** FIX REAL SCHEDULING + INVITEES + CONFIRMATION EMAIL / NO FAKE SUCCESS
**Status:** Core defect fixed, verified on production's own engine, and deployed.
Two acceptance items remain open and are named in full below.

---

## 1. The defect, and the proof it was real

A meeting was scheduled on a physical device. The API answered `201` with a
complete meeting object. The wizard advanced. **No row was ever written.**

That is not an inference. Production's own sequences kept the record:

| Evidence | Value |
| --- | --- |
| `private_meetings` rows | 3 |
| `private_meetings_id_seq.last_value` | 5 |
| **Conclusion** | **ids 3 and 4 were issued to rows that no longer exist** |

Two bookings were written and then erased. The sequence cannot be rolled back by
a transaction abort, so it is the one witness that survives one.

### Mechanism

PostgreSQL aborts an entire transaction when a single statement fails. SQLite
does not. `services/db.py` therefore rolls the *connection* back to keep it
usable — correct when nobody has claimed recovery, catastrophic here.

The original trigger was one column: `_recipient_email` asked `users` for
`user_id=? OR id=?`. `users` is keyed on `user_id` and **has no `id`**. On
PostgreSQL an unknown column is a hard error before a single row is read. The
`except` around it turned that into "no address" and carried on — but the
statement had already poisoned the transaction. The meeting, its host
participant, its audit row and its three reminders were already gone.

`create_meeting` then built its response **from the in-memory dict it still
held**, and returned it. That is the whole shape of the failure: the payload
never touched the database, so it could not know the database was empty.

### Why 7,000 passing tests never saw it

The suite's own fixture had invented a `users.id` column. Every test therefore
had the column production lacked, and the broken `OR id=?` lookup found what it
asked for, every time.

---

## 2. What was fixed

### 2.1 The original lookup
Corrected to key on `user_id` only, with a test asserting the fixture cannot
declare a `users.id` again (`test_no_fixture_in_this_directory_keys_users_on_an_invented_id`).

### 2.2 Containment: `_nonfatal`
A savepoint context manager. `db.py` deliberately does not roll the connection
back while a savepoint is open, leaving recovery to whoever opened it — so
damage is bounded to the block and the meeting survives.

It includes a **health probe** (`SELECT 1`), which is the part that is easy to
omit: these blocks catch their own errors several frames down, so an exception
reaching the context manager is the *uncommon* case. A clean exit proves
nothing. Asking the connection directly is the only way to find a failure that
was already absorbed.

### 2.3 Three further instances, found by running on real PostgreSQL

Verifying against a throwaway copy of production's PostgreSQL 18.6 — the first
time this code had ever run on anything but SQLite — exposed three more blocks
with the identical shape:

| Site | Why it mattered |
| --- | --- |
| `_audit` → `audit.record` | `audit.record` catches its own exception and returns `False`, which *reads* as best-effort. A record **about** a booking could destroy the booking. |
| `_blocked` → `blocked_users` probe | Its whole premise is that the table may be absent — the one case where the swallow most needed to work. |
| `_resolve_member_by_email` → `users` | The one the harness actually hit. It raised **"Meeting not found"** about a meeting inserted a few statements earlier. |

Only the third was reachable in that run. The other two were one schema
difference away from costing somebody the same booking.

### 2.4 The guard is structural, not behavioural

Because only one of three was reachable, the regression test asserts the
**shape** rather than hunting instances: an AST pass over the whole module
requiring that no `try/except` which swallows a database failure sits outside a
savepoint.

- `_nonfatal` is exempt — it is built from the pattern it exists to make safe.
- `sweep_meetings` is exempt — it catches `PrivateMeetingRejected`, which
  application logic raises over a *healthy* connection.

**This had to be structural.** SQLite does not poison a transaction, so all
three blocks behave impeccably under the suite whether savepointed or not. A
behavioural test would have stayed green through the entire defect.

**Negative control performed.** One call site was restored to its unsavepointed
form; the test failed and named the exact function and line
(`_resolve_member_by_email() line 2115`). The file was then restored and
verified byte-identical.

---

## 3. Verification on production's own engine

A scratch database `pm_verify_tmp` on the **same PostgreSQL 18.6 server** as
production, loaded with production's exact pre-migration table shapes and wound
back to the shape production had at deploy time. Guarded by a
`current_database() == 'pm_verify_tmp'` assertion that refuses to run otherwise.
Production's `railway` database was never written to.

**27 of 27 checks passed.**

| § | Check | Result |
| --- | --- | --- |
| 1 | Migration runs against production's real shape; `invitee_key`/`invitee_email`/`invitee_name` added; old `UNIQUE(meeting_id, invitee_user_id)` dropped; identity index created; a pre-existing member invite backfilled onto `u:7703` | PASS |
| 2 | Meeting **row** persisted — title, start, timezone, duration, agenda read back from the table, not the payload | PASS |
| 3 | **Two** outside guests survive the old constraint; addresses normalised to lowercase; guests **named, not counted**; distinct identity keys | PASS |
| 4 | Reminders are **rows**, offsets `[1440, 60, 15]`, all `PENDING` | PASS |
| 5 | Double-tap with one idempotency key yields **one** meeting | PASS |
| 6 | With `private_audit_events` **dropped**, the booking, its invitee and all three reminders **survive** | PASS |
| 7 | `last_value == count(*)` — no sequence gap | PASS |

§7 is the direct inverse of the fingerprint production is still carrying.
§6 is the one that would have failed before this work: it previously raised
`PrivateMeetingRejected: Meeting not found.`

Scratch database dropped afterwards; nothing left on the server.

---

## 4. Deployment

| Item | Value |
| --- | --- |
| Fix commit | `551a5297` — *bookkeeping must not be able to cancel the booking* |
| Verified running | all **12** Railway services, status SUCCESS |
| Liveness | `https://pulsesoc.com/` → **200** |
| Route registered | `/api/private-office/meetings` → **401, not 404** |

The 401 matters specifically: optional route packs register inside
`except Exception` blocks, so a 404 would have meant the subsystem silently
vanished at boot. It is registered and auth-gated.

`origin/main` has since advanced to `8d36b018` (unrelated pulse work from a
parallel session); the fix is confirmed still present there.

---

## 5. Repository repair

Local `main` was a **stale parallel lineage**, not an advanced one. `git cherry`
marked 11 of its 15 commits as already upstream by patch-id, and the content
diff settled it:

```
origin/main → main:  368 insertions, 9067 deletions
```

Pushing it would have reverted `services/pulsesoc_voip_push.py` (781 lines),
`tests/test_voip_pushkit_delivery.py` (900), `tests/private_office/test_private_contacts.py`
(808), `services/private_office/relationships.py` (787) — **and this mission's
own fix**, which appeared 0× in local `main` and 1× upstream. It would also have
required a force push, since `origin/main` was not an ancestor.

**Repair performed, preserving everything:**

1. Another session's uncommitted edits (ProfileHeader / profileNeon, 297 lines)
   copied verbatim to `/tmp/other-session-wip/` before anything was touched.
2. Backup branch `backup/main-4a217dd5-preff` created; all four genuinely
   divergent commits confirmed reachable from it.
3. `git reset --keep` used throughout — it *refuses* rather than clobbers. It
   refused twice, correctly, until each obstacle was preserved and cleared.
   `--hard` was never used.
4. Result: `main` == `origin/main` == `8d36b018`, working tree clean, and the
   previously-missing files confirmed present.
5. The other session's WIP turned out to be **already committed upstream** — the
   restored tree is byte-identical to what they were editing. Nothing lost.

Post-repair: **161 tests green** across the four meetings suites.

---

## 6. What is NOT done

Stated plainly rather than rounded up.

| Item | Status |
| --- | --- |
| **Host confirmation email actually delivered** | **Not proven end-to-end.** Reminder rows and the queueing path are proven; confirming real delivery means sending mail on your behalf, which needs your explicit go-ahead. |
| **Physical-device acceptance** | **Not done.** The simulator account (@PulseSocMusic) has no active membership and all Private Office data belongs to `user_id 1`. I did not purchase a membership. A P3r7or build is the remaining path. |
| **The two lost production meetings** | **Not recoverable.** Ids 3 and 4 were rolled back; no row, no payload, no log of their contents survives. |
| **Pre-existing production rows** | The 3 surviving meetings have empty titles and the one scheduled meeting has an empty timezone — written before this fix. Not repaired; no source of truth to repair them from. |
| **Systemic `audit.record` exposure** | ~19 other Private Office modules call `audit.record` directly with the same false best-effort promise (~70 call sites). Fixing it *inside* `audit.py` would close all of them at once. Deliberately scoped out of this mission; a `claude/audit-savepoint-systemic` branch exists. |

---

## 7. The line this mission was given

> A MEETING IS NOT "SCHEDULED" BECAUSE THE UI ADVANCED TO THE NEXT SCREEN.

Every check in §3 reads the **row**, never the response payload — because the
payload was the thing that lied. And §6, the booking made with the audit table
dropped, is the one that proves the principle generalises: a failure in
something that merely *describes* a meeting can no longer delete it.
