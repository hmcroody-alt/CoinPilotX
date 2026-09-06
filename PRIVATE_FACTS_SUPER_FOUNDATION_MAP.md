# PRIVATE FACTS — SUPER FOUNDATION MAP

Mission Section 3 deliverable. Forensic map of the Private Facts architecture as
it exists **before** any Super System implementation work.

Branch: `codex/emergency-live-audio-recovery` · Mapped: 2026-09-05
Method: source inspection only. No schema, route, screen or test was modified to
produce this document.

**Headline finding: a canonical Private Facts foundation already exists and is
substantially built.** It has one writer, one table, an owner-scoped reader, a
real provenance vocabulary, temporal validity, supersession, a contradiction
engine, an evidence reference layer, statically-enforced write boundaries, the
Private Office second lock, tier entitlement, a kill switch, a UNDX read
capability, privacy-scrubbed telemetry, and a native screen with an honest state
machine. **Section 3's instruction "do not create a second facts system" applies.
The work is extension, not construction.**

The gaps are real and are enumerated in §11 — but they are gaps in a foundation,
not an absence of one.

---

## 1 — CURRENT TABLES

Owned by `services/private_office/schema.py`. Created imperatively (there is no
migration framework — see `CLAUDE.md`); all DDL is `CREATE TABLE IF NOT EXISTS`
plus introspected column adds, and is idempotent.

| Table | Constant | Role |
|---|---|---|
| `private_facts` | `FACTS_TABLE` | The fact ledger |
| `private_graph_nodes` | `NODES_TABLE` | Capital Graph nodes |
| `private_graph_edges` | `EDGES_TABLE` | Capital Graph edges |
| `private_audit_events` | `AUDIT_TABLE` | Audit trail |
| `private_office_security` | `SECURITY_TABLE` | Passcode / second-lock config |
| `private_office_unlock_grants` | `GRANTS_TABLE` | Office unlock grants |

### `private_facts` columns

```
id                  INTEGER PK AUTOINCREMENT
owner_user_id       INTEGER NOT NULL
fact_key            TEXT NOT NULL          -- dedupe key, see §3
subject_type        TEXT NOT NULL          -- currently only "NODE"
subject_id          TEXT NOT NULL
fact_type           TEXT NOT NULL          -- ^[a-z0-9][a-z0-9_.]{0,63}$
value_type          TEXT NOT NULL          -- STRING|NUMBER|MONEY|PERCENT|DATE|BOOLEAN
typed_value         TEXT NOT NULL          -- canonical text of the value
value_number        REAL                   -- same value as float when comparable
provenance_type     TEXT NOT NULL
provenance_ref      TEXT NOT NULL DEFAULT ''  -- encoded ProvenanceRef
confidence          REAL NOT NULL DEFAULT 0.0
observed_at         TEXT NOT NULL
valid_from          TEXT NOT NULL          -- NOT NULL by design
valid_to            TEXT                   -- nullable
sensitivity         TEXT NOT NULL
domain              TEXT NOT NULL
lifecycle_state     TEXT NOT NULL DEFAULT 'ACTIVE'
conflict_id         TEXT NOT NULL DEFAULT ''
created_at          TEXT NOT NULL
updated_at          TEXT NOT NULL
UNIQUE(owner_user_id, fact_key)
```

Indexes: `(owner, subject_type, subject_id, fact_type)`,
`(owner, fact_type, lifecycle_state)`, `(owner, domain, sensitivity)`.

`value_number` is a deliberate second representation of the same value; it is
what lets the contradiction engine ask magnitude questions (35% vs 40%) rather
than string-equality questions. Documented at length in the `facts.py` docstring.

### Adjacent stores that are NOT the fact ledger

| Store | Module | Relationship to facts |
|---|---|---|
| `private_documents`, `private_document_claims` | `documents.py` | Raw files + `PROPOSED` extracted claims. Claims become facts only via `review_claim`. |
| Operations records (6 primitives) | `records.py` | Obligations / events / decisions / requests / risks / opportunities. Separate lifecycle. |
| Structured record envelope + typed fields | `structured_records.py` | Template-driven records with field-level masking and AES-256-GCM for RESTRICTED. **Separate kill switch from `private_facts`, deliberately.** |

---

## 2 — CURRENT FACT VOCABULARY

