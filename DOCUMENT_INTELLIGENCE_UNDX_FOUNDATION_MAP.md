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

---
---

# ADDENDUM A — INDEPENDENT RE-VERIFICATION

Added 2026-09-06 against `main` @ `6ebc1cf2`. Method: source inspection only, plus
`git show` against the map's own commit to distinguish *stale* entries from *wrong* ones.

HEAD moved to `1703281e` during this addendum — the shared checkout again. Re-checked:
`git diff 6ebc1cf2..1703281e` touches **none** of the source files cited below
(`documents.py`, `facts.py`, `retrieval.py`, `evidence.py`, `schema.py`,
`undx_feature_reads_spec.py`, `undx_policy.py`, `undx_knowledge_map.py`,
`private_office_documents_routes.py`), so every line number here holds at `1703281e`.
`health.py` did change (`3d42a72c`, Operations reporting); gap A-e was re-verified against
it and still holds.

§14 above asked for exactly this: *"Anything in this map that touches those files should be
re-read before it is edited."* This addendum is that re-read. **The map above is sound in
its architecture, its posture (EXTEND, not CREATE), and its lock/writer/provenance
analysis.** What follows corrects five specific claims and adds one finding that changes
the mission's sequencing.

Nothing above has been deleted. Corrections are recorded here so the original reasoning
and the correction sit side by side.

---

## A1 — NEW FINDING: document facts are unreachable through graph retrieval

**Not recorded anywhere in this repository before now. This is a defect in shipped
behaviour, not a missing feature.**

`documents.review_claim()` writes accepted claims with a bare string literal:

```python
# services/private_office/documents.py:475
subject_type="OWNER",
subject_id=str(owner),
```

Every other writer in the package uses the constant `facts.SUBJECT_NODE` (`= "NODE"`,
`facts.py:80`) — `relationships.py:112,121,161,187,318` and
`obligation_projection.py:196,209,219,508,616`. Documents is the **sole** exception, and
`"OWNER"` is not a declared constant anywhere in the package.

The write succeeds silently because `facts.record_fact()` does not validate `subject_type`
against a closed set — it uppercases and truncates to 32 chars (`facts.py:844-846`) and
accepts anything non-empty. There is no `SUBJECT_TYPES` tuple in `model.py`.

**The consequence.** `retrieval.retrieve()` — the single gated read path that Capital
Graph, the obligation projector and every reasoning intent go through — filters facts with
a hard `subject_type=SUBJECT_TYPE_NODE` (`retrieval.py:457` and `:498`, where
`SUBJECT_TYPE_NODE = "NODE"` at `:84`).

> **No fact produced by accepting a document claim has ever been reachable through
> `retrieval.retrieve()`.**

Compounding it: `review_claim()` *does* create a `NODE_DOCUMENT` graph node for the
document (`documents.py:491-497`), but the fact it just wrote does not point at that node.
The node has no facts; the facts have no node.

**Scope of the damage.** Partial, and worth stating precisely rather than alarmingly.
`facts.list_facts()` treats `subject_type` as an *optional* filter (`facts.py:1748`,
`1781-1783`), and the UNDX `private_facts_list` executor passes none
(`undx_agent_tools.py:2654-2660`). So document facts **are** visible in the flat fact list.
They are unreachable *through the graph*, not unreachable entirely.

**Why this changes sequencing.** §10 above concludes that the brief's "Capital Graph only
via confirmed facts" is *"structurally already true — the module cannot be written to
directly."* That is correct about writes and incomplete about reads: document facts never
arrive there at all. Any "ask a question about your documents" surface built on
`retrieval.retrieve()` will return nothing from documents and will do so silently — the
exact error/empty conflation this codebase is disciplined about everywhere else.

**This should be resolved before UNDX document capability work begins.** It is also a
decision, not a task: attaching document facts to the `NODE_DOCUMENT` node changes what
`retrieve()` returns for existing members, and the two candidate fixes (re-subject the
facts vs. teach retrieval a second subject axis) have different blast radii.

---

## A2 — CORRECTION to gap #8: documents **are** registered with UNDX

