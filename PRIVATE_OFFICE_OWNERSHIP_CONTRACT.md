# PRIVATE OFFICE — OWNERSHIP CONTRACT

**Mission:** 1A — Canonical Entitlements + Foundation Ownership Lock
**Stage:** 1 (with Stage 19 risk-ownership resolution folded in)
**Date:** 2026-09-01
**Source of truth for prior state:** `PRIVATE_OFFICE_EXISTING_FOUNDATION_MAP.md`
**Status:** BINDING. Every later Private Office stage is checked against this document.

---

## 0. WHAT THIS DOCUMENT IS

Eleven domains of state are named below. For each, exactly one module is declared the
**canonical owner**. Feature code may read through the owner's public API and may not
reimplement, duplicate, or write around it.

The rule this enforces: *no ambiguous co-ownership.* A domain with two owners has no truth,
only a race.

Where an owner is being **created** by this mission, that is stated. Where an owner
**already exists**, it is reused and this document freezes it — a later stage proposing a
replacement must first amend this contract with a stated reason.

### Corrections to the Stage 0 map

Stage 0 was written from three parallel reconnaissance passes. Deeper recon for this stage
corrected it in four places. These corrections are load-bearing and are recorded here rather
than buried:

1. **Trust/risk owners: eleven, not four.** Stage 0 reported four. The precise inventory
   (§12) found eleven score producers, of which four score an *overall user* and therefore
   genuinely compete.
2. **Client-side tier deciders: six, not two.** Stage 0 named `AppNavigator.tsx` and
   `ProfileHeader.tsx`. There are six, using **four different string arrays** plus one bare
   truthiness test (§13).
3. **Pulse Briefings has no pluggable provider model.** Stage 0 implied a "fact-provider
   model" to extend. There is none — sources are hardcoded in one function. The extension
   point is real but it is a convention, not a registry (§9).
4. **`mobile-native/` does not use Zustand**, contrary to `CLAUDE.md`. Session state is a
   React Context. Also: `.github/workflows/protection.yml` does not exist.

---

## 1. ENTITLEMENTS

**Canonical owner:** `services/business_os/entitlements/` — resolver `service.py`,
migration seam `facade.py`, catalog `schema.py`.

**Tier ladder owner (new, created by this mission):**
`services/private_office/tiers.py`.

The existing subsystem is capability-keyed, not tier-keyed. It answers *"does this subject
hold `premium.crypto.portfolio`?"* It has no concept of "what tier is this person on." The
four-tier product model needs that concept, so one module derives it — from the existing
umbrella-key pattern already established at `schema.py:63-67`, where `premium.access` is
documented as *the umbrella MEMBERSHIP key*.

The tier ladder is therefore four umbrella keys resolved through the existing resolver:

| Tier | Umbrella key | Rank |
|---|---|---|
| FREE | *(none — the absence of all three)* | 0 |
| PREMIUM | `premium.access` | 1 |
| PRIVATE | `private.access` | 2 |
| PRIVATE_OFFICE | `private_office.access` | 3 |

Effective tier = the highest-ranked umbrella key the subject holds. Derivation lives in
exactly one function. No route, template, or client re-derives it.

**Frozen decisions:**

- `service._resolve` (`service.py:191`) keeps its fixed precedence:
  `suspended → active → grace → grandfathered → revoked → none`. The tier ladder consumes
  its verdicts; it does not reimplement precedence.
- `facade.account_hold` (`facade.py:172`) remains the **single** definition of an account
  hold. A hold outranks any paid grant. The tier resolver calls it; it does not restate the
  rule.
- New tiers are **catalog data** — rows in `business_os_ent_products`, `_plans`, `_catalog`
  — not new tables and not new code branches.
- Grant provenance stays inside the closed `_VALID_SOURCES` set (`service.py:62-66`).