All in `services/private_office/model.py`, with `normalize_*` functions that
return `None` for anything outside the tuple. Nothing free-form reaches a column.

**Domains** (7) — this is the current category axis:
`GENERAL`, `FINANCIAL`, `LEGAL`, `HEALTH`, `FAMILY`, `IDENTITY`, `SECURITY`.
Default `GENERAL`.

**Sensitivity** (5, ranked): `PUBLIC` < `INTERNAL` < `CONFIDENTIAL` <
`HIGHLY_SENSITIVE` < `RESTRICTED`. Default `CONFIDENTIAL`.
`sensitivity_within(value, ceiling)` implements the read ceiling.

**Value types** (6): `STRING`, `NUMBER`, `MONEY`, `PERCENT`, `DATE`, `BOOLEAN`.
`NUMERIC_VALUE_TYPES` marks which populate `value_number`.

**Lifecycle states** (3): `ACTIVE`, `SUPERSEDED`, `ARCHIVED`.

**Node types** (9): `PERSON`, `BUSINESS`, `PROPERTY`, `INSURANCE_POLICY`,
`CONTRACT`, `DOCUMENT`, `PROFESSIONAL`, `ASSET`, `LIABILITY`.

**Relations** (6): `OWNS`, `ADVISED_BY`, `COVERED_BY`, `SECURED_BY`,
`GOVERNED_BY`, `DESCRIBES` — with `RELATION_ENDPOINTS` constraining which node
type pairs each relation may join.

`fact_type` itself is free-text-within-a-regex (`^[a-z0-9][a-z0-9_.]{0,63}$`),
namespaced by convention (`portfolio.quantity`). It is not an enum.

---

## 3 — CURRENT WRITER

**`services/private_office/facts.py` is the single canonical writer.** 899 lines.

Public write surface:

| Function | Purpose |
|---|---|
| `record_fact(cur, **kwargs)` | The only fact insert. Wraps `_record_fact`. |
| `supersede_facts(cur, ...)` | Retires prior ACTIVE rows for one `(owner, subject, fact_type)`. |

Read surface: `list_facts`, `list_facts_for_subjects`, `count_facts`,
`count_facts_by_domain`.
Helpers: `normalize_value`, `fact_key`, `staleness`, `decode_provenance_ref`.

**Write outcomes:** `written` / `refreshed` / `rejected`.
`PrivateFactRejected` is raised (not returned) for programming errors — unknown
domain, missing owner, unnormalizable value — because a caller that ignores a
`{"status": "rejected"}` dict is how facts stop being written silently.

**Dedupe key.** `fact_key` hashes owner + subject + fact_type + value + source +
validity window. **Provenance is part of the key**, so two independent sources
asserting the same value produce two rows — corroboration is preserved, which is
exactly the signal the contradiction engine needs. A repeat from the *same*
source refreshes `observed_at` instead of inserting.

**Freshness is computed, not stored.** There is no `is_stale` column.
`staleness(row, at=...)` evaluates at read time against
`FRESHNESS_HORIZON_DAYS`:

| Provenance | Horizon (days) |
|---|---|
| `VERIFIED` | 90 |
| `PROVIDER_ASSERTED` | 60 |
| `DOCUMENT_EXTRACTED` | 365 |
| `USER_ASSERTED` | 180 |
| `INFERRED` | 30 |
| `ESTIMATED` | 14 |
| `STALE` / `CONFLICTING` | 0 |

These are **citation horizons, not expiry**. Nothing is deleted; a stale fact is
still returned but must be quoted *as of* its observation date.

### Write boundary guard

`tests/private_office/test_private_write_boundary.py` — a **static** AST + regex
scanner over the whole tree.

- A violation is any write (`INSERT`/`UPDATE`/`DELETE`/`REPLACE`/`DROP`/
  `TRUNCATE`) naming a private table, by literal name or via a `schema` module
  constant, from a module outside `services/private_office/`.
- Inside the package, only `WRITER_MODULES` may write:
  `schema.py`, `facts.py`, `graph.py`, `audit.py`, `contradictions.py`,
  `records.py`, `structured_records.py`.
- The guard tests itself: `test_guard_detects_known_violations` feeds it
  synthetic offending code; file counts and unparseable-file lists are asserted
  so a scanner that silently skips the tree cannot pass.