Gap #8 reads *"Zero `private.documents.*` capabilities registered … High — the whole UNDX
arc."* **This is incorrect, and it was incorrect when written.**

`private.documents.list` is registered. It is declared in
`services/private_office/undx_feature_reads_spec.py:47-58` — a module §11 above did not
examine, having looked only at `services/undx_capability_registry.py`. The five Private
Office feature reads (documents, people, briefings, shield, concierge) are declared there
and **derive** into the other surfaces:

| Surface | Where |
|---|---|
| Policy table | `undx_policy.py:85` → `pulsesoc.private_documents.list`, `risk: read_only`, `confirmation: False` |
| Knowledge map — output schema | `undx_knowledge_map.py:2842-2849` |
| Knowledge map — screen | `undx_knowledge_map.py:2886` → `PrivateDocuments` |
| Knowledge map — service | `undx_knowledge_map.py:2895` → `services.private_office.documents` |
| Executor name | `undx_feature_reads_spec.py:131` → `private_documents_list` |

The spec's docstring states the design intent (lines 5-9): *"three registration surfaces
that agree by construction cannot drift apart by review."*

**Why this correction matters more than its size suggests.** A reader acting on gap #8 as
written would register `private.documents.list` a second time, in the wrong module,
breaking the single-declaration invariant that the spec exists to hold — which is the
"do not build a second foundation" failure in miniature.

**The real gap is narrower and should replace #8:** the one registered capability is a
read of *document metadata* — `id, title, original_name, extension, mime_type, size_bytes,
extraction_state, extraction_note, domain, sensitivity, created_at, updated_at`. It
returns no claims, no extracted values and no evidence. There is no capability that can
answer *"what obligations do I have?"*, *"when does this expire?"* or *"where did this fact
come from?"*. **New capabilities must be added to `undx_feature_reads_spec.py`'s
`CAPABILITIES` tuple, not to a new registry.**

Note also gap #3 (*"No registry ↔ knowledge-map parity test"*) is partly satisfied for
these five: `tests/private_office/test_private_records_undx_spec.py:108-164` guards spec
drift and a `WIRING_COMPLETE` flag, asserting `stage_deferral_is_honest`.

---

## A3 — CORRECTION to gap #13: object-storage deletion exists

Gap #13 reads *"R2/S3 objects are never deleted on document delete … `documents.py:661-664`
… a real retention defect."* **This is incorrect, and `git show 62d8ad5f` confirms it was
incorrect at this map's own commit.**

The cited lines `661-664` are the *local* unlink. The object-storage delete is the block
immediately below, at `documents.py:667-676`:

```python
if document.get("storage_provider") in {"r2", "s3"}:
    try:
        client = media_storage.object_client()
        if client is not None:
            import os as _os
            client.delete_object(
                Bucket=_os.getenv("R2_BUCKET") or _os.getenv("S3_BUCKET"),
                Key=storage_key)
    except Exception:
        pass
```

There is no retention defect. Both copies are removed, best-effort, and the row survives
soft-deleted so provenance stays resolvable.

The residual observation worth keeping: both deletes are `except Exception: pass`, so a
failed object-storage delete leaves bytes behind **silently**. That is a real but much
smaller gap than "never deleted" — it is an observability gap, not a retention gap.

---

## A4 — CORRECTION to gap #4: `SOURCE_UNAVAILABLE` now exists (stale, not wrong)

Gap #4 reads *"No `SOURCE_UNAVAILABLE`; deleted document leaves facts looking intact."*
This was accurate when written and has since been closed by `89251ec3`
(*"evidence availability as a third, independent axis"*, 2026-09-06) — the day **after**
this map was committed in `62d8ad5f`.

`services/private_office/evidence.py` now defines a full availability vocabulary:

```
AVAILABILITY_AVAILABLE          :102
AVAILABILITY_ARCHIVED           :105
AVAILABILITY_SUPERSEDED         :109
AVAILABILITY_EXPIRED            :112
AVAILABILITY_SOURCE_UNAVAILABLE :116
AVAILABILITY_NOT_FOUND          :119
AVAILABILITY_UNKNOWN            :124
```

