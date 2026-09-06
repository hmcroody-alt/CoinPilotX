# DOCUMENT INTELLIGENCE + UNDX — FOUNDATION MAP

**Mission Stage 1 deliverable. Forensic map only — no code was changed by this stage.**

| | |
|---|---|
| Repo | `/Users/hmcherie/Desktop/CoinPilotX` |
| Branch | `main` (ahead of `origin/main` by 8) |
| HEAD at start | `da5d530f44af77ce48d380f68d93aeecfb47f0b6` |
| HEAD at finish | `6aa5670ecfc48cb338ccf50e3f2a06ef1e529063` — **moved mid-session** |
| Working tree | dirty throughout (36 paths), in-progress Private Office work by a concurrent agent |
| Worktrees | 18 |
| Files changed by this stage | 1 (this document) |
| Date | 2026-09-06 |

---

## 0 — HEADLINE FINDING

**Document Intelligence already exists, is already governed, and is already honest about
what it cannot do.** The mission brief describes building a system whose foundations are,
in large part, already in the repository:

- `services/private_office/documents.py` (683 lines) is a single governed writer over
  `private_documents` and `private_document_claims`.
- `services/private_office_documents_routes.py` (312 lines) is the HTTP surface, with
  auth → feature gate → second lock in that order on **every** route.
- `mobile-native/src/screens/PrivateDocumentsScreen.tsx` (463 lines) already does upload,
  list, detail, and per-claim accept/reject.
- Extraction already *proposes*; only explicit member review turns a proposal into a fact,
  written through `facts.record_fact()` with `DOCUMENT_EXTRACTED` provenance and a locator
  back into the source document.

**Consequence: the correct posture is EXTEND, not CREATE.** The brief's own first rule —
"Do not build a second document foundation" — is the operative one, and the risk of
violating it is high, because the brief's language ("turn Document Intelligence from a
passive parser into…") reads as though the parser is all that exists. It is not. The
authority separation the brief demands is already implemented; what is missing is
*capability*, not *governance*.

### Premises in the brief that repository evidence corrects

1. **"Document Intelligence is a passive parser."** It is a governed claim pipeline with a
   review gate, audit rows, evidence locators, and a fact handoff. The parser is one stage
   inside it.
2. **The authority boundaries need to be established.** They already are, and they are
   statically enforced (`tests/private_office/test_private_write_boundary.py`).
3. **`CLAUDE.md` describes the branch state.** It says the tree is dirty on
   `codex/emergency-live-audio-recovery`. It is dirty on `main`, with entirely different
   files, and HEAD moved during this stage. Any plan assuming a specific tree is planning
   against a tree that is not here.
4. **"PULSESOC RTC = AGORA ONLY. Do not introduce LiveKit."** The repository uses LiveKit
   for calls and Live today. This stage reads that constraint as scoped to this mission —
   change zero RTC files, add no new LiveKit — and not as a description of the repo. It is
   worth an explicit confirmation before any stage that touches media.

---

## 1 — THE CANONICAL DOCUMENT

**Table: `private_documents`.** Defined at `services/private_office/documents.py:96-116`.
There is no second document store; no legacy duplicate was found.

Columns: `id`, `owner_user_id`, `title`, `original_name`, `extension`, `mime_type`,
`size_bytes`, `sha256`, `storage_provider`, `storage_key`, `extraction_state`,
`extraction_note`, `domain`, `sensitivity`, `lifecycle_state`, `created_at`, `updated_at`.

**Companion table: `private_document_claims`** (`documents.py:118-132`) holds extraction
output as `PROPOSED` / `ACCEPTED` / `REJECTED`, with `fact_id` linking an accepted claim to
the row it produced in `private_facts`.

**Canonical writer: `services/private_office/documents.py`.** Nothing else writes either
table. Entry points: `store_document()` (`:201`), `process_document()` (`:366`),
`review_claim()` (`:443`), `delete_document()` (`:641`), `fetch_content()` (`:616`).

**The write boundary is statically enforced.** `tests/private_office/test_private_write_boundary.py`
AST-scans the repository and fails if any module outside the writer allowlist emits
INSERT/UPDATE/DELETE against a private table. The guard is itself tested against 13
synthetic violations. This is the single most important fact in this document: a new
Document Intelligence module **cannot** write documents, claims, or facts directly. It must
call the existing writers.

---

