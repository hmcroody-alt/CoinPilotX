# PRIVATE OFFICE — SUPER FOUNDATION MAP (Stage 0)

**Mission:** PulseSoc Private Office — Super Master Engineering Mission
**Stage:** 0 — forensic foundation map. Precondition for Stages 1–102.
**Date:** 2026-09-03
**Status:** COMPLETE. **No code was changed to produce this document.**

---

## 0. How to read this

This map answers one question: *what exists in the tree today, and what is still a
build?* It supersedes `PRIVATE_OFFICE_EXISTING_FOUNDATION_MAP.md` (dated 2026-09-01),
which is now materially stale — the entire `services/private_office/` package, the four
private tables, the four-rung tier resolver, the HTTP surface, the native screens and the
eleven-file test suite all landed **after** that document was written. Where the two
disagree, this one is current; where this one is silent, that one still stands.

Every claim below was established by reading source or running a command in this session.
Where something could not be established it is recorded as unknown rather than assumed,
in §9.

Classification vocabulary, as the mission specifies: **IMPLEMENTED**, **PARTIAL**,
**NOT_IMPLEMENTED**, **PROVIDER_REQUIRED**, **BLOCKED**, **LEGACY**, **DUPLICATE**.

---

## 1. Repository ground truth

| Field | Value |
|---|---|
| Branch | `main` |
| HEAD | `65c3f7c1 fix(live): surface archived replays across media lanes` |
| `main` vs `origin/main` | main is **+1**, deliberately unpushed |
| Working tree | **DIRTY**, and dirty with *other people's work* |

The tree currently carries ~27 modified files and ~13 untracked files belonging to at
least two concurrent missions — messenger/realtime (`pulse_communications_v2/`,
`mobile-native/src/api/messenger*.ts`) and multi-guest calls/live
(`mobile-native/src/calls/*`, `src/live/*`, `CallScreen.tsx`). Four foundation-map
documents from those missions sit untracked at the repo root.

**Operational consequence, and it is the single most important line in this document:**
`bot.py` was observed changing size *between two reads* in the previous session. This
checkout is not quiescent. Rule 15's prohibition on `git add -A` / `git reset --hard` /
`git clean -fd` / force-push is not a style preference here — any of them would destroy
live work belonging to another agent. Every stage must stage explicitly, by path.

---

## 2. The Private Office package — what is actually built

`services/private_office/` is **15 modules, 5,801 lines**, plus
`services/private_office_routes.py` (454 lines). It is registered into Flask at
`bot.py:1289` via `_load_route_pack("private_office", "services.private_office_routes")`
— i.e. inside the optional route-pack `try/except`, so a boot-time import error would
make the whole subsystem vanish silently rather than fail the deploy.

`__init__.py` states the ownership boundary explicitly and correctly: this package does
not reimplement entitlements, capability registration, governed execution, verification,
or Briefings. That boundary is the mission's Rule 8/9 written down *before* the fact, and
it holds in the code.

| Module | Lines | What it owns | Class |
|---|---|---|---|
| `model.py` | 391 | Shared vocabulary: 7 domains, 5 sensitivity levels, 8 provenance types, 6 value types, 9 node types, 6 relation types, 3 lifecycle states, plus `relation_permits`, `sensitivity_within`, `provenance_strength` | IMPLEMENTED |
| `schema.py` | 523 | DDL + idempotent bootstrap for `private_facts`, `private_graph_nodes`, `private_graph_edges`, `private_audit_events`; process-role tagging; `require_private_schema` | IMPLEMENTED |
| `tiers.py` | 281 | The four-rung ladder `FREE < PREMIUM < PRIVATE < PRIVATE_OFFICE`, `resolve_tier`, degraded-resolver state, account-hold precedence, legacy premium bridge | IMPLEMENTED |
| `feature_matrix.py` | 302 | 10 feature specs with implementation state + per-feature env kill switch; duplicate feature ids raise at import | IMPLEMENTED |
| `access.py` | 100 | `decide()` / `allowed()` — one refusal vocabulary (`NOT_ENTITLED`, `FEATURE_DISABLED`, `NOT_IMPLEMENTED`, `UNAVAILABLE`) | IMPLEMENTED |
| `facts.py` | 813 | The **only** sanctioned fact writer (`record_fact`) and owner-scoped readers; provenance refs, value normalisation, staleness, fact keys | IMPLEMENTED |
| `graph.py` | 818 | Nodes and edges: `upsert_node`, `record_edge`, `retire_edge`, `set_node_lifecycle`, `list_nodes`, `neighbors`, counts | IMPLEMENTED (substrate) / **unexposed** — see §4 |
| `contradictions.py` | 446 | Interval overlap, material incompatibility, `detect_conflicts`, `mark_conflicts` | IMPLEMENTED (substrate) / **unexposed** |
| `retrieval.py` | 539 | `retrieve()` — the only sanctioned path for anything outside the package (UNDX above all) to obtain private context; applies owner, authorization, sensitivity, domain and purpose before any row leaves | IMPLEMENTED (substrate) / **unexposed** |
| `office.py` | 408 | Projection layer: verification states, provenance projection, domain summary, entry state, per-child state | IMPLEMENTED |
| `audit.py` | 201 | `record` / `record_denied` over `private_audit_events`; unknown actions warn rather than silently pass | IMPLEMENTED |
| `telemetry.py` | 375 | Six declared events; `sanitize` structurally forbids a fact *value* reaching a log line | IMPLEMENTED |
| `health.py` | 318 | Read-only operator surface: schema section, substrate counts, retrieval, telemetry, overall verdict | IMPLEMENTED |
| `status.py` | 234 | Catalog coverage, tier counts, provider availability, resolver health | IMPLEMENTED |

