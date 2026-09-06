# CAPITAL GRAPH — SUPER FOUNDATION MAP

Forensic map of the existing Capital Graph foundation, produced before any
implementation (mission §3). Every claim below was verified by reading the
named file at the named line in this working tree (main @ d21014c8).

**Revalidated at main @ 6ebc1cf2.** Sections 9 and 12 carry the corrections;
sections 1-8, 10, 11 and 13 were re-checked and still hold. Treat any section
without a revalidation stamp as verified only as of d21014c8 — the surrounding
Private Office missions move quickly and this file has already gone stale once.

---

## 1. CURRENT NODE TYPES (9)

`services/private_office/model.py` — `NODE_TYPES`:

| Node type | Mission §5 mapping |
|---|---|
| PERSON | PERSON ✓ |
| BUSINESS | ORGANIZATION (existing synonym — do NOT add ORGANIZATION) |
| PROPERTY | PROPERTY ✓ |
| INSURANCE_POLICY | (coverage layer; no mission synonym needed) |
| CONTRACT | CONTRACT ✓ |
| DOCUMENT | DOCUMENT ✓ |
| PROFESSIONAL | PROVIDER (existing synonym — do NOT add PROVIDER) |
| ASSET | ASSET ✓ (crypto holdings project here as `portfolio:SYM`) |
| LIABILITY | OBLIGATION/OWES target ✓ |

Missing vs. mission §5: ACCOUNT, WALLET, INCOME_SOURCE, PORTFOLIO_CONTAINER,
OBLIGATION-as-node. model.py's own docstring declares the vocabulary "a
*foundation* cap, not a product cap" — extension is the sanctioned path,
synonyms are not.

## 2. CURRENT EDGE TYPES (6) + ENDPOINT MATRIX

`model.py` — `RELATION_TYPES` + `RELATION_ENDPOINTS`, enforced by
`relation_permits()` (pair matrix; graph.py refuses anything else):

- OWNS: (PERSON|BUSINESS) → (BUSINESS|PROPERTY|ASSET|LIABILITY)
- ADVISED_BY: (PERSON|BUSINESS) → PROFESSIONAL
- COVERED_BY: (PERSON|BUSINESS|PROPERTY|ASSET) → INSURANCE_POLICY
- SECURED_BY: LIABILITY → (PROPERTY|ASSET)
- GOVERNED_BY: (BUSINESS|PROPERTY|ASSET|LIABILITY) → CONTRACT
- DESCRIBES: DOCUMENT → (7 types), one direction only

Missing vs. mission §6: HOLDS, OWES/OWED_BY, GENERATES_INCOME, PAYS,
HELD_AT, CUSTODIED_BY, PARTY_TO, CONNECTED_TO, MANAGED_BY, BELONGS_TO,
DERIVED_FROM. Existing coverage: OWNS ✓, SECURED_BY ✓, ADVISED_BY≈MANAGED_BY
for professionals, DESCRIBES≈SUPPORTED_BY inverse, GOVERNED_BY≈PARTY_TO for
contracts. Any addition must extend RELATION_ENDPOINTS, not bypass it.

## 3. CURRENT WRITERS (single-writer discipline INTACT)

**Sole canonical writer:** `services/private_office/graph.py` (818 lines) —
`upsert_node` / `record_edge` / `retire_edge` / `set_node_lifecycle`.
Identity: `node_key = sha256(type\x1f external_ref)` per owner; `edge_key`
includes provenance. Cross-owner reads answer "not found" (anti-oracle).
`MAX_NEIGHBOURS=200`.

**Consumers that write through it (all synchronous, in-transaction):**
- `portfolio_projection.py` — PERSON `user:{id}`, ASSET `portfolio:SYM`,
  OWNS edges, portfolio.* facts. Full-state convergent; sell→ARCHIVE +
  retire edge; rebuy→reactivate the SAME logical node.
- `documents.py:491-497` — NODE_DOCUMENT on claim acceptance, plus
  DOCUMENT_EXTRACTED fact (facts.py) with locator into the document.
- `relationships.py:104-127` — NODE_PERSON + name/role facts
  (USER_ASSERTED). No edges written; dedup is the member's decision.

**Facts writer:** `facts.py._record_fact` (single writer, static guard at
line 14). Provenance is part of fact_key → same value from a second source
is a second row (corroboration), same source refreshes. STALE/CONFLICTING
rejected as write origins. `supersede_facts()` retires per (subject, type).

**No writer exists for:** liabilities, accounts, entities/org structure,
income sources, obligations→graph, facts→graph (beyond the three above).

## 4. CURRENT PROJECTORS