## 2 — STORAGE IDENTITY AND CONTENT AUTHORIZATION

Bytes go through `services/media_storage.py`. Local root is `PRIVATE_UPLOAD_ROOT`
(`PRIVATE_MEDIA_UPLOAD_DIR`, default `instance/private_uploads`); object storage is
Cloudflare R2 or S3 via boto3 under `MEDIA_STORAGE_PROVIDER=r2|s3`.

Key format: `private-office/{owner_user_id}/{sha256[:16]}/{sanitized_filename}` — owner is
in the key, so a mis-scoped read is visible in the path itself.

**No public URL scheme serves this prefix.** `public_media_url()` does not generate links
into `private-office/*`. Content leaves only through
`GET /api/private-office/documents/<id>/content` (`private_office_documents_routes.py:220-255`),
which is owner-gated and responds `Cache-Control: no-store, max-age=0, must-revalidate`.
The route docstring states the reasoning plainly: a cached private document is a leaked
private document.

**Upload limits are server-side and real.** `MAX_DOCUMENT_BYTES = 20 MiB`
(`documents.py:75`); the route reads `MAX_DOCUMENT_BYTES + 1` specifically so it can detect
an oversized file rather than truncate it. Type allowlist is by **extension**, not by
client-declared MIME — `ALLOWED_EXTENSIONS` (`documents.py:60-70`) is
`pdf, png, jpg, jpeg, heic, txt, md, csv, json`, and the comment states that the client's
declared MIME is advisory and is not stored as truth. Anything else raises
`PrivateDocumentRejected`.

---

## 3 — ROUTES AND LOCK ORDERING

All six routes live in `services/private_office_documents_routes.py` and share one entry
helper, `_entry()` (`:64-77`):

```
1. po_http._current_user()          → 401 if absent
2. po_http._resolve_for(user)
   po_http._gate(resolved, "private_office.document.extraction")   → refusal if gated
3. po_http._office_lock_gate(user)  → 423 if the second lock is not open
```

**No data is read before the lock.** That ordering — AUTH → ENTITLEMENT → LOCK → READ — is
exactly what the brief's Stage 65 asks for, and it is already the implementation.

| Route | Method | Notes |
|---|---|---|
| `/api/private-office/documents` | GET | list + `provider_status` + `limits`; audit row `DOCUMENT_READ` |
| `/api/private-office/documents` | POST | multipart, validated, SHA-256 deduplicated, processed eagerly |
| `/api/private-office/documents/<id>` | GET | detail + claims, PROPOSED first |
| `/api/private-office/documents/<id>/content` | GET | owner-only byte stream, `no-store` |
| `/api/private-office/documents/<id>` | DELETE | soft delete |
| `/api/private-office/claims/<id>/review` | POST | `{"decision":"accept"\|"reject"}` — the only extraction→fact path |

**Second lock mechanics** (`services/private_office_routes.py:303-331`): passcode must be
set (423 signalling setup required if not), grant token arrives in a request header, is
validated against session and device binding, and expires (15 min default, 1–24 h
configurable). Owner lifetime Premium satisfies the *entitlement* check at step 2 and has
no effect at step 3 — which is precisely the separation the brief requires.

---

## 4 — ACL, OWNER SCOPING, SENSITIVITY

Owner scoping is **structural, not a runtime check**: `owner_user_id = ?` is a clause of
every query in `documents.py`. `get_document()` selects `WHERE id = ? AND owner_user_id = ?`.
Cross-owner access is not refused; it is unrepresentable.

Sensitivity tiers exist in `services/private_office/model.py:80-99`: `PUBLIC`, `INTERNAL`,
`CONFIDENTIAL`, `HIGHLY_SENSITIVE`, `RESTRICTED`, with a `SENSITIVITY_RANK` ordering.
Documents carry a `sensitivity` column and default to `CONFIDENTIAL` on upload
(`documents.py:225-226`).

**GAP.** The sensitivity *ceiling* is enforced in `services/private_office/retrieval.py`
for facts and graph reads. It is **not** enforced on the document list/detail/content
routes. A document tagged `RESTRICTED` is returned to its owner exactly like an `INTERNAL`
one. For an owner-only surface this is defensible today; it stops being defensible the
moment document content enters a shared UNDX context, which is what this mission does.

---

## 5 — DELETE AND ARCHIVE SEMANTICS

