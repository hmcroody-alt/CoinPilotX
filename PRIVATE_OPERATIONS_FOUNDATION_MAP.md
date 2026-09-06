# PRIVATE OPERATIONS — FOUNDATION MAP

Forensic map of the Private Office → Operations subsystem as it exists on `main`
(d21014c8, 2026-09-05), produced **before** any Operations Super System implementation.
Verdict up front: **an Operations system already exists and is shipped** — six record
primitives, one canonical writer module, gated HTTP routes, second-lock enforcement,
owner isolation, audit, UNDX read capabilities, a native screen, and test suites on both
sides. The mission must **extend this system in place**. Building a parallel
"operations" table/writer/screen would violate the existing single-writer boundary and
the mission's own DO-NOT-BUILD-A-SECOND-SYSTEM rule.

---

## 1. CURRENT SOURCE OF TRUTH

`services/private_office/records.py` (~1,500 lines) is the single source of truth for
operations data. It owns:

- The six record-type specs (types, statuses, closing statuses, per-type columns,
  identity keys) — `SPECS`, from line ~210.
- Table DDL (self-owned; `table_ddl()` at line ~447) and indexes.
- The only sanctioned writers: `create_record` (line 922), `update_record` (line 1015),
  `revise_record` (line 1133 — **not yet HTTP-wired**, deliberate deferral per module
  docstring lines 85–107).
- Readers: `list_records` (1263), `count_open` (1356), `get_record` (~1236).
- Dedup identity: `record_key = SHA256(identity fields)[0:48]`, unique per
  `(owner_user_id, record_key)` — create is idempotent by construction.

HTTP surface: `services/private_office_routes.py` lines 815–1138 —
`GET/POST /api/private-office/records/<view>`,
`POST /api/private-office/records/<view>/<id>/status`,
`GET /api/private-office/attention`.

A static write-boundary guard (`tests/private_office/test_private_write_boundary.py`)
already enforces that no other module INSERTs into the record tables. **The mission's
"one canonical Operations writer" requirement is already satisfied and mechanically
enforced.**

## 2. TABLES

Six tables, one per type, sharing 19 core columns (line 384: owner_user_id, record_key,
title, summary, status, lifecycle_state, supersedes_id, revision, domain, sensitivity,
provenance_state/ref, source_type, source_ref, related_entity_ids, related_document_ids,
created_at, updated_at, closed_at) plus per-type extras:

| Table | Type | Extra columns |
|---|---|---|
| `private_obligations` | OBLIGATION | obligation_type*, due_at (idx), amount_text/number, currency |
| `private_domain_events` | EVENT | event_type*, occurred_at* (idx) |
| `private_decisions` | DECISION | question*, assumptions, deadline_at (idx), outcome |
| `private_requests` | REQUEST | category*, priority (LOW/NORMAL/HIGH/URGENT), confidentiality, deadline_at (idx), assigned_provider_id |
| `private_risks` | RISK | risk_type*, severity, coverage_state, review_required |
| `private_opportunities` | OPPORTUNITY | opportunity_type*, relevance_score |

(* = required.) Indexes per table: `(owner, lifecycle_state, status)`,
`(owner, created_at)`, `(owner, <each indexed extra>)`.

Related tables in the same trust domain: `private_audit_events` (schema.py 217),
`private_office_unlock_grants` (schema.py 251), `private_office_jobs` (jobs.py — 
bookkeeping only, not a queue), `private_concierge_messages` (FK → private_requests).

## 3. WRITERS

- `records.create_record(cur, *, record_type, owner_user_id, actor_user_id, purpose,
  **fields)` — dedupes by record_key; audits `PRIVATE_RECORD_CREATE`.
- `records.update_record(...)` — updatable fields are a closed tuple (line 1009):
  `status, outcome, assigned_provider_id, severity, coverage_state, review_required,
  priority`. Audits `PRIVATE_RECORD_UPDATE`.
- `records.revise_record(...)` — supersede-and-replace for substance changes; keeps
  history (`lifecycle_state=SUPERSEDED`, `supersedes_id`). **Not exposed over HTTP.**
- Route-level body allowlist (`_RECORD_BODY_FIELDS`, routes 839–847) filters
  owner_user_id / source_type / provenance; `source_type` pinned to USER.
- No direct SQL from screens, routes, UNDX, workers, or other services — statically
  guarded.

## 4. READERS

- Routes: list per view (audited `PRIVATE_RECORD_READ`, object_id=view), attention
  endpoint (open counts for all six types + obligations due within 14 days, capped 5).
- `retrieval.retrieve_records` (retrieval.py 596) — five-gate reader (owner /
  authorization / sensitivity / domain / purpose) used by UNDX; bounds + `truncated`
  flag; audits `PRIVATE_CONTEXT_RETRIEVED`.