- `portfolio_projection.py` (617 lines) — the model projector. Convergent
  full-state `project_user`; outbox consumer `process_pending`/`drain`;
  `reconcile(repair=True)` reuses `project_user` (no second repair writer);
  `portfolio_view` prices at READ TIME via `market_data.live_market_board`
  (Decimal, None-never-0, totals only when complete, per-asset evidence
  `{fact_ids, provenance}`, sync status).
- `portfolio_events.py` (246 lines) — `portfolio_outbox`; events carry NO
  payload (convergence is structural); flag
  `PORTFOLIO_CAPITAL_PROJECTION_ENABLED` default ON; post-commit kick +
  lazy sweep in `portfolio_view`; `enqueue` never raises; MAX_DRAIN=200;
  `sync_status` = {pending, failed, last_event_at, last_processed_at, enabled}.

No other projector exists. Facts and Operations records reach no graph
surface except through the three writers in §3.

## 5. CURRENT READERS

**`capital_graph.py` (605 lines)** — read boundary, test-pinned to contain
no graph import, no SQL, no `private_graph_`/`private_facts` literals, and
an explicit `_retrieval.retrieve(` call (test_capital_graph.py:159-179).
- VIEW_INTENTS: holdings→property_portfolio, coverage→insurance_coverage,
  structure→business_structure, documents→legal_documents. "Views may be
  added here freely; intents may not."
- NO_AGGREGATE_VALUE: payload must contain no net_worth/total_value/…
  field and no top-level money scalar (test:328-354). Any §20/§49 net
  position must live in a NEW module/endpoint with its own contract, or
  this pin must be consciously renegotiated — not silently violated.
- TRUTH_STATES precedence: CONFLICTING > MISSING > STALE > weakest
  provenance; unrecognized → PRO_REVIEW.
- `_denied()` — identical envelope for absent/foreign/blank (anti-oracle,
  test:440-466). Projection privacy: no owner_user_id/node_key/edge_key.
- `entity()` = depth-1 seed; `relationships()` = thin projection of entity.

**`retrieval.py` (724 lines)** — the five-gate walk:
owner → actor==owner (refusal, no delegation) → sensitivity lower-of →
domain join policy (HEALTH/IDENTITY/SECURITY isolated) → purpose (closed
audit vocabulary). BFS with deque, deterministic insertion order,
MAX_DEPTH=3 / MAX_NODES=100 / MAX_EDGES=250 / MAX_FACTS=400, truncation
always reported, dangling edges KEPT. Facts attached via one batch
`list_facts_for_subjects`; conflicts via one batch
`contradictions.detect_conflicts`; staleness from each fact's computed
`freshness`. Callers cannot widen ceiling/domains past the intent policy.
Reusable helpers: `domain_join_permitted`, `_lower_ceiling`, `_seed_nodes`.
No pagination in `retrieve()` (records pagination exists in
`retrieve_records`, cursor `before_id`).

## 6. CURRENT SOURCES

- **Holdings ledger (only ledger):** `portfolio_items` + legacy
  `manual_portfolio`. One basis-less lot ⇒ basis_complete=False ⇒ basis
  fact retired entirely (never a partial sum).
- **Facts:** `private_facts` — provenance-keyed, temporally windowed
  (valid_from/valid_to), computed freshness horizons (VERIFIED 90d,
  PROVIDER 60d, DOCUMENT 365d, USER 180d, INFERRED 30d, ESTIMATED 14d,
  STALE/CONFLICTING 0). Stale facts returned + flagged, never hidden.
- **Conflicts:** computed by `contradictions.detect_conflicts` (tolerance:
  numeric ±0.5% rel, PERCENT ±1% abs, dates zero-tolerance, text
  normalized/containment=elaboration); 24h SIMULTANEITY window; open-ended
  earlier fact implicitly superseded by a later one >24h apart.
  `mark_conflicts` stamps conflict_id WITHOUT touching provenance or
  lifecycle; `unresolved` is hard-coded true — no auto-resolve path exists.
- **Operations (authoritative for obligations, §28):** `records.py` — six
  record types (OBLIGATION, EVENT, DECISION, REQUEST, RISK, OPPORTUNITY).
  OBLIGATION: `private_obligations` (obligation_type, due_at, amount_text,
  amount_number float, currency; identity (type,title,due_at));
  `effective_status()` derives OVERDUE/DUE_SOON(≤14d). NO recurrence
  field — a cash-flow layer must not fabricate cadence. Reads:
  `get_record` / `list_records(due_before, statuses, domains, ceiling)` /
  `count_open`. Revisions: SUPERSEDED + supersedes_id.