`lifecycle_state` is `ACTIVE` or `DELETED`. `delete_document()` (`:641`) sets `DELETED`,
best-effort removes the local bytes, and **keeps the row** so accepted claims retain a
resolvable provenance trail. `list_documents()` filters to `ACTIVE`.

**There is no `ARCHIVED` state.** The brief's "archived ≠ deleted" requirement has nothing
to distinguish yet.

**There is no cascade.** Claims survive with a `document_id` pointing at a `DELETED` row;
facts survive with `DOCUMENT_EXTRACTED` provenance and a locator into content that no
longer exists. R2/S3 objects are not deleted at all (`documents.py:661-664`).

**GAP — the largest one on the evidence side.** Nothing anywhere expresses
`SOURCE_UNAVAILABLE`. A fact whose document was deleted looks identical to a fact whose
document is intact, and `evidence.resolve_refs()` will report the deleted document's ref as
`exists=False` without that reaching any caller as a state. The brief's Stage 17 asks for
exactly this, and it is genuinely absent.

---

## 6 — EXTRACTION AND OCR AS THEY STAND

**Deterministic extraction, text formats only.** `EXTRACTABLE_EXTENSIONS = (txt, md, csv, json)`
(`documents.py:72`). `extract_pairs()` (`:351`) does line regex for text/markdown, csv
reader for CSV, flattening for JSON, producing `(key, value, locator)` tuples. Bounds:
`MAX_CLAIMS_PER_DOCUMENT = 20`, `MAX_CLAIM_VALUE_CHARS = 200`.

**Extraction states** (`documents.py:77-86`): `EXTRACTED`, `NO_CLAIMS`, `PROVIDER_REQUIRED`,
`FAILED`. PDFs and images are stored, listed and streamed, and marked `PROVIDER_REQUIRED`
with the note that OCR/PDF extraction requires a provider that is not integrated.

**No PDF or OCR library is installed.** `requirements.txt` contains no pypdf, PyPDF2,
pdfplumber, fitz/PyMuPDF, pytesseract, or vision client. The module docstring says this
outright and explains the choice: *"a fabricated extraction would be worse than none."*

This is the honest-failure discipline the brief's Stage 9 demands, already in place — and
it is also the hard blocker on the brief's core promise. **The mission asks for
understanding of PDF contracts and scanned images. There is currently no way to read a
single byte of text out of either.** Closing that requires either a new dependency or a
provider integration, and it is a decision, not an implementation detail.

**No async pipeline.** Extraction runs eagerly inside the upload request. There is no
`PROCESSING` state and no job for reprocessing. `services/private_office/jobs.py` is
imported by `documents.py` but the path is synchronous. A 20 MiB PDF through an OCR
provider cannot run this way.

---

## 7 — THE TRUTH AUTHORITY: PRIVATE FACTS

Writer: `services/private_office/facts.py`, `_record_fact()` (`:650-672`). Schema:
`services/private_office/schema.py:122-153`.

**Provenance vocabulary** (`model.py:117-165`) — 11 source values plus 2 derived:
`VERIFIED`, `PROVIDER_ASSERTED`, `DOCUMENT_EXTRACTED`, `SYSTEM_OBSERVED`, `HUMAN_CONFIRMED`,
`USER_ASSERTED`, `MEETING_DERIVED`, `INFERRED`, `UNDX_PROPOSED`, `ESTIMATED`,
`LEGACY_UNKNOWN`; derived-only `STALE` and `CONFLICTING`.

> The brief names `DOCUMENT_DERIVED`. **It does not exist.** `DOCUMENT_EXTRACTED` (strength
> 60) is the existing term and it already carries the required meaning. Introducing
> `DOCUMENT_DERIVED` alongside it would split one concept across two vocabularies, which is
> the drift the brief elsewhere forbids. Recommend reusing `DOCUMENT_EXTRACTED` and, if a
> distinction between *parsed* and *model-inferred* is wanted, using the existing
> `UNDX_PROPOSED` for the latter — it exists precisely for this.

**Verification states** (`model.py:217-245`) are a separate axis from provenance, which
matters for the brief's "high confidence ≠ verified" rule — the separation is already
modelled. Active: `UNVERIFIED` (default), `USER_CONFIRMED`, `EVIDENCE_SUPPORTED`,
`VERIFIED`, `PROVIDER_VERIFIED`, `NEEDS_REVIEW`. Terminal: `CONFLICTING`, `DISPUTED`,
`SUPERSEDED`, `EXPIRED`, `REVOKED`, `LEGACY_UNKNOWN`.