**This is a good foundation and it should be built on, not around.** The discipline in it
is real: a single canonical writer enforced by a test
(`tests/private_office/test_private_write_boundary.py`), a feature matrix that refuses to
import with a duplicate id, a telemetry module that cannot leak a value, and a retrieval
chokepoint that exists precisely so callers do not assemble context from `facts` and
`graph` by hand.

---

## 3. The HTTP and agent surface — much thinner than the substrate

`services/private_office_routes.py` exposes **five** endpoints:

| Route | Method | Class |
|---|---|---|
| `/api/private-office/entitlement` | GET | IMPLEMENTED |
| `/api/private-office/overview` | GET | IMPLEMENTED |
| `/api/private-office/facts` | GET | IMPLEMENTED |
| `/api/private-office/facts` | POST | IMPLEMENTED |
| `/api/admin/private-office/status` | GET | IMPLEMENTED |

`services/undx_capability_registry.py` (79 registered capabilities) contains **exactly
one** Private Office capability: `private.facts.list` at line 1158 — read-only,
`SELF_ACCOUNT_ONLY`, with the domain enum imported from `model.py` rather than retyped.

So: the graph, the contradiction detector and the private retrieval engine — 1,803 lines
of working substrate — have **no HTTP route, no UNDX capability, and no caller outside
the package**. `retrieval.py` imports `graph`; nothing imports `retrieval`.

This is the defining shape of the current state, and it is deliberate rather than
accidental: `feature_matrix.py` marks `capital_graph` and `relationship_intelligence`
`NOT_IMPLEMENTED` with the note *"Flip to IMPLEMENTED only when a real writer and
owner-scoped reader exist."* The matrix is being honest about the **surface**, not the
substrate. That is the right call, and it means Stage 1 work here is mostly *exposure and
wiring*, not new domain code.

---

## 4. The declared feature matrix, verbatim

| Feature id | Min tier | Declared state | Reality check |
|---|---|---|---|
| `advanced_undx` | PREMIUM | IMPLEMENTED | accurate |
| `market_pulse` | PREMIUM | IMPLEMENTED | accurate |
| `private_facts` | PRIVATE | IMPLEMENTED (kill switch `PRIVATE_FACTS_ENABLED`) | accurate — full path exists: writer, reader, projection, route, capability |
| `capital_graph` | PRIVATE | NOT_IMPLEMENTED | **substrate exists** (`graph.py`, 818 lines); surface does not |
| `relationship_intelligence` | PRIVATE | NOT_IMPLEMENTED | same — depends on the same substrate |
| `private_briefings` | PRIVATE | NOT_IMPLEMENTED | `services/pulse_briefings/` exists and is reusable; the Private Office *fact provider* into it does not |
| `private_shield` | PRIVATE | PROVIDER_REQUIRED | accurate |
| `private_shield.breach_monitoring` | PRIVATE | PROVIDER_REQUIRED | accurate |
| `private_office.document.extraction` | PRIVATE | PROVIDER_REQUIRED | accurate — no OCR/PDF extraction library in the tree |
| `human_concierge` | PRIVATE_OFFICE | NOT_IMPLEMENTED | accurate — needs a staffed process, not code |

The three PROVIDER_REQUIRED rows carry the correct reasoning in their notes, including the
one that matters most: breach monitoring *"must never render a clean state: 'no breaches
found' when nothing has looked is a fabricated security assurance."* That is Rule 1
already enforced at the data layer. **BLOCKED** until a real provider and a commercial
agreement exist; it is a procurement decision, not an engineering one.

---

## 5. Domain model coverage against the mission's ten primitives

The mission names ten shared primitives. Everything else is supposed to be a view over
them. Four exist; six do not.

