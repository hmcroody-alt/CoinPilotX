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

`BLOCKED` is raised by two different things and deliberately not split. A
`REQUEST` in `WAITING_ON_PROVIDER` raises it, and so does any record with an open
dependency (see **Dependencies** below). They mean the same thing to the member —
this is waiting on something that is not you — and a second reason would need its
own rank and its own sentence on screen to express a distinction the member does
not have to act on differently.

Being blocked **adds** a reason, it does not replace them. An overdue obligation
that is also blocked is still overdue: the blocker is what the member has to go
and chase, not grounds to stop showing the deadline. Since `primary_reason` is
the strongest reason present and `BLOCKED` ranks last, being blocked only decides
the ranking of records that had nothing more urgent to say. A blocked record is
never dropped from the queue — something waiting on a blocker that never clears
is precisely what a member needs to see.

`operations.UNSUPPORTED_REASONS` declares the reasons the model cannot support
and why — `EXPIRING_OPPORTUNITY` because `private_opportunities` has no expiry
column, `ESCALATED` because no escalation state exists. They are declared rather
than omitted so a screen can say "not tracked" instead of implying "none found".
The API returns them alongside the queue.

Bounds: 50 items returned, 120 rows scanned per type, 25 recent activity entries,
a 14-day window for "recently". Eleven fixed queries whose cost does not grow with
the size of the account — the original six, plus one per linkable type to resolve
blockers in bulk. That second group is issued **once for the whole call**, not
once per type and never once per record: resolving a record's blockers
individually would make the query count grow with the size of the account, which
is the one property this read model is built to keep. Measured at 11 for both 40
and 140 records. The header count is **not** capped — a member with 300 overdue
items is told 300 and shown the worst 50.

## Overview

`operations.overview(cur, owner_user_id=...)` is the executive summary in one
call: `as_of`, `needs_attention`, `due_today`, `due_this_week`, `overdue`,
`pending_decisions`, `open_requests`, `awaiting_response`, `active_risks`,
`active_high_risks`, `active_opportunities`, `recently_completed`,
`expiring_opportunities`, `counts`, `attention`, `recent_activity`,
`recent_window_days`, `unsupported`, `blocked`.

Five properties of that list are load-bearing.

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

**`blocked` counts every blocked record, not the blocked ones on the page.** Same
rule as `active_high_risks` and for the same reason, but the failure is sharper
here: blocked items rank last, so they are the first thing a 50-item cap cuts.
Counting the page would report "3 blocked" to a member with 80, and would do it
most confidently at the moment the account is most jammed. It is also **not**
subtracted from `needs_attention` — a blocked item still needs attention, it just
needs a different action — so the two numbers overlap by design.

**Recent activity comes from the audit ledger**, not reconstructed from the rows
as they currently stand. Current rows can tell you what a record *is*, never what
happened to it; inferring the second from the first relabels every past change
with today's outcome.

"Open" means one definition applied six times: `lifecycle_state` is `ACTIVE`, so
a superseded revision is not counted beside the row that replaced it, and the
status is one the type does not treat as an ending. Dropping either half breaks a
count — the first inflates everything after the member edits anything, the second
means completed work never leaves the dashboard.

## Dependencies

`private_record_links` holds edges between the existing types. It is the only new
table the Operations work added, and it earns one by storing something none of the
six can: a relationship *between* records rather than a property of one.

One link type, `DEPENDS_ON`, written as `source DEPENDS_ON target` — the source
waits, the target blocks. Direction is not a detail; it is the whole meaning of
the row, and a reversed edge reads as a perfectly valid dependency pointing the
wrong way, which nothing downstream can detect.

`records.link_records`, `unlink_records`, and `dependencies_for` are the surface.
Linking is idempotent: re-linking an existing pair is accepted and changes
nothing, because a member clicking twice has not made a second dependency.

**`EVENT` cannot be linked.** `linkable_types()` derives this from the specs
rather than listing it — a type is linkable exactly when it has closing statuses.
An event has already happened, so it can never *stop* blocking, and an edge to one
would be a blocker that no action can clear.

### What is refused, and why refusal is the safe direction

- **Self-dependency.** A record cannot depend on itself.
- **Cycles.** `A DEPENDS_ON B` closes a loop exactly when B already reaches A,
  checked by `_reaches` **before** the insert, so the store never holds a cycle
  even briefly.
- **Cross-owner.** Endpoints must belong to the caller.
- **Ceiling.** `MAX_DEPENDENCIES_PER_RECORD` (64) per record.