**Any new fact mutation added by this mission must land inside `facts.py`, or the
guard must be deliberately extended — it will not be bypassed by accident.**

---

## 4 — CURRENT READERS

| Reader | Module | Notes |
|---|---|---|
| `list_facts` / `list_facts_for_subjects` | `facts.py` | Owner-scoped in SQL, sensitivity ceiling, domain filter, limit/offset |
| `count_facts`, `count_facts_by_domain` | `facts.py` | Ceiling-aware counts |
| `retrieve()` | `retrieval.py` | The five-gate governed reader for UNDX/context |
| `retrieve_records()` | `retrieval.py` | Same for Operations records |
| `domain_summary`, `project_facts`, `project_fact` | `office.py` | Wire projection for the client |
| `capital_graph.py` | | Reads **only** through `retrieval.retrieve` |

`retrieval.py` bounds: `MAX_DEPTH=3`, `MAX_NODES=100`, `MAX_EDGES=250`,
`MAX_FACTS=400`, `MAX_RECORDS=200`.

**Intents** (7): `property_portfolio`, `insurance_coverage`, `business_structure`,
`legal_documents`, `health_context`, `identity_context`, `general`.

**Domain isolation.** `ISOLATED_DOMAINS` (health/security class) may only be
joined with `ISOLATION_COMPANIONS` (`GENERAL`). `domain_join_permitted()` refuses
otherwise. Denial reasons are a bounded vocabulary: `actor_is_not_owner`,
`domain_join_not_permitted`, `unknown_intent`, `no_owner`.

**Views** for records: `obligations`, `events`, `decisions`, `requests`, `risks`,
`opportunities`.

---

## 5 — CURRENT PROVENANCE TYPES

`model.PROVENANCE_TYPES` (8), with `PROVENANCE_STRENGTH` ranking and a
`DEGRADED_PROVENANCE` frozenset:

`VERIFIED`, `PROVIDER_ASSERTED`, `DOCUMENT_EXTRACTED`, `USER_ASSERTED`,
`INFERRED`, `ESTIMATED`, `STALE`, `CONFLICTING`.

### `ProvenanceRef` (frozen dataclass, `facts.py`)

```
source_type   str
source_id     str
locator       str      -- e.g. "page=4;section=3.1"
```

Encoded into the `provenance_ref` TEXT column; `decode_provenance_ref()` reads it
back. `locator` is what makes a document-derived fact checkable — a human can be
shown the clause rather than asked to trust the extraction. The shape exists even
though OCR does not.

### Mission §15 vocabulary vs. what exists

| Mission requires | Current equivalent | Status |
|---|---|---|
| `USER_ASSERTED` | `USER_ASSERTED` | present |
| `DOCUMENT_DERIVED` | `DOCUMENT_EXTRACTED` | present, different name |
| `PROVIDER_VERIFIED` | `PROVIDER_ASSERTED` + `VERIFIED` | **split across two; the mission's semantics ("provider confirmed") map to neither cleanly** |
| `SYSTEM_OBSERVED` | — | **missing** |
| `MEETING_DERIVED` | — | **missing** |
| `HUMAN_CONFIRMED` | — | **missing** |
| `UNDX_PROPOSED` | `INFERRED` (approximate) | **not a distinct type** |

`STALE` and `CONFLICTING` are present as provenance values but are arguably
*verification* states wearing provenance clothing — see §11 Gap 1.

**Provenance cannot be client-supplied.** `POST /api/private-office/facts` hard-
codes `USER_ASSERTED` and offers no parameter for it, because a client that could
name its own provenance could label its own typing `VERIFIED`.

---

## 6 — CURRENT VERIFICATION STATES

**There is no `verification_state` column.** This is the single largest
structural gap against the mission.

What exists instead, spread across three axes:

1. `provenance_type` — carries `VERIFIED`, `STALE`, `CONFLICTING` as if they were
   sources.
2. `lifecycle_state` — `ACTIVE` / `SUPERSEDED` / `ARCHIVED`.
3. `staleness()` — computed at read time.

