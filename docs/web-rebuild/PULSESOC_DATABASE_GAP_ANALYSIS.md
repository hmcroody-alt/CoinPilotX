# PulseSoc — Database Gap Analysis

**Question:** what must change in the database before a web client can be built against it —
and what must explicitly *not* change?

**Answer: almost nothing structural, and seven specific things.** The database is shared, it
already serves the browser today, and no new database may be created. The gaps are (a) a small
set of missing indexes, (b) a set of **decisions** about duplicate table lineages, and (c) one
data-integrity sentinel that will silently delete content if handled naively.

Verified against production Postgres 18.6 by introspection, 2026-09-12. Full catalog in
`PULSESOC_DATABASE_INVENTORY.md`.

---

## 0. Ground truth

| Metric | Value |
|---|---:|
| Base tables (`public`) | **884** |
| Columns | 9,458 |
| Indexes | 2,045 |
| **FOREIGN KEY constraints** | **0** |

`CLAUDE.md`'s "~170 tables in `AUTO_PK_TABLES`" is wrong by 5×. `bot.py` has ~550
`CREATE TABLE` statements, so production also carries a large tail of tables no current code
creates.

**There is no migration framework.** Schema is created imperatively in `bot.init_db()`
(`_init_db_impl`, `bot.py:110179–119009`) and every change must be idempotent.

---

## 1. The one thing that is already solved

The most common assumption about this project — *"the web client will need a session store"* —
is false. It already has one.

`mobile_security_sessions` is, despite its name, **the shared web + native session store**:

| `platform` | sessions | | `device_label` | sessions |
|---|---:|---|---|---:|
| **`web`** | **7,446** | | `desktop-web` | 4,743 |
| `ios` | 2,686 | | `ios-app` | 1,111 |

The browser is already a first-class citizen: refresh rotation, `session_family_id`,
`reuse_detected_at`, `revoked_reason`, `last_risk_score` and per-platform labelling all work for
web today. **No new session infrastructure is required.**

Two traps around it:

- **`sessions` (226 rows) is not an auth table** despite the name. It holds `utm_source`,
  `referrer`, `landing_page` — anonymous visitor analytics. Do not wire auth to it.
- **The Flask cookie session is not server-revocable.** There is no `SESSION_TYPE` and no
  Flask-Session backend, so it is the default client-side signed cookie with no server-side row.
  A "sign out everywhere" button cannot actually kill a web cookie session today. This is a
  security gap, and it is in `PULSESOC_WEB_SECURITY_MODEL.md`, not here.

---

## 2. GAP A — Duplicate lineages: decisions, not migrations

**This is the largest risk in the entire rebuild, and it costs nothing to avoid.** Seven core
concepts each have 2–3 competing table families. Building the web client against the wrong one
is wasted effort that will not be discovered until integration.

| Concept | Competing families | Live data | **Decision** |
|---|---|---|---|
| **Messages** | `comm_v2_*` (18 tables) · `pulse_conversations`+`pulse_messages` · `conversations`+`conversation_members` | **`comm_v2_messages` = 1,099** vs `pulse_messages` = 29 | **Use `comm_v2_*`** — it holds the live data |
| **Notifications** | `notifications` (35 cols, 60 MB) · `pulse_notifications` · `notification_*` (10) · `pulse_notification_*` (3) | `notifications` = 62,279; `pulse_notifications` = 12,895 | **Unresolved — must be settled before the bell is built** |
| Status / stories | `pulse_status` · `pulse_statuses` · `pulse_stories` | `pulse_status` = 51; others ~empty | Use `pulse_status` |
| Friends | `pulse_friends` · `pulse_friendships` · `pulse_friend_requests` | both ~empty | Settle before building |
| Mutes | `pulse_user_mutes` · `pulse_muted_users` | both ~empty | Settle before building |
| Sellers / orders | `marketplace_*` (**integer** ids) · `business_os_mkt_*` (**text** ids) | — | See §5 — type mismatch |
| Rooms | `pulse_room_*` (text `room_id`) · `arena_room_*` (integer) | — | Arena is legacy |

> **Tell-tale of a dead lineage:** `pulse_messages` carries **8 indexes, two of them exact
> duplicates, for 29 rows.** Index count is a fossil of intent, not of use. Always check row
> counts before trusting that a well-indexed table is the live one.

**Action:** one decision record per row, written before the corresponding phase starts. No
schema change required — this is an architecture decision, and making it late is what costs
money.

---

## 3. GAP B — `user_id = 0`: the sentinel that will delete content

