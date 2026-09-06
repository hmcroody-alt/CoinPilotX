# PRIVATE OPERATIONS — FOUNDATION MAP

**Mission section 2 deliverable. Forensic map only — no code was changed.**

| | |
|---|---|
| Repo | `/Users/hmcherie/Desktop/CoinPilotX` |
| Branch | `main` |
| HEAD | `c0eb553d710f8ff196ab011c330e5fcec512ac8c` |
| Working tree at start | clean |
| Working tree at finish | **dirty — a concurrent agent modified it mid-session, see below** |
| Files changed by this mission | 0 (this document is the only file written by this mission) |
| Date | 2026-09-05 |

---

## 0 — HEADLINE FINDING

**A canonical Operations authority already exists, and it is good.**

`services/private_office/records.py` (1,375 lines) is a single governed writer over six
bounded domain primitives. It already satisfies, in code, most of what mission sections
3 through 9 ask to be built:

- one writer per primitive, no direct SQL from callers (statically enforced);
- `owner_user_id` required on every write and first clause of every read;
- bounded status vocabularies per type with declared closing states;
- deterministic identity keys giving idempotent creates;
- revision/supersede semantics that preserve history;
- provenance required on anything derived from another artifact;
- metadata-only audit row and privacy-safe telemetry counter on every write;
- derived status (`OVERDUE` / `DUE_SOON`) computed at read, never stored.

The HTTP surface, the UNDX read capabilities, the native screen, and a scoped test gate
are all wired on top of it.

**Consequence for the mission: building "the canonical Operations writer" would create
exactly the second ledger sections 3 and 117 forbid.** The correct posture is EXTEND, not
CREATE. The gap list in section 12 below is scoped accordingly.

### Three premises in the mission brief that repository evidence disproves

1. **"Transform Operations into the canonical execution layer."** It already is one for
   six of the seven required types. See sections 1–4.
2. **Branch state.** `CLAUDE.md` describes a dirty tree on
   `codex/emergency-live-audio-recovery`. The tree is clean on `main`. Any plan that
   assumed uncommitted audio work is present is planning against a tree that is not here.
3. **`records.py` module docstring is stale.** Lines 87–88 declare status
   `ROUTE_WIRING_DEFERRED_DUE_TO_CONCURRENT_SECURITY_WORK` and
   `UNDX_WIRING_DEFERRED_DUE_TO_CONCURRENT_SECURITY_WORK`. Both deferrals have since been
   resolved — routes exist at `services/private_office_routes.py:1040`, `:1111`, `:1171`,
   `:1237`, and `services/private_office/undx_records_spec.py:55` reads
   `WIRING_COMPLETE = True`. The docstring is the only thing still saying otherwise. This
   is a documentation defect, not an architecture defect, and it is the kind that causes a
   later mission to rebuild something that exists.

### Concurrent-work hazard

`git worktree list --porcelain` reports **21 worktrees**, of which 7 sit under
`.claude/worktrees/` on `claude/*` branches and carry their own edited copies of
`services/private_office_routes.py` and `services/undx_knowledge_map.py`. All are marked
`prunable — gitdir file points to non-existent location`. Three more under
`/sessions/happy-zen-volta/` are `locked initializing`.

Do not run `git worktree prune`, and do not assume a grep hit under `.claude/worktrees/`
reflects the state of `main`. Every citation in this document was verified against the
main tree specifically.

**The main tree also moved while this map was being written.** At the start of the pass
`git status --short` was empty. At the end it reported:

```
 M services/private_office/obligation_projection.py
 M services/private_office_routes.py
?? PRIVATE_FACTS_SUPER_FOUNDATION_MAP.md
?? services/private_office/capital_overview.py
?? tests/private_office/test_capital_overview.py
```

