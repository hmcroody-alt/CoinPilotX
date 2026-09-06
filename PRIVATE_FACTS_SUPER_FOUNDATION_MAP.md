# Private Facts — Super Foundation Map

**Status:** forensic inventory. Written before any implementation, as required.
**Scope:** what the Private Facts foundation *is today*, in this repository, at
commit `6b771754` on branch `claude/nostalgic-neumann-d9391f`.
**Purpose:** decide what already exists so that the Private Facts Super System
**extends one fact store rather than founding a second one.**

Every claim below is a citation, not a recollection. Where a gap is asserted, the
absence was checked by grep across `services/`, `bot.py`, `tests/` and
`mobile-native/src/`, not inferred from a module's docstring.

---

## Verdict first

**A canonical fact foundation already exists and is materially complete on five of
the ten axes this mission cares about.** It has one table, one writer, a static
guard that enforces the writer, a provenance vocabulary with strength ranking, a
five-level sensitivity lattice, a real temporal model, computed-not-stored
staleness, and a contradiction detector that is more careful than most of what
gets shipped under that name.

**Therefore: do not build a second facts system.** The mission extends
`private_facts` in place.

Two things sharpen that verdict:

1. **There is already a second, dormant fact store** —
   `services/undx_brain/facts.py` over `pulse_ai_truth_facts`, behind
   `UNDX_BRAIN_FACTS_ENABLED`, which defaults off. Nothing calls it. It shares no
   DDL, no writer and no read path with `private_facts`. It is named here so that
   nobody — including this mission — extends it, merges it, or adds a third store
   next to it. **It is out of scope. It stays off.**
2. **The single largest gap is not a missing feature, it is a missing axis.**
   `PROVENANCE_VERIFIED` is a member of `PROVENANCE_TYPES`. "Where did this come
   from" and "how thoroughly has it been checked" are the same column today. That
   is precisely the conflation the mission forbids, and it is the finding that
   shapes every other design decision downstream.

---

## CURRENT TABLES

### The fact ledger

`private_facts` — `services/private_office/schema.py:87`, DDL at lines 121-160.

| column | type | note |
|---|---|---|
| `id` | INTEGER PK | |
| `owner_user_id` | INTEGER NOT NULL | in every WHERE clause in the package |
| `fact_key` | TEXT NOT NULL | identity; `UNIQUE(owner_user_id, fact_key)` |
| `subject_type` | TEXT NOT NULL | |
| `subject_id` | TEXT NOT NULL | |
| `fact_type` | TEXT NOT NULL | |
| `value_type` | TEXT NOT NULL | |
| `typed_value` | TEXT NOT NULL | canonical serialised form |
| `value_number` | REAL NULL | numeric shadow for comparison, NULL for DATE/BOOLEAN/STRING |
| `provenance_type` | TEXT NOT NULL | |
| `provenance_ref` | TEXT NOT NULL DEFAULT '' | sorted-key JSON blob, not a foreign key |
| `confidence` | REAL NOT NULL DEFAULT 0.0 | |
| `observed_at` | TEXT NOT NULL | when the source said it |
| `valid_from` | TEXT NOT NULL | when it started being true |
| `valid_to` | TEXT NULL | NULL means "still holds" |
| `sensitivity` | TEXT NOT NULL | |
| `domain` | TEXT NOT NULL | |
| `lifecycle_state` | TEXT NOT NULL DEFAULT 'ACTIVE' | |
| `conflict_id` | TEXT NOT NULL DEFAULT '' | |
| `created_at` / `updated_at` | TEXT NOT NULL | |

Indexes (`schema.py:340-344`), all owner-leading:

- `(owner_user_id, subject_type, subject_id, fact_type)`
- `(owner_user_id, fact_type, lifecycle_state)`
- `(owner_user_id, domain, sensitivity)`

`valid_from` is NOT NULL and `valid_to` is nullable on purpose: the schema
comment states that a fact with no beginning cannot be placed in time, and
placing facts in time is the contradiction engine's entire job.

### Sibling tables in the same package