Mission §16 asks for 11 states (`UNVERIFIED`, `USER_CONFIRMED`,
`EVIDENCE_SUPPORTED`, `VERIFIED`, `PROVIDER_VERIFIED`, `NEEDS_REVIEW`,
`CONFLICTING`, `DISPUTED`, `SUPERSEDED`, `EXPIRED`, `REVOKED`) held **separately
from provenance**. Currently 3 of these live in provenance, 2 in lifecycle,
1 is computed, and 5 (`USER_CONFIRMED`, `EVIDENCE_SUPPORTED`, `NEEDS_REVIEW`,
`DISPUTED`, `REVOKED`) do not exist anywhere.

`confidence` exists as `REAL NOT NULL DEFAULT 0.0` and is correctly independent
of provenance.

---

## 7 — CURRENT SUPERSESSION AND CONTRADICTION HANDLING

### Supersession — `facts.supersede_facts()`

Deliberately narrow: one owner, one subject, one `fact_type`; the row named by
`keep_fact_id` survives; everything else ACTIVE in that scope becomes
`SUPERSEDED` with `valid_to` set. It cannot cross a subject or a type, so a
projector cannot bulk-retire facts other writers recorded.

Built for **projections** — when Portfolio says a quantity changed, the old
quantity is the previous state of one ledger, not a second opinion.

**No `supersedes_id` / `superseded_by_id` columns exist.** Supersession is a
state flag plus a `valid_to` stamp. There is no traversable A → B → C chain, so
"what replaced this?" is not answerable, and cycle prevention is not applicable
because there is nothing to cycle.

### Contradictions — `contradictions.py` (446 lines)

Real engine, not a stub.

- `windows_overlap()` — two facts only compete if their validity intervals
  overlap. `SIMULTANEITY_HOURS = 24`.
- `materially_incompatible(left, right)` returns a bounded reason or `None`:
  `values_differ_beyond_tolerance` (numeric, tolerance-based),
  `dates_differ`, `boolean_values_differ`, `text_values_differ`.
- `detect_conflicts()` — bounded by `MAX_SCAN=2000`, `MAX_GROUP=50`.
- `conflict_id(owner, fact_keys)` — deterministic id over the competing set.
- `mark_conflicts()` — stamps `conflict_id` onto the rows. `contradictions.py` is
  in `WRITER_MODULES` for exactly this.

**Detection is implemented. Resolution is not.** There is no
`resolve_conflict()`, no "keep A / keep B / mark as separate" operation, no
resolution audit action, and no route. Nothing records who resolved a conflict or
what won.

---

## 8 — CURRENT SECURITY GATES

### Route gate order (`private_office_routes.py`)

```
_current_user()              → 401 if absent
_resolve_for(user)           → tier resolution
_gate(resolved, FEATURE_ID)  → entitlement + kill switch
_office_lock_gate(user)      → 423 PRIVATE_OFFICE_LOCKED
work(cur)                    → owner taken from session, never from body
```

`FACTS_FEATURE_ID = "private_facts"`.

**Owner isolation is structural, not checked.** `POST /api/private-office/facts`
has no `owner_user_id` parameter — the owner comes from `user["user_id"]`. There
is nothing to forge.

**The overview endpoint is careful about counts.** A locked request gets the
product state and the lock state and **zero counted domains** — counts are Office
data. A failed summary returns `503` with no `domains` key rather than seven
confident zeros over real data.

### Second lock

`services/private_office/security.py` (773 lines) + `office.py`.
Tables: `private_office_security`, `private_office_unlock_grants`.
Routes: `/security/setup|unlock|lock|change|reset|status|biometric`.
Native: `privateOffice/officeLock.ts`, `PrivateOfficeLockGate.tsx`,
plus `officeLock.test.ts`.

Owner lifetime membership passes entitlement but **still** must unlock — asserted
by `test_owner_office_membership.py`.

### Entitlement / kill switch

`feature_matrix.py`. `private_facts`: `minimum_tier=TIER_PRIVATE`,
`server_enforced=True`, `implementation=IMPLEMENTED`,
`flag_env=PRIVATE_FACTS_ENABLED`.

Availability vocabulary: `ENTITLED` / `NOT_ENTITLED` / `FEATURE_DISABLED` /
`NOT_IMPLEMENTED`.
Implementation vocabulary: `IMPLEMENTED` / `SHADOW` / `PROVIDER_REQUIRED` /
`DISABLED` / `NOT_IMPLEMENTED`.