with `availability_for()` (`:248`), `is_resolvable()` (`:273`), `may_verify()` (`:278`),
`historical_attribution()` (`:289`) and `resolve_refs()` (`:357`).

The delete semantics in §5 above now interlock with it correctly: `get_document()` filters
to `ACTIVE` and returns `None`, but `resolve_refs()` deliberately does **not** filter on
lifecycle, so a soft-deleted document still resolves and reports a non-`AVAILABLE`
availability. A fact from a deleted document keeps its citation, and that citation
truthfully says the source is gone.

Fail-closed twice over (`evidence.py:248-270`): not found → `NOT_FOUND`; found but with an
unrecognised lifecycle → `UNKNOWN`, never `AVAILABLE`. A ref naming another member's row
resolves identically to a ref naming nothing — no label, lifecycle or availability leaks.

**Gap #4 should be struck.**

---

## A5 — CORRECTION to gap #7: page/section citation needs no schema work

Gap #7 reads *"No page/section anchor model; locators are line/cell only … High —
citations are the mission's honesty mechanism."* **Half right.**

Correct at the extraction layer: claim locators are `line=N`, `row=N`, `key=X` only
(`documents.py:311-348`), because the extractor only reads text formats.

Incorrect at the evidence layer: `private_fact_evidence` already carries `locator` **inside
its UNIQUE key** (`schema.py:459`):

```sql
UNIQUE(owner_user_id, fact_id, evidence_type, evidence_ref, locator)
```

The schema comment states the reasoning explicitly — *"page 4 of the schedule" and "page 11
of the schedule" are two distinct citations of one document*, and collapsing them would
make the second attachment look like a duplicate of the first and be silently dropped.