**Freshness horizons** (`facts.py:170-197`): `VERIFIED` 90 days, `DOCUMENT_EXTRACTED` 365
days, `LEGACY_UNKNOWN`/`STALE` born stale.

**Supersession** (`facts.py:959-1000`): bidirectional `supersedes_id` / `superseded_by_id`,
history preserved on both ends, no cascade.

---

## 8 — EVIDENCE

`services/private_office/evidence.py` (164 lines). **There is no evidence table.** Evidence
is a *reference contract*: refs are `"kind:id"` strings, packed as a JSON array into the
citing row's column.

Eleven kinds: fact, node, edge, obligation, event, decision, request, risk, opportunity,
document, finding, briefing. `resolve_refs(cur, owner_user_id, refs)` returns
`{ref, kind, id, exists, label}` per ref and is owner-scoped — a cross-owner ref resolves to
`exists=False` rather than leaking a label.

**Locators** live in the fact's `provenance_ref` (`ProvenanceRef` at `facts.py:200-250`)
with `source_type`, `source_id`, `locator`. Document claims populate this on acceptance
(`documents.py:481-485`).

**GAP.** The locator today is whatever the text extractor produced — a line number or a CSV
cell. There is **no page/section anchor model**, because there is no paginated extraction.
The brief's evidence-location mapping (Stages 6–8, 16) has to introduce that, and it should
extend `ProvenanceRef.locator` rather than add a parallel structure.

---

## 9 — CONTRADICTIONS AND REVIEW

**Contradictions:** `services/private_office/contradictions.py`. Temporal-overlap gated
(`windows_overlap`, `:123-169`) — an open-ended earlier fact implicitly closed by a later
assertion is *not* a conflict. Value comparison (`materially_incompatible`, `:180-230`)
uses relative tolerance for money/numbers (0.5%), absolute for percentages (±1%), zero for
dates; text containment is elaboration, not conflict. `conflict_id` is a deterministic hash,
so re-runs are stable. `mark_conflicts()` stamps the id and deliberately does **not**
rewrite `provenance_type` — the explanation survives the conflict.

**Review:** there is no generic review queue. Document claims use the inline path
`review_claim()` (`documents.py:443-513`). The attention queue in
`services/private_office/operations.py:269-310` covers records, not claims.

> The brief warns against building a second review queue. The concrete risk here is the
> opposite of what it anticipates: there is **no** queue to duplicate, and the temptation
> will be to build one. Claims are reviewed inline on the document detail screen. If
> volume makes that insufficient, the extension point is `briefings.py` (which already
> composes unreviewed claims into a briefing) and `shield.py` (which already emits an
> unreviewed-claim finding) — not a new queue.

---

## 10 — DOWNSTREAM AUTHORITIES

**Operations** — primitives in `services/private_office/records.py`: six types
(OBLIGATION, EVENT, DECISION, REQUEST, RISK, OPPORTUNITY), lifecycle ACTIVE/SUPERSEDED/
ARCHIVED, derived status computed at read. `operations.py` is read-only and owns attention
classification. **GAP: there is no proposal→confirmation path.** `records.create_record()`
writes directly. The brief's "obligations become Operation *proposals*" has nothing to
attach to; either the proposal state is added to `records.py` (extending the canonical
writer) or document-derived obligations stay as facts until a member acts. The former is
correct; the latter is what exists.

**Capital Graph** — `capital_graph.py` performs no writes at all. It reads through
`retrieval.retrieve()` only, and therefore sees ACTIVE facts filtered by owner, sensitivity,
domain and purpose. There is an explicit `NO_AGGREGATE_VALUE` stance. The brief's "Capital
Graph only via confirmed facts" is structurally already true — the module cannot be written
to directly.

**Relationship Intelligence** — `relationships.py`. A person is a `PERSON` graph node;
everything about them is facts with `subject_type="NODE"`. **There is no entity resolution.**
`add_person()` creates a node per call; deduplication is member-driven. So the brief's
"resolve an extracted entity to an existing canonical person without auto-creating one" has
no matcher to call — it must be built, and it must default to *proposing* a match.

**Briefings** — `briefings.py`, owns `private_office_briefings` and `_briefing_items`,
composes deterministically and already includes unreviewed document claims, with
`evidence.pack_refs()` citations.