Sibling rows: `private_office.records`, `private_office.operations`,
`private_office.document.extraction`, `private_shield`,
`private_shield.breach_monitoring` (`PROVIDER_REQUIRED` — and the matrix note
explicitly forbids rendering a clean state, because "no breaches found" when
nothing has looked is a fabricated security assurance).

### Field encryption

`field_crypto.py` — AES-256-GCM at rest for `RESTRICTED` structured-record
fields. **Applies to `structured_records`, not to `private_facts`.** Fact
`typed_value` is plaintext in the column.

---

## 9 — CURRENT AUDIT AND TELEMETRY

### Audit — `audit.py`, table `private_audit_events`

Fact-relevant actions: `PRIVATE_FACT_CREATE`, `PRIVATE_FACT_SUPERSEDE`,
`PRIVATE_FACT_READ`, `PRIVATE_CONFLICT_DETECTED`, `PRIVATE_ACCESS_DENIED`,
`PRIVATE_CONTEXT_RETRIEVED`, `PRIVATE_GRAPH_READ/WRITE`,
`PRIVATE_DOCUMENT_CLAIM_REVIEWED`.

Also present: full Office security actions, record actions
(`PRIVATE_RECORD_CREATE/UPDATE/REVISE/READ/FIELD_REVEAL`), document, briefing,
shield, concierge and meeting action families.

Reads are audited with `result_count` and `purpose`, not with values.

**Missing for the mission:** `FACT_CONFIRM`, `FACT_REVISE`, `FACT_DISPUTE`,
`FACT_ARCHIVE`, `FACT_REVOKE`, `CONFLICT_RESOLVED`, `FACT_EXPORT`,
`SOURCE_LINKED`, `SOURCE_UNLINKED`.

### Telemetry — `telemetry.py`

This module is a genuine strength and should be extended rather than worked
around.

- `FORBIDDEN_FIELDS` — a frozenset of field names that may never be emitted.
- `sanitize(event, fields)` coerces every value through `KIND_COUNT` /
  `KIND_FLAG` / `KIND_ENUM` against a declared vocabulary; unknown enum values
  collapse to `"other"`; counts clamp at `MAX_COUNT = 1_000_000`.
- `spec_is_sound()` self-checks the spec and is asserted by
  `test_private_observability.py`.

Fact events: `private_office.fact_write`, `.conflict_detected`,
`.context_retrieved`, `.context_denied`, `.schema_state`, `.graph_write`,
`.record_write`, `.record_closed`, `.records_retrieved`, meeting events.

**No event carries a fact value.** `fact_write` emits outcome, domain,
sensitivity, provenance_type, superseded — all enums.

### Health — `health.py`, `status.py`

States `healthy` / `degraded` / `unavailable`; implementation `LIVE` /
`NOT_READY`. Sections: schema, substrate (counts), retrieval, meetings,
telemetry. Counts return `None` rather than `0` when unreadable — an unreadable
store is not an empty store.

---

## 10 — CURRENT UNDX CAPABILITIES AND PROJECTIONS

### UNDX

| Piece | Location |
|---|---|
| Tool executor | `services/undx_agent_tools.py::private_facts_list` (~L2579) |
| Capability registration | `services/undx_capability_registry.py` (~L1166), tool `pulsesoc.private_facts.list` |
| Policy | `services/undx_policy.py` — `risk: read_only`, `confirmation: False`, `canonical_key: user_id` |
| Knowledge map | `services/undx_knowledge_map.py` — `resource_type="private_fact"`, native route `/pulse/private-office/facts` |
| Fact presentation discipline | `services/undx_fact_policy.py` |
| Tests | `tests/private_office/test_private_facts_capability.py`, `test_private_facts_kill_switch.py` |

`undx_fact_policy.py` classes: `CURRENT_VERIFIED`, `CURRENT_UNVERIFIED`,
`ROADMAP_APPROVED`, `HISTORICAL`, `UNKNOWN`, each with exactly one presentation
rule, plus a `FACT_POLICY_REQUIRED_PHRASE` ("UNDX fact discipline") that the
provider boundary asserts and fails closed on. **This is the honesty machinery
Mission §42 asks for — but it is about UNDX's claims regarding PulseSoc itself,
not about the member's private facts.** It is a good pattern to reuse; it is not
already doing the §42 job.

