# PulseSoc Status — Upload / Visibility Regression Recovery

**Verdict: PARTIAL — root cause found, proven, and fixed in code. Not yet deployed, not yet device-accepted.**

Status of the fix: the defect is understood down to the character, the fix is committed to the
working tree with tests that fail without it, and the failure has been reproduced and cleared
in a harness that runs psycopg2's exact parameter-substitution step. What has **not** happened
is deployment to Railway and an owner-run upload on a real device. Until both are done this
cannot be called STATUS RESTORED, and per your instruction it is not being called that.

---

## Stage 0 — Foundation

| Item | Value |
| --- | --- |
| Branch | `release/full-sweep-20260826` |
| Working tree | dirty **before** this work, and deliberately left that way |
| Files I touched | `services/db.py`, `bot.py`, `tests/test_sql_placeholder_translation.py` (new) |
| Concurrent work preserved | yes — private-office and premium/entitlements changes untouched |

No `git add -A`, no `reset`, no stash, no commit. Pre-existing modified files
(`mobile-native/src/api/premium.ts`, `ProfileHeader.tsx`, `AppNavigator.tsx`, `ProfileScreen.tsx`,
`session/auth.ts`, `services/business_os/entitlements/schema.py`) and untracked work
(`services/private_office/`, `tests/private_office/`, the two `PRIVATE_OFFICE_*.md` maps,
`PREMIUM_REGRESSION_MATRIX.md`, `mobile-native/src/entitlements/`) are exactly as I found them.

Real-time audio gate: `python3 scripts/realtime_audio_change_gate.py --base origin/main --head HEAD`
→ *"No protected real-time audio path changed (72 files inspected)."* No livestream, video-call,
or audio-call path was read or written.

---

## Stage 1 — Reproduction and classification

Not reproduced by guessing; reproduced from production. Railway → project
`coinpilotx-alert-worker` → service `CoinPilotX`, deploy logs filtered on `PULSE_STATUS_RAIL_FAILED`:

- ~50 occurrences between `2026-09-02T17:30Z` and `2026-09-04T00:06Z`
- across `user_id` 1, 4, 15, 19
- **100% of them** carry the same error: `not all arguments converted during string formatting`

`GET /api/pulse/status/rail` returned HTTP 500 on **every request, for every user, for the entire
window.** There is no intermittency and no user-specific condition.

**Failure class: D — the READ/list endpoint fails.** Not A (upload), not B (persistence),
not C (create response), not E (privacy), not F (expiry), not G (media), not H (cache).

---

## Stage 2 — Database truth

CREATE is healthy. `api_pulse_status_create()` (`bot.py:42518`) contains no SQL comments, so it
translates cleanly and its INSERT runs. Statuses uploaded during the outage **were written to
`pulse_status` and are still there.** Nothing was lost. This matters for the fix's acceptance
criteria: once deployed, previously "vanished" statuses that have not expired should reappear.

---

## Stage 3–5 — Read pipeline, API contract, create response

The break is in the read pipeline's first step and nothing downstream of it ever executed.

`pulse_status_active_rows()` — `bot.py:38860` — is the query behind both `items` and
`rail_items`. It raises before a single row is fetched. Therefore:

- the serializer `pulse_status_payload()` (`bot.py:40809`) never ran — reviewed anyway, it is sound
- the client parser `normalizeStatus` / `reconcileStatusItems` (`mobile-native/src/api/status.ts`)
  never received a payload
- no API contract drift exists; the contract was never exercised

The route's own `except Exception` (`bot.py:41547`) is where the user-visible string comes from:

```python
return jsonify({"ok": False, "message": "PulseSoc Status could not load.", ...}), 500
```

`pulseApi()` turns a non-OK response into `PulseApiError(data.message)`;
`StatusScreen.tsx:108` assigns that message to `error`; line 273 then renders
`"Status unavailable"`. So both strings the owner saw on device are the backend's own 500,
relayed verbatim. The native app was reporting the truth.

---

## Stage 13 — Root cause, exactly

`services/db.py::_replace_question_placeholders` rewrites SQLite `?` placeholders into
psycopg2's `%s` before every statement runs on Postgres. It correctly skipped `?` inside string
literals and quoted identifiers. **It had no notion of SQL comments.**

The Status rail query carries this comment:

```sql
-- The Status rail is a discovery surface: it puts other people's faces
-- on the home screen. QA/test and deactivated authors are excluded,
-- but the viewer always keeps their own Status.
AND (s.user_id=? OR {discovery_visible_sql('u')})
ORDER BY s.created_at DESC
LIMIT ?
```

The apostrophe in **`people's`** was read as the opening quote of a string literal. That literal
never closes, so the translator believed every remaining character was inside a string and
stopped converting. The last two `?` — the self-visibility check and the `LIMIT` — reached
psycopg2 unconverted.

Measured against the real statement:

| | count |
| --- | --- |
| `?` in the SQL | 7 |
| parameters bound | 7 |
| `%s` produced by the **old** translator | **5** |
| `%s` produced by the **new** translator | **7** |

Seven parameters into five placeholders is precisely `not all arguments converted during string
formatting`. Reproduced directly:

```
OLD -> TypeError: not all arguments converted during string formatting
NEW -> substitutes cleanly
```

**Why nobody caught it.** This is a Postgres-only defect. Locally and in CI the app talks to a raw
`sqlite3` connection that never enters `CompatCursor`, so `_replace_question_placeholders` is
simply not on the code path. The SQL is valid, the tests pass, the build is green, and the feature
is dead in production. This is the same failure shape already documented in
`tests/test_users_schema_columns_sql.py`.