| Primitive | State | Where |
|---|---|---|
| **FACT** | IMPLEMENTED | `private_facts` + `facts.py` — with full provenance: value, type, source ref, confidence, staleness, supersession |
| **ENTITY** | IMPLEMENTED (substrate) | `private_graph_nodes` + `graph.py`, 9 node types |
| **RELATIONSHIP** | IMPLEMENTED (substrate) | `private_graph_edges` + `graph.py`, 6 relation types, `relation_permits` type-checks endpoints |
| **DOCUMENT** | PARTIAL | exists only as `NODE_DOCUMENT`, a node type. No document table, no upload path into the private substrate, no extraction (PROVIDER_REQUIRED) |
| **OBLIGATION** | NOT_IMPLEMENTED | no table, no module, no scanner. `grep` for `private_obligations` returns nothing |
| **EVENT** | NOT_IMPLEMENTED | `private_audit_events` is an *audit* log, not a domain event stream. Do not conflate them |
| **DECISION** | NOT_IMPLEMENTED | nothing |
| **REQUEST** | NOT_IMPLEMENTED | nothing |
| **RISK** | NOT_IMPLEMENTED | and constrained: `__init__.py` states nothing in this package may emit a score about a *person* — that stays owned by `services/user_trust_engine.py` |
| **OPPORTUNITY** | NOT_IMPLEMENTED | nothing |

**Truth/provenance model:** the mission's seven truth states map onto `model.py`'s eight
provenance types almost exactly — `VERIFIED`, `PROVIDER_ASSERTED`, `DOCUMENT_EXTRACTED`,
`USER_ASSERTED`, `INFERRED`, `ESTIMATED`, `STALE`, `CONFLICTING`. The mission's `KNOWN`
is this model's `VERIFIED`/`PROVIDER_ASSERTED`; `MISSING` is absence; `PRO_REVIEW` has no
counterpart yet. `provenance_strength()` gives the ordering that the rule *"no inferred
value may silently overwrite a known value"* needs. **The provenance model is built. Use
it; do not design a second one.**

---

## 6. Native client

| Surface | State |
|---|---|
| `PrivateOfficeScreen.tsx` | IMPLEMENTED — renders server-decided structure; handles LOADING / ENTRY_UNKNOWN / ENTRY_UPGRADE_REQUIRED / ENTRY_UNAVAILABLE / available / unavailable children; tappability from `child.opens`, never re-derived |
| `PrivateFactsScreen.tsx` | PARTIAL — read path complete with a provenance sheet and six distinct empty/refusal states (`UNAVAILABLE` is deliberately never drawn as `EMPTY`). **No create/edit UI**, although `POST /facts` exists server-side |
| `src/api/privateOffice.ts` | IMPLEMENTED — two endpoints, closed unions, unknown values dropped rather than defaulted, degraded read returns "unknown" not "no access" |
| `src/entitlements/canonicalTier.ts` | IMPLEMENTED — single client authority reading `/api/private-office/entitlement`; replaced four mutually inconsistent client-side deciders |
| Navigation | IMPLEMENTED — `PrivateOffice` and `PrivateFacts` registered unconditionally in `AppNavigator`; gating happens inside the screens against server state |
| i18n | IMPLEMENTED — all strings under `premium:privateOffice.*` in `catalogs/en/extended.json`; no hardcoded English in either screen |
| `PremiumCenterScreen.tsx` | PARTIAL — correctly refuses to know the tier ladder and reads Private Office entry state from the server, but its own layout is still binary premium/free |

**Client-side entitlement truth — the hard-rule check.** The four conflicting deciders
named in the prior audit (`ProfileHeader`, `AppNavigator`, `LiveHostSessionScreen`,
`api/premium.ts`) are **gone**, consolidated into `canonicalTier.ts`. Two residues remain
and should be recorded rather than silently tolerated:

- `mobile-native/src/entitlements/membershipMark.ts:25` keeps a copy of the server's
  display predicate `{active, founder, lifetime, trial}`. It is documented, returns a
  bare `boolean`, and exposes no tier or feature id, so no gate can consume it. It exists
  because the canonical endpoint answers only for the caller and therefore cannot be used
  to read a *stranger's* tier. **Acceptable, but it is a copy and must be kept in step.**
- `mobile-native/src/screens/MusicScreen.tsx:140` derives an `uploadReadyHint` from
  `premium_status` containing `"premium"` or `"founder"`. Cosmetic by name, client-side by
  nature. **PARTIAL / residual** — worth folding into `canonicalTier` when Music is next
  touched, not worth a dedicated change now.

---

## 7. Reuse decisions — what Stages 1+ must NOT rebuild