None of that is this mission's work — a concurrent agent is editing the same package right
now. `private_office_routes.py` grew by roughly 159 lines mid-pass, which moved every route
citation in this document; all of them were re-anchored against the file as it stands at
`2026-09-06 02:45:58Z`. The presence of `PRIVATE_FACTS_SUPER_FOUNDATION_MAP.md` and
`capital_overview.py` suggests a parallel Private Facts / Capital mission is in flight.

Two consequences. **Line numbers in this document are perishable** — treat the quoted
identifier as the anchor and the line number as a hint. And **coordinate before implementing
any gap in section 12**, because at least one other agent is writing into
`services/private_office/` on the same branch.

---

## 1 — CURRENT SOURCE OF TRUTH

`services/private_office/records.py` is the authority for Operations action state.

Its own statement of the rule, from the module docstring (`records.py:22–31`): one
canonical writer per primitive, routes and UNDX call the writer and never `INSERT`;
`owner_user_id` is required on every write and is the first clause of every read; every
write leaves a metadata-only audit row and a privacy-safe counter.

The rule is not merely documented. `records.py:903–907` explains that the `INSERT`
interpolates its table name through `private_table_for()` rather than through
`spec["table"]` precisely so a static guard can recognise the statement — a write whose
table name reaches the SQL by a route no regex can follow is a write the guard silently
stops protecting. That guard is `tests/private_office/test_private_write_boundary.py`
(519 lines).

This is the strongest evidence in the repository that the "one canonical writer" property
is real rather than aspirational: the code was shaped to remain checkable.

---

## 2 — CURRENT TABLES

Six tables, generated from one spec table (`records.py:252`) rather than written out six
times. The stated reason (`records.py:38–41`): the failure mode of six near-copies is that
five get the `owner_user_id` clause and the sixth gets it in the `SELECT` but not the
`UPDATE`.

| Type | Table | Statuses | Closing | Wire name for closure |
|---|---|---|---|---|
| `OBLIGATION` | `private_obligations` | OPEN, RESOLVED, DISMISSED | RESOLVED, DISMISSED | `resolved_at` |
| `EVENT` | `private_domain_events` | RECORDED | — | — |
| `DECISION` | `private_decisions` | OPEN, UNDER_REVIEW, DECIDED, ABANDONED | DECIDED, ABANDONED | `decided_at` |
| `REQUEST` | `private_requests` | OPEN, IN_PROGRESS, WAITING_ON_USER, WAITING_ON_PROVIDER, COMPLETED, CANCELED | COMPLETED, CANCELED | `completed_at` |
| `RISK` | `private_risks` | OPEN, MONITORING, MITIGATED, ACCEPTED, RESOLVED, DISMISSED | RESOLVED, DISMISSED | `resolved_at` |
| `OPPORTUNITY` | `private_opportunities` | NEW, REVIEWING, INTERESTED, PASSED, CLOSED | PASSED, CLOSED | `closed_at_projected` |

Two naming decisions are load-bearing and should not be "cleaned up" later:

**`private_domain_events`, not `private_events`** (`records.py:277–281`). The member's own
history of their affairs versus the access log over it are one letter apart in conversation
and opposite in kind, so the distinction is held in the table name itself.

**One stored `closed_at` column for all six, renamed per type at serialization**
(`records.py:245–251`). Six columns named `resolved_at`/`decided_at`/`completed_at` would
be six code paths for "when did this stop being open", and the sixth is the one nobody
updates. The per-type name is applied at serialization, where a rename is presentation and
cannot desynchronise.

### Core columns (all six)

`records.py:384–404`: `owner_user_id`, `record_key`, `title`, `summary`, `status`,
`lifecycle_state`, `supersedes_id`, `revision`, `domain`, `sensitivity`,
`provenance_state`, `provenance_ref`, `source_type`, `source_ref`, `related_entity_ids`,
`related_document_ids`, `created_at`, `updated_at`, `closed_at`.

**There is no `metadata_json` column on any of the six** (`records.py:69–75`), for the same
structural reason `private_audit_events` has no `detail_json`: a field that exists to hold
"a bit of context" ends up holding a policy number. References are normalized ids validated
to be id-shaped; free text is confined to named, length-capped fields.