| table | module | role |
|---|---|---|
| `private_graph_nodes` | `schema.py:88` | entities the facts are *about* |
| `private_graph_edges` | `schema.py:89` | relationships between them |
| `private_audit_events` | `schema.py:90` | access + write trail |
| `private_office_security` | `schema.py:91` | passcode hash, lockout counters |
| `private_office_unlock_grants` | `schema.py:92` | second-lock grant tokens |
| `private_documents`, `private_document_claims` | `documents.py:55-56` | document store and its extracted claims |
| `private_obligations`, `private_events`, `private_decisions`, `private_requests`, `private_risks`, `private_opportunities`, `private_domain_events` | `records.py` | Batch C operational primitives |
| `private_structured_records`, `private_record_fields`, `private_record_revisions` | `structured_records.py:302-304` | Batch D structured record envelope + typed field projection + history |
| `private_office_jobs` | `jobs.py:25` | background job ledger |

### The other fact store — dormant, out of scope

`pulse_ai_truth_facts`, read by `services/undx_brain/facts.py`. Its own docstring
(line 83): *"Nothing calls this module. It is behind `UNDX_BRAIN_FACTS_ENABLED`,
which defaults off."* Registered in `services/undx_brain/config.py:515` with
default `"0"`. It has a `valid_from` column that nothing reads (line 12).

**Named so it stays named. Not extended, not merged, not duplicated.**

---

## CURRENT FACT VOCABULARY

Defined once, in `services/private_office/model.py` (391 lines). Every other
module imports from it rather than restating it.

**Domains (7)** — `GENERAL, FINANCIAL, LEGAL, HEALTH, FAMILY, IDENTITY,
SECURITY`. Default `GENERAL`.

**Sensitivities (5)** — `PUBLIC, INTERNAL, CONFIDENTIAL, HIGHLY_SENSITIVE,
RESTRICTED`, with `SENSITIVITY_RANK` giving them an order so a release ceiling is
a comparison rather than a lookup table. Default `CONFIDENTIAL` — the safe end,
chosen deliberately.

**Value types (6)** — `STRING, NUMBER, MONEY, PERCENT, DATE, BOOLEAN`.
`NUMERIC_VALUE_TYPES = {NUMBER, MONEY, PERCENT}` — DATE is excluded, which is why
date comparison is exact and money comparison has tolerance.

**Lifecycle states (3)** — `ACTIVE, SUPERSEDED, ARCHIVED`.

**Node types (9)** and **relation types (6)** with `RELATION_ENDPOINTS`
constraining which node types a relation may join.

**Fact type grammar** — `_FACT_TYPE_RE = ^[a-z0-9][a-z0-9_.]{0,63}$`
(`facts.py`). Validated *as written* and deliberately not lowercased first, so a
caller sending `Net_Worth` is rejected rather than silently normalised into a
different fact type than the one they meant.

**Identity** — `fact_key` is a hash over owner, subject, fact type **and
provenance**. The consequence is load-bearing and intentional: two independent
sources asserting the same value produce two rows, because the system needs to be
able to say "two sources agree" rather than collapsing that into "one source
said." A repeat from the *same* source refreshes `observed_at` instead of
inserting.

**Normalizers never guess.** `normalize_domain`, `normalize_sensitivity` and
friends return `None` on an unrecognised input — they do not raise and they do
not default. The module comment gives the reason: defaulting an unrecognised
sensitivity to `PUBLIC` publishes whatever the caller misspelled, and defaulting
it to `RESTRICTED` hides data the owner is entitled to see, silently.

---

## CURRENT WRITER

**One.** `services/private_office/facts.py::record_fact` (813 lines total).

- Public surface: `record_fact`, `list_facts`, `list_facts_for_subjects`
  (batched, `MAX_SUBJECT_BATCH = 200`), `count_facts`, `count_facts_by_domain`,
  `normalize_value`, `fact_key`, `staleness`, `decode_provenance_ref`,
  the `ProvenanceRef` dataclass, and `PrivateFactRejected(ValueError)`.
- Write outcomes: `STATUS_WRITTEN`, `STATUS_REFRESHED`, `STATUS_REJECTED`.
- Nine invariants raise `PrivateFactRejected` rather than writing a
  structurally-valid-but-wrong row.