`_reaches` walks to `MAX_DEPENDENCY_DEPTH` (32) and **fails closed**: exhausting
the bound returns "yes, reachable", refusing the link. This is the deliberate
choice and it is worth stating plainly, because the opposite is the more natural
thing to write. A wrongly refused link is visible immediately and the member can
retry or restructure. A wrongly admitted one creates a cycle that every later
traversal walks while the rules claim it cannot exist — invisible at the moment it
happens and unbounded afterwards. The bound exists to stop a runaway walk, and a
bound that admits the edge when it trips is not a safety mechanism.

A cross-owner endpoint and a nonexistent one produce the **same** refusal, `no
such <type> record`, byte for byte. Saying "that belongs to someone else" would
turn the link endpoint into an existence oracle for other members' record ids. An
unknown *type*, by contrast, is reported precisely: which types exist is true for
everybody and leaks nothing. The tests assert the two id-refusals are string-equal
rather than merely both-rejecting.

### Blocked is derived, never stored

A record is blocked when it has at least one `DEPENDS_ON` edge to a target that is
`ACTIVE` and not in a closing status. There is no `blocked` column and no sweep
that sets one. A stored flag is only as accurate as the last run of whatever
maintains it, and the failure mode of a stopped sweep is silent and maximally
misleading: everything reports healthy. Closing a blocker unblocks its dependents
on the next read, with nothing scheduled in between.

`open_blocker_counts_all` answers this for every type in five queries, one per
linkable type, and `attention()` calls it once per request.

`blocked_record_ids` is defined as `set(open_blocker_counts(...))` rather than as
its own query. That is not a shortcut — two readers answering the same question by
different routes is exactly how the bug below got in, and a set that is literally
the keys of the count map cannot drift from it.

### Revisions carry their dependencies

`revise_record` supersedes a row and writes a new one with a new id.
`_repoint_links` moves every edge touching the old id onto the new one, at write
time.

This was found by writing the test, not by reading the code. Without it, three
things broke and none of them raised: `dependencies_for` and the bulk blocker
reader disagreed with each other; closing a live blocker never unblocked its
dependent; and revising a dependent silently dropped every blocker it had. The
last is the worst — an edit unrelated to dependencies quietly marks blocked work
as ready.

Re-pointing at write time beats having each reader walk supersession chains,
because "each reader" is two readers today and more later, and they have already
been shown to drift. It is safe by construction: the new id is fresh, so no
`UNIQUE` collision and no self-loop is reachable, and relabelling one node can
neither create nor destroy a cycle.

### Enforcement

`private_record_links` is in `PRIVATE_TABLES` in the write-boundary guard, so only
`records.py` may write it. This matters more than for the other tables. The
database cannot express "no cycles" — that rule lives only in the writer, so a
direct `INSERT` is precisely how a loop would get in, after which every traversal
is walking a graph its own invariants say is impossible. A direct `DELETE` is the
mirror image: it makes blocked work read as ready to start.

The rejection battery is `tests/private_office/test_private_record_links.py`, and
the sixteen G-3 entries in
`scripts/private_office/operations_mutation_battery.py` are what keep it from
passing vacuously. Two of those mutations survived their first run — one that made
depth exhaustion admit the link, one that counted the blocked total from the
truncated page — because every existing stage happened to stay inside the bounds.
`stage_bounds` exists because of that, and both are caught now.

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

A fourth value, `FEATURE_ROW_MISSING`, exists because `feature_matrix.availability`
answers an unknown feature id with `NOT_IMPLEMENTED` — the same word it uses for
a feature that genuinely was never built. Health resolves the row first and names
that case separately, so a typo in the id cannot be read as "operations was never
shipped". A lookup that raises leaves `enabled` as `null`: unknown, not disabled.

The section takes no user identifier, like the rest of that surface. A health
endpoint that accepts a user id is an oracle, and an oracle behind an admin check
is one credential away from being an oracle.

An operations kill switch that is off does **not** degrade the overall health
state. "Dark on purpose" is not a fault, and reporting it as one trains operators
to ignore the state field.

## Deliberately absent

No task type, no project type, no approval engine, no recurrence, no escalation
writes, no UNDX mutations. Each was considered and deferred rather than
half-built. The dependency table was on this list and no longer is — see
**Dependencies** above. There is still no dependency *view*: the graph is
enforced and drives `blocked`, but no screen renders it. `UNSUPPORTED_REASONS` and `NO_DEADLINE_REASON` are where
the gaps are recorded in code, so a screen can name them instead of implying a
zero.

## Related

- `docs/private_office/PRIVATE_OFFICE_CONFIGURATION.md` — flags and environment
- `services/private_office/records.py` — the writer, the specs, the transition engine
- `services/private_office/operations.py` — attention and overview
- `services/private_office/retrieval.py` — the five-gate reader and domain isolation
- `tests/private_office/test_private_write_boundary.py` — the write-boundary enforcement