### Schema ownership

DDL lives in `records.py` and is applied by `ensure_records_schema` (`records.py:497`) /
`require_records_schema` (`records.py:554`) — idempotent, never raises, caches only success.
Registration into `schema.TABLES` was deliberately deferred (`records.py:76–84`) to avoid a
merge conflict in the one module that decides whether the database is usable. That deferral
is still in force and is still defensible; it is listed as gap G-14 below only because it
should eventually land, not because it is wrong today.

---

## 3 — CURRENT WRITERS

Five functions in `records.py`, all keyword-only, all taking an explicit cursor.

| Function | Line | Purpose |
|---|---|---|
| `create_record` | 922 | The only supported way to create any of the six |
| `update_record` | 1015 | Move status / closure / outcome / assignment only |
| `revise_record` | 1133 | Supersede a row and write a new one carrying `supersedes_id` |
| `get_record` | 1230 | Single record, owner-scoped |
| `list_records` | 1263 | Bounded list, owner-scoped |
| `count_open` | 1356 | Active non-closing count, owner-scoped |

### The update/revise split is the history guarantee

`UPDATABLE` (`records.py:1004–1012`) is a six-element tuple — `status`, `outcome`,
`assigned_provider_id`, `severity`, `coverage_state`, `review_required`, `priority` — and
the docstring names it as the enforcement rather than a convention: *a caller passing
`question=` to `update_record` gets a rejection rather than a silently rewritten decision
log*. Substance changes route to `revise_record`, which marks the old row `SUPERSEDED` and
writes a new `ACTIVE` one. Rationale (`records.py:60–67`): a decision log whose question is
overwritten as the decision evolves is a record of the conclusion with the reasoning
deleted.

**This split is a hard constraint on every future Operations feature.** Any new mutation
must be classified as a status move or a substance change before it is implemented.

### Idempotency

`record_key` (`records.py:680`) is `sha256(record_type ⋮ revision ⋮ identity…)[:48]`.
Identity fields are declared per type in `SPECS`, and `create_record:962–963` appends
`source_type` and `source_ref` to the identity tuple before hashing. A repeated create from
the same nightly sweep returns `{"status": "existing"}` rather than a second row
(`records.py:966–984`).

Two details worth preserving: `revision` is part of the key so a revision can carry the same
identity as the row it supersedes without colliding (`records.py:690–693`); and the id is
recovered by re-selecting on `(owner_user_id, record_key)` rather than `cur.lastrowid`,
because `lastrowid` is `None` on PostgreSQL for these tables (`records.py:687–689`,
`:911–918`). Any new table added to this family must follow both.

### Rejection vs failure

`PrivateRecordRejected` (`records.py:128`) is explicitly distinct from
`PrivateSchemaMissing`: one means the caller asked for something incoherent, the other
means the store cannot be reached. The stated reason — *a caller that cannot tell those
apart will retry the first one forever* — is honoured all the way to the client: the route
returns the writer's message verbatim with 400 (`private_office_routes.py:1145–1150`) and the
native layer surfaces it as `REJECTED` with the message unmodified
(`privateRecords.ts:83–86`).

---

## 4 — CURRENT READERS

| Reader | Location | Notes |
|---|---|---|
| HTTP list | `private_office_routes.py:1040` | `GET /api/private-office/records/<view>` |
| HTTP create | `private_office_routes.py:1111` | `POST /api/private-office/records/<view>` |
| HTTP status move | `private_office_routes.py:1171` | `POST /api/private-office/records/<view>/<id>/status` |
| HTTP attention | `private_office_routes.py:1237` | `GET /api/private-office/attention` |
| UNDX retrieval | `retrieval.py:596` `retrieve_records` | The single UNDX read path |
| Obligation→Capital projection | `obligation_projection.py:174` | Reads obligations, writes graph |
| Briefings | `briefings.py` | Composes open records into a briefing |
| Shield | `shield.py` | Scans for overdue obligations among other conditions |
| Concierge | `concierge.py` | Owns the REQUEST-backed desk |
| Relationships | `relationships.py` | Reads OBLIGATION/REQUEST as a person's commitments |
| Native screen | `PrivateOperationsScreen.tsx` | Six views |