- `record_fact` wraps `_record_fact` to increment a rejection counter, so a
  writer failing repeatedly is visible in telemetry rather than only in a caller's
  exception handler.

**The single-writer rule is enforced statically, not by convention.**
`tests/private_office/test_private_write_boundary.py` regex/AST-scans every Python
file in the repo for SQL naming a private table from a module outside
`WRITER_MODULES` (lines 53-75): `schema.py`, `facts.py`, `graph.py`, `audit.py`,
`contradictions.py`, `records.py`, `structured_records.py`. Nothing else, inside
the package or out.

The guard is proven non-vacuous by its own tests: a synthetic corpus of 30+
deliberate violations must all be caught, 20+ intentional passes must all survive,
and the file count and unparseable-file list are asserted rather than merely
computed — so a scanner that silently skipped the tree would fail.

**Consequence for this mission:** every new write path — supersession, verification
transitions, evidence links, conflict resolution — must land inside `facts.py` (or
a new module explicitly added to `WRITER_MODULES` with a reason), or the guard
fails. This is a feature.

---

## CURRENT READERS

**HTTP — two routes, both in `services/private_office_routes.py`:**

| route | handler | notes |
|---|---|---|
| `GET /api/private-office/facts` | `api_private_office_facts()` (line 385) | one domain's facts, caller's own only |
| `POST /api/private-office/facts` | `api_private_office_create_fact()` (line 464) | provenance fixed as `USER_ASSERTED`, owner from session, never from body |

Feature id `private_facts` (`private_office_routes.py:90`), deliberately separate
from the records feature id so one kill switch cannot take out both stores.

**In-process readers:**

| module | what it reads facts for |
|---|---|
| `retrieval.py:456` | `list_facts_for_subjects` — the batched context read |
| `office.py:265` | `count_facts_by_domain` — the home summary counts |
| `relationships.py` | writes identity facts through `record_fact` for PERSON nodes |
| `documents.py` | an *accepted* document claim becomes a fact via `record_fact` |
| `records.py:872` | `normalize_value` for MONEY, shared normalisation |
| `health.py:194` | surfaces `MAX_SUBJECT_BATCH` in the health payload |
| `evidence.py` | maps kind `"fact"` → `("private_facts", "fact_type")` for ref resolution |

**Native — one screen.** `mobile-native/src/screens/PrivateFactsScreen.tsx` (657
lines), body wrapped in `PrivateOfficeLockGate`. API layer
`mobile-native/src/api/privateOffice.ts` (695 lines) exports `getPrivateFacts(domain?)`
and `createPrivateFact(draft)`.

The result union is already the discriminated shape this mission asks for:

```ts
export type PrivateFactsResult =
  | { state: "READY"; facts: PrivateFact[]; domain: string }
  | { state: "NOT_ENTITLED"; minimumTier: string }
  | { state: "FEATURE_DISABLED" }
  | { state: "NOT_IMPLEMENTED" }
  | { state: "UNAVAILABLE" }
  | { state: "LOCKED"; setupRequired: boolean }
  | { state: "ERROR"; message: string };
```

And the error/empty separation is already correct (lines 147-150):

```ts
setResult(next);
setState(next.state === "READY" && next.facts.length === 0 ? "EMPTY" : next.state);
```

EMPTY is only reachable from READY. A failed fetch cannot render as "no facts."
EMPTY gets no retry button; UNAVAILABLE and ERROR both do. **This requirement is
already satisfied — do not rewrite it, extend around it.**

---

## CURRENT PROVENANCE TYPES

Eight, in `model.py`, with a strength ranking:

| type | strength |
|---|---|
| `VERIFIED` | 100 |
| `PROVIDER_ASSERTED` | 80 |
| `DOCUMENT_EXTRACTED` | 60 |
| `USER_ASSERTED` | 40 |
| `INFERRED` | 20 |
| `ESTIMATED` | 10 |
| `STALE` | 0 |
| `CONFLICTING` | 0 |

`DEGRADED_PROVENANCE = {STALE, CONFLICTING}` — the two that mean "do not rely on
this," separated from the six that mean "here is where it came from."