**Explicitly not owners:** `services/premium_entitlement_service.py`,
`services/premium_identity_engine.py`, `services/premium_visibility_engine.py`,
`services/premium_crypto_access.py`. These are the four legacy deciders. They remain in
place for Premium (removing them is out of scope and would be the "broad unrelated
refactor" Rule 16 forbids), but they are **frozen**: no Private Office code may call them,
and the PRIVATE and PRIVATE_OFFICE tiers have no legacy fallback at all.

---

## 2. UNDX CAPABILITIES

**Canonical owner:** `services/undx_capability_registry.py` — `REGISTRY` at line 239,
`CapabilitySpec` at lines 44-93.

Private Office capabilities are registered **in this file**, as `CapabilitySpec` instances,
subject to the existing import-time invariants (`__post_init__`, lines 95-128) which require
that every write declare a `verifier` and a `target_field`, and that every mutable field be
listed in `verified_fields`.

**A second registry is forbidden.** So is any capability-like dispatch table under
`services/private_office/`.

Registration is a **triple** commitment, not a single one — the authorization surface test
(`tests/undx_agent/test_authorization_surface.py`) fails unless all three agree:

1. `services/undx_capability_registry.py` — the `CapabilitySpec`
2. `services/undx_policy.py:41` — `PRODUCTION_TOOL_REGISTRY` entry for the `tool_name`
3. `services/undx_knowledge_map.py` — a `_live(...)` record

Plus a regenerated `tests/undx_agent/authorization_surface_baseline.py`, since a new
capability id is reported as newly reachable.

---

## 3. ACTION EXECUTION

**Canonical owner:** `services/undx_tool_gateway.py`, entry `execute()` at line 679.

Pipeline, frozen in this order: auth → `require(capability_id)` → `validate_arguments` →
`_enforce_permission_scope` → `policy.evaluate` → confirmation mint/redeem → idempotency →
ledger reservation → `_run_executor` → `_settle` (verify, status, audit).

**No parallel action runtime.** No `private_office_executor.py`, no direct service call that
bypasses the gateway for anything the gateway can express.

Executors live in `services/undx_agent_tools.py` (`EXECUTORS`, line 2513) with the fixed
signature `(user_id: int, arguments: dict[str, Any]) -> ToolResult`. No connection is passed
in; executors obtain their own through the service layer.

**Where tier gating goes.** The gateway performs **no entitlement check** today — verified,
not assumed: there is no reference to entitlement, premium, tier, or subscription anywhere in
`undx_tool_gateway.py`. Rather than widen the gateway (which would put a product concept
inside a governance primitive that 120 existing capabilities depend on), **tier gating for
Private Office capabilities is enforced inside the executor**, which raises a typed
`AgentError`. That converts to a failed `ToolResult` and a terminal-failure receipt — an
auditable denial, not a silent one.

This is a deliberate trade: it keeps the gateway generic, at the cost of each Private Office
executor having to make the check. The mitigation is that the check is a single shared
decorator in `services/private_office/`, so there is still exactly one implementation.

---

## 4. VERIFICATION

**Canonical owner:** `services/undx_verification.py` — `VERIFIERS` (lines 1405-1442),
dispatch `verify()` (line 1445).

Verdicts are `VerificationState` (`services/undx_agent_contracts.py:221-229`):
`VERIFIED | PENDING | FAILED | IMPOSSIBLE`.

**Frozen:** `VERIFIED` is the only state that yields `VERIFIED_SUCCESS` for a write
(`undx_tool_gateway.py:602-611`). Private Office writes must supply a real read-back
verifier registered in this module. A verifier that returns `VERIFIED` without reading state
back is a Rule 12 violation and must fail review.

`AgentOutcome.COMPLETED = frozenset({VERIFIED_SUCCESS})` stays as it is. Nothing may be
reported to a user as done on `ACCEPTED_UNVERIFIED`.

---

## 5. PRIVATE FACTS

**Canonical owner (new):** `services/private_office/facts_service.py`, over the table
`private_facts`.

No feature code writes `private_facts` directly. All writes go through the service, which
owns validation, owner-scoping, normalization, provenance, dedupe, freshness, contradiction
marking, and audit emission.

**Relationship to the existing fact stores.** Two already exist and both stay where they
are:

- `pulse_ai_truth_facts` (`services/undx_architecture.py:500`, `record_fact`) — **zero
  production callers**. It is not adopted, because adopting a store with no writer, no
  owner-scoping semantics in use, and no consumers would mean inheriting an untested design
  for the most sensitive data in the product. It is left untouched.
- `services/pulse_briefings/facts.py` — a *briefing fact pack*, an ephemeral per-cycle dict
  that is never persisted as facts. Different concept, same word. Not a store.

`private_facts` is therefore the **second persisted fact store in the repo, and the first
one with a writer.** That is a knowing acceptance of the §12-style hazard, justified by the
above and recorded here so it is a decision rather than an accident.

---

## 6. PRIVATE GRAPH

**Canonical owner (new):** `services/private_office/graph_service.py`, over
`private_graph_nodes` and `private_graph_edges`.

No feature code inserts edges directly. The service owns node resolution, edge validation,
provenance, owner isolation, duplicate suppression, and relationship lifecycle.

**`pulse_ai_knowledge_edges` is not adopted.** It has the right shape for edges but has
**zero callers** and **no node table**, so there is nothing to reuse but a schema sketch. It
is left in place, unused, rather than extended — extending it would give the impression that
a working graph existed.

Initial node types: `PERSON, BUSINESS, PROPERTY, DOCUMENT, PROFESSIONAL, INSURANCE_POLICY,
CONTRACT, ASSET, LIABILITY`. Initial edge types: `OWNS, ADVISED_BY, COVERED_BY, SECURED_BY,
GOVERNED_BY, DESCRIBES`. The closed sets are enforced in the service. Future types are added
deliberately, not by passing a new string.

---

## 7. RISK SCORING

**See §12 for the full inventory and the reasoning.** The decision:

**Composition hierarchy, three layers, with exactly one owner per layer.**

| Layer | Owner | Scope | Scale |
|---|---|---|---|
| Raw signal | the producing domain module | one artifact (a text blob, a listing, a session) | its own |
| Domain score | the domain module | one subject in one domain | 0-100 int |
| **Overall user risk** | **`services/user_trust_engine.py`** | the user | 0-100 int |

**`user_trust_engine` is declared the sole owner of any overall user trust/risk value.** It
wins on one objective ground: it is the only overall-user scorer whose output gates a real
capability — `privilege_engine.level_for_trust` turns its `trust_score` into
`can_go_live / can_sell / can_teach / can_upload_video`, persisted at `bot.py:45492`. Every
other overall-user scorer is presentation or dead.

**The Private Office produces no overall user risk value.** Its risk detection is
*obligation-scoped and opportunity-scoped* — "this policy lapses in 9 days", "these two
documents disagree about the renewal date". Those are findings about *objects the user
owns*, never a score about *the user*. This is the line that keeps the Private Office from
becoming a fifth overall scorer, and it is the single most important constraint in this
document.

Frozen consequences:

- No Private Office module may emit a field named `risk_score`, `trust_score`, or
  `risk_level` scoped to a user.
- Private Office findings carry a **severity** (`INFO | ATTENTION | URGENT`) attached to a
  graph node or fact, not to a person.
- If a later stage genuinely needs an overall user risk value, it **reads**
  `user_trust_engine`. It does not compute one.

---

## 8. BRIEFING SIGNIFICANCE

**Canonical owner:** `services/pulse_briefings/` — weights and threshold at `facts.py:19-31`
(`WEIGHTS`, `SEND_THRESHOLD = 10`), scoring at `facts.py:205-226`, fingerprint at
`facts.py:258-283`, engine and delivery at `engine.py`.

**`private_briefing_worker.py` is forbidden.** So is any second significance model, second
quiet-hours implementation, second dedupe scheme, or second delivery path. `alert_worker.py`
already drives the cycle and remains the only driver.

The Private Office contributes **candidate facts and a significance contribution**, through
the convention documented in §9. It does not decide whether to send.

---

## 9. THE BRIEFINGS EXTENSION SEAM (correction to Stage 0)

There is **no provider registry**. Verified: no `register()`, no `PROVIDERS` list, no
`Protocol`, no ABC, no entry points anywhere in `services/pulse_briefings/`. Sources are
hardcoded inside `facts.build_briefing_facts` (`facts.py:229-255`).

The existing convention, which the Private Office provider follows exactly:

- a module exposing `collect_<domain>_facts(cur, user_id, ...) -> dict[str, Any] | None`
- a matching `<domain>_significance(dict) -> int`
- wired by direct import and gated by a boolean

**Two hazards, both binding:**

1. The provider must be gated by a **new env flag defaulting off**. It must not reuse
   `engine._env_flag(name, "true")`, whose default is on.
2. The provider's key must be added to `fact_fingerprint`'s `signature` dict
   **conditionally** — only when private-office facts are actually present. Adding it
   unconditionally changes every existing fingerprint, and the first cycle after deploy
   would send a briefing to every user on the platform. This is a real, specific production
   incident and the reason the seam is documented here rather than left to judgement.

---

## 10. DOCUMENT EXTRACTION

**Canonical owner:** none, deliberately. This domain is **PROVIDER_REQUIRED**.

There is no OCR, PDF, or document-parsing library anywhere in the tree. The Private Office
therefore owns only:

- document **metadata** (`private_graph_nodes` of type `DOCUMENT`)
- upload/reference **ownership**
- a **classification seam**
- an **extraction-provider interface** with no implementation behind it

Extraction status is pinned to `PROVIDER_REQUIRED` in the service registry (§11). No code
path may return extracted content, and none may report "no findings" for a document that was
never read.

**Note on duplicate ownership:** eight file-metadata tables already exist.
`private_graph_nodes` of type `DOCUMENT` is a **reference**, holding a pointer to the
existing media/file record plus graph relationships. It does not restate size, mime type,
checksum, or storage location. This is what keeps it from becoming the ninth.

---

## 11. PROVIDER INTEGRATIONS

**Canonical owner (new):** `services/private_office/service_registry.py`.

Every Private Office module declares exactly one state:

`IMPLEMENTED | SHADOW | PROVIDER_REQUIRED | DISABLED | NOT_IMPLEMENTED`

This registry is the only place a capability's readiness is asserted, and it is what the
client reads to decide what is tappable. Its purpose is to stop aspirational code from
becoming product truth — the precise failure the Stage 0 map found in
`services/undx_brain/` (~12,000 lines, every flag defaulting to `0`, documented as if
operational).

**Pinned states at Stage 1, not negotiable by a later stage without a real provider:**

| Capability | State | Reason |
|---|---|---|
| `private_shield.breach_monitoring` | `PROVIDER_REQUIRED` | No HIBP / DeHashed / SpyCloud / IntelX integration exists |
| `private_office.document.extraction` | `PROVIDER_REQUIRED` | No OCR or PDF library exists |

**The Sentinel boundary.** `services/sentinel/` covers **platform** security intelligence —
CISA KEV advisories and GitHub security alerts about PulseSoc's own dependencies. That is
categorically not personal identity exposure, breach monitoring, or dark-web monitoring.
Three independent facts make it unusable for the latter: wrong data domain, **the string
"sentinel" appears zero times in `bot.py`** so it is not registered at all, and its master
switch defaults off. Its semantics must not be overloaded. A future provider goes behind the
interface in this registry, not behind Sentinel's name.

---

## 12. AUDIT

**Canonical owner for agent execution:** `pulse_ai_tool_operations`
(`services/undx_architecture.py:111-117`), written by the gateway. Unchanged.

**Canonical owner for Private Office data access (new):**
`services/private_office/audit.py`, over `private_office_audit`.

Records `actor, owner, action, object_type, object_id, timestamp`, and nothing else.
**Private values are never logged** — not fact values, not node labels, not document
contents, not query text. Object ids are recorded; object contents are not. A helper that
takes a value and logs it does not exist by design.

---

## 13. STAGE 19 — THE RISK OWNERSHIP INVENTORY

Eleven score producers, in three tiers. Only the first group competes.

### Overall-user scorers (the real conflict)

| Module | Producer | Output | Persisted | Consumers |
|---|---|---|---|---|
| `services/user_trust_engine.py` | `calculate_trust_score` :15, `calculate_reputation_scores` :67 | trust 0-100, risk 0-100, safety 0-100, band | `user_trust_profiles`, `user_privilege_profiles.trust_score` (bot.py:45487-45501) | **`privilege_engine.level_for_trust` → real capability gates** (bot.py:45476, 45492) |
| `services/command_center_worker/security_engine.py` | `get_user_risk_score` :345 | risk 0-100, level enum, `trust = 100 - risk` :310 | `user_trust_score` :322 | **written and never read.** Only reader `command_center_client.get_user_risk_score` :440 has one caller: `scripts/command_center_security_audit.py:145`. Flag `COMMAND_CENTER_ENABLED` defaults **False** |
| `services/dashboard_account_command_center.py` :1409-1426 | inline | `security_score` 0-100, `risk_level` Low/Med/High | not persisted | presentation (bot.py:7041, 7867) |
| `bot.py:79421` `account_security_score` | inline | 0-100 + label | not persisted | presentation (bot.py:79473, 79502, 79572) |
| `services/sentinel/financial_risk.py` `assess` :103 with `entity_role="USER"` | — | float 0.0-1.0 | sentinel table | **test-only. Dead code.** |

**The concrete divergence.** `user_trust_engine` and `security_engine` both score
`user_id`, both on 0-100, and both persist a column literally named `trust_score` — in two
different tables. Their polarity is unrelated: `security_engine` derives `trust = 100 - risk`
from security events only, while `user_trust_engine` builds trust additively from profile
completeness and referrals and subtracts moderation strikes.

A brand-new user with no security events scores **trust 100 / "Low" risk** under
`security_engine` and **10 / "Needs trust signals" / privilege level "Visitor"** under
`user_trust_engine`. Ninety points apart on an identically named scale.

Secondary conflict: `risk_level` is a Low/Medium/High enum in
`dashboard_account_command_center.py:1422` and a Low/Medium/High/**Critical** enum in
`security_engine.py:120` — same name, different cardinality, same subject.

### Domain scorers (no conflict — different subjects)

`scam_shield.py` (a text blob), `pulse_moderation_engine.py` (a post/comment),
`revenue_safety_engine.py` (a listing), `seller_lifecycle.py` (a seller application),
`bot.py:30702` (a crypto dashboard view, whose `wallet_safety` and `scam_exposure` are
hardcoded literals 75 and 70), `sentinel/identity_trust.py` (a session, dead),
`sentinel/external_fusion.py` (a threat indicator, dead).

`predictive_ai_engine.forecast_scam_risk` :42 — **no consumers, dead code.**

### Resolution

Declared in §7: three-layer composition, `user_trust_engine` sole owner of overall user
risk, Private Office produces object-scoped severity and never a user score.

`security_engine`'s user-level score is **not** promoted and **not** deleted by this mission
— deleting it is an unrelated refactor. It is recorded here as writing dead data behind a
default-off flag, and Private Office code must not read it.

---

## 14. STAGE 3 SCOPE — THE SIX CLIENT DECIDERS

Stage 0 found two. The complete list, with four mutually inconsistent rules:

| # | Site | Rule | Nature |
|---|---|---|---|
| 1 | `mobile-native/src/navigation/AppNavigator.tsx:323` | `["active","premium","founder"]` | app-wide identity fan-out; leaks into composed post authors via `draftToContentModel.ts:63` |
| 2 | `mobile-native/src/components/ProfileHeader.tsx:204` | `["active","premium","founder","lifetime"]` | badge + tier label |
| 3 | `mobile-native/src/screens/MusicScreen.tsx:137-142` | **substring** `.includes("premium")` / `.includes("founder")` | upload status pill |
| 4 | `mobile-native/src/screens/LiveHostSessionScreen.tsx:642` | `["active","verified","pro","premium"]` | host checkmark |
| 5 | `mobile-native/src/api/premium.ts:113-117`, helper `:230-232` | `["active","trialing","premium","founder"]` | **a real client-side entitlement authority** feeding four API modules |
| 6 | `mobile-native/src/screens/ProfileScreen.tsx:315,335` | `Boolean(profile.premium_status)` | bare truthiness — `"expired"` and `"cancelled"` read as premium |

Plus the normalizer that manufactures the field:
`mobile-native/src/session/auth.ts:100` coalesces `premium_status || subscription_status`
into one raw string, which is what every site above then string-matches.

**Web surfaces are clean.** Verified: `premium_status`, `subscription_status`, `is_premium`,
`isPremium`, `lifetime_premium`, `subscription_plan`, `is_pro`, `pro_active` and `founder`
appear **nowhere** in `static/`. Jinja in `templates/account.html` reads server-computed
flags on an `access` object, which is the correct pattern. The problem is confined to
`mobile-native/`.

**The single derivation point after this mission:** the server-issued tier lands on
`PulseUser`, is mapped once in `normalizeSessionUser` (`auth.ts:87`), and is read through
one hook beside `useAuth()`. All six sites read that hook.

---

## 15. WHAT THIS CONTRACT FORBIDS

A stage that does any of the following fails review, regardless of justification:

1. A second entitlement resolver, or client-side re-derivation of tier.
2. A second capability registry or a Private Office execution runtime.
3. A verifier that reports `VERIFIED` without reading state back.
4. Direct writes to `private_facts`, `private_graph_nodes`, or `private_graph_edges` from
   feature code.
5. Any Private Office field named `risk_score`, `trust_score`, or `risk_level` scoped to a
   user.
6. `private_briefing_worker.py`, or a second significance/quiet-hours/dedupe implementation.
7. An unconditional key added to `fact_fingerprint`'s signature dict.
8. Any output claiming breach/dark-web monitoring results, including a clean "no breaches
   found".
9. Any output claiming document extraction results while no extractor is integrated.
10. Logging a private value into `private_office_audit`.
11. Overloading Sentinel's semantics to imply personal monitoring.
12. Touching livestream audio, livestream infrastructure, video-call audio, audio-call
    audio, the unified audio session, or the radio/audio coordinator. Unconditional.
