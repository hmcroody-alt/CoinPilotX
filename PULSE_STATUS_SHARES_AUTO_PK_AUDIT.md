# pulse_status_shares / AUTO_PK_TABLES — Stages 0–4 (analysis only)

**Read-only. No code changed. No commit. No verdict declared.**
Stages 5–11 are parked until the P0 Status rail fix is deployed and device-accepted,
per the strict closure order.

The headline is not what the mission brief expected, so it is worth stating first:

> **`pulse_status_shares` does not require registration. There is no failure mode.
> The audit that went looking for one found 27 other tables that probably do.**

---

## Stage 0 — Preservation

Nothing from the P0 fix was modified. `services/db.py` comment-aware translation,
the rail diagnostics in `bot.py`, and `tests/test_sql_placeholder_translation.py`
are untouched. Private-office, Premium, and entitlements work untouched. No
livestream, audio-call, video-call, or realtime-protected path was read or written.
Only new file added: `scripts/auto_pk_tables_audit.py` (a read-only audit tool).

---

## Stage 1 — What AUTO_PK_TABLES actually does

Proven from source, not assumed. `services/db.py::CompatCursor.execute`, line 714:

```python
if table and table in AUTO_PK_TABLES and "RETURNING" not in translated.upper() and not append_do_nothing:
    returning_pk = AUTO_PK_TABLES[table]
    translated = f"{translated.rstrip().rstrip(';')} RETURNING {returning_pk}"
...
self.lastrowid = None
if returning_pk:
    row = self._cursor.fetchone()
    if row:
        self.lastrowid = row[0]
```

Membership controls **exactly one thing**: whether an INSERT gets `RETURNING <pk>`
appended so that `cur.lastrowid` — a SQLite-only concept — has a value on Postgres.
It does not affect sequences, insert rewriting, or duplicate handling.

Three conditions must all hold for the mechanism to fire: the statement must match
`INSERT INTO <table>`, the table must be registered, and the statement must not be
an `INSERT OR IGNORE` (those get `ON CONFLICT DO NOTHING` and deliberately no
`RETURNING`). **That last one matters: registering a table whose call site uses
`INSERT OR IGNORE` fixes nothing.**

The failure signature is therefore always the same, and always Postgres-only: an
unregistered table's INSERT succeeds, `lastrowid` stays `None`, and the caller either
dies on `int(None)` or — worse, because it is silent — coerces to `0` and writes a
row that points at nothing. `services/db.py` already carries four in-line post-mortems
of exactly this (`pulse_saved_collections`, `pulse_notifications`, `pulse_briefings`,
`pulse_ad_wallets`), each of which was a live production outage that CI could not see.

---

## Stage 2 — pulse_status_shares traced

Canonical schema (`bot.py:111620`):

```sql
CREATE TABLE IF NOT EXISTS pulse_status_shares (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    status_id INTEGER,
    user_id INTEGER,
    surface TEXT,
    created_at TEXT
)
```

**There is no recipient column.** This is not a delivery table — it is a share-event
log used for a counter. That reframes several later stages of the brief.

Every call site:

| Site | Operation |
| --- | --- |
| `bot.py:43009` (`api_pulse_status_share`) | the only INSERT |
| `bot.py:43012` | `SELECT COUNT(*) ... WHERE status_id=?` |
| `bot.py:38875` (rail) | `COUNT(*) AS share_count` subquery |
| `bot.py:40951` (payload) | `COUNT(*) AS share_count` subquery |
| `mobile-native/src/api/status.ts:273` | `shareStatus()` → reads `share_count` only |

The route inserts, then immediately counts. **It never reads `cur.lastrowid`.**
No route, service, worker, or native caller anywhere in the repo consumes the share
row's `id`.

---

## Stage 3 — Postgres reproduction

**NOT APPLICABLE — there is nothing to reproduce.**

The INSERT is plain (not `INSERT OR IGNORE`), so on Postgres it executes and commits
correctly with or without registration. The only consequence of absence is that
`cur.lastrowid` stays `None` — a value this code path never reads. The share count
comes from a `COUNT(*)`, which is dialect-neutral.

**Proven failure mode: none.** Registering it is hygiene — it removes a trap for the
next person who adds a caller that does want the id — not a repair.

---

## Stage 4 — Blast-radius audit

Tool: `scripts/auto_pk_tables_audit.py`. It parses every `CREATE TABLE` in `bot.py`,
`services/`, and the root workers, classifies the primary key, and separately finds
every `INSERT INTO <table>` whose id is read back before the next `.execute()`.
`INSERT OR IGNORE` sites are excluded, because registration would not help them.