**UNDX has exactly one private-fact capability: `list`.** There is no
`private_facts.review`, `.conflicts`, `.changed_since`, `.sources`, `.propose`,
or any write path.

### Projections

| Projector | Direction | Notes |
|---|---|---|
| `portfolio_projection.py` (616 ln) | Portfolio → facts | Uses `supersede_facts` with namespaced `fact_type` |
| `obligation_projection.py` (476 ln) | facts/records → Operations | |
| `capital_graph.py` (604 ln) | facts + graph → member view | Reads only via `retrieval.retrieve` |
| `graph.py` (818 ln) | node/edge writer | In `WRITER_MODULES` |
| `relationships.py` (418 ln) | Relationship Intelligence | |
| `briefings.py` (442 ln) | Briefings | Writes back via `records.create_record` citing the briefing |
| `shield.py` (478 ln) | Shield | Consumes contradictory facts, unreviewed claims, expired facts |

**There is no facts → Capital Graph edge projector.** `graph.py` writes nodes and
edges, and `capital_graph.py` reads them, but no module turns a
`PERSON OWNS ASSET` fact into an edge. Mission §44 asks for one.

### Documents → facts

`documents.py`. `process_document` extracts deterministic key/value pairs from
`txt`/`md`/`csv`/`json` only — **there is no OCR and no PDF extraction in this
repository**. Pairs become `private_document_claims` rows with status `PROPOSED`.
`review_claim(decision=accept)` calls the canonical fact writer and stamps the
claim `ACCEPTED` with the resulting `fact_id`; `reject` stamps `REJECTED`.
`MAX_CLAIM_VALUE_CHARS = 200` — the document body is not copied into fact
metadata.

**This is already the §36 governed flow, for the four text formats it supports.**

---

## 11 — CURRENT GAPS

Ordered by structural depth. Each is stated against what exists, not against a
blank page.

**Gap 1 — Verification is not separable from provenance.**
No `verification_state` column. `VERIFIED`, `STALE` and `CONFLICTING` are
provenance values. Five mission states have no representation at all. This is the
root gap: §16, §31, §32, §106 and most of the Review Queue depend on it.

**Gap 2 — No supersession chain.**
No `supersedes_id` / `superseded_by_id`. "What replaced this?" is unanswerable.
§19, §20, §62.

**Gap 3 — No fact history table.**
History is reconstructible only from `private_audit_events`, which records
create/supersede/read but not confirm/revise/dispute/resolve. §29 (Timeline),
§62, §109.

**Gap 4 — Conflict detection without conflict resolution.**
`detect_conflicts` and `mark_conflicts` exist; nothing resolves. No keep-A /
keep-B / mark-as-separate, no resolution record, no audit action, no route.
§21, §22, §74.

**Gap 5 — No review queue.**
No needs-review population, no priority ranking, no confirm/reject/dismiss
operations on facts. Document claims have their own `PROPOSED` queue, which is
the nearest existing analogue and the right model to generalize. §31, §32.

**Gap 6 — Category taxonomy mismatch.**
7 domains vs. the mission's 11 categories. `ASSET`, `OBLIGATION_CONTEXT`,
`BUSINESS`, `RELATIONSHIP`, `PROPERTY`, `ACCOUNT/SERVICE`, `OTHER` have no
domain. `HEALTH` and `FAMILY` have no mission category. Note that `HEALTH` and
`SECURITY` carry isolation semantics in `retrieval.ISOLATED_DOMAINS` — changing
this axis is not cosmetic.

**Gap 7 — Provenance vocabulary is short by four.**
`SYSTEM_OBSERVED`, `MEETING_DERIVED`, `HUMAN_CONFIRMED`, `UNDX_PROPOSED` do not
exist. `DOCUMENT_EXTRACTED` vs `DOCUMENT_DERIVED` and `PROVIDER_ASSERTED` vs
`PROVIDER_VERIFIED` are naming/semantic mismatches that need a deliberate
decision, not a rename in passing — `PROVENANCE_STRENGTH`,
`DEGRADED_PROVENANCE`, `FRESHNESS_HORIZON_DAYS` and
`telemetry.PROVENANCE_VOCAB` all key off these strings.