**Shield** — `shield.py`, owns `private_shield_findings`, member-triggered, deduplicating,
lifecycle OPEN → ACKNOWLEDGED → RESOLVED/DISMISSED. Already emits findings for unreviewed
claims and extraction gaps.

---

## 11 — THE UNDX LAYER

**Registry:** `services/undx_capability_registry.py`. A capability is a `CapabilitySpec`
(`:45-93`) with 20 fields — `capability_id`, `description`, `intents`, `risk`,
`confirmation`, `tool_name`, `permission`, `fields`, `executor`, `verifier`, `native_route`,
`result_card`, `audit_category`, plus write-only fields (`target_field`, `verified_fields`,
`undo_capability_id`, `undo_argument_map`) and behaviour flags.

**Existing `private.*` capabilities: exactly one — `private.facts.list`** (`:1158`). By
comparison the registry holds 21 crypto, 11 feed, 7 marketplace, 6 messages capabilities.
**No document capability exists.** All eight the mission names must be added.

**Adding a capability touches, at minimum:** the registry; `services/undx_knowledge_map.py`
(a `ProductCapabilityRecord`, `:337+`); `services/undx_agent_tools.py` (executor);
`services/undx_verification.py` (verifier — empty string for read-only);
`services/undx_agent_policy.py` (only if new gating is needed); and the test estate.

**Read-only vs write:** `RiskLevel.READ_ONLY` is the default (`agent_policy.py:124`);
`is_write` (`:131`) is a rank comparison. All eight document capabilities should be
`read_only` with `verifier=""`.

**Safety precedence** (`agent_policy.py:28-72`) is a strict hierarchy —
`UNDX_EMERGENCY_KILL_SWITCH` > `UNDX_WRITE_KILL_SWITCH` > `UNDX_AGENT_DISABLE_WRITES` >
`UNDX_V4_DISABLE_WRITES` > per-mode enables > per-capability allow/deny — plus
`REQUIRED_WRITE_GUARDS`, seven invariants that fail closed. Covered by
`tests/undx_agent/test_safety_precedence.py`.

**Native routes:** `undx_knowledge_map.py:226` already declares
`"PrivateDocuments": "/pulse/private-office/documents"`. The route exists; nothing points
at it.

**Retrieval bounds already exist** in `services/private_office/retrieval.py:73-80` —
`MAX_DEPTH=3`, `MAX_NODES=100`, `MAX_EDGES=250`, `MAX_FACTS=400`, surfaced via a `truncated`
flag. Five authorization gates run in order (`:18-31`): owner-scoped by construction, actor
must be owner, sensitivity ceiling, domain join policy, purpose from a closed vocabulary.
Six intents (`:88-96`) including `INTENT_LEGAL_DOCUMENTS`.

> The brief's bounded-retrieval constants (`MAX_DOCUMENT_RESULTS`, `MAX_DOCUMENT_CHUNKS`,
> `MAX_CHUNKS_PER_DOCUMENT`, `MAX_PAGES_PER_QUERY`, `MAX_CROSS_DOCUMENT_CONTEXT`,
> `MAX_COMPARISON_DOCUMENTS`) belong **inside `retrieval.py` alongside the existing five**,
> not in a new module. That is the one sanctioned way out of the package.

**Prompt-injection defense: effectively absent.** What exists is input hygiene, not
injection defense: `clean()` (`undx_agent_contracts.py:376-379`) collapses a value to a
bounded single line; `_clean_text()` (`undx_router.py:255-257`) strips tags from history;
`validate_arguments` refuses free-form tool payloads; deep-link substitution is
character-class restricted; a canonical argument hash binds a confirmation to exact
arguments. **None of this addresses document text as untrusted content entering a model
prompt**, because no document text has ever entered one. This is new work, and it is the
highest-risk item in the mission: an uploaded PDF is attacker-controlled the moment a
member is emailed one.

**Model routing:** `undx_router.py:46-102`, five providers with OpenAI as the implicit
fallback lane (`:200`). `_safe_error()` (`:137-158`) redacts keys from exception text.
There is no retry or degrade path and no test of the fallback.