### `/attention` is the existing needs-attention engine

`private_office_routes.py:1237–1295`. It returns open counts for all six views plus the
five obligations due soonest inside a 14-day horizon, **in one call** — the stated reason
being that the Office Home cannot then render counts and a due-soon list that disagree
about the same store.

Its failure behaviour is correct and should be treated as the reference pattern
(`:1279–1286`): an unreadable store returns `503 {"state": "unavailable"}`, with the comment
*refusing beats rendering confident zeros over real obligations*.

### Bounds

| Bound | Value | Location |
|---|---|---|
| `DEFAULT_LIMIT` | 50 | `records.py:230` |
| `MAX_LIMIT` | 200 | `records.py:231` |
| `retrieval.MAX_RECORDS` | 200 | `retrieval.py:578` |
| UNDX `DEFAULT_LIMIT` / `MAX_LIMIT` | 10 / 25 | `undx_records_spec.py:62–63` |
| `MAX_REFS` | 25 | `records.py:228` |
| `MAX_TITLE` / `MAX_SUMMARY` | 200 / 2000 | `records.py:223–224` |

`list_records:1277–1281` states the bound is not a parameter a caller can raise past
`MAX_LIMIT`: *an unbounded list over a shared table is the query that is fine for four
years and then is not*.

Pagination is `ORDER BY id DESC LIMIT n` with a `before_id` cursor
(`records.py:1332–1341`). It is deterministic and duplicate-free under stable data. It is
**not** the `timestamp + id` cursor mission section 67 specifies — see gap G-9.

### The filter-widening rule

Three separate places return `[]` rather than everything when a caller names only
unrecognised values — statuses (`records.py:1300–1306`), domains (`:1308–1314`), sensitivity
ceiling (`:1316–1318`). Stated reason: *a typo widens a filter*. This is a load-bearing
invariant and a prime mutation-test target.

---

## 5 — CURRENT OPERATION TYPES

Six: `OBLIGATION`, `EVENT`, `DECISION`, `REQUEST`, `RISK`, `OPPORTUNITY`
(`records.py:141–152`).

Mission section 4 requires seven at minimum — the six above plus `TASK` — and lists
`PROJECT` as required and `APPROVAL`, `MILESTONE`, `FOLLOW_UP` as optional. **`TASK`,
`PROJECT` and `APPROVAL` do not exist.** See gaps G-1 and G-2.

Note that the mission's own section 10 asks for the OBLIGATION/TASK distinction to be
explicit — obligation is *why* something must happen, task is the *work* that satisfies it.
The existing store has the "why" half only.

### Type discriminators

Each type carries a token column (`obligation_type`, `event_type`, `risk_type`,
`opportunity_type`, `category`) validated against `^[A-Z][A-Z0-9_]{0,47}$`
(`records.py:216`) and **never case-folded** — folding would accept `PolicyRenewal` and
store a *different* type from `POLICY_RENEWAL`, created by the store rather than by the
caller. The native form normalises to this grammar client-side before sending
(`PrivateOperationsScreen.tsx:68–72`), so "insurance renewal" becomes `INSURANCE_RENEWAL`
rather than a 400.

---

## 6 — CURRENT STATES

Per-type vocabularies as tabulated in section 2. There is **no single shared lifecycle
vocabulary** across the six; each declares its own `statuses` and `closing` tuple.