**So page- and section-level citation is already supported by the store. No migration and
no "anchor model" is required.** Given there is no migration framework in this repo, that
is a meaningful reduction in scope. What remains is producing page anchors during
extraction — which is downstream of the OCR decision (gap #1), not independent of it.

---

## A6 — CORRECTION to premise #4: RTC is **Agora**, not LiveKit ⚠ safety-relevant

§0 premise-correction #4 states: *"The repository uses LiveKit for calls and Live today.
This stage reads that constraint as scoped to this mission … and not as a description of
the repo."*

**This is wrong, and it inverts a safety constraint.** Verified on `6ebc1cf2`:

```
mobile-native/package.json:71   "react-native-agora": "4.6.2"
requirements.txt:19             agora-token-builder==1.0.0
mobile-native/patches/          react-native+0.81.5.patch   (only patch present)
```

There is no `@livekit` package, no LiveKit Python dependency, and no LiveKit patch. The
brief's *"PULSESOC RTC = AGORA ONLY. Do not introduce LiveKit"* is an accurate description
of the repository, not a mission-scoped constraint layered over a LiveKit codebase.

The danger in leaving this uncorrected is specific: a reader told LiveKit is already
present may add a LiveKit dependency believing it introduces nothing new. It would be a
new RTC stack alongside Agora.

(`CLAUDE.md` makes the same error, describing LiveKit for calls/live. It is stale there
too. Untracked `tests/test_pulsesoc_call_livekit_grants.py` in the working tree is foreign
work referencing a package that is not installed — flagged, not touched, not this
mission's.)

---

## A7 — GAPS NOT IN THE LIST ABOVE

| # | Gap | Where | Severity |
|---|---|---|---|
| A-a | **sha256 dedupe is a check, not a constraint.** No `UNIQUE(owner_user_id, sha256)`; dedupe is a read-then-write probe (`documents.py:229-239`). Two concurrent identical uploads can both insert, defeating the module's own stated invariant that one document means one reviewable row | `documents.py:96-116` | Medium |
| A-b | **Reprocessing is not idempotent.** No UNIQUE on `(document_id, fact_type, locator)` in the claims table, so re-running `process_document()` duplicates every claim | `documents.py:118-133` | Medium — becomes live the moment OCR ships and documents need re-extraction |
| A-c | **`claims_created` counts wrong.** `COUNT(*) … WHERE owner_user_id=? AND document_id=?` (`documents.py:410-415`) returns *all* claims for the document, not the ones this run inserted. A second run reports the cumulative total as though new | `documents.py:410-415` | Low today, load-bearing with A-b |
| A-d | **Documents are not registered in Backend OS.** No `private_office.document.*` entry in `services/backend_management_registry.py` — no admin surface, no declared risk level, no named `audit_log_table`, no owner | `backend_management_registry.py` | Medium |
| A-e | **`private_office_health` has no documents section.** Schema, substrate, retrieval, meetings, operations, telemetry and entitlement are covered; the vault is not. No extraction-state distribution, no claim backlog | `services/private_office/health.py` | Low |
| A-f | **No `document → conversation` linkage is ever created.** `conversations.link(link_type="DOCUMENT", target_id=doc_id)` exists and is idempotent (`conversations.py:572-629`), but nothing in the document path calls it. No document↔meeting attachment table exists at all | `conversations.py`, `meetings.py` | Low |

---

## A8 — REVISED GAP ORDERING

Merging §13 with this addendum, and ordering by what blocks what:

| Rank | Gap | Note |
|---|---|---|
| 1 | **A1 — document facts unreachable via `retrieve()`** | New. A shipped defect. Blocks every retrieval-backed answer; must precede capability work |
| 2 | #1 — no PDF/OCR capability | Unchanged. A decision, not a task |
| 3 | #2 — no prompt-injection defence for document text | Unchanged. Theoretical until a capability returns content; load-bearing the instant one does |
| 4 | #8 **as revised** — no document *reasoning* capability | Registration exists; **extend `undx_feature_reads_spec.py`**, do not create a registry |
| 5 | #5 — synchronous extraction only; no `PROCESSING` state | Unchanged. OCR cannot run in-request |
| 6 | #6 — 404 / unmapped statuses collapse into `FeatureEmptyPanel` | Unchanged. The error-never-empty rule |
| 7 | A-b / A-c — reprocessing not idempotent, count wrong | Prerequisites for re-extraction, therefore for #1 |
| 8 | #9 — no Operation proposal state | Unchanged |
| 9 | #10 — no entity resolution | Unchanged; must default to *proposing* a match |
| 10 | A-a, A-d, #11, #12, A-e, A-f | Medium/low |
| — | #3 | Partly satisfied — see A2 |
| — | #4 | **Struck** — closed by `89251ec3` |
| — | #7 | **Reduced** — evidence layer already supports page locators |
| — | #13 | **Struck** — object deletion exists; residual is silent-failure observability |

---

## A9 — WHAT THIS ADDENDUM DID NOT ESTABLISH

- Still nothing was run. No test executed, no route called, no device touched. §14's caveat
  stands unchanged.
- **A1's blast radius was not measured.** Whether any member has accepted document claims
  in production — and therefore how many facts are currently stranded — was not queried.
  Production Postgres is reachable only via `DATABASE_PUBLIC_URL` under
  `railway run --service Postgres`; that was not done.
- Whether the Postgres schema matches the SQLite DDL in `documents.py` remains unverified,
  as §14 noted. `private_documents` is **not** in `bot.init_db()`'s `AUTO_PK_TABLES`; the
  writer relies on `cur.lastrowid` with a `SELECT … ORDER BY id DESC LIMIT 1` fallback
  (`documents.py:267-275`), which is portable but was not exercised against Postgres here.
- Whether `private_office.document.extraction` is enabled in production is still unknown.

## A10 — STAGE 1 STATUS

Stage 1 is **complete**. The map above plus this addendum together satisfy the brief's
requirement to record the canonical document table, writer, storage identity, ACL,
delete/archive semantics, extraction engine, OCR path, fact proposal path, evidence model,
provenance, UNDX capabilities, UI and gaps.

The verdict is unchanged and reinforced: **the foundation exists and its governance is
correct — single writer, two locks, structural owner scope, propose-then-review, provenance
carrying locators, three-axis evidence, honest capability states, read-only UNDX.** What is
missing is *understanding*, not *governance*.

The one revision to the plan is A1: before Document Intelligence can answer questions, the
facts it already produces have to be reachable by the thing that would answer them.

**RTC hard lock held: zero Agora / audio / video / livestream files were read or modified
in producing this addendum.**