**Drift risk, named:** there is **no test asserting registry ↔ knowledge-map parity.**
`tests/test_undx_platform_knowledge.py` checks manifest count floors, not membership. The
mission demands coherence across seven declaration sites and the repository currently has
no mechanism that would notice incoherence. **Writing that parity test is a prerequisite,
not a Stage 102 deliverable** — without it, "no drift" is an assertion rather than a
property.

---

## 12 — THE NATIVE SURFACE

`mobile-native/src/screens/PrivateDocumentsScreen.tsx` (463 lines) already renders upload
(document picker, `:157-175`), the list with per-document extraction-state icons
(`:185-196`), and a detail panel with per-claim accept/reject writing straight to the
server (`DocumentDetail`, `:266-382`). It is wrapped in `PrivateOfficeLockGate` (`:61-67`).
Registered in `AppNavigator.tsx:43`.

Sibling screens: `PrivateOfficeScreen`, `PrivateFactsScreen`, `PrivateOperationsScreen`,
`PrivatePeopleScreen`, `PrivateBriefingsScreen`, `PrivateShieldScreen`,
`PrivateConciergeScreen`, `PrivateMeetingsScreen`, `PrivateMeetingRoomScreen`,
`PrivateOfficeSecurityScreen`.

**Grant handling:** `mobile-native/src/privateOffice/officeLock.ts`. Headers `X-Office-Grant`
and `X-Office-Device` (`:40-41`). The grant is held **in memory only** (`:89`, `:98`) and
attached by `officeRequestHeaders()` (`:161-168`). It therefore persists across screens.
Across backgrounding it depends on a relock preference (`:212-226`, default `immediate`) via
`noteOfficeBackgrounded()` / `noteOfficeForegrounded()` (`:233-249`); the server TTL is the
real lock and the local clear only prevents a stale frame. Device id is persisted in
SecureStore (`:184-208`) and bound server-side.

**Error model:** `mobile-native/src/privateOffice/FeatureStatePanels.tsx` gives shared
`FeatureRefusalPanel` (`:52-82`), `FeatureLoadingPanel` (`:42-50`), `FeatureEmptyPanel`
(`:84-92`). `privateFeatures.ts` `refusal()` (`:49-65`) maps 503/504 → `UNAVAILABLE`,
423 → `LOCKED`, network failure → `ERROR`. The states `READY`, `NOT_ENTITLED`,
`FEATURE_DISABLED`, `NOT_IMPLEMENTED`, `UNAVAILABLE`, `ERROR`, `LOCKED` render as visually
distinct panels in `PrivateDocumentsScreen` (`:154-212`).

**The false-empty risk is real but narrower than the brief assumes.** A refusal does not
collapse into empty — that is already handled. What collapses is a **404**, and any status
the `refusal()` map does not name: those fall through, and a `READY` result with a
zero-length array renders `FeatureEmptyPanel` — "No documents yet" — regardless of why the
array is empty (`:143-149`, `:177-182`). There is no `PROCESSING` state on the client
because there is none on the server. Both need adding.

**Feature flags:** server-side entitlement lives in `services/private_office/feature_matrix.py`
and is never exposed to the client — mobile learns availability only from the state word in
a response. Separately, `mobile-native/src/launch/readiness.ts` is a hard-coded client table
(`READY` / `BUILDING` / `COMING_SOON`, `:63-107`) in which Private Office does not appear,
so it defaults to `READY`. The document flag is `private_office.document.extraction`,
server-side only.

**Backend OS:** `services/backend_management_registry.py` maps 100+ features to admin
routes, owners, audit tables and risk levels. `services/private_office/health.py` runs four
distinct checks (entitlement resolver, schema, fact/graph volume, retrieval refusals) and
returns `HEALTHY` / `DEGRADED` / `UNAVAILABLE`; counts return `None`, not `0`, when they
could not be taken — the anti-cosmetic-green discipline the brief asks for, already
implemented. `services/private_office/status.py` deliberately takes **no user identifier**
so it cannot be used as an entitlement oracle.

**i18n:** 11 locales (`en, es, fr, ht, pt, de, ar, hi, ja, ko, zh`), split core (common,
auth, errors) and extended, with an explicit loader map because Metro cannot resolve a
computed `require` (`catalogs/index.ts:87-99`). Coverage is CI-gated —
`i18n/__tests__/coverage.test.ts` fails on any missing **or** orphaned key, with plural-family
collapsing per language. `scripts/find-hardcoded-strings.mjs` is the hardcoded-string gate.
Document keys already exist under `premium.privateOffice.documents.*` (en/extended.json
`:4770-4828`) covering upload, duplicate, empty, the four extraction states, and the three
claim statuses.