Mission section 12 proposes a general vocabulary (DRAFT, OPEN, PLANNED, IN_PROGRESS,
WAITING, BLOCKED, AWAITING_APPROVAL, COMPLETED, CANCELLED, DEFERRED, EXPIRED). Adopting it
wholesale would be a **breaking change to five of six existing types** and would invalidate
stored rows. Section 12's own caveat — *do not let every operation type invent incompatible
lifecycle semantics unless necessary* — is satisfied by the current design in spirit: the
vocabularies are bounded and declared in one table, they are simply not identical.

Recommendation: leave the six as they are; give any *new* type (TASK, PROJECT) a vocabulary
drawn from the section 12 list. Do not migrate the existing five.

### Derived state

`effective_status` (`records.py:702`) computes `OVERDUE` and `DUE_SOON` at read time from
`due_at` and server now, with `DUE_SOON_WINDOW = 14 days` (`records.py:186`).

The reasoning (`records.py:44–55`) is the correct answer to mission sections 15 and 63
already: storing them would mean an obligation is only overdue if some sweep ran, and a
sweep that fails leaves a store that reports every obligation as `OPEN` — healthy-looking
and wrong.

**Mission section 63's requirement is already met**: `effective_status:709–710` returns the
stored status unchanged unless the type is `OBLIGATION` *and* the stored status is exactly
`OPEN`, so a resolved obligation with a past due date is resolved, not overdue.

Two limitations: only `OBLIGATION` has derived state, so a `DECISION` past its
`deadline_at` and a `REQUEST` past its `deadline_at` are **not** surfaced as overdue
anywhere (gap G-6); and no state machine validates transitions — `update_record` accepts
any status in the type's vocabulary, so `RESOLVED → OPEN` is currently permitted with no
governed reopen semantics (gap G-4).

---

## 7 — CURRENT PRIORITY MODEL

`PRIORITIES = ("LOW", "NORMAL", "HIGH", "URGENT")` (`records.py:208`) — exactly mission
section 14's bounded set.

**It exists on `REQUEST` only** (`records.py:333`, default `NORMAL`). Obligations, decisions,
risks and opportunities have no priority column. `priority` is in `UPDATABLE`
(`records.py:1011`) so it can be moved on requests.

Risk severity is a separate, deliberately distinct vocabulary: `SEVERITIES = (UNKNOWN, INFO,
LOW, MODERATE, HIGH, CRITICAL)` (`records.py:189–191`), default `UNKNOWN`. Mission section
14's rule — priority should not silently equal risk severity — is structurally satisfied
because the two are different columns on different tables with different vocabularies.

### The coverage-state rule is worth quoting for any future work

`COVERAGE_STATES = (UNKNOWN, PROVIDER_REQUIRED, PROVIDER_REVIEWED, SELF_ASSERTED)`
(`records.py:203–206`), and there is **deliberately no value meaning "fine"**
(`records.py:196–202`): *do not automatically call something safe because no provider data
exists* is a rule about what the absence of data is allowed to mean, and the only way to
hold it is to make the truthful state the one you get for free.

Any new enum added to Operations should be designed the same way.

---

## 8 — CURRENT AUDIT

`services/private_office/audit.py` (322 lines). Actions relevant here
(`audit.py:84–93`): `PRIVATE_RECORD_CREATE`, `PRIVATE_RECORD_UPDATE`,
`PRIVATE_RECORD_REVISE`, `PRIVATE_RECORD_READ`, `PRIVATE_RECORD_FIELD_REVEAL`.

Every write in `records.py` emits one — including the dedupe path, where an `existing`
result still records a `PRIVATE_RECORD_CREATE` (`records.py:973–978`), which is right: the
member's client did attempt a write and that attempt is the auditable event.

Reads are audited too, with `result_count` but not content:
`private_office_routes.py:1073–1081` (per view) and `:1263–1271` (attention, `object_id`
`"attention"`).

Audit payloads are metadata-only by construction — `private_audit_events` has no
`detail_json` column (`records.py:69–75`), and `audit.safe_object_id` applies the same
id-shape rule as `records.safe_ref` (`records.py:219–221`): anything with a space, a
currency symbol or an `@` is a value wearing an id's clothes. Mission section 73's "do not
place sensitive content in audit payloads" is enforced by schema, not by discipline.