| Classification | Count |
| --- | --- |
| REGISTERED_CORRECTLY | 342 |
| **MISSING_LIVE** — generated PK, unregistered, **id is read back** | **27** |
| MISSING_LATENT — generated PK, unregistered, id never read | 282 |
| COMPOSITE_PK — no single surrogate key to return | 20 |
| MANUAL_PK — caller supplies the key | 95 |
| NO_PK | 16 |
| REGISTERED_NO_SCHEMA — listed but no CREATE TABLE found | 13 |

782 table definitions; 355 AUTO_PK_TABLES entries.

`pulse_status_shares` classifies as **MISSING_LATENT** — the benign bucket.

### The 27 MISSING_LIVE candidates

```
account_health_events              arena_teams                     pulse_content_promotions
account_strike_appeals             intelligence_delivery_jobs      pulse_daily_mentor_conversations
arena_chat_messages                marketplace_offers              pulse_friend_requests
arena_chat_threads                 marketplace_returns             pulse_live_destinations
arena_friend_challenges            pulse_ad_adsets                 pulse_music_reports
arena_matches                      pulse_ad_media_assets           sentinel_financial_exposure
arena_message_requests             sentinel_financial_reconciliations
arena_play_sessions                sentinel_financial_risk         sentinel_identity_risk
arena_playbooks                    user_welcome_events             verification_requests
                                                                   watch_rules
```

Four verified by hand so far — these are real, not detector noise:

- `services/live_destination_service.py:93` — `return int(cur.lastrowid)`. On Postgres
  this is `int(None)` → **TypeError**. Live-destination creation would 500.
- `services/marketplace_offers_routes.py:329` — `_load_offer(cur, int(cur.lastrowid))`.
  Same shape: marketplace offer creation.
- `bot.py:3721` — `return int(getattr(cur, "lastrowid", 0) or 0)`, so it returns **0**
  silently rather than raising. The quiet variant.
- `bot.py:84781` — `entity_id=str(cur.lastrowid or "")` on the friend-request
  notification, so on Postgres the notification's `entity_id` is `""` and tapping it
  cannot open the request.

Three of these sit on customer-facing paths (live destinations, marketplace offers,
friend requests) and would present as a 500 or a dead notification, not as anything
resembling a database error. That is the same disguise the Status rail wore.

The remaining 23 have **not** been hand-verified. The detector is a heuristic and
false positives are expected — `pulse_saved_sounds` and `pulse_reel_sound_saves` were
already dropped as `INSERT OR IGNORE`, and `account_restrictions` / `account_strikes`
moved to LATENT once the window was tightened to end at the next `.execute()`. Per the
brief, I am **not** sweeping these into anything. They need per-site confirmation and
their own mission.

---

## Interim answers to the final-report fields

| Field | Answer |
| --- | --- |
| AUTO_PK_TABLES purpose | Appends `RETURNING <pk>` to INSERTs so `lastrowid` works on Postgres. Nothing else. |
| pulse_status_shares requires registration | **NO** — hygiene only, no consumer of the id |
| Proven failure mode | **None** |
| Postgres reproduction | **NOT APPLICABLE** |
| Other omissions found | **27 candidates, 4 hand-verified as real; 23 unverified** |
| Fix | Not applied — parked behind P0 closure |
| Audio / live / call protection | **PASS** — no protected path read or written |
| Device QA | **BLOCKED** — requires P0 deploy first |
| Verdict | **Not declared.** Stages 5–11 not executed. |

---

## Recommendation

Registering `pulse_status_shares` is a one-line hygiene change with no user-visible
effect, and on its own it does not justify a mission. The mission earned its keep by
what it turned up instead. My suggestion is to redirect it: verify the remaining 23
MISSING_LIVE candidates, then fix the confirmed set — starting with
`pulse_live_destinations`, `marketplace_offers`, `marketplace_returns`, and the four
`sentinel_*` tables, since financial and identity-risk rows silently keyed to `0` are
worse than a 500.

Stages 6–9 of the original brief (recipient semantics, expiration of shares,
share-duplicate idempotency) rest on a premise the schema does not support: there is
no recipient and no uniqueness constraint, and a double-tap does create two rows —
by design, since the table is an event log feeding a counter. If share-to-recipient
is meant to exist as a product feature, that is a build, not a repair, and should be
specified before anyone writes tests against it.