**Gap 8 — No explicit expiration.**
`valid_to` exists; there is no `EXPIRED` lifecycle state, no `expires_at`
distinct from `valid_to`, and no expiring-soon window. Freshness horizons are
citation policy, not expiry. §63, §111.

**Gap 9 — Two HTTP routes for the whole surface.**
Only `GET` and `POST /api/private-office/facts`. Mission §87 lists 15. Missing:
overview, detail, revise, confirm, dispute, supersede, history, evidence,
conflicts, resolve, review, sources, timeline.

**Gap 10 — No search.**
`list_facts` filters by domain with limit/offset. No text search over fact type
or value. §27, §28.

**Gap 11 — Native screen is a single flat list.**
`PrivateFactsScreen.tsx` (657 lines) is well built — six-state machine, no seed
data, `UNAVAILABLE` never drawn as `EMPTY`, provenance sheet per row, domain
grouping from server data. But there are no Overview / Timeline / Sources /
Review tabs, no fact detail screen, no conflict screen. §23, §24, §78.

**Gap 12 — `last_verified_at` does not exist.**
`observed_at` is the only verification timestamp. §5, §18.

**Gap 13 — No secrets refusal.**
Nothing in `record_fact` refuses a value that looks like a password, token, key
or card number. `structured_records` has RESTRICTED field crypto;
`private_facts` has neither crypto nor refusal. §53.

**Gap 14 — No source-integrity checking.**
Deleting a document does not mark dependent facts `SOURCE_UNAVAILABLE`.
`evidence.resolve_refs` resolves live refs but nothing sweeps for broken ones.
§51, §97.

**Gap 15 — No export.** §76.

**Gap 16 — No facts → Capital Graph edge projector.** §44.

**Gap 17 — UNDX has one read capability and no proposal path.** §40, §41, §43.

**Gap 18 — No meeting derivation and no concierge fact path.**
`meetings.py` is 1,920 lines and produces no facts. §38, §39, §99.

---

## 12 — WHAT MUST NOT BE REBUILT

Per Section 3's closing instruction and Section 4's single-writer rule:

- `facts.py` remains the **only** durable fact mutator. Confirm / revise /
  dispute / archive / revoke / resolve all belong inside it.
- `schema.py` remains the only DDL owner. New columns go through
  `TABLE_ADDED_COLUMNS`, idempotently.
- `test_private_write_boundary.py` and its `WRITER_MODULES` set stay authoritative.
- `telemetry.sanitize` stays the only path to an emitted event.
- The route gate order stays: auth → tier → entitlement/kill-switch → office lock
  → owner scope → operation.
- `retrieval.retrieve` stays the governed reader; nothing new reads rows directly.
- `structured_records` keeps its own kill switch. It is not the fact ledger.
- Real-time audio, Agora, calls, livestream and camera foundations are untouched.
  Expected diff: **0 files**.

---

## 13 — EXISTING TEST SURFACE

`tests/private_office/` — 33 test modules plus `conftest.py`.

Directly fact-bearing: `test_private_foundation.py`, `test_private_substrate.py`
(owner isolation at runtime), `test_private_write_boundary.py` (static guard),
`test_private_retrieval.py`, `test_private_facts_capability.py`,
`test_private_facts_kill_switch.py`, `test_private_office_routes.py`,
`test_private_office_wire_contract.py`, `test_private_observability.py`,
`test_office_security.py`, `test_owner_office_membership.py`,
`test_private_documents.py`, `test_capital_graph.py`,
`test_portfolio_projection.py`, `test_obligation_projection.py`,
`test_private_shield.py`.

Native: `mobile-native/src/screens/__tests__/PrivateFactsScreen.test.tsx` (367
lines), `privateOffice/__tests__/officeLock.test.ts`.

---

## 14 — SECTION 143 PRE-IMPLEMENTATION STATUS

| Field | Value |
|---|---|
| FOUNDATION MAP | **PASS** |
| CANONICAL FACT WRITER | `services/private_office/facts.py` |
| SECOND FACT LEDGER CREATED | **NO** |
| AGORA FILES CHANGED | **0** |
| AUDIO FILES CHANGED | **0** |
| PUSH | **NO** |

All other Section 143 fields are unassessed — no implementation has been
performed. This document is the Section 3 deliverable only.