Audit coverage per mission section 73 is complete for create / update / read / revise. It
is absent for the mutations that do not yet exist (assign, block, unblock, delegate,
dependency add/remove, approval request/approve/reject).

---

## 9 — CURRENT NOTIFICATIONS, REMINDERS AND SCHEDULING

**There is no Operations notification or reminder path.** This is the single largest gap.

`services/private_office/jobs.py` (167 lines) is explicit about what it is
(`jobs.py:1–8`): a metadata-only record of background work, *bookkeeping, not a queue* —
the Procfile gains no new process. It offers `create_job` / `start_job` / `finish_job` /
`fail_job` / `list_jobs`, all owner-scoped. It does not fire anything.

`briefings.py:12–18` states the engine is member-triggered, **schedules nothing and pushes
nothing** — a deliberate choice, because the alternative (a Private Office fact provider
inside the shared Pulse Briefings engine) would have changed every existing briefing
fingerprint and paged users on the first cycle.

So: no due-soon notification, no deadline reminder, no recurrence, no escalation timer
exists for Operations today. Mission sections 23, 24, 75, 76 are unbuilt. Mission section
76's instruction — *do not build another timer daemon, use the canonical scheduler* — means
the first task of that work is identifying which existing PulseSoc scheduler is canonical
for this purpose. `jobs.py` is explicitly not it.

### Telemetry

`telemetry.py:209–211`: `private_office.record_write`, `private_office.record_closed`,
`private_office.records_retrieved`. Dimensions are `outcome`, `record_type`, `domain`,
`sensitivity`, `provenance_type`, `superseded` — counters and bounded enums only, no
content. Mission section 74's forbidden list is structurally satisfied.

`create_record` emits the rejection counter once at the wrapper rather than at each of the
~20 raise sites inside `_prepare` (`records.py:939–943`), because *a counter that has to be
remembered at every raise site is a counter that is wrong*.

### Health

`services/private_office/health.py` (373 lines) does **not** currently register the six
record tables — the only table reference is `_meetings.RECORDINGS_TABLE` (`health.py:230`).
Mission section 78's Backend OS registration for Operations is unbuilt (gap G-11).

---

## 10 — CURRENT UNDX CAPABILITIES

`services/private_office/undx_records_spec.py` (217 lines).

- `WIRING_COMPLETE = True` (line 55) — the deferral described in `records.py`'s docstring
  is resolved.
- `RISK = "read_only"`, `CONFIRMATION = "never"`, `PERMISSION = "self_account_only"`,
  `AUDIT_CATEGORY = "private_records_read"`,
  `SERVICE_ROUTE = "services.private_office.retrieval.retrieve_records"` (lines 134–138).
- Six capabilities, one per view (line 69), bounded at 10 default / 25 max.

The deferral mechanism itself is worth recording because it is a pattern to reuse
(`records.py:96–108`): while `WIRING_COMPLETE` was `False`, the test suite asserted the six
capabilities were *absent* from all three authorization surfaces, so "deferred" could not
quietly become "forgotten" or "half-registered"; flipping the flag inverts the same test
into a presence check, so the flag cannot be flipped without the registration being real.

`services/undx_capability_registry.py:2544–2601` cross-checks every capability's boundary
against **three independent records** and refuses to resolve a disagreement — the safe
reading of "the records disagree" is not to pick one.

**UNDX cannot write Operations.** Mission sections 39, 40 and 102 (governed UNDX writes with
confirmation, read-back and undo) are entirely unbuilt. Given `RISK = "read_only"` and
`CONFIRMATION = "never"` are asserted across three surfaces, adding writes is a change to
the authorization model and not a small one — it must be a separate capability with its own
risk class, not a widening of these six.