**81% of `pulse_posts` rows have `user_id = 0`** — 1,915 posts. There is no user with id 0.

This is a live sentinel value, and it creates a specific, silent failure:

> A web feed query written the obvious modern way — `INNER JOIN users ON posts.user_id = users.id`
> — **silently drops 81% of the feed.** No error, no warning. The page just looks empty-ish, and
> the cause is four layers away from the symptom.

The current code works because it does not join; it resolves authorship in Python.

**Options, in order of preference:**

1. **Insert a real system user with id 0** and keep the sentinel meaningful. Cheapest and
   safest. Caveat: an explicit-id `INSERT` into `users` must not desync `users_user_id_seq`.
2. Backfill the 1,915 posts to real authors, if the mapping is recoverable.
3. Do nothing, and mandate `LEFT JOIN` + a documented sentinel. Fragile — it relies on every
   future query author knowing.

**This must be resolved before the feed phase**, and it also blocks the `pulse_posts.user_id`
foreign key in §4.

---

## 4. GAP C — Zero foreign keys

884 tables, 9,458 columns, **0 foreign key constraints**. Every relationship — user→post,
post→comment, conversation→message, order→line-item — exists only as convention inside query
code.

**Today this is contained** because a single backend written by one team is the only writer.
**Introducing a second client that writes to the same tables is exactly the condition that ends
that containment.** There is no database-level backstop against orphaned or inconsistent rows.

**Do not add 884 tables' worth of constraints.** That is neither necessary nor safe. Add them
where the graph is already verified clean:

| Add FK on | Status |
|---|---|
| 11 core social-graph relationships (§3.3 of the inventory) | **Clean — 0 orphans verified.** Add and `VALIDATE` immediately |
| `pulse_posts.user_id → users.id` | **Blocked on §3.** Add after the sentinel decision |
| Everything else | Leave alone. Revisit per-area as each phase lands |

Risk is low: all target tables are small. Use `NOT VALID` + `VALIDATE CONSTRAINT` so the write
path is never blocked during deploy.

---

## 5. GAP D — Type mismatch that SQLite tests cannot catch

`marketplace_*` tables use **integer** ids. `business_os_mkt_*` tables use **text** ids.

Local development and the test suite run on **SQLite**, which is permissive about type affinity
and will happily compare an integer to a text id. **Production Postgres will not** — it raises.

> A web checkout path that crosses from `marketplace_*` into `business_os_mkt_*` can pass every
> local test and fail in production on the money path.

**Action:** assert on the **query binding**, not on the behaviour. A test that checks "the
checkout succeeded" passes under SQLite regardless; a test that checks the parameter type sent
to the driver catches it. This is a known trap in this repo, and it is on the highest-value path
in the rebuild.

---

## 6. GAP E — Missing indexes (the actual schema changes)

Four changes. All are `CREATE INDEX CONCURRENTLY`. None takes a long lock.

| # | Change | Why | Risk |
|---:|---|---|---|
| 1 | `UNIQUE INDEX CONCURRENTLY` on `lower(nullif(users.username, ''))` | **`/@username` is a sequential scan today.** No index, no UNIQUE. Every web profile page hit scans the table | **Low** — the `nullif` is what tolerates the 6 blank usernames. Not a partial predicate; see below |
| 2 | `UNIQUE INDEX CONCURRENTLY` on `lower(nullif(users.email, ''))` | Login is a seq scan | **Low** — same `nullif` form for the 3 blank emails |
| 3 | `UNIQUE` + index on `active_sessions.session_hash` | Table has **only a pkey index**. Session lookup is a seq scan and duplicate hashes are not prevented | **Low** — plain expression; a session hash is never legitimately blank |
| 4 | Drop the duplicate UNIQUE indexes on `pulse_saved_items` and the two identical indexes on `pulse_messages` | Pure write-amplification | **Low** |

Plus one maintenance job, not a schema change:

| 5 | **Session retention.** ~~9,728 of 10,132 `mobile_security_sessions` rows are `revoked`/`rotated` and are never deleted.~~ **Superseded — see §5a.** The count was right and the conclusion was wrong: this is a privacy job, not a deletion job. Built as `scripts/web_rebuild/phase0_session_sweep.py` |

### 5a. Why the session sweep is not a DELETE

The original entry read as an easy win — ten thousand rows, 96% of them dead,
nobody is using them. Three measurements against production say otherwise, and
each one inverts part of the premise.