**Freshness horizons** (`FRESHNESS_HORIZON_DAYS`, `facts.py`), in days, per
provenance type: VERIFIED 90, PROVIDER_ASSERTED 60, DOCUMENT_EXTRACTED 365,
USER_ASSERTED 180, INFERRED 30, ESTIMATED 14, STALE 0, CONFLICTING 0. A scanned
passport stays trustworthy far longer than an inference does, and the table says
so rather than a single global TTL pretending they are the same.

**Staleness is computed at read time and never stored.** There is no column, no
sweeper job, and no window in which the database says "fresh" because the sweeper
has not run yet.

**`provenance_ref`** is a `ProvenanceRef` dataclass — `source_type`, `source_id`,
`locator`, `observed_at`, `confidence` — serialised as sorted-key JSON so the
encoding is deterministic and therefore hashable into `fact_key`. It is a blob in
a TEXT column, **not a foreign key and not a link table row.**

**Missing from this vocabulary** and required by the mission: `MEETING_DERIVED`,
`HUMAN_CONFIRMED`, `UNDX_PROPOSED`, `LEGACY_UNKNOWN`.

---

## CURRENT VERIFICATION STATES

**None. The axis does not exist — but something is wearing its name.**

This is the headline finding of the map, and it is worse than a plain absence.
There is no `verification_state` column, no verification vocabulary in
`model.py`, and no transition function in `facts.py`. What there *is* is
`services/private_office/office.py::verification_state(provenance_type)` — a
five-label bucket (`VERIFIED / SOURCED / SELF_REPORTED / ESTIMATED /
NEEDS_REVIEW`) computed **from provenance alone** and published to the native
client as the `verification` field of `project_provenance`.

So the wire already carries a field called `verification` whose value is derived
entirely from where the data came from and not at all from anyone checking it.
That is the conflation this mission names, shipped, with the misleading name
already in the payload. It is a trust bucket, and the honest fix is to keep it as
a *fallback for legacy rows* under a name that says so, while the real axis goes
in beside it.

`PROVENANCE_VERIFIED` is the same mistake one level down — a *provenance* value
being asked to do a *verification* value's job. Today the system can say "this
came from a verified source." It cannot say:

- unverified
- self-asserted, never checked
- checked against a document
- confirmed by a human
- confirmed by a counterparty
- verified and since expired
- verification failed
- verification disputed

All eight of those collapse into either "provenance is VERIFIED" or "provenance is
not VERIFIED," and the second bucket is doing far too much work. Worse,
`confidence` is a separate REAL column, so a high-confidence *inference* is
representable — which is the right primitive — but there is no way to record that
a human actually looked at it and agreed, other than by overwriting provenance and
destroying the record of where it came from.

**Design consequence:** verification must be added as a **new independent column**
alongside `provenance_type`, not carved out of it. `PROVENANCE_VERIFIED` should be
retained for backward compatibility of existing rows and migrated conservatively
— an existing `VERIFIED` row's *source* is known, its *verification history* is
not, and inventing one would be fabrication.

The `office.verification_state` bucket should be renamed to what it is
(`trust_bucket`) with its old name kept as an alias, since the route pack, the
native projection and the surface tests all import it and a silent behaviour
change hiding inside a rename is worse than the confusing name. The two
vocabularies must share **no values**, so that a string in hand can only ever
answer one of the two questions.

---

## CURRENT SECURITY GATES

Canonical order, confirmed at `services/private_office_routes.py:384-398` and
identical in the structured-records pack:

1. `_current_user()` → **401** if absent
2. `_resolve_for(user)` → entitlement resolution
3. `_gate(resolved, FEATURE_ID)` → **403** tier, **404**/`FEATURE_DISABLED` kill switch
4. `_office_lock_gate(user)` → **423** locked / setup required
5. `_with_cursor(work)` → ensures schema, commits **only** on success
6. `_no_store(payload, status)` → response headers

**Owner isolation** is structural: `owner_user_id` appears in every WHERE clause
in the package, including inside `evidence.resolve_refs`, so a reference naming
another member's row resolves `exists=False` — byte-identical to a reference
naming nothing at all. A foreign id and a nonexistent id are indistinguishable.