`services/undx_knowledge_map.py:2803` maps each view to
`GET /api/private-office/records/<view>`.

---

## 11 — CURRENT UI

`mobile-native/src/screens/PrivateOperationsScreen.tsx` (664 lines) plus
`mobile-native/src/api/privateRecords.ts` and a 372-line test.

Six views (`privateRecords.ts:24–32`), mirroring `retrieval.RECORD_VIEWS`
(`retrieval.py:567–574`).

### The state model is already the mission's honesty requirement

`privateRecords.ts:67–81` — `READY`, `NOT_ENTITLED`, `FEATURE_DISABLED`, `NOT_IMPLEMENTED`,
`UNAVAILABLE`, `LOCKED`, `ERROR`, with `EMPTY` derived in the screen
(`PrivateOperationsScreen.tsx:66`).

The screen's own docstring states the rule (`PrivateOperationsScreen.tsx:13–20`): these are
seven different sentences, and `UNAVAILABLE` must never be drawn as `EMPTY` — *"we could
not look" dressed as "nothing needs you" is how a member misses a deadline the server knew
about*.

**Mission sections 68 and 105 are therefore already satisfied on the native side.** The
remaining question is whether a test proves it, which is gap G-13.

Similarly `PrivateOperationsScreen.tsx:5–11`: every row came from the server, no seed rows,
no placeholders — *a fabricated obligation in a screen whose promise is "this is what needs
your attention" would be worse than an empty one*. Mission section 43's "every number must
come from real records" holds today.

### Status vocabulary comes from the server

The status sheet lists exactly the statuses the server returned for this view, in the
server's order (`PrivateOperationsScreen.tsx:21–27`); a local list would be a second copy of
`records.SPECS[...]["statuses"]` and would go stale the first time one is added. The route
returns them (`private_office_routes.py:1105`).

### What the UI does not have

No Overview/dashboard tab, no Timeline, no detail screen, no projects tab, no dependency
view, no calendar bucketing. The screen is six flat lists with a create form and a status
sheet. Mission sections 42–57 are largely unbuilt.

---

## 12 — CURRENT GAPS

Every gap is stated against evidence above. Ordered by architectural risk, not by mission
section number.

### Tier 1 — new domain surface (each is a schema change)

| ID | Gap | Mission § | Notes |
|---|---|---|---|
| G-1 | No `TASK` type | 4, 10 | The OBLIGATION/TASK "why vs work" split does not exist. Add as a seventh entry in `SPECS`, not as a new module. |
| G-2 | No `PROJECT` type, no grouping | 4, 11, 50 | Requires a parent link plus computed progress from defined states. |
| G-3 | No dependency graph | 17, 18, 54, 93 | Nothing models "A depends on B". Needs a junction table, self-dependency rejection, cycle detection, cross-owner rejection. The only Operations feature here that genuinely warrants a new table. |
| G-4 | No transition validation | 13, 86 | `update_record` accepts any status in the vocabulary. `RESOLVED → OPEN` is currently legal with no governed reopen. |
| G-5 | No approvals | 19, 94 | No `REQUESTED/APPROVED/REJECTED/EXPIRED/REVOKED` state anywhere. |

### Tier 2 — engines and read model

| ID | Gap | Mission § | Notes |
|---|---|---|---|
| G-6 | Derived state is obligation-only | 6, 15, 63 | `DECISION.deadline_at` and `REQUEST.deadline_at` never surface as overdue. `effective_status:707` is the single place to extend. |
| G-7 | No blocked state or blocker reason | 18 | Nothing can express *why* something is blocked. |
| G-8 | No recurrence | 23, 24, 95 | And no canonical scheduler identified. `jobs.py` is explicitly not a queue. |
| G-9 | Pagination is `id`-only | 67 | `before_id` cursor, not `timestamp + id`. Correct today; not the mission's contract. |
| G-10 | No Overview read model | 42, 43, 65 | `/attention` is the seed — counts + due-soon in one call — but has no overdue / blocked / pending-decision / high-risk buckets. |
| G-11 | Not registered in Backend OS health | 78, 79 | `health.py` does not know the six tables exist. |
| G-12 | No integrity diagnostics | 80 | No read-only checker for invalid status, missing owner, orphan links, dangling refs. |