**`rotated` is a live credential tier, not a dead one.**
`messenger_media_cookie_user_id()` (`bot.py:91458`) authenticates on
`status IN ('active','rotated') AND COALESCE(revoked_at,'')='' AND refresh_expires_at >= ?`.
Its docstring explains why: shipped handsets render message media carrying only
the persistent cookie, and refusing them breaks installed builds. All **1,723**
`rotated` rows have an empty `revoked_at` and a 2036 expiry, so all 1,723
currently authenticate — 451 of them last rotated 60–90 days ago. The genuinely
dead population is the 8,008 `revoked` rows.

**Deleting a dead row removes a security control, silently.**
`rotate_mobile_refresh_token()` (`bot.py:31288`) detects a replayed refresh
token by looking its hash up *with no status and no expiry filter*. Finding the
row is the entire mechanism — it is what triggers family revocation, the
`refresh_token_reuse` event and the user notification. Delete the row and the
lookup returns nothing, the function falls through to `return None, {}`, and a
replayed stolen token becomes a plain 401: no event, no revocation, no alert.
Housekeeping would have disarmed reuse detection with no failing test and no log
line.

**The obvious retention key is inert.** `MOBILE_REFRESH_TOKEN_TTL_SECONDS` is
`60*60*24*3650`. Keying on `refresh_expires_at` — which is what "TTL sweep"
suggests — deletes nothing until 2036. Retention has to key on *when the row
stopped being live*: `revoked_at` / `rotated_at` / `last_seen_at`. `created_at`
is not a valid fallback either; it is always populated, so including it means no
row is ever skipped for want of a date, and it answers the wrong question — a
session created 1,000 days ago may have been revoked this morning.

**And the stated motive was wrong.** The table is **12 MB**. Size is not the
problem. The problem is that **9,730 of 9,730 dead rows still carry a
`user_agent` and 9,729 still carry an `ip_hash`** — device and network
identifiers retained indefinitely for sessions that ended, some over two months
ago.

So the job is to **tombstone, not delete**: clear the seven payload columns
(`user_agent`, `ip_hash`, `device_hash`, `access_token_hash`, `metadata_json`,
`device_label`, `country`) and keep the evidence columns (`refresh_token_hash`,
`session_family_id`, `user_id`, the timestamps, `reuse_detected_at`). That beats
the original proposal on every axis: reuse detection keeps working *forever*
rather than being capped at a retention horizon, identifiers leave on a 30-day
clock instead of never, and roughly half the dead-row bytes are reclaimed anyway.

Measured on production, dry run: **5,454** rows past the 30-day horizon, 9,730 at
a zero-day horizon, 0 rows undated, 0 deletable on a first pass.

**One carve-out is load-bearing.** `mobile_refresh_reuse_grace_allowed()`
(`bot.py:31118`) forgives a benign refresh desync only when the stale row is
`rotated`, within `PULSESOC_REFRESH_REUSE_GRACE_SECONDS`, and matches on
`device_hash` **or** `ip_hash`. Tombstoning clears exactly those two columns.
Strip a row inside that window and a legitimate refresh is reclassified as
theft: the user is signed out everywhere and emailed a security warning. The
script reads the same environment variable the app reads and refuses to touch
anything newer than the window plus a margin.

`--delete` exists, is off by default, and only removes rows that were already
tombstoned on an *earlier* run — a row must be visible in one invocation's
output before a later one can remove it.

### 5b. The finding underneath: reuse detection is firing on itself

**47% of the table (4,786 rows) is `revoked_reason='refresh_token_reuse'`, and
those events belong to nine users.** One accounts for 2,259 of them across 35
families and 11 devices. Single families were revoked for reuse up to **19
times**. Nine users do not commit 4,786 token thefts, and a correctly-detected
family is revoked once and then dead — a family that re-triggers nineteen times
is a client that keeps coming back with a credential the server has already
condemned. The dominant bucket is `platform='web'` / `device_label='desktop-web'`
(2,226).

This is a false-positive loop in refresh rotation. It is the largest single
consumer of this table, it signs real users out and sends them "suspicious
session activity" alerts, and **the web rebuild adds exactly the second client
leg that drives it** — a cookie leg and a bearer leg rotating against one family.
It is out of scope for a retention job because fixing it changes authentication
behaviour, but it should be settled before the web client ships, not after.

