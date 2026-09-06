# Private Office — Operations

The Operations layer turns the six existing Private Office record primitives into
an executive read model: what is live, what is late, what needs the member, and
what just happened. It adds no seventh primitive, no second ledger and no task
system. Everything below is a lifecycle rule, a derived value or a read over
tables that already existed.

Feature id `private_office.operations`, minimum tier `PRIVATE`, kill switch
`PRIVATE_OPERATIONS_ENABLED`. All three are declared once, in
`services/private_office/feature_matrix.py`, and every gate resolves through
`feature_matrix.availability`.

## The six primitives

`OBLIGATION`, `EVENT`, `DECISION`, `REQUEST`, `RISK`, `OPPORTUNITY`. Each has its
own table and its own status vocabulary, declared in `records.SPECS`. There is no
shared status enum, and the transition engine below deliberately did not create
one — it formalises the six vocabularies that already existed rather than
replacing them with a tidier one.

`services/private_office/records.py` is the only module permitted to `INSERT`
into those tables. That is enforced, not documented:
`tests/private_office/test_private_write_boundary.py` fails if another module
learns to write. Routes and UNDX call `create_record`, `update_record` and
`revise_record`; nothing calls SQL directly.

## Lifecycle: the transition engine

`records.check_transition(record_type, current, target, *, reopen=False)`
classifies a status change and returns one of `TRANSITION_STAY`,
`TRANSITION_MOVE`, `TRANSITION_CLOSE`, `TRANSITION_REOPEN`. It raises
`PrivateRecordRejected` on anything illegal rather than returning a falsey
verdict, so there is no way to call it and carry on past a refusal by forgetting
to check the result.

The rules, derived from the six type contracts as they already behaved:

A **working** status may move to any other working status, and to any closing
status. Closing is a one-way door unless the type says otherwise.

A **closed** record refuses a move to a different closing status. Changing
`RESOLVED` to `DISMISSED` would overwrite how the record ended, which is
information the member cannot get back. The refusal names the reopen path
instead of silently accepting.

**Reopening is explicit.** `records.REOPENABLE` lists, per type, the closing
statuses that can be returned from — `OBLIGATION` and `RISK` from `RESOLVED` or
`DISMISSED`, `DECISION` from `ABANDONED`, `REQUEST` from `CANCELED`,
`OPPORTUNITY` from `PASSED`, and `EVENT` from nothing, because it has no closing
statuses to return from. A reopen lands on the type's `default_status` and
nowhere else, and the caller must pass `reopen=True`. A stale status passed
through from a form is not consent to undo a closure.

A **restatement** of the current status returns `TRANSITION_STAY` and is
idempotent — the writer reports `STATUS_UNCHANGED` rather than writing a new
revision. A retried request does not accumulate history.

A record carrying a status the spec no longer declares is **refused outright**.
The transition rules were written against a vocabulary such a row predates, so
none of them are known to apply, and guessing is worse than stopping.

`records.allowed_transitions(record_type, current)` answers the same question
read-only and side-effect free, so a screen can render exactly the controls the
writer will accept. A UI drawing a button the writer refuses is a lie told in
advance.

Refusals are audited. A denied transition writes
`audit.ACTION_RECORD_TRANSITION_DENIED`, because "someone repeatedly tried to
reopen a closed matter" is a fact worth keeping.

`records.UPDATABLE` bounds what `update_record` will move at all: status,
outcome, assigned provider, severity, coverage state, review flag, priority.
Anything else is a change to the substance of the record and goes through
`revise_record`, which keeps the previous version rather than overwriting it.

## Deadlines: derived, never stored

`DUE_SOON` and `OVERDUE` are computed from a deadline column and the server clock
every time a row is serialized. They are never columns. A stored `OVERDUE`
depends on a sweep having run, and a sweep that quietly stops leaves a store
reporting that everything is still `OPEN` — healthy-looking and wrong.

Only three of the six can be late. `records.DEADLINE_FIELDS`:

| Type | Deadline column |
| --- | --- |
| `OBLIGATION` | `due_at` |
| `DECISION` | `deadline_at` |
| `REQUEST` | `deadline_at` |