| Foundation | Decision |
|---|---|
| UNDX capability registry (79 capabilities, import-time invariants, read-back verification) | **REUSE.** Register new Private Office capabilities here. A second registry is forbidden by `__init__.py`. |
| `undx_tool_gateway.execute()` | **REUSE** as the governed execution path. |
| `services/pulse_briefings/` | **REUSE** as the Private Briefing delivery layer. Build the fact *provider* into it; do not build a second engine. |
| `services/business_os/entitlements` | **REUSE.** Grants stay there; `tiers.py` only *maps* them onto the ladder. |
| `services/private_office/*` | **REUSE AS-IS.** Extend by adding modules under the stated ownership contract. |
| `services/user_trust_engine.py` | **REUSE** for any score about a person. The Private Office may not emit one. |
| `retrieval.retrieve()` | **REUSE as the only door.** Nothing outside the package may query `facts` or `graph` directly. |
| `services/sentinel/` | **DO NOT REUSE** for personal exposure monitoring — wrong domain, and it monitors the platform's own dependencies. |

---

## 8. Verification-gate findings (evidence, not assertion)

Two things were measured rather than assumed, and both matter for the mission's rule that
every PASS needs evidence.

**8.1 The Private Office test suite is order-dependent.** Run per file, all eleven files
pass (58 tests). Run as a directory, **six fail**:

```
FAILED tests/private_office/test_entitlement_routes.py::test_entitlement_returns_the_full_contract
FAILED tests/private_office/test_entitlement_routes.py::test_account_hold_is_reflected_over_http
FAILED tests/private_office/test_entitlement_routes.py::test_degraded_resolve_returns_200_with_ok_false
FAILED tests/private_office/test_entitlement_routes.py::test_status_returns_health_for_an_admin
FAILED tests/private_office/test_private_observability.py::test_observability_suite
FAILED tests/private_office/test_private_substrate.py::test_substrate_suite
```

The first is `assert 401 == 200` — an auth stub from an earlier module leaking rather than
a genuine regression. `schema.py` also holds process-global state (`_SCHEMA_READY`,
`_PROCESS_ROLE`) with a `reset_schema_cache()` that some tests evidently do not call.
**This must be fixed before any stage claims a green suite**, because a suite whose
result depends on invocation order cannot serve as a verification gate.
`test_private_write_boundary.py` additionally exceeds 12s (it scans `bot.py`) and should
be given an explicit budget rather than being allowed to look like a hang.

**8.2 `PRIVATE_FACTS_ENABLED` is undocumented.** It is the live kill switch for the one
IMPLEMENTED private feature, and it does not appear in `.env.example`. There is already a
protection test (`tests/protection/test_environment_contract.py`) that fails on
undocumented variables — it is currently red for the concurrent multi-guest-call mission's
variables, which is exactly how this one slipped through. One line in `.env.example`
closes it.

Note the switch's polarity: absent means **enabled**. That is the right default for a
kill switch on shipped code, but it means a fresh environment turns private facts on
without anyone choosing to.

---

## 9. Limits of this map

- **Production flag values remain unverified.** Railway redacts variable values for
  connected OAuth apps. Everything above describes code defaults.
- **Local SQLite is not production PostgreSQL.** With schema created imperatively and no
  migration framework, the private tables' existence in production must be verified from
  a runtime signal — `schema.py` already emits one via `require_private_schema`, which is
  the right thing to read.
- **The tree was moving while this was written.** Line numbers in files owned by the
  concurrent missions may already have shifted; those inside `services/private_office/`
  were stable across the session.
- **Nothing here was executed against a device or simulator.** No claim in this document
  is a claim about runtime behaviour on hardware.

---

## 10. Stage 0 verdict and what follows

**Stage 0: PASS.** No code changed.

Three findings should shape the sequencing:

1. **The substrate is built and the surface is not.** `graph.py`, `contradictions.py` and
   `retrieval.py` are 1,803 lines of working, disciplined code with zero callers. The
   mission's execution priority puts SHARED DOMAIN MODEL first and CAPITAL GRAPH fourth —
   but the domain model largely exists, and the capital graph is mostly a wiring job:
   routes, a UNDX capability, a native screen, and flipping one feature-matrix row once a
   real writer and owner-scoped reader are reachable. That is a much shorter path than the
   stage list implies, and it is the highest-value first tranche.

2. **Six of the ten primitives do not exist.** OBLIGATION, EVENT, DECISION, REQUEST, RISK
   and OPPORTUNITY are genuine builds. OBLIGATION is the one with immediate product value
   (deadlines, renewals, expiries) and the one with the clearest reuse story — it reads
   facts, it writes to Briefings. RISK is constrained by an existing ownership boundary
   and should be sequenced after a conversation about who owns scoring.

3. **The verification gate is not currently trustworthy.** Fix the order-dependent test
   failures before anything else claims PASS against this suite. This is small work and
   everything downstream depends on it.

The next decision — how much of Stages 1–102 to attempt, and in what order — is a scoping
question, and §7 plus the three findings above are the input to it.