### Tier 3 — integrations

| ID | Gap | Mission § | Notes |
|---|---|---|---|
| G-15 | No Facts → Operations projection | 25, 26, 96 | `obligation_projection.py` runs the *opposite* direction: Obligations → Capital Graph. A fact with a deadline creates nothing. |
| G-16 | Meetings do not create records | 31, 32, 99 | `meetings.py:159` has an `OBLIGATION` artifact type but no `records.create_record` call anywhere in the module. |
| G-17 | No UNDX writes | 39, 40, 102 | `RISK = "read_only"` asserted across three authorization surfaces. Adding writes is a new capability class, not a widening. |
| G-18 | No notifications or reminders | 75, 76 | Nothing in Private Office pushes for Operations. |
| G-19 | No escalations | 22 | No escalation record, reason, severity or resolution state. |
| G-20 | No delegation | 20, 21 | `assigned_provider_id` exists on REQUEST only; no scoped-context model for a delegate. |

### Tier 4 — hygiene

| ID | Gap | Notes |
|---|---|---|
| G-13 | False-empty regression test not confirmed | The native types are correct; whether a test *proves* `UNAVAILABLE ≠ EMPTY` was not verified in this pass. Mission §105 requires it. |
| G-14 | Six tables not in `schema.TABLES` | Deliberate (`records.py:76–84`) and still defensible. Should eventually land. |
| G-21 | `records.py` docstring stale | Lines 87–88 claim route and UNDX wiring are deferred. Both are complete. Fix this first — it is the cheapest change here and it prevents a future mission rebuilding what exists. |
| G-22 | Test harness is not pytest-native | `test_private_records.py` (802 lines) and `test_operations_routes.py` (511 lines) each expose one `test_*` entrypoint over hand-rolled `stage_*` functions and a `check()` helper. Coverage is real and broad; granularity is not, so a single stage failure reports as one failed test. Relevant to mission §107's mutation battery, which needs to attribute a caught mutation to a specific stage. |

---

## 13 — RECOMMENDED SEQUENCE

Derived from the dependency structure of the gaps, not from mission section order.

1. **G-21** — correct the stale docstring. One edit, prevents the most expensive possible
   mistake (rebuilding the writer).
2. **G-4** — transition validation in `update_record`. Small, self-contained, and every
   later state feature depends on transitions being governed.
3. **G-6** — extend `effective_status` to decision and request deadlines. One function.
4. **G-10** — the Overview read model over the six existing primitives, extending
   `/attention`. No schema change. Highest visible value per unit of risk.
5. **G-3** — the dependency graph. First genuine new table; needs the full cycle /
   self / cross-owner rejection battery of mission §93.
6. **G-1, G-2** — TASK and PROJECT as new `SPECS` entries.
7. **G-5** — approvals.
8. **G-8, G-18** — recurrence and notifications, gated on first identifying the canonical
   scheduler.
9. **G-17** — UNDX writes, last, because it is an authorization-model change requiring
   coordinated edits across three surfaces.

---

## 14 — VERDICT

**FOUNDATION MAP: PASS.**

**SECOND OPERATIONS LEDGER CREATED: NO.** No code was changed by this mission.

The canonical Operations authority exists, is well-reasoned, and is statically enforced.
The mission's directive is correctly read as *extend and deepen*, and any plan that starts
by creating a new Operations store should be rejected on the evidence in sections 1–4.

`AGORA FILES CHANGED: 0` · `AUDIO FILES CHANGED: 0` · `VIDEO CALL FILES CHANGED: 0` ·
`LIVESTREAM FILES CHANGED: 0` · `PUSH: NO`
