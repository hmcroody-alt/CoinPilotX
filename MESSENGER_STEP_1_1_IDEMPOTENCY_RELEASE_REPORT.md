# MESSENGER STEP 1.1 — IDEMPOTENCY INDEX OBSERVABILITY + PRODUCTION RELEASE

Status: **PARTIAL — release candidate complete and green, push blocked on credentials.**

---

## RELEASE BASE

`b7fdafe1f94793ad6ef25d5a84741550858e5842` — the tip of `origin/main`, re-verified against
the remote immediately before commit and again immediately before the push attempt. It is
newer than the `b7fdafe1`-or-later floor the mission set, and it is unchanged since the
worktree was cut, so the candidate is still a fast-forward.

Clean worktree at `CoinPilotX-idempotency-release`, branch `release/messenger-idempotency-p0`.
The main checkout was never staged or merged into; it remains dirty with concurrent agent work
and was left untouched.

## STEP 1 RELEASE COMMIT

`db13ecb372034cea71b24f17d320652f42e732d2` — `fix(messenger): enforce stable idempotent message identity`.

Cherry-picked from `550048c913521bb2bc1f7e845e48023b389ca4a7` and from nothing else. The five
excluded commits (`ba83996a`, `c9377982`, `b9ad31e3`, `6645171c`, `65c3f7c1`) are absent. Content
equivalence was checked file by file against the original: five of the six files hash identically,
and `ChatScreen.tsx` differs only by the media-auth block that the new base already carried.

## STEP 1.1 COMMIT

`359256b95376b8307b0d899bdbc1227e8591cb53` — `fix(messenger): expose idempotency index health`.

Four files: `pulse_communications_v2/service.py`, `pulse_communications_v2/routes.py`,
`tests/test_messenger_send_idempotency.py`, `tests/test_messenger_idempotency_index_health.py`.

Exactly two controlled release commits sit on the base, as specified.

### What changed

The installer no longer returns a bare boolean. It returns a structured status —
`state`, `hard_uniqueness_active`, `duplicate_groups`, `duplicate_rows`, `index_name`,
`checked_at`, `error_class` — and the four states are reached by inspecting the database,
never by matching on driver exception text. The order is deliberate: duplicates are counted
*before* any `CREATE` is attempted, so "blocked by historical data" is established by looking
at the data rather than by reading whatever string the driver happens to produce, which would
have silently reclassified itself on a driver or server upgrade.

Creation succeeding is not the end of the check. The resulting index is read back out of the
catalog and its uniqueness, validity, readiness, column list and partial predicate are compared
against a declared shape. This closes a real hole: `CREATE UNIQUE INDEX IF NOT EXISTS` matches on
the *name* alone, so an index wearing the right name and the wrong columns would previously have
been reported as full protection while enforcing nothing. That case is now reported as
`INSTALL_ERROR` with `error_class=IndexShapeMismatch` and `hard_uniqueness_active=false`.

PostgreSQL is inspected through `pg_index` (`indisunique`, `indisvalid`, `indisready`,
`pg_get_expr(indpred, indrelid)`, and the key columns in order); SQLite through `PRAGMA index_list`,
`PRAGMA index_info` and the stored `CREATE` statement. The dialect is chosen from the live
connection rather than a module-level flag, so a SQLite test is never routed into a
PostgreSQL-only catalog call. Predicates are compared after normalisation, because PostgreSQL
prints the predicate back through its own formatter — `client_message_id <> ''` returns as
`(client_message_id <> ''::text)`, and a raw string comparison would report a correct index as
malformed.

The result is no longer discarded. `_ensure_schema_ready` captures it, publishes it into a
process-visible read-only snapshot owned by the communications service
(`service.message_idempotency_health()`), and emits exactly one structured startup line:

```
PULSE_COMM_V2_IDEMPOTENCY_INDEX state=… hard_uniqueness_active=… index=… duplicate_groups=… duplicate_rows=… error_class=…
```