The other three are listed in `records.NO_DEADLINE_REASON` with the reason, kept
as text so the next reader does not "fix" the omission. `EVENT` is the clearest
case: `occurred_at` is when something happened, so every event ever recorded has
a past one, and a naive rule would mark the member's entire history overdue and
drown every real obligation in it. `RISK` carries severity and coverage, not a
date. `OPPORTUNITY` has no expiry column.

"Soon" differs per type, in `records.DUE_SOON_WINDOWS`: obligations 14 days,
decisions 7, requests 3. Not one shared window, because a tax filing fourteen
days out is worth surfacing and a concierge request fourteen days out would push
genuinely urgent rows off the top of the queue. The obligation window is 14 days
because that is what it already was — restated, not changed.

## Attention

`operations.attention(cur, owner_user_id=..., limit=...)` classifies live records
and returns a ranked queue. A record can carry several reasons; the queue sorts
by the strongest one it has.

`operations.REASON_RANK`, strongest first: `OVERDUE`, `HIGH_RISK`, `DUE_SOON`,
`RESPONSE_REQUIRED`, `MISSING_REQUIRED_CONTEXT`, `DECISION_REQUIRED`, `BLOCKED`.
It is an ordered tuple rather than a dict of weights so that inserting a reason
forces a decision about where it sits.

Two placements worth knowing. `HIGH_RISK` sits above `DUE_SOON` because a
critical uninsured exposure outranks a request due Thursday — the deadline will
still be there on Wednesday and the exposure may not be. `BLOCKED` sits last
because a request waiting on a provider is on the queue so the member can see it
is moving, not because it needs them; ranking it with the things that do need
them is how a queue fills with items nobody can act on, which is how members
learn to ignore the queue.

`operations.UNSUPPORTED_REASONS` declares the reasons the model cannot support
and why — `EXPIRING_OPPORTUNITY` because `private_opportunities` has no expiry
column, `ESCALATED` because no escalation state exists. They are declared rather
than omitted so a screen can say "not tracked" instead of implying "none found".
The API returns them alongside the queue.

Bounds: 50 items returned, 120 rows scanned per type, 25 recent activity entries,
a 14-day window for "recently". Six fixed queries whose cost does not grow with
the size of the account. The header count is **not** capped — a member with 300
overdue items is told 300 and shown the worst 50.

## Overview

`operations.overview(cur, owner_user_id=...)` is the executive summary in one
call: `as_of`, `needs_attention`, `due_today`, `due_this_week`, `overdue`,
`pending_decisions`, `open_requests`, `awaiting_response`, `active_risks`,
`active_high_risks`, `active_opportunities`, `recently_completed`,
`expiring_opportunities`, `counts`, `attention`, `recent_activity`,
`recent_window_days`, `unsupported`.

Four properties of that list are load-bearing.

**`expiring_opportunities` is the string `UNSUPPORTED`, not a number.** No expiry
column exists on `private_opportunities`, so the question is unanswerable rather
than answered zero. It is not `null` either: `null` at the far end of a wire is
indistinguishable from a serialization bug, and this needs to be legible to the
screen as a declared gap. The reason is in `unsupported`.

**Every number is a SQL count, not a length.** `active_high_risks` is counted in
the database rather than by filtering the attention page, because the page is
capped at 50 — counting it would make the number agree with the dashboard
exactly until the member has more than fifty things wrong, which is the moment
the number matters.

**`active_risks` and `active_high_risks` are two numbers with two names.** A
single `active_high_risks` that actually counted all live risks would be the same
class of untruth as a fabricated zero, just harder to notice.

**Recent activity comes from the audit ledger**, not reconstructed from the rows
as they currently stand. Current rows can tell you what a record *is*, never what
happened to it; inferring the second from the first relabels every past change
with today's outcome.

"Open" means one definition applied six times: `lifecycle_state` is `ACTIVE`, so
a superseded revision is not counted beside the row that replaced it, and the
status is one the type does not treat as an ending. Dropping either half breaks a
count — the first inflates everything after the member edits anything, the second
means completed work never leaves the dashboard.

## HTTP surface

All operations routes pass `_operations_entry` in
`services/private_office_routes.py`: authentication, then the
`private_office.operations` entitlement gate, then the Private Office second
lock. The 423 for a locked Office is produced before any cursor opens, so no read
runs and the response carries no evidence that any record exists.