- Briefings (`briefings.generate_briefing`) composes open obligations/risks/requests/
  decisions/opportunities with evidence refs. Shield (`shield.scan`) reads overdue
  obligations. Relationships/concierge read records for commitments per person and
  request threads.

## 5. OPERATION TYPES

Bounded, enum-closed today: `OBLIGATION, EVENT, DECISION, REQUEST, RISK, OPPORTUNITY`
(records.py 141–151); views map in `retrieval.RECORD_VIEWS`. Sub-typing via per-type
token columns (`^[A-Z][A-Z0-9_]{0,47}$`).

**Vs. mission spec:** mission asks for OBLIGATION, DECISION, REQUEST, RISK,
OPPORTUNITY, TASK, PROJECT (+ optional APPROVAL, MILESTONE, FOLLOW_UP).
Missing: **TASK, PROJECT** (and the optional trio). Present-but-not-in-spec: EVENT
(immutable domain history — keep; it is the Timeline substrate).

## 6. STATES / LIFECYCLE

Per-type status vocabularies (validated on write; any status in the spec tuple is
accepted — **no transition matrix**):

- OBLIGATION: OPEN → RESOLVED | DISMISSED
- EVENT: RECORDED (immutable)
- DECISION: OPEN, UNDER_REVIEW → DECIDED | ABANDONED
- REQUEST: OPEN, IN_PROGRESS, WAITING_ON_USER, WAITING_ON_PROVIDER → COMPLETED | CANCELED
- RISK: OPEN, MONITORING, MITIGATED, ACCEPTED → RESOLVED | DISMISSED
- OPPORTUNITY: NEW, REVIEWING, INTERESTED → PASSED | CLOSED

Orthogonal axes that already exist and must not be conflated:
- `lifecycle_state`: ACTIVE | SUPERSEDED (revision history, system-owned).
- `effective_status` (derived at read, never stored): OVERDUE / DUE_SOON
  (14-day window, records.py 186) for obligations.
- `closed_at` auto-stamped when status enters the closing set.

**Vs. mission spec:** mission's unified machine (DRAFT/OPEN/PLANNED/IN_PROGRESS/
WAITING/BLOCKED/AWAITING_APPROVAL/COMPLETED/CANCELLED/DEFERRED/EXPIRED) does not exist.
There is no DRAFT anywhere, no BLOCKED (no dependencies), no AWAITING_APPROVAL (no
approvals), no DEFERRED/EXPIRED, and no enforced transition graph.

## 7. PRIORITY MODEL

`PRIORITIES = ("LOW", "NORMAL", "HIGH", "URGENT")` (records.py 208) — **REQUEST only**,
default NORMAL, updatable. No other type has priority. RISK has `severity`
(UNKNOWN…CRITICAL) which is a different, correct axis. No global ranking/needs-attention
scoring exists; the attention endpoint is counts + a fixed 14-day due-soon slice.

## 8. AUDIT

`private_audit_events` via `services/private_office/audit.py` — identity-only rows
(action, object_type, object_id ≤64 chars, purpose from a closed vocabulary,
result_count); no values, no free text, best-effort writes (failures never block).
Operations actions: `PRIVATE_RECORD_CREATE/UPDATE/REVISE/READ/FIELD_REVEAL`. Every
existing route read/write audits. Purposes include `undx_context`, `user_request`,
`briefing_candidate`, etc.

## 9. NOTIFICATIONS

**None.** Zero integration between record mutations/due dates and `pulse_notifications`
or push. No reminder sweep, no "due tomorrow," no status-change notifications. Audit
rows exist; user-facing alerts do not.

## 10. UNDX CAPABILITIES

All Private Office UNDX surfaces are **read-only by deliberate design**:

- `undx_records_spec.py` — six list capabilities (`private.obligations.list` …
  `private.opportunities.list`), routed through the five-gate
  `retrieval.retrieve_records`, gated on `private_office.operations` via
  `access.decide()` in `undx_agent_tools.py` (~line 650). `WIRING_COMPLETE = True`.
- `undx_feature_reads_spec.py` — documents/people/briefings/shield/concierge reads.
- `undx_capital_spec.py` — portfolio projection read.

Registration pattern: single vocabulary source per spec module; registry/policy/
knowledge-map all derive from it; deterministic tool naming; feature gate colocated;
identity-only audit. **No UNDX write capability exists anywhere in Private Office** —
the mission's "governed writes with high-impact confirmation" is a deliberate policy
reversal, not a gap-fill, and needs explicit owner sign-off.

## 11. UI

`mobile-native/src/screens/PrivateOperationsScreen.tsx` (665 lines) + API layer
`mobile-native/src/api/privateRecords.ts` (tagged result unions, never throws).

- Six view chips (obligations/events/decisions/requests/risks/opportunities). No
  Overview, no Projects, no Timeline tabs; no detail screen; no edit-after-create
  (status move only); no pagination, search, or filters.