Nothing in that line, or in the snapshot, carries a message body, a conversation id, a sender id
or a client id — only counts. The snapshot is exposed through the existing admin-gated health
pattern at `GET /admin/health/messenger-idempotency`, using the same `_current_admin()` / 403
guard as `admin_health_deep`. No public unauthenticated debug endpoint was created.

Losing the index does not take Messenger offline; the send path is still correct without it
(lookup plus a conflict-safe insert). What it does now is show up as **degraded** rather than as
indistinguishable from healthy.

`scripts/messenger_idempotency_audit.py` is byte-identical to Step 1 — still read-only, no DELETE,
UPDATE or automatic merge.

## GATES

| Gate | Result |
| --- | --- |
| Step 1 backend idempotency tests | 20 passed |
| New four-state / shape / health / telemetry tests | 21 passed |
| Messenger media-auth + voice contract suites | 78 passed combined |
| `tsc --noEmit` (mobile-native) | exit 0 |
| Jest `messengerIdentity` + `messengerOrdering` | 23 passed |
| Real-time audio protection gate (`--base b7fdafe1 --head HEAD`) | exit 0 — no protected path changed |
| `git diff origin/main..HEAD --name-only` | 10 files, Step 1 + Step 1.1 only |

The ten files are the five native Step 1 files, `pulse_communications_v2/service.py` and
`routes.py`, the audit script, and the two test files. Zero Live, Agora, Private Office, Premium,
Status, foreign-locale or unrelated media-auth content.

The four states are covered directly: a clean database installs and enforces; a correct existing
index is recognised rather than reinstalled; historical duplicates block installation and leave
every row in place; and a genuine failure is its own state. Wrong columns, non-unique, and a wrong
predicate are each asserted to be non-healthy, and the expected predicate is pinned literally.

## PUSH — BLOCKED

`git push origin HEAD:main` fails from this environment with
`git@github.com: Permission denied (publickey)`; the HTTPS remote fails with
`could not read Username for 'https://github.com'`. There are no push credentials in the sandbox.
No force push was attempted, and nothing was pushed.

`origin/main` was re-fetched immediately before the attempt and is still `b7fdafe1`, so the
candidate remains a clean fast-forward. To release, run from the main checkout:

```
cd /Users/hmcherie/Desktop/CoinPilotX
git fetch origin
git push origin release/messenger-idempotency-p0:main
```

If `origin/main` has advanced by then, do not force — the candidate must be recreated on the new base.

## REMAINING FIELDS — NOT YET OBTAINABLE

| Field | Value |
| --- | --- |
| DEPLOYED SHA | not deployed — push blocked |
| DUPLICATE AUDIT | not run in production |
| PRODUCTION MESSAGE ROWS | unknown |
| ROWS WITH CLIENT IDS | unknown |
| DUPLICATE GROUPS | unknown |
| DUPLICATE ROWS | unknown |
| INDEX STATE | absent in production |
| INDEX PRESENT / UNIQUE / VALID / READY | no / no / no / no |
| INDEX COLUMNS | n/a |
| INDEX PREDICATE | n/a |
| HARD_UNIQUENESS_ACTIVE | **false** in production |
| SILENT DEGRADED MODE | **ELIMINATED in code** — cannot be confirmed in production until deploy |
| STEPS 3–8 | **BLOCKED** |
| FINAL | **PARTIAL** |

Production currently runs `b7fdafe1`, which predates Step 1 entirely: the index does not exist,
the audit script is not deployed, and there is no `PULSE_COMM_V2_IDEMPOTENCY_INDEX` signal in the
logs. The audit cannot be run remotely either — Railway's OAuth integration returns variable names
with values redacted, so no direct database URL is available from here.

Steps 3–8 stay blocked until the deployed SHA matches the pushed SHA, the startup line reports one
explicit state, the read-only audit returns zero duplicate groups, and the catalog confirms the
index present, unique, valid, ready, with the expected columns and predicate.