**Second lock.** `private_office_security` holds a salted KDF hash and
`hash_version`; no plaintext passcode column exists anywhere in the schema.
Server-side `failed_attempt_count` and `locked_until` are the rate limit; the
client counter is UX only. `UNIQUE(user_id)`: one lock per member by construction.

**Step-up is not the unlock grant.** The grant answers "was the Office opened
recently," which is right for a masked list. Step-up (`security.verify_step_up`)
answers "does the person holding the device know the passcode *right now*," which
is right for handing over a passport number — and it mints nothing, so there is no
reveal token to steal or replay.

**Audit** (`private_audit_events`) records actor, owner, action, object type/id,
purpose, outcome and `result_count`. **It structurally cannot hold a value** —
there is no value column, so a future careless caller cannot log one.

**Telemetry** (`telemetry.py`, 435 lines) emits `private_office.fact_write`,
`.conflict_detected`, `.context_retrieved`, `.context_denied`, `.schema_state` and
the record-write family. Counters and fact *types* only. **Never values.**

**Field encryption** exists in the structured-record store (`field_crypto.py`, AAD-bound)
but **not in the fact store**. Facts carry a sensitivity label, not ciphertext.
This is consistent with "secrets are not facts" — but it is currently a convention
rather than an enforced boundary.

---

## CURRENT UNDX CAPABILITIES

**Exactly one, and it is read-only.**

`services/undx_capability_registry.py:1158`:

```
capability_id  private.facts.list
tool_name      pulsesoc.private_facts.list
risk           RiskLevel.READ_ONLY
confirmation   ConfirmationPolicy.NEVER
permission     PermissionScope.SELF_ACCOUNT_ONLY
executor       private_facts_list
native_route   /pulse/private-office/facts
audit_category private_facts_read
fields         domain (enum, optional), limit (int 1-25, default 10)
```

The scope comment is explicit: there is no field naming another account, so the
only owner this capability can reach is the caller — structurally, not by policy.

**There is no UNDX write path to facts.** No propose, no confirm, no resolve.
`services/undx_agent_tools.py` exposes the list tool and nothing else.
`services/undx_policy.py` and `services/undx_knowledge_map.py` reference facts for
routing and description only.

This is a good starting position: the mission's `UNDX_PROPOSED` provenance can be
introduced without first having to *undo* an existing autonomous writer.

---

## CURRENT PROJECTIONS

| projection | module | lines | reads facts today? |
|---|---|---|---|
| Capital Graph | `capital_graph.py` | 604 | no direct `facts.*` call |
| Relationship Intelligence | `relationships.py` | 418 | **writes** identity facts via `record_fact` |
| Context retrieval | `retrieval.py` | 724 | `list_facts_for_subjects` (batched) |
| Office home summary | `office.py` | 409 | `count_facts_by_domain` |
| Documents → claims | `documents.py` | 683 | accepted claim → `record_fact` |
| Operations primitives | `records.py` | 1375 | shares `normalize_value` only |
| Health / status | `health.py`, `status.py` | 318 / 234 | surfaces limits, not rows |

So the projection layer today is **one true consumer** (`retrieval`), **one
counter** (`office`), and **two producers** (`relationships`, `documents`).
Capital Graph does not read the fact ledger at all, which is a gap rather than a
design choice.

---

## CURRENT GAPS

Eleven, each with the requirement that names it. Ordered by how much of the rest
depends on it.

**1. No verification axis.** Provenance and verification are one column.
Cannot express unverified / document-checked / human-confirmed / expired /
failed / disputed. *This is the load-bearing gap; items 2, 4, 5, 6 and 9 all
depend on it existing first.*

**2. No supersession chain.** `LIFECYCLE_SUPERSEDED` is in the vocabulary, but
there are no `supersedes_id` / `superseded_by_id` columns and no supersede
function. A correction today is either a new independent row or an in-place
update. There is no cycle prevention because there is no chain to cycle.