**Blast radius.** I scanned every SQL string literal in `bot.py` and `services/` (8,133 of them),
comparing comment-stripped `?` counts against post-translation `%s` counts. **Exactly one
statement in the entire codebase was affected: `bot.py:38866`, the Status rail.** No other
subsystem was silently degraded by this.

Regarding "which commit broke it": the defect is a *collision* between a long-standing translator
weakness and a comment added to the rail query. The comment block above `AND (s.user_id=? OR ...)`
is what armed it. I have not attributed it to a specific SHA — the honest statement is that the
outage began when that comment landed, and the log window opens 2026-09-02T17:30Z.

---

## Stage 14 — Fix and tests

**Fix 1 — `services/db.py::_replace_question_placeholders`.** Now tracks `--` line comments and
`/* */` block comments in addition to quotes, so comment text can no longer desynchronise the
scanner. Backslash-escape handling was narrowed to inside string literals, where it belongs.

**Fix 2 — `bot.py::api_pulse_status_rail` diagnostics (Stage 12).** The handler now emits
`code=STATUS_LIST_FETCH_FAILED` with `error_type` and `lane`, and returns `error_code` in the JSON
body. The exception *class* was the missing clue: `not all arguments converted during string
formatting` reads like an application bug until you see it is a psycopg2 `ProgrammingError`.

**Tests — `tests/test_sql_placeholder_translation.py`, 11 cases, all passing:**

Translator unit tests cover the apostrophe-in-line-comment case, apostrophe in a block comment,
a `?` inside a comment (must *not* become a placeholder), `?` inside a string literal and inside a
quoted identifier (the behaviour the old version got right, pinned so a future rewrite cannot fix
comments by breaking strings), doubled `''` escapes, and a `--` sequence inside a string literal.

Query-specific tests lift the real rail f-string out of `bot.py` with `ast` and assert that the
translated `%s` count equals both the literal `?` count and the length of the parameter tuple
`bot.py` actually binds. A companion test asserts the triggering comment is still present, so
deleting the comment cannot silently hollow out the regression test.

A repo-wide test asserts no SQL literal in `bot.py` or `services/` loses a placeholder to a
comment, so the next query written this way fails in CI rather than in production.

Full run: `python3 -m unittest tests.test_sql_placeholder_translation tests.test_users_schema_columns_sql`
→ **19 tests, OK.**

---

## Stage 11 — Honest note on all-or-nothing failure

You asked for per-item isolation so one bad Status cannot blank the rail. **That would not have
prevented this outage,** and I am not going to present it as the fix. The failure happened at
`cur.execute()`, before any row existed to isolate. Per-item isolation remains a reasonable
hardening item; it is a separate piece of work and is listed as follow-up, not as part of this P0.

---

## Stages 6–10 — Assessed, not implicated

Self-visibility, cache invalidation, privacy/audience filters, expiration logic, and media URL
validation were all reviewed and are sound. None of them was ever reached: no payload left the
server. They are marked N/A rather than PASS, because a code path that never executed has not
been proven by this incident.

---

## Secondary finding (not the cause, not fixed)

`pulse_status_shares` is missing from `AUTO_PK_TABLES` in `services/db.py`. Without it the shim
does not append `RETURNING id`, so `lastrowid` is unreliable for the share route on Postgres.
This is a real latent bug but it is unrelated to the outage, and I have deliberately **not**
bundled a schema-shim change into a P0 fix. Say the word and I will do it as its own change.

---

## PASS / FAIL matrix

| Stage | Item | Result |
| --- | --- | --- |
| 0 | Foundation preserved, no foreign work disturbed | PASS |
| 1 | Reproduced exactly, classified (class D) | PASS |
| 2 | Database truth — statuses persist correctly | PASS |
| 3 | Read pipeline traced to failing statement | PASS |
| 4 | API contract drift | N/A — contract never exercised |
| 5 | Create response verified healthy | PASS |
| 6 | Immediate self-visibility | N/A — blocked by stage 3 |
| 7 | Cache / query invalidation | N/A — no payload to cache |
| 8 | Privacy / audience filters | N/A — reviewed, sound, never reached |
| 9 | Expiration logic | N/A — reviewed, sound, never reached |
| 10 | Media URL validation | N/A — reviewed, sound, never reached |
| 11 | All-or-nothing failure removed | NOT DONE — would not have prevented this; see note |
| 12 | Error diagnostics (`STATUS_LIST_FETCH_FAILED`) | PASS |
| 13 | Root cause identified precisely | PASS |
| 14 | Automated tests | PASS — 11 new, 19 total green |
| 15 | Real-device acceptance | **BLOCKED — owner-executed, requires deploy** |
| 16 | Final report | PASS |

---

## What has to happen next, in order

1. Deploy `services/db.py` and `bot.py` to Railway service `CoinPilotX`.
2. Watch the deploy log for `PULSE_STATUS_RAIL_FAILED` — it must stop appearing entirely.
3. On a real device: upload a Status, confirm it appears in "Your Status" **immediately**, confirm
   it appears in the rail, kill and relaunch the app, confirm it is still there, and confirm a
   second account that follows you can see it.
4. Statuses uploaded during the outage that have not yet expired should reappear on their own.
   If they do, that independently confirms the diagnosis — the data was always there.

Do not treat the disappearance of "Status unavailable" as the acceptance signal. The acceptance
signal is a real uploaded Status, visible and playable, surviving an app restart.