**Commands:** `npm run typecheck` (`tsc --noEmit`), `npm run i18n:validate`,
`npm run i18n:hardcoded`, `npm test` (jest), `npm run verify` (typecheck + i18n:validate +
jest). Jest must be sharded to fit the 45 s sandbox ceiling — one shard per invocation.

**Tests:** mobile — `PrivateFactsScreen.test.tsx`, `PrivateOfficeScreen.test.tsx`,
`PrivateOperationsScreen.test.tsx`, `privateRecords.test.ts`, `privateOfficeSecurity.test.ts`.
Backend — 22 files under `tests/private_office/`, including `test_private_documents.py`
(vault, claim pipeline, HTTP gate order, extraction state, provider edge, owner isolation,
payload projection) and `test_private_write_boundary.py`. Note: **`pytest` is not installed
in this sandbox**; use `python3 -m unittest`.

---

## 13 — GAP LIST

Ordered by how much of the mission each blocks.

| # | Gap | Where it lands | Severity |
|---|---|---|---|
| 1 | No PDF or OCR capability of any kind; no library, no provider | `documents.py`, `requirements.txt` | **Blocks the mission's core promise.** A decision, not a task |
| 2 | No prompt-injection defense for document text entering a model prompt | new; `undx_agent_contracts.py` adjacent | **Highest risk.** Attacker-controlled input by design |
| 3 | No registry ↔ knowledge-map parity test | `tests/` | **Prerequisite.** Without it "no drift" is unverifiable |
| 4 | No `SOURCE_UNAVAILABLE`; deleted document leaves facts looking intact | `model.py`, `evidence.py`, `facts.py` | High |
| 5 | Synchronous extraction only; no `PROCESSING` state, no reprocessing job | `documents.py`, `jobs.py`, routes, native | High — OCR cannot run in-request |
| 6 | 404 and unmapped statuses collapse into `FeatureEmptyPanel` | `privateFeatures.ts`, `PrivateDocumentsScreen.tsx` | High |
| 7 | No page/section anchor model; locators are line/cell only | `ProvenanceRef.locator` | High — citations are the mission's honesty mechanism |
| 8 | Zero `private.documents.*` capabilities registered | registry + 6 sites | High — the whole UNDX arc |
| 9 | No Operation proposal state; `create_record()` writes directly | `records.py` | Medium — obligations arc depends on it |
| 10 | No entity resolution; `add_person()` creates unconditionally | `relationships.py` | Medium |
| 11 | Sensitivity ceiling not enforced on document routes | documents routes | Medium — becomes serious once content reaches UNDX |
| 12 | No `ARCHIVED` lifecycle state for documents | `documents.py` | Low |
| 13 | R2/S3 objects are never deleted on document delete | `documents.py:661-664` | Low but a real retention defect |
| 14 | No model-failure retry or degrade path, and no test of the OpenAI fallback | `undx_router.py` | Low |
| 15 | Semantic search: no embedding store, no chunking, nothing | new | Scope question, not a defect |

---

## 14 — WHAT THIS STAGE DID NOT ESTABLISH

- Nothing was run. No test was executed, no route was called, no device was touched. This
  is a reading of source, and source can be stale — `records.py`'s own docstring was found
  stale in a prior mission for exactly this reason.
- The working tree was dirty and HEAD moved during the stage. Line numbers cited here are
  against `6aa5670e` and a tree containing another agent's uncommitted edits to
  `records.py`, `obligation_projection.py`, `portfolio_projection.py`,
  `private_office_routes.py`, `privateRecords.ts`, `PrivateOperationsScreen.tsx`, and all
  11 i18n extended catalogs. Anything in this map that touches those files should be
  re-read before it is edited.
- Whether `private_office.document.extraction` is enabled in production is unknown.
- Whether the Postgres schema matches the SQLite DDL in `documents.py` was not verified.

---

## 15 — CARRIED-FORWARD DEBT FROM THE PRIOR MISSION

Unrelated to this mission but still owed, and recorded here so it is not lost: the live
audio work is committed at `997b8869` on `codex/emergency-live-audio-recovery` with six
unpushed commits. The push is proxy-blocked from this sandbox and must be run from the
Mac. Physical device validation of that fix has not been performed.