**3. Conflict resolution is structurally unreachable.** `contradictions.py`
detects well — `windows_overlap`, `materially_incompatible` returning a reason
code, deterministic `conflict_id` (`"conf_" + sha256[:32]`, values never in the
hash), `SIMULTANEITY_HOURS = 24`, `MAX_SCAN = 2000`, `MAX_GROUP = 50`, relative
tolerance for MONEY/NUMBER (0.005), absolute for PERCENT (0.01), zero for DATE,
substring-containment exemption for TEXT. But its own docstring states:
*"`unresolved` is hard-coded true and there is no code path that sets it false."*
`mark_conflicts` deliberately does not restamp provenance or move rows to
SUPERSEDED — correct restraint, but it means **a member cannot resolve anything.**
No resolution record, no losing-evidence preservation, no "mark as separate."

**4. No fact history table.** `private_record_revisions` exists for structured
records; there is no equivalent for facts. `updated_at` moves and nothing records
what it moved from.

**5. No evidence link table for facts.** `evidence.py` (164 lines) is a
*resolver*: `KINDS` for 12 ref kinds, `MAX_REFS = 20`, `_REF_PATTERN`,
`format_ref`/`parse_ref`/`normalize_refs`/`pack_refs`/`unpack_refs`, and
owner-scoped `resolve_refs` returning `{ref, kind, id, exists, label}`.
**Nothing in that module writes.** So a fact cannot cite its evidence, which means
a verified badge would today have no trail behind it — the exact orphan-badge
failure the mission prohibits. `SOURCE UNAVAILABLE` has a natural home
(`exists=False` already models it) but no fact-side consumer.

**6. No review queue.** No priority ranking, no explanation of why an item
surfaced, no queue read model.

**7. Four provenance types missing:** `MEETING_DERIVED`, `HUMAN_CONFIRMED`,
`UNDX_PROPOSED`, `LEGACY_UNKNOWN`. The last one matters for migration: existing
rows whose origin is genuinely unknown must become `LEGACY_UNKNOWN`, not be
promoted to `USER_ASSERTED` and not be flattered to `PROVIDER_VERIFIED`.

**8. Two of fifteen routes exist.** `GET` and `POST /api/private-office/facts`.
Missing: overview, detail, history, evidence, conflicts, conflict resolution,
review queue, sources, timeline, expiring, supersede, verify, integrity.

**9. No integrity checks.** No duplicate detection distinct from contradiction
(duplicate ≠ contradiction — the current model's provenance-in-`fact_key` design
actually makes the distinction *representable*, which helps), no orphan-provenance
scan, no supersession-cycle scan, no verified-without-evidence scan.

**10. No overview / timeline / sources read model.** `count_facts_by_domain`
exists and is the only aggregate. No bounded, paginated list contract beyond the
single-domain fetch.

**11. Native surface is one screen.** `PrivateFactsScreen` is a flat list. No
Overview / Facts / Timeline / Sources / Review tabs, no fact detail with
Details / Evidence / History / Related, no conflict resolution UI, no add-fact
flow beyond `createPrivateFact`.

---

## What this map commits the implementation to

1. **Extend `private_facts`.** No new fact table. No fork. `pulse_ai_truth_facts`
   stays dormant and untouched.
2. **Add verification as a new column**, independent of `provenance_type`. Do not
   repurpose or delete `PROVENANCE_VERIFIED`.
3. **All new writes go through `facts.py`** or a module explicitly added to
   `WRITER_MODULES` with a stated reason — otherwise the static guard fails, and
   it should.
4. **Extend `model.py`'s vocabularies in place.** Every consumer already imports
   from there; restating a vocabulary anywhere else reintroduces the drift the
   module exists to prevent.
5. **New tables are permitted only for genuinely new relations** — fact history,
   fact evidence links, conflict resolutions — never for a parallel copy of the
   ledger itself.
6. **Migration is conservative.** Unknown origin becomes `LEGACY_UNKNOWN`.
   Existing `VERIFIED` provenance does not become a verification *history* that
   nobody recorded.
7. **Do not rewrite what already passes.** The native result union, the
   ERROR-never-EMPTY handling, the gate order, the audit table's structural
   inability to hold values, and the computed-not-stored staleness are all
   already correct.