> **The partial-index trap — measured, not theorised.** The textbook fix for blank
> values is `CREATE UNIQUE INDEX ... WHERE username <> ''`. Under a prepared
> statement that index works for exactly **five** executions and is then abandoned
> for a sequential scan, silently and forever: on the 6th execution Postgres
> switches to a **generic plan**, and a generic plan cannot prove `$1 <> ''`
> against a parameter it has not yet seen, so the partial index is no longer
> provably applicable. Every web request goes through a prepared statement.
> `/@username` would be indexed for five hits per statement and seq-scan every hit
> after that, with no error and no log line. Moving the `nullif` **into the indexed
> expression** removes the predicate entirely, so there is nothing left for the
> planner to fail to discharge. `scripts/web_rebuild/phase0_indexes.py` asserts
> this by `EXPLAIN EXECUTE`-ing each index seven times and failing if the index
> name drops out of the plan.

> **The invalid-index trap.** `CREATE INDEX CONCURRENTLY` cannot run inside a
> transaction, and when it fails it leaves behind a corpse with
> `pg_index.indisvalid = false`. That corpse satisfies `IF NOT EXISTS`, so every
> subsequent run skips the build and the index is never created — and an invalid
> index is never used by the planner either. The script checks `indisvalid`, drops
> the corpse, and rebuilds, rather than trusting `IF NOT EXISTS`.

Both are encoded in `scripts/web_rebuild/phase0_indexes.py`, which is **dry-run by
default** (`--apply` to execute) and was verified end to end against a throwaway
Postgres 18.6 container loaded with a fixture matching production exactly — 39
users, 6 blank usernames, 3 blank emails, the same duplicate-index pairs.

---

## 7. GAP F — Shapes that will not survive a web client

Not blocking, but they will surface quickly under browser traffic patterns.

| Issue | Where | Consequence |
|---|---|---|
| **`OFFSET` pagination** + computed `ORDER BY` | feed | Degrades with depth. Browsers deep-paginate far more than a phone. Move to keyset pagination |
| **`media_ids_json` is TEXT** | `pulse_posts` | There is no join to write. The web client must replicate the Python-side resolution at `services/pulse_feed_engine.py:589` — or, better, the API should return resolved media and the web client should never see the column |
| **`users` is a 106-column god table** | profile | A web profile page over-fetches badly. Needs a projection at the API layer, not a schema change |
| **`entity_id` is TEXT and polymorphic** | notifications | Deep links must be resolved in Python. Affects the notification→URL mapping directly |
| Polymorphic `content_type`/`content_id` TEXT | `pulse_saved_items` | Each saved item's target resolves per-type in Python |

**The pattern across all five: resolve at the API layer, not in the web client.** Every one of
these is a reason the web client must consume the same JSON the native app consumes rather than
querying closer to the metal. That is an architectural guarantee worth stating explicitly — it
is in `PULSESOC_WEB_TARGET_ARCHITECTURE.md`.

---

## 8. The cheapest surface

`user_settings` is a key/value table with `UNIQUE (user_id, setting_key)`. **Web-only
preferences need no schema change at all** — new keys, no DDL. Same for
`notification_preferences`, `privacy_preferences`, `pulse_region_preferences`,
`pulse_translation_preferences`.

---

## 9. Summary: what actually has to happen

| Type | Count | When |
|---|---:|---|
| **Decisions** (duplicate lineages) | 7 | **Before the corresponding phase** — zero cost now, expensive later |
| Index additions | 3 | Phase 0 |
| Index removals | 2 | Phase 0 |
| Data decision (`user_id = 0`) | 1 | Before the feed phase |
| FK additions (core social graph) | 11 | Incremental, `NOT VALID` + `VALIDATE` |
| Maintenance job (session TTL) | 1 | Before launch |
| **New tables** | **0** | — |
| **New databases** | **0** | Explicitly forbidden |
| **Destructive migrations** | **0** | Explicitly forbidden |

---

## 10. Methodology constraints

- **Never run DDL against production to test it.** Verify Postgres-only changes on a throwaway
  Docker Postgres 18 first — SQLite will not reproduce type or constraint behaviour (§5).
- **Everything must be idempotent.** There is no migration framework; `init_db()` re-runs.
- **`CONCURRENTLY` for every index** on a table with live traffic.
- **`NOT VALID` then `VALIDATE CONSTRAINT`** for every FK, so writes are never blocked on deploy.
- **`ensure_schema(conn)` can hang a route on Postgres.** Passing a route's connection skips the
  commit, so the DDL rolls back and a second connection then blocks on the uncommitted catalog
  lock. There are ~26 unswept call sites. Do not add more.

---

## Cross-references

- `PULSESOC_DATABASE_INVENTORY.md` — full 884-table catalog, index and constraint detail
- `PULSESOC_WEB_SECURITY_MODEL.md` — session revocability, the 0-FK posture
- `PULSESOC_WEB_REBUILD_PHASE_PLAN.md` — when each decision is due