- Error honesty is already correct and tested: READY+[] → EMPTY; UNAVAILABLE (503/504/
  network), NOT_ENTITLED (with tier), FEATURE_DISABLED, NOT_IMPLEMENTED, LOCKED (423 →
  local relock via `lockOfficeLocally()`), ERROR are all distinct states
  (screen lines 305–375; jest pins EMPTY ≠ UNAVAILABLE).
- Lock: wrapped in `PrivateOfficeLockGate`; every call carries `officeRequestHeaders()`
  (X-Office-Device always, X-Office-Grant when unlocked).
- Status vocabulary is server-supplied per view (client renders
  `premium:privateOffice.operations.status.<WORD>` with verbatim fallback) — the server
  can extend vocabularies without a client release.
- Navigation: `PrivateOperations` route with optional `{view}` param; Private Office
  home tile + attention strip deep-link into it.
- Tests: 14 screen cases (`PrivateOperationsScreen.test.tsx`), 25 API cases
  (`privateRecords.test.ts`).
- Reference pattern for new work: `mobile-native/src/privateOffice/meetings/` module
  (types/api/session store/tests) is the most recent, richest module shape.

Backend tests: `tests/private_office/test_operations_routes.py` — 11 stages (auth 401,
unknown view 404, create+list all six, body allowlist, transitions, owner isolation
404-indistinguishable, second lock 423, attention, projection leak check, read audit,
kill switch `PRIVATE_OPERATIONS_ENABLED=false` → 404).

Feature flag: `feature_matrix.py` line 200 — `private_office.operations`, TIER_PRIVATE,
server_enforced, IMPLEMENTED, `PRIVATE_OPERATIONS_ENABLED` (default on).

## 12. GAPS (mission spec vs. reality)

Genuinely missing — to build:

1. **Types**: TASK, PROJECT (+ optional APPROVAL/MILESTONE/FOLLOW_UP) — new SPECS
   entries + tables via the existing spec machinery.
2. **Lifecycle**: no transition matrix (any in-vocab status accepted); no DRAFT,
   BLOCKED, AWAITING_APPROVAL, DEFERRED, EXPIRED semantics.
3. **Priority on all actionable types** (today REQUEST only).
4. **Dependencies**: nothing. related_entity_ids/related_document_ids are untyped
   pointers with no blocking semantics, no self/cycle/cross-owner rejection.
5. **Approvals**: nothing beyond concierge's single `assigned_provider_id`.
6. **Recurring operations**: nothing; `private_office_jobs` is bookkeeping, not a
   scheduler; no repo-canonical recurrence engine for Private Office.
7. **Notifications/reminders**: zero.
8. **Needs-attention engine**: only counts + fixed 14-day obligations slice; no
   explainable ranking.
9. **Facts → operations projection** (`source_fact_id` idempotent): does not exist.
   `source_type/source_ref` columns and the record_key dedup give it a natural landing.
10. **UNDX governed writes**: read-only today by design (policy decision required).
11. **UI**: Overview/Projects/Timeline tabs, detail screen, edit/revise flow,
    pagination, ranking display.
12. **Backend OS registration**: absent from `backend_management_registry.py`
    (meetings has a row at line 243 to copy).
13. **Health section**: `health.py` has no operations section (meetings landed one).
14. **Integrity diagnostics**: none (read-only checker to build; no auto-repair).
15. **revise_record HTTP wiring**: writer exists, route deliberately deferred.
16. **Bounded reads**: `list_records` supports limit/before_id but routes don't expose
    pagination params to the client; screen fetches everything.

Already satisfied — do not rebuild:

- One canonical writer (statically enforced) • owner isolation (structural,
  404-indistinguishable) • second lock (423 on every route) • identity-only audit •
  feature flag + kill switch • error honesty in UI • UNDX bounded reads •
  idempotent create • revision history model • per-type status vocabularies
  served to the client.

## 13. INTEGRATION HOOK POINTS (for the build phases)

- **Facts**: `facts.record_fact` / provenance vocab; projection should write via
  `records.create_record` with `source_type=FACT`, `source_ref=fact:<id>` (evidence.py
  ref grammar), record_key dedup ⇒ idempotency.
- **Briefings**: add operations section to `SECTIONS` + composition loop
  (briefings.py ~57).
- **Shield**: add finding kinds (stale request, overdue obligation aging, blocked
  chains) to `KINDS` + `scan()`.
- **Meetings**: post-meeting follow-ups → REQUEST/OBLIGATION records citing
  `meeting:<id>`.
- **Concierge**: REQUEST records already are the concierge queue — extend, don't fork.
- **Capital graph / relationships / documents**: cite via related_entity_ids /
  related_document_ids + evidence refs; no new edge types required initially.
- **Telemetry/health/registry**: copy the meetings pattern (telemetry.py, health.py,
  backend_management_registry.py line 243, tests/private_office/test_private_observability.py).