- **Documents/evidence:** `private_documents` + `private_document_claims`
  (PROPOSED→ACCEPTED/REJECTED via governed `review_claim`); acceptance is
  the ONLY path from a document into facts+graph. `evidence.format_ref()`
  makes resolvable document→fact→node pointers.
- **Relationships:** person directory + commitments via
  `related_entity_ids` citation of `node_ref` in OBLIGATION/REQUEST rows.
- **Prices:** `market_data.live_market_board(limit=80)` at read time only;
  never persisted; provider observation time carried for freshness labels.

## 7. CURRENT FLAGS

- `PORTFOLIO_CAPITAL_PROJECTION_ENABLED` (default on) — outbox/projector.
- `CAPITAL_GRAPH_ENABLED` — feature gate (feature_matrix `capital_graph`).
- `UNDX_AGENT_READS_ENABLED` — UNDX read surface.
Mission §99: preserve these three; no flag explosion.

## 8. CURRENT GATES (order, verified in private_office_routes.py:610-800)

login (401) → `_resolve_for` tier + `_gate(resolved, CAPITAL_FEATURE_ID)`
(entitlement/flag refusals) → `_office_lock_gate` (423) → view validation
(400 listing real views, never a silent default) → capital_graph/portfolio
read → `denied` ⇒ 403 (summary/portfolio) or 404 identical-envelope
(entity/relationships). Exceptions ⇒ `_capital_failure()` 503
"state: unavailable" — an unreadable graph is NEVER an empty graph.
No route-level audit (retrieval records the read; a second record would
double-count). Matches mission §75-77 exactly; the office grant persists
across tabs and the owner does NOT bypass the second lock.

## 9. CURRENT UNDX

- `undx_capital_spec.py` (106 lines): COMPLETE spec —
  CAPABILITY_ID `private.capital.portfolio`, FEATURE_ID `capital_graph`,
  RISK read_only, CONFIRMATION never, PERMISSION self_account_only,
  AUDIT_CATEGORY private_capital_read, native route
  `/pulse/private-office/capital-graph`, `_ASSET_FIELDS` allowlist,
  `execute()` hook.
- ~~**GAP:** never `_register`ed in `undx_capability_registry.py`~~ —
  **CLOSED at 6ebc1cf2.** The registry now derives the capability from
  `undx_capital_spec` (~1263-1269), following the same derive-from-spec-module
  pattern as `_register_private_record_capabilities`. The check in
  tests/private_office/test_capital_undx_capability.py that pinned this gap
  should now pass; re-run it before relying on either statement.

## 10. CURRENT UI (mobile-native)

- `src/api/capitalGraph.ts` (553): 4 views, tagged results
  (READY/EMPTY/…/LOCKED via 423/DENIED/UNAVAILABLE/…), parse fns,
  `isNotFound()` for entity routes.
- `CapitalGraphScreen.tsx` (1185): PrivateOfficeLockGate wrapper; 4-chip
  switcher (mission wants 8 tabs, horizontal scroll); `settle()` requires
  portfolio READY+empty before claiming EMPTY on portfolio-backed views;
  holdings dashboard (totalValue vs pricedValue labels, priced-only
  allocation, freshness); coverage/structure/documents panels;
  `lockOfficeLocally()` on LOCKED.
- `CapitalEntityScreen.tsx` (492): entity card + truth mark, relationship
  rows (independent-failure note, push-navigates), facts with provenance/
  stale marks, conflict competitors. Route `CapitalEntity {id, view?}`.
- `PrivateOfficeLockGate.tsx` (939): doors CHECKING/UNAVAILABLE/
  UPGRADE_REQUIRED/SETUP/LOCKED/UNLOCKED. `applyStatus()` (223-232) treats
  server `status.unlocked` as authoritative: server-locked + local grant ⇒
  `lockOfficeLocally()` + LOCKED door. Deliberate hardening; test mocks
  must model it.

## 11. BASELINE FAILURES IN SCOPE (exact identities)

1. **Backend:** `tests/private_office/test_capital_undx_capability.py` —
   one check: `registry.REGISTRY.get("private.capital.portfolio")` is
   None. Fix: register the spec in undx_capability_registry.py.
2. **Native:** `CapitalGraphScreen.test.tsx` ×2 ("names the tier wall…",
   "keeps switched-off and never-built…"). Both render twice; second
   mount's `unlockDoor()` skips (local grant present) → gate `check()`
   gets the mock's STATIC `unlocked:false` → authoritative `applyStatus()`
   relocks → LOCKED door → waitFor times out. Fix belongs in the TEST MOCK
   (report `unlocked` from grant state), not in the production gate.

Out-of-scope baseline failures (pre-existing, untouched): jest
ConversationControlCenter, BusinessOsSection, LoginScreen, PresenceHub,
PrivateFacts, SellerStore ×2, undx ×2, sessionStoreSimulatorFallback;
backend test_pulsesoc_call_livekit_grants, `import bot` smoke.