| Method | Path |
| --- | --- |
| `GET` | `/api/private-office/records/<view>` |
| `POST` | `/api/private-office/records/<view>` |
| `POST` | `/api/private-office/records/<view>/<record_id>/status` |
| `GET` | `/api/private-office/attention` |
| `GET` | `/api/private-office/operations/overview` |

An unknown `<view>` is a 404 that names the real views rather than quietly
serving a different collection.

`POST /records/<view>` takes an allowlist of body fields, not a passthrough.
`source_type` is pinned to `USER` and `provenance_type` is absent entirely — a
client that could name its own provenance could label its own typing verified.
`relevance_score` is absent for the same reason: that column is only ever what a
named source supplied, and the member's own enthusiasm is not a score.

**Failure is a refusal, never an empty result.** Both read endpoints return
`503` with `state: "unavailable"` if the store cannot be read. A dashboard of
zeros is indistinguishable from a member with nothing outstanding, and that is
the one confusion this surface must never cause.

Reads are audited: `audit.ACTION_RECORD_READ` with the view name and a result
count.

## There is deliberately no UNDX overview capability

The six per-type list capabilities exist and read through
`retrieval.retrieve_records`, which is the same reader the HTTP surface uses.
That structural parity is the point: a deadline rule reaches the agent in the
same commit as the screen, or in neither.

An `operations.overview` capability was considered and **rejected on repository
evidence**. `retrieval.ISOLATED_DOMAINS` holds `HEALTH`, `IDENTITY` and
`SECURITY`, and `retrieval.domain_join_permitted` refuses to combine them.
The overview is a cross-domain aggregate by construction. UNDX reads through
`INTENT_GENERAL`, which is the `GENERAL` domain at an `INTERNAL` ceiling, so an
overview capability could only do one of two things: bypass
`domain_join_permitted`, or produce a total that silently excludes the isolated
domains and therefore contradicts the number on the member's own screen.

Both were refused. Weakening isolation to make an agent convenient is the wrong
trade, and an agent that confidently states a different total than the dashboard
is worse than an agent that cannot answer. The absence is asserted by test, so it
cannot be closed by accident.

## Health

`health.private_office_health()` carries an `operations` section with two halves.

`policy` is read from code constants and costs nothing — record types, deadline
fields and the reasons the other three have none, due-soon windows, the reason
ranking, unsupported reasons and the bounds. It survives a dead database, which
is deliberate: during an incident it is the half most likely to identify the
fault, because "is this deployment's due-soon window what I expect" is a
different question from "is the data there".

The volume half reports `availability` (asked of `feature_matrix` at
`PRIVATE_OFFICE` tier, so the answer can never be "not entitled" — this surface
has no member, and the only reason it can be refused belongs to the deployment),
`implementation`, and one count per table.

A count is an integer or `None`. `None` means *we could not count*, never *there
are none*. Three situations present to a member identically as "my Overview is
empty" and need three different responses: the kill switch is off
(`availability: FEATURE_DISABLED` — check the env var, do not page anyone), the
schema is not on this database (`implementation: NOT_READY`, counts `None`), or
the tables really are empty (`implementation: LIVE`, counts are zeros). Only the
last is a member with nothing recorded.

The section takes no user identifier, like the rest of that surface. A health
endpoint that accepts a user id is an oracle, and an oracle behind an admin check
is one credential away from being an oracle.

An operations kill switch that is off does **not** degrade the overall health
state. "Dark on purpose" is not a fault, and reporting it as one trains operators
to ignore the state field.

## Deliberately absent

No task type, no project type, no approval engine, no recurrence, no dependency
table, no escalation writes, no UNDX mutations. Each was considered and deferred
rather than half-built. `UNSUPPORTED_REASONS` and `NO_DEADLINE_REASON` are where
the gaps are recorded in code, so a screen can name them instead of implying a
zero.

## Related

- `docs/private_office/PRIVATE_OFFICE_CONFIGURATION.md` — flags and environment
- `services/private_office/records.py` — the writer, the specs, the transition engine
- `services/private_office/operations.py` — attention and overview
- `services/private_office/retrieval.py` — the five-gate reader and domain isolation
- `tests/private_office/test_private_write_boundary.py` — the write-boundary enforcement