## 12. GAPS vs. MISSION (what gets built)

> **Revalidated at main @ 6ebc1cf2.** The list below was written at d21014c8 and
> four of its nine entries have since been closed. Re-verified entry by entry
> against HEAD rather than trusted — an out-of-date gap list is worse than no
> gap list, because it sends the next session to rebuild work that already
> exists. That nearly happened here.

**CLOSED since d21014c8 — do not rebuild:**

- ~~1. UNDX capability unregistered~~ — **CLOSED.** `undx_capability_registry.py`
  now derives the capability from `undx_capital_spec` (registry ~1263-1269).
- ~~5. No overview/command-center read model~~ — **CLOSED.**
  `capital_overview.py:154 overview()` composes portfolio + liabilities and
  honours the NO_AGGREGATE_VALUE pin: it publishes `net_position.estimated`
  alongside `complete`, `incomplete_reasons`, `excluded` and `basis`, and
  repeats the disclaimer in-payload. It does not assert a net worth.
- ~~8. Integrity diagnostics absent~~ — **CLOSED.**
  `services/private_office/integrity.py` (six checks: `cross_owner_edges`,
  `orphan_edges`, `duplicate_node_identity`, `unknown_vocabulary`,
  `edges_into_retired_nodes`, `portfolio_projection_drift`), exposed at
  `GET /api/private-office/capital-graph/integrity`, repairs nothing.
- ~~9. Mutation battery not encoded~~ — **PARTIALLY CLOSED.** `test_integrity.py`
  carries a five-mutation battery. The §106 battery for pricing/basis/dedup/
  owner-scope is still absent.

**STILL OPEN:**

2. No Backend OS feature registration for capital_graph (§86).
   `backend_management_registry.py` contains zero occurrences of "capital".
3. Node/edge vocabulary short of §4 — still 9 node types and 6 relation types
   (`model.py:410`, `:471`). Missing nodes: ACCOUNT, WALLET, INCOME_SOURCE,
   OBLIGATION-as-node, PORTFOLIO_CONTAINER. Missing relations: HOLDS, OWES,
   GENERATES_INCOME, PAYS, HELD_AT, CUSTODIED_BY, PARTY_TO, MANAGED_BY,
   BELONGS_TO, DERIVED_FROM. Consequence: "PERSON OWNS BROKERAGE; BROKERAGE
   HOLDS AAPL" (§7, §17) is **unrepresentable today**. Extend
   RELATION_ENDPOINTS; never bypass `relation_permits()`.
4. No facts→graph or operations→graph projection (§16-23). Note before
   attempting: `fact_type` is an OPEN namespace (`facts.py:243`
   `^[a-z0-9][a-z0-9_.]{0,63}$`), not a closed vocabulary — so a projector must
   carry an explicit declared mapping of capital fact types. Projecting
   whatever happens to be present would be fabrication under §15.
6. Read surfaces — overview ✓, cash-flow ✓, obligations ✓, exposure ✓ and
   integrity ✓ now exist. **Still missing: `graph` (bounded hops) and
   `history` (§44, §92).** `structure` and `documents` exist as *views* on
   `GET /capital-graph?view=`, not as their own routes.
7. **Native is the widest gap and the one that blocks §110-114 acceptance.**
   `capitalGraph.ts` calls exactly two paths (`CAPITAL_GRAPH_PATH`,
   `CAPITAL_PORTFOLIO_PATH`) and `CAPITAL_VIEWS` is still the original four
   (holdings, coverage, structure, documents). The overview, obligations,
   exposure, cash-flow and integrity surfaces are built, gated and tested on
   the server and are **unreachable from the product** — the member sees a
   four-chip holdings screen while the Capital Command Center exists behind it.
   No device acceptance of §47 is possible until the client consumes them.

## 13. INVARIANTS THAT MUST SURVIVE EVERY CHANGE

- graph.py stays the only graph writer; capital_graph.py stays SQL-free
  and graph-import-free (test-pinned).
- Conflicts never auto-resolved; provenance never upgraded/downgraded.
- Prices read-time only; unknown basis/price is None, never 0; totals only
  when complete; partial totals labeled priced-only.
- Anti-oracle envelopes (absent == foreign == blank).
- Five retrieval gates in order; domain isolation; truncation reported.
- Owner isolation is P0; cross-owner edge = CRITICAL defect.
- Error ≠ empty at every layer (503 unavailable vs. empty payloads;
  native settle() rules).
- Real-time audio untouched: 0 Agora/call/livestream files; gate run anyway.
