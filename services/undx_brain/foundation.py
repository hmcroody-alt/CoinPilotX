"""The Foundation map: which existing module owns each Brain responsibility (PART 1).

This module builds nothing. It is a declaration, checked by a test, of where each of the
Foundation responsibilities already lives — because the honest finding when this work
started was that almost all of them were already implemented, spread across sixteen
``services/undx_*.py`` modules plus the provider and safety layers, and the actual gap
was that nothing said so.

Two failure modes motivate it.

The first is **rebuilding what exists**. A responsibility with no named owner looks
unimplemented. Confirmation, idempotency, verification and audit receipts were all shipped
and tested; a Brain that "added" them would have produced a second confirmation store
beside :mod:`services.undx_architecture`'s, and two confirmation stores is worse than
either one alone.

The second is **silent removal**. Ownership recorded in prose rots. Recorded as
``(module, symbol)`` pairs that :func:`verify` imports, a responsibility whose owner is
deleted or renamed becomes a failing test rather than an unnoticed hole.

The map is deliberately allowed to say ``PARTIAL`` and ``UNOWNED``. A map with no gaps
is a map that stopped looking, and the gaps are the part worth reading — they are the
next work, and they are stated here rather than discovered later.

Nothing here is imported by the runtime. It is architecture documentation that fails a
build, which is the only kind that stays true.
"""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from enum import Enum


class Ownership(str, Enum):
    """How completely a responsibility is discharged by its named owner."""

    #: One module owns it, and the named symbols implement it.
    OWNED = "owned"
    #: Implemented, but split across modules, or covering only part of the
    #: responsibility as PART 1 describes it. The ``gap`` field says which part.
    PARTIAL = "partial"
    #: No implementation exists. ``owners`` is empty and ``gap`` states what is missing.
    #: This is a finding, not a defect in the map.
    UNOWNED = "unowned"


@dataclass(frozen=True)
class Responsibility:
    """One Foundation responsibility and the code that discharges it."""

    key: str
    summary: str
    ownership: Ownership
    #: ``(module, symbol)`` pairs. ``verify`` imports each module and asserts each
    #: symbol is present, so this tuple is executable documentation.
    owners: tuple[tuple[str, str], ...] = ()
    #: Why the answer is not simply "this module does it". Required for PARTIAL and
    #: UNOWNED, and worth stating even for OWNED where the placement is surprising.
    note: str = ""
    gap: str = ""


#: The responsibilities named in PART 1, each mapped to code verified to exist at the
#: time of writing by reading the module's AST rather than by recollection.
FOUNDATION: tuple[Responsibility, ...] = (
    Responsibility(
        key="canonical_identity",
        summary="Stable ids and content hashes for requests, tasks, and arguments.",
        ownership=Ownership.OWNED,
        owners=(
            ("services.undx_agent_contracts", "new_id"),
            ("services.undx_agent_contracts", "canonical_hash"),
            ("services.undx_architecture", "argument_hash"),
        ),
        note=(
            "Two hashes, deliberately. ``canonical_hash`` identifies a payload; "
            "``argument_hash`` identifies a *call*, and it is the one idempotency and "
            "confirmation redemption compare against."
        ),
    ),
    Responsibility(
        key="request_contract",
        summary="The typed shapes every stage passes: request, tool call, result, response.",
        ownership=Ownership.OWNED,
        owners=(
            ("services.undx_agent_contracts", "AgentRequest"),
            ("services.undx_agent_contracts", "ToolCall"),
            ("services.undx_agent_contracts", "ToolResult"),
            ("services.undx_agent_contracts", "AgentResponse"),
            ("services.undx_agent_contracts", "AgentTask"),
        ),
    ),
    Responsibility(
        key="authorization_scope",
        summary="What the caller is permitted to touch, enforced before the executor runs.",
        ownership=Ownership.OWNED,
        owners=(
            ("services.undx_agent_contracts", "PermissionScope"),
            ("services.undx_tool_gateway", "_enforce_permission_scope"),
        ),
        note=(
            "Enforcement is inside the gateway rather than in each executor, so a new "
            "capability cannot forget to check."
        ),
    ),
    Responsibility(
        key="owner_scoped_reads",
        summary="Cross-account isolation: a read returns only what this account owns or may see.",
        ownership=Ownership.OWNED,
        owners=(
            ("services.undx_operator", "search_owned_crypto_alerts"),
            ("services.undx_operator", "search_authorized_resources"),
            ("services.undx_operator", "search_visible_content"),
        ),
        note=(
            "Three functions rather than one because 'mine', 'I am authorized to act on "
            "it' and 'I can see it' are three different questions, and collapsing them "
            "is how a visible-to-me record becomes an actionable-by-me record."
        ),
    ),
    Responsibility(
        key="capability_registry",
        summary="The closed set of things UNDX can do, with their specs and undo graph.",
        ownership=Ownership.OWNED,
        owners=(
            ("services.undx_capability_registry", "CapabilitySpec"),
            ("services.undx_capability_registry", "get"),
            ("services.undx_capability_registry", "require"),
            ("services.undx_capability_registry", "write_capability_ids"),
            ("services.undx_capability_registry", "unregistered_tool_names"),
        ),
        note=(
            "``unregistered_tool_names`` is the parity check that makes the set closed: "
            "an executor with no spec is reported, not silently callable."
        ),
    ),
    Responsibility(
        key="argument_validation",
        summary="Required fields, types and choices checked before anything executes.",
        ownership=Ownership.OWNED,
        owners=(
            ("services.undx_agent_contracts", "FieldSpec"),
            ("services.undx_agent_contracts", "validate_arguments"),
            ("services.undx_agent_runtime", "resolve_arguments"),
            ("services.undx_agent_runtime", "missing_required"),
        ),
    ),
    Responsibility(
        key="policy_engine",
        summary="Deterministic allow/deny, risk classification, and rollout gating.",
        ownership=Ownership.OWNED,
        owners=(
            ("services.undx_agent_policy", "evaluate"),
            ("services.undx_agent_policy", "classify_risk"),
            ("services.undx_agent_policy", "Decision"),
            ("services.undx_policy", "compile_context"),
        ),
        note=(
            "``undx_agent_policy`` decides; ``undx_policy`` compiles the versioned "
            "context that shapes the prompt. Different jobs that both answer to the "
            "word 'policy', which is why they are named separately here."
        ),
    ),
    Responsibility(
        key="feature_flags",
        summary="Per-user, per-capability and per-stage activation, defaulting closed.",
        ownership=Ownership.OWNED,
        owners=(
            ("services.undx_agent_policy", "flags"),
            ("services.undx_agent_policy", "user_enabled"),
            ("services.undx_agent_policy", "capability_enabled"),
            ("services.undx_agent_policy", "writes_available"),
            ("services.undx_brain.config", "resolve"),
        ),
        note=(
            "``undx_brain.config`` is additive, not a replacement: it declares the "
            "Brain's own variables in one place so the set is knowable without grepping, "
            "and leaves the existing UNDX flags exactly where they are."
        ),
    ),
    Responsibility(
        key="rollout_gating",
        summary="Cohort and percentage gating so a change reaches QA before production.",
        ownership=Ownership.OWNED,
        owners=(
            ("services.undx_agent_policy", "log_rollout_surface"),
            ("services.undx_policy", "v5_user_enabled"),
        ),
    ),
    Responsibility(
        key="governed_gateway",
        summary="The single path from an intended tool call to a settled outcome.",
        ownership=Ownership.OWNED,
        owners=(
            ("services.undx_tool_gateway", "execute"),
            ("services.undx_tool_gateway", "GatewayOutcome"),
            ("services.undx_tool_gateway", "_settle"),
        ),
        note="Every write goes through ``execute``. That is the property worth protecting.",
    ),
    Responsibility(
        key="confirmation",
        summary="Human approval minted, redeemed once, and revocable before the write.",
        ownership=Ownership.OWNED,
        owners=(
            ("services.undx_architecture", "create_confirmation"),
            ("services.undx_architecture", "consume_confirmation"),
            ("services.undx_architecture", "revoke_confirmation"),
            ("services.undx_architecture", "approval_state"),
            ("services.undx_tool_gateway", "_mint_confirmation"),
            ("services.undx_tool_gateway", "_redeem"),
        ),
        note=(
            "Storage in ``undx_architecture``, protocol in the gateway. Batches 22–24 "
            "hardened exactly this seam: an approval is spent once and a rejected one "
            "leaves nothing behind."
        ),
    ),
    Responsibility(
        key="idempotency",
        summary="A repeated call does not repeat the effect.",
        ownership=Ownership.OWNED,
        owners=(
            ("services.undx_architecture", "begin_tool_operation"),
            ("services.undx_architecture", "record_tool_result"),
            ("services.undx_architecture", "prepare_tool_operation"),
        ),
    ),
    Responsibility(
        key="verification",
        summary="Independent read-back proving the write landed, separate from its response.",
        ownership=Ownership.OWNED,
        owners=(
            ("services.undx_verification", "verify"),
            ("services.undx_agent_contracts", "VerificationState"),
            ("services.undx_tool_gateway", "_verify"),
        ),
        note=(
            "The distinction this module exists for — a mutation's own response is a "
            "claim, not evidence — is the same distinction "
            ":mod:`services.undx_brain.truth` encodes as ``EXECUTED`` versus "
            "``VERIFIED_SUCCESS``."
        ),
    ),
    Responsibility(
        key="audit_receipts",
        summary="A durable record of what was attempted, decided, executed and verified.",
        ownership=Ownership.OWNED,
        owners=(
            ("services.undx_agent_contracts", "AgentReceipt"),
            ("services.undx_tool_gateway", "_receipt"),
            ("services.undx_tool_gateway", "_last_resort_receipt"),
        ),
        note=(
            "``_last_resort_receipt`` matters more than its name suggests: it is what "
            "keeps an unhandled failure auditable instead of invisible."
        ),
    ),
    Responsibility(
        key="failure_recovery",
        summary="Operations that failed after the point of no return are flagged, not lost.",
        ownership=Ownership.OWNED,
        owners=(
            ("services.undx_architecture", "flag_operation_for_reconciliation"),
            ("services.undx_agent_contracts", "AgentError"),
        ),
    ),
    Responsibility(
        key="task_persistence",
        summary="Multi-turn work survives the end of a request.",
        ownership=Ownership.OWNED,
        owners=(
            ("services.undx_architecture", "persist_plan"),
            ("services.undx_architecture", "resume_plan"),
            ("services.undx_architecture", "create_continuation"),
            ("services.undx_architecture", "pending_continuation"),
            ("services.undx_architecture", "burn_continuation"),
        ),
    ),
    Responsibility(
        key="working_context",
        summary="The bounded set of things held in mind for one request.",
        ownership=Ownership.PARTIAL,
        owners=(
            ("services.undx_brain.workspace", "Workspace"),
            ("services.undx_brain.workspace", "open_workspace"),
            ("services.undx_brain.workspace", "Slot"),
            ("services.undx_brain.workspace", "SLOTS"),
            ("services.undx_brain.workspace", "Summary"),
        ),
        gap=(
            "The container exists and enforces every property §5 asks for: nine slots "
            "with structural ceilings, a total item and character budget, owner scoping "
            "through the same resolver memory uses, secrets refused at entry, expiry as "
            "abandonment, and nothing durable without an explicit ``retain``. What is "
            "still open is that nothing *fills* it on the live path. Context is still "
            "assembled per call site in ``undx_architecture``, so today this bounds a "
            "workspace nobody opens. Half of that is now answerable: "
            "``attention.place_into`` fills the skill slot from a focus rather than from "
            "the eighty-record registry, and it does so without a single refusal, which "
            "is the guarantee — do not load all capabilities into every request — "
            "enforced rather than described. The other half — what the person wants "
            "*done* with what attention selected — now has an answer too, in "
            "``goals.understand``, though what that answer often is is \"not determined "
            "by this sentence\". All three are still off by flag and none is called from "
            "``undx_architecture`` yet, so the live path is unchanged."
        ),
        note=(
            "Two refusals here are the whole point and both are the less convenient "
            "option. A full slot refuses instead of evicting, because the entry an "
            "eviction policy drops first is the oldest, and the oldest is usually the "
            "constraint the person stated in their first sentence. And a different value "
            "under an occupied key is refused rather than overwritten: silent "
            "replacement is exactly how the resource changes between the moment it is "
            "understood and the moment it is acted on. A correction goes through "
            "``revise`` so it appears in the record."
        ),
    ),
    Responsibility(
        key="attention",
        summary="Which parts of the product one request is about, and which it is not.",
        ownership=Ownership.PARTIAL,
        owners=(
            ("services.undx_brain.attention", "attend"),
            ("services.undx_brain.attention", "place_into"),
            ("services.undx_brain.attention", "Focus"),
            ("services.undx_brain.attention", "Area"),
            ("services.undx_brain.attention", "Cue"),
            ("services.undx_brain.attention", "Concern"),
            ("services.undx_architecture", "apply_attention"),
        ),
        gap=(
            "§6's worked example passes on the real map: \"Why is my account acting "
            "strange?\" opens account health, sessions and devices, notifications, "
            "settings and support tickets, and leaves Marketplace, music and crypto "
            "shut. This entry used to add that nothing on the live path called it. "
            "That is no longer true: ``undx_architecture.apply_attention`` runs inside "
            "``build_plan``, which ``pulse_ai_service`` calls on every send, so a real "
            "request now routes and the plan carries the focus it produced.\n\n"
            "What the call site is allowed to do is deliberately narrow. It rewrites "
            "the plan's retrieval objective and nothing else. It does not touch "
            "``plan['skills']``, and that restraint is the point rather than an "
            "omission: ``attend`` matches on the words in the request, so letting it "
            "add a skill would make a phrase somebody typed into a grant of capability "
            "and would fuse a text router with an authorisation decision. Routing "
            "decides what UNDX looks at; the policy layer decides what it may do.\n\n"
            "Three things keep this PARTIAL. The gate defaults off, so production "
            "still selects context the old way until it is turned on. The routing "
            "vocabulary is the map's own text, so a request phrased in words no "
            "capability record uses reaches nothing by design and returns an empty "
            "focus rather than a guess — and the call site is required to leave "
            "retrieval unnarrowed in that case, because turning \"we did not "
            "understand this\" into \"there is nothing to find\" is the more dangerous "
            "of the two errors. And the concern frames are editorial, hand-written, "
            "and cover two question shapes out of many."
        ),
        note=(
            "The hard half of §6 is the negative clause, and one rule does most of it: "
            "an area activates only on a *structural* cue — a recorded phrasing, a "
            "capability id, a resource type, an area heading or a concern — never on "
            "description prose alone. Both false positives found while building this "
            "were prose. \"Find my Bitcoin alert\" opened Marketplace and Music because "
            "\"find\" is in their phrasings, and the §6 example itself opened Social "
            "relationships because \"account\" appears in the text describing blocking "
            "— which would have offered ``social.follow``, a write, to somebody "
            "reporting that their account was misbehaving.\n\n"
            "Two further decisions are load-bearing. There is no second catalogue: "
            "every area, resource type and phrasing is read at import from "
            "``services.undx_knowledge_map``, so a capability removed from the registry "
            "stops being attendable immediately instead of lingering in a hand-kept "
            "copy. And a request matching nothing activates nothing — no default area, "
            "no fallback to the most popular one — because activating something so the "
            "turn has material to work with is how a question nobody understood gets "
            "answered confidently about the wrong subject."
        ),
    ),
    Responsibility(
        key="goal_understanding",
        summary="What the person wants to be true when the turn ends — or that the sentence does not say.",
        ownership=Ownership.PARTIAL,
        owners=(
            ("services.undx_brain.goals", "understand"),
            ("services.undx_brain.goals", "Goal"),
            ("services.undx_brain.goals", "Shape"),
            ("services.undx_brain.goals", "REPAIR_FRAMES"),
            ("services.undx_brain.goals", "SCOPE_FRAMES"),
        ),
        gap=(
            "§7's three phrasings of one object read three different ways on the real "
            "eighty-capability registry: \"Find my Bitcoin alert\" settles on "
            "``crypto.alerts.list``; \"Fix my Bitcoin alert\" settles on nothing and "
            "offers ``crypto.alerts.get`` and ``crypto.alerts.list`` as the reads that "
            "would determine it; \"Help me manage my alerts\" reads as a scope no single "
            "registered capability satisfies. What is not done is the same thing that is "
            "not done for attention: nothing on the live path calls it. "
            "``undx_agent_runtime.undx_handle`` still takes ``match_capability``'s best "
            "capability as the goal, which is the behaviour that lets \"fix my alert\" become "
            "``crypto.alerts.delete``. Two narrower gaps are honest ones. The repair and "
            "scope frames are hand-written editorial lists — every entry is defended by "
            "a test, but a phrasing nobody thought of reads as ``UNKNOWN`` rather than "
            "as repair. And a goal is never settled *against state*: this layer reads no "
            "account data at all, which is precisely why \"fix\" cannot be settled here "
            "and must be handed back as an inspection."
        ),
        note=(
            "Everything else in the request path is built to converge. "
            "``match_capability`` returns its best capability, argument resolution fills "
            "the fields, the gateway runs it — and given \"my alert is broken, fix it\" "
            "that machinery finds *something*, because \"my\" and \"alert\" are enough to "
            "score a match. The most plausible reading a scoring matcher can produce for "
            "that sentence is a delete. This layer exists to say the honest thing "
            "instead: the goal is not determined yet, here is the read that would "
            "determine it, and no write may be selected until it has been done. So an "
            "unsettled goal is a *result*, not a failure — ``settled`` is false and "
            "``ok`` is true, because the system understood the sentence perfectly and "
            "understood that the sentence does not name an operation.\n\n"
            "Three rules keep that from decaying. An unsettled goal never resolves to a "
            "write: ``inspect_with`` is filtered on the map's own ``risk_class`` and "
            "``capability_id`` is empty whenever ``settled`` is false. A frame belongs in "
            "``REPAIR_FRAMES`` only if it names *nothing that could be executed* — "
            "\"restore\", \"reset\" and \"turn back on\" fail that test, and a test "
            "enforces it against the live registry rather than trusting the list. And "
            "inference from vocabulary alone is allowed exactly once and only when it "
            "cannot reach a write: when no registered phrasing matches but every "
            "executable capability in range is a read, the goal is an undetermined "
            "retrieval; if a single write is in range the inference is refused and the "
            "goal stays unknown.\n\n"
            "``asks_for_action`` is named after ``undx_agent_runtime.asks_for_the_action`` "
            "and reports only that. The runtime has two negation mechanisms — that "
            "frame-level one, and the verb-scoped ``_negation_blocks`` inside the matcher "
            "— and \"do not delete alert 3\" passes the first while being stopped by the "
            "second. An earlier name, ``writes_excluded``, would have read false for a "
            "sentence whose write was in fact excluded."
        ),
    ),
    Responsibility(
        key="planning",
        summary="Bounded plan construction and skill selection.",
        ownership=Ownership.PARTIAL,
        owners=(
            ("services.undx_architecture", "build_plan"),
            ("services.undx_architecture", "select_skills"),
            ("services.undx_architecture", "seed_registries"),
            ("services.undx_architecture", "apply_bounds"),
            ("services.undx_brain.bounds", "Budget"),
            ("services.undx_brain.bounds", "admit"),
            ("services.undx_brain.bounds", "Ledger"),
            ("services.undx_brain.execution", "Step"),
            ("services.undx_brain.execution", "StepOutcome"),
            ("services.undx_brain.execution", "Run"),
            ("services.undx_brain.execution", "execute"),
        ),
        gap=(
            "All four ceilings have a caller now. ``build_plan`` runs every plan through "
            "``bounds.admit``, and an over-budget plan is blocked rather than shortened; "
            "``undx_brain.execution.execute`` walks an admitted plan through a "
            "``Ledger``, which is what the tool-call, retry and timeout ceilings had "
            "been waiting for. A call is spent per attempt rather than per step, so a "
            "retry cannot buy its way past the tool-call budget; a write is never "
            "retried, because a timeout does not say whether the first attempt landed; "
            "and expiry stops a run where it stands instead of carrying its remaining "
            "steps into the next request. A run that stops part-way reports ``ok=False`` "
            "and names both the writes that landed and the ones whose outcome is "
            "unknown, which are different lists on purpose.\n\n"
            "What is not done. Nothing on the live path calls ``execute``: it is behind "
            "``UNDX_BRAIN_EXECUTOR_ENABLED``, which defaults off, and with the flag off "
            "it performs nothing rather than quietly collapsing to one step. Plan "
            "execution in the runtime is still one step per request, so the multi-step "
            "path is exercised only by its own tests. The executor also does not decide "
            "what the steps are — it is handed an ordered list and never reorders it, "
            "so plan *construction* past ``build_plan``'s fixed four-node shape still "
            "has no owner. And it cannot undo anything: when a run stops with two of "
            "three writes landed, it says so and stops, which is honest but leaves the "
            "person holding a half-finished goal that nothing offers to reverse."
        ),
        note=(
            "``BOUNDED_NODE_TYPES`` decides what counts as a step. Only acting nodes do; "
            "the understand/retrieve/verify scaffolding appears on every plan, so "
            "counting it would mean a one-step ceiling refused every request ever "
            "served.\n\n"
            "``execution`` performs nothing itself and imports nothing that could — no "
            "gateway, no registry, no policy engine, no database. It is handed a "
            "``perform`` callable and can only count. That is the structural reason it "
            "cannot become a second path to the gateway, and a test asserts the absence "
            "of those imports rather than trusting the docstring. The one shape it will "
            "not accept from ``perform`` is an ambiguous one: anything that is not a "
            "``StepOutcome`` reads as ``UNKNOWN``, so a callable rewritten to return a "
            "boolean fails closed instead of reporting every step done."
        ),
    ),
    Responsibility(
        key="action_selection",
        summary="Choosing which one operation to run, out of everything that could be run.",
        ownership=Ownership.PARTIAL,
        owners=(
            ("services.undx_agent_runtime", "match_capability"),
            ("services.undx_architecture", "select_skills"),
            ("services.undx_agent_policy", "evaluate"),
            ("services.undx_brain.goals", "understand"),
            ("services.undx_brain.selection", "rank"),
            ("services.undx_brain.selection", "select"),
            ("services.undx_brain.selection", "Separator"),
            ("services.undx_brain.selection", "Selection"),
            ("services.undx_brain.selection", "NEAR_TIE"),
        ),
        gap=(
            "Holding two candidates at once now has an owner and using the result does "
            "not. ``undx_brain.selection.rank`` keeps the matcher's whole ranked list "
            "instead of its argmax — the matcher's own scoring functions, borrowed "
            "rather than reimplemented, with a test holding the top of the list against "
            "``match_capability`` across all 254 registered phrasings — and ``select`` "
            "separates a near-tie on declared data: a read over a write, a reversible "
            "write over an irrecoverable one, a narrower blast radius over a wider one. "
            "Two contested writes that none of those rules tell apart come back "
            "undecided, which on the live registry is all sixteen pairs of an operation "
            "with its own inverse. So \"two capabilities score alike\" is now sayable, "
            "and the one-point gap between pausing somebody's alerts and listing them "
            "is visible rather than silently resolved.\n\n"
            "What is not done. Nothing on the live path calls any of it: ``select`` is "
            "behind ``UNDX_BRAIN_SELECTION_ENABLED``, which defaults off, and "
            "``undx_agent_runtime.undx_handle`` still takes ``match_capability``'s single "
            "answer — one spec or ``None``, the runners-up discarded inside the matcher "
            "before any caller could weigh them. "
            "``undx_architecture.select_skills`` is untouched and still a set intersection against each "
            "skill's declared tools plus four hard-coded words in the message text. The "
            "write-separation rules cannot be reached through a sentence at all, because "
            "no registered phrasing puts two writes in the same band; they are exercised "
            "against real capability pairs with the band assembled by the test, and a "
            "test asserts that is still true so the day a phrasing contests two writes "
            "is a failure rather than a surprise. And the preference order is a "
            "judgement — reversibility before width before undo cost — defended by what "
            "it does to the registry's 120 write pairs, not derived from anything."
        ),
        note=(
            "``goals.understand`` is listed as an owner because it is the only thing "
            "here that can decline to select. Its contribution is negative and that is "
            "the point: for a request that names no operation it returns an unsettled "
            "goal with an empty ``capability_id``, where the matcher alone would have "
            "returned its best guess and the gateway would have run it. Selection that "
            "cannot abstain is not selection.\n\n"
            "``selection.select`` abstains in the same shape and for the same reason, "
            "one layer down: where ``goals`` declines because the sentence names no "
            "operation, this declines because the sentence names two and the scoring "
            "cannot tell which. Its undecided result has no ``best_guess`` field, on "
            "purpose — a refusal with a fallback attached is advice, and a caller that "
            "genuinely wants the highest-scoring capability can still call "
            "``match_capability``, which is unchanged and still returns exactly that."
        ),
    ),
    Responsibility(
        key="prediction",
        summary="What would happen if this ran, worked out before it runs.",
        ownership=Ownership.PARTIAL,
        owners=(
            ("services.undx_brain.prediction", "predict"),
            ("services.undx_brain.prediction", "check"),
            ("services.undx_brain.prediction", "Reversal"),
            ("services.undx_brain.prediction", "Prediction"),
            ("services.undx_brain.prediction", "Outcome"),
            ("services.undx_architecture", "simulate_operation"),
            ("services.undx_architecture", "causal_analysis"),
        ),
        gap=(
            "Three of the four things this responsibility names now have an owner and "
            "one still does not. ``undx_brain.prediction.predict`` reads a proposed "
            "call against the capability registry's undo graph and answers differently "
            "for capabilities that differ there: the state the verifier should read "
            "back, whether the call is reversible *now* or only after it verifies and "
            "yields a canonical id, which prior values it destroys that are recorded "
            "nowhere, and which other declared writes contend for the same resource. "
            "``check`` scores that against what was actually read back, which is what "
            "makes it falsifiable rather than descriptive. What has no owner is "
            "causal inference. ``causal_analysis`` still partitions observations using "
            "a ``kind`` field the caller already set and reports "
            "``root_cause_confirmed`` from a flag the caller also set; nothing infers a "
            "cause from evidence, and ``prediction`` does not attempt to — it describes "
            "one proposed call and stops. Nothing on the live path calls ``predict`` "
            "either. It has exactly one importer, ``undx_brain.selection``, which reads "
            "a prediction to prefer the reversible of two contested writes — one dark "
            "module calling another, since both sit behind flags whose defaults are off. "
            "With "
            "``UNDX_BRAIN_PREDICTION_ENABLED`` off both entry points answer ``ok=False``, "
            "and selection then refuses to choose rather than falling back to the score. "
            "The gateway still executes writes without asking what they would do first."
        ),
        note=(
            "``simulate_operation`` is retained as an owner and is the weakest one "
            "here. It checks the tool is registered, redacts anything whose key looks "
            "secret, and returns a ``predicted_outcome`` that is one of two constants "
            "chosen by whether the caller passed a failure string; asked about a call "
            "that undoes itself by negating a boolean and one that destroys a row with "
            "no undo, it returns the same answer, and a test in "
            "``tests/undx_brain/test_prediction.py`` pins exactly that so the claim "
            "cannot quietly go stale. It is not *wrong*, though, and the distinction "
            "mattered for whoever filled it: every field it returns is either a fact "
            "(``production_write: False``) or an explicit statement of ignorance "
            "(``\"Real outcome requires an authorized tool result.\"``), so adding real "
            "inference beside it required retracting nothing."
        ),
    ),
    Responsibility(
        key="reasoning",
        summary="Domain and cross-domain readings over retrieved records.",
        ownership=Ownership.OWNED,
        owners=(
            ("services.undx_domain_reasoning", "build_reading"),
            ("services.undx_domain_reasoning", "DomainReading"),
            ("services.undx_cross_domain", "build_cross_reading"),
            ("services.undx_cross_domain", "is_cross_domain"),
        ),
    ),
    Responsibility(
        key="specialist_domains",
        summary="Reading a record the way somebody who knows that product area would read it.",
        ownership=Ownership.PARTIAL,
        owners=(
            ("services.undx_domain_reasoning", "ANALYSERS"),
            ("services.undx_domain_reasoning", "domain_for"),
            ("services.undx_cross_domain", "build_cross_reading"),
            ("services.undx_knowledge_map", "PRODUCT_AREAS"),
        ),
        gap=(
            "Ten analysers, against eighty capabilities across forty-four product "
            "areas. Account health, verification, support tickets, groups, events, "
            "music, creator analytics, presence privacy and localisation have a "
            "specialist reading; everything else — Marketplace, crypto alerts, feed "
            "posts, reels, messages, saved content, security, premium, ads, live — "
            "falls back to the shape-only layer, which counts records and reports "
            "degradation and says nothing about what the records mean. That fallback is "
            "correct behaviour and it is also the gap: seventy capabilities currently "
            "answer with arithmetic."
        ),
        note=(
            "Each analyser is bound to a capability id rather than to an area, which is "
            "the stricter of the two choices and deliberate. An analyser keyed by area "
            "would run against records from any capability filed under it, including "
            "ones whose fields it has never seen; keyed by capability, an analyser only "
            "ever reads the record contract it was written for, and a new capability in "
            "a covered area gets shape-only reasoning until somebody writes its "
            "analyser instead of getting a neighbour's by accident.\n\n"
            "The ceiling on all of this is the module's own rule: an analyser may count, "
            "quote a declared field, and compare it to a value the schema defines — it "
            "may not judge. So \"specialist\" here means knowing which field decides the "
            "question, not having an opinion about the answer. Creator analytics is the "
            "test case: the average engagement score is reported and never called low, "
            "because no baseline exists in the data and the judgement would be the "
            "module's own wearing the evidence layer's badge."
        ),
    ),
    Responsibility(
        key="adversarial_check",
        summary="Self-challenge and confidence calibration before a claim is made.",
        ownership=Ownership.OWNED,
        owners=(
            ("services.undx_architecture", "adversarial_verify"),
            ("services.undx_architecture", "calibrate_confidence"),
            ("services.undx_architecture", "causal_analysis"),
        ),
    ),
    Responsibility(
        key="metacognition",
        summary="Knowing what it does not know, and saying so before it is asked.",
        ownership=Ownership.PARTIAL,
        owners=(
            ("services.undx_brain.evidence", "derive"),
            ("services.undx_brain.evidence", "Assessment"),
            ("services.undx_brain.truth", "meets"),
            ("services.undx_brain.truth", "hedge_for"),
            ("services.undx_brain.goals", "Goal"),
            ("services.undx_brain.calibration", "pair"),
            ("services.undx_brain.calibration", "calibrate"),
            ("services.undx_brain.calibration", "by_capability"),
            ("services.undx_brain.calibration", "Verdict"),
            ("services.undx_brain.calibration", "Calibration"),
            ("services.undx_architecture", "calibrate_confidence"),
        ),
        gap=(
            "Within one turn this is real and enforced: ``derive`` reads how well a "
            "thing is known off the evidence rather than off the model's tone, "
            "``may_claim_live_state`` and ``may_say_done`` gate the two claims that "
            "cause the most damage when wrong, ``hedge_for`` supplies the wording, and "
            "an unsettled ``Goal`` says outright that the request does not name an "
            "operation. Across turns there is now one thing and only one: "
            "``calibration.calibrate`` joins ``agent_action`` and ``message_answered`` "
            "to ``feedback_recorded`` on the ``message_id`` all three carry, so of the "
            "answers already given it can say which were judged wrong, and "
            "``by_capability`` can say it per capability. So the second of the three "
            "questions this entry used to list as unanswered — whether a capability "
            "keeps being selected and then corrected — has an answer, and the table "
            "that used to be read only by a row count now has a reader that opens a "
            "row."
            "\n\n"
            "What is still missing is most of it. Nothing on the live path calls "
            "``calibrate``; it is behind ``UNDX_BRAIN_CALIBRATION_ENABLED``, which "
            "defaults off, and no module imports it. The third question — whether one "
            "*question shape* reliably goes wrong — has no owner at all, because "
            "nothing on the writing side records what shape a question had; "
            "``agent_action`` carries a capability id and ``message_answered`` carries "
            "a provider and a latency, and neither is the shape of what was asked. The "
            "answer that arrives is also only ever the one people volunteered: "
            "``calibrate`` reports the unjudged count beside every rate for that "
            "reason, but reporting the bias is not correcting it, and no correctness "
            "rate here is a rate over the answers, only over the rated ones. And "
            "``memory_corrected`` carries a ``memory_id`` and no ``message_id``, so a "
            "correction to something UNDX remembered cannot be attributed to the answer "
            "that produced it — a gap in the eleven writing call sites, not something "
            "the reader can infer around. Noticing is also still not learning: nothing "
            "acts on any of this, by design and with a test holding the design in "
            "place."
        ),
        note=(
            "``calibrate_confidence`` is listed for completeness and is the weakest "
            "owner here. It is a four-way lookup over four booleans the *caller* "
            "supplies — authoritative, current, conflicting, inferred — so it converts "
            "a judgement already made into a score and a phrase, and calls nothing to "
            "check any of the four. ``evidence.derive`` is the one that reads state, and "
            "it is the one to build on."
            "\n\n"
            "The two halves of this entry answer different questions and must not be "
            "read as one capability. ``evidence`` and ``truth`` decide what may be said "
            "*now*, before the response is sent, and are binding. ``calibration`` says "
            "what the record shows about answers already sent, and is advisory to a "
            "human reading it — its refusal to reach ``selection`` is what keeps the "
            "second from quietly becoming the first. That refusal is deliberate: a "
            "capability that is often corrected has not been shown to have caused the "
            "correction, and down-ranking it on that evidence would be a causal claim "
            "made where nobody reviewing the selection code would see it. "
            "``MIN_JUDGED`` is 12 because 12 is the smallest sample whose worst-case "
            "95% Wilson interval is narrower than the coarsest distinction the rate "
            "would ever be used to draw, and the test recomputes that rather than "
            "asserting the number."
        ),
    ),
    Responsibility(
        key="factuality_enforcement",
        summary="What the response is permitted to assert, given the evidence in hand.",
        ownership=Ownership.OWNED,
        owners=(
            ("services.undx_response_intelligence", "_prohibited_claims"),
            ("services.undx_response_intelligence", "_allowed_numbers"),
            ("services.undx_response_intelligence", "_sayable"),
            ("services.undx_response_intelligence", "EvidenceView"),
        ),
        note=(
            "Private names, deliberately cited. The enforcement is real and tested; "
            "the leading underscores record that no caller outside the module should "
            "reach past ``build_plan`` to invoke it piecemeal."
        ),
    ),
    Responsibility(
        key="communication",
        summary="Turning a settled outcome into words and a native card, deciding nothing.",
        ownership=Ownership.OWNED,
        owners=(
            ("services.undx_response_intelligence", "build_plan"),
            ("services.undx_response_intelligence", "ResponsePlan"),
            ("services.undx_agent_runtime", "build_card"),
            ("services.undx_agent_contracts", "NativeCard"),
        ),
        note=(
            "PART 10's requirement that the communication layer never decides success "
            "holds structurally: it receives a settled ``GatewayOutcome`` and has no "
            "path back to the executor."
        ),
    ),
    Responsibility(
        key="native_context_validation",
        summary="Client-supplied UI context treated as untrusted before it reaches reasoning.",
        ownership=Ownership.OWNED,
        owners=(
            ("services.undx_architecture", "sanitize_ui_context"),
            ("services.undx_architecture", "notification_action_from_text"),
        ),
    ),
    Responsibility(
        key="prompt_injection_boundary",
        summary="Observed content is framed as data, never as instruction.",
        ownership=Ownership.PARTIAL,
        owners=(
            ("services.undx_architecture", "sanitize_ui_context"),
            ("services.pulse_ai_safety", "classify_request"),
            ("services.pulse_ai_safety", "redact_sensitive_text"),
            ("services.undx_brain.corpus", "prompt_block"),
            ("services.undx_brain.envelope", "seal"),
            ("services.undx_brain.envelope", "neutralise"),
            ("services.undx_brain.envelope", "wrap"),
            ("services.undx_brain.envelope", "Provenance"),
            ("services.undx_brain.envelope", "is_sealed"),
            ("services.pulse_ai_web_search", "context_block"),
            ("services.pulse_ai_knowledge", "build_system_prompt"),
        ),
        gap=(
            "This entry used to say that each source of untrusted text is fenced by "
            "whatever module handles it, so the boundary was real but not uniform. That "
            "was wrong on the facts, not merely incomplete, and correcting it is the "
            "more useful half of this note. Two things were found by running the code "
            "rather than by reading it. First, ``prompt_block``'s fence could be "
            "escaped: a record whose summary contained the closing tag rendered a second "
            "one, and everything after it read as text outside the fence — the position "
            "that carries instruction authority. Second, and larger, live web search "
            "results were not fenced at all. ``pulse_ai_web_search.context_block`` "
            "renders them with a preamble and no envelope, "
            "``pulse_ai_service`` inserts that string into the ``knowledge`` list, and "
            "``pulse_ai_knowledge.build_system_prompt`` renders ``knowledge`` into the "
            "**system message** under the heading ``Approved PulseSoc knowledge``. Text "
            "from a stranger's web page — the most attacker-controllable input in the "
            "system, since anybody who can rank for a query can write into it — was "
            "arriving labelled as approved, in the message that carries the most "
            "authority in the request.\n\n"
            "Both are now wired. ``prompt_block`` calls ``neutralise`` unconditionally, "
            "so the corpus escape is closed in every deployment rather than behind a "
            "flag — escaping only touches reserved tags, so there was no behaviour to "
            "gate. ``context_block`` and the personalization-memory section of "
            "``build_system_prompt`` call ``wrap``, which *is* gated, because sealing "
            "changes prompt text for every request and that is a real behaviour change "
            "however safe its direction. With the flag off both render byte-identically "
            "to what they always rendered.\n\n"
            "A third source was found while wiring the second, and it is the reason to "
            "read this entry rather than trust its summary. Personalization memory is "
            "the person's own words, replayed, under a heading calling it "
            "user-approved — which is true about how it was stored and says nothing "
            "about what it may now command. An instruction is addressed to a moment, "
            "and replaying one from three weeks ago is how a system acts on a request "
            "that was already satisfied or retracted. It is now sealed as "
            "``REMEMBERED``.\n\n"
            "One claim in the original gap has been withdrawn rather than fixed. It "
            "listed ``sanitize_ui_context`` as an uneven fence; it is not a fence at "
            "all, and does not need to be. Native context is key-allowlisted into a "
            "dict that ``pulse_ai_service`` stores and returns to the client, and it is "
            "never passed to ``build_messages`` — verified by reading the call, not "
            "assumed. Keeping text out of the prompt entirely is a stronger guarantee "
            "than fencing it, so the right move was to stop describing it as a weaker "
            "one.\n\n"
            "PARTIAL rather than OWNED, for two reasons that will not be closed by more "
            "wiring. The gate defaults off, so every deployment that exists today still "
            "sends web-search text unfenced until somebody sets the flag; a boundary "
            "that is switched off is not a boundary, and calling this OWNED would be "
            "claiming the deployed system has a property it does not have. And the "
            "structural limit remains: an envelope stops a payload escaping its "
            "position and does not stop it arguing from inside. No amount of fencing "
            "changes that, so no amount of fencing will make this entry OWNED."
        ),
        note=(
            "The mechanism is worth stating because the obvious reading of 'envelope' is "
            "weaker than what is implemented. Sealing does not ask the payload to avoid "
            "the closing token; it removes the payload's ability to produce one, by "
            "escaping every reserved tag before rendering, case-insensitively and "
            "tolerant of whitespace inside the tag. The invariant that buys is one line "
            "long and is asserted against every breakout shape the tests could think of: "
            "the closing fence appears exactly once, whatever the payload is. A nonce "
            "would give the same guarantee and was rejected for a non-security reason — "
            "it makes the rendered prompt differ on every request, which costs "
            "testability, log diffs and upstream caching, and escaping does not depend "
            "on the tag being secret anyway.\n\n"
            "``Provenance`` answers two questions per source rather than one, because "
            "they have different answers. Exactly one source may instruct, and "
            "remembered text is not it even though it was the person's own words: an "
            "instruction is addressed to a moment, and replaying one from three weeks "
            "ago is how a system acts on a request that was already satisfied or "
            "retracted. And ``speaks_to_account_state`` is false for every source "
            "including the person, because somebody saying they have three alerts does "
            "not create three alerts. That property exists to be false rather than to "
            "vary; the database and ``truth`` answer state, and this module defers to "
            "them instead of restating them."
        ),
    ),
    Responsibility(
        key="deep_links",
        summary="Canonical native destinations for a capability or result.",
        ownership=Ownership.OWNED,
        owners=(
            ("services.undx_knowledge_map", "native_navigation_view"),
            ("services.undx_knowledge_map", "AuthorizationScope"),
        ),
    ),
    Responsibility(
        key="product_knowledge",
        summary="What PulseSoc can do, and how well that is known.",
        ownership=Ownership.OWNED,
        owners=(
            ("services.undx_knowledge_map", "get"),
            ("services.undx_knowledge_map", "product_knowledge_view"),
            ("services.undx_knowledge_map", "ProductCapabilityRecord"),
            ("services.undx_platform_knowledge", "retrieve"),
            ("services.undx_brain.knowledge", "retrieve"),
        ),
        note=(
            "Three layers over three artifacts, which is intentional and worth stating "
            "so the next reader does not consolidate them by mistake. "
            "``undx_knowledge_map`` is hand-curated and authoritative; "
            "``undx_platform_knowledge`` serves the generated manifest; "
            "``undx_brain.knowledge`` serves the 1,682-record source corpus with "
            "provenance and trust attached. Only the last one carries a trust level, "
            "which is why it is the one a claim can be qualified from."
        ),
    ),
    Responsibility(
        key="skill_lifecycle",
        summary="Discovered → mapped → implemented → tested → live-verified → available.",
        ownership=Ownership.PARTIAL,
        owners=(
            ("services.undx_knowledge_map", "ImplementationStatus"),
            ("services.undx_knowledge_map", "ReadinessClass"),
            ("services.undx_knowledge_map", "classify_readiness"),
            ("services.undx_knowledge_map", "readiness_matrix"),
        ),
        gap=(
            "``ImplementationStatus`` and ``ReadinessClass`` already express most of "
            "PART 6's lifecycle, but they classify *product capabilities*, not skills, "
            "and nothing gates skill availability on reaching a stage. The lifecycle is "
            "described and measured; it is not yet enforced."
        ),
    ),
    Responsibility(
        key="degradation_tracking",
        summary="A partial read is reported as partial, all the way to the response.",
        ownership=Ownership.OWNED,
        owners=(
            ("services.undx_personal_intelligence_service", "collecting"),
            ("services.undx_agent_contracts", "ToolResult"),
            ("services.undx_agent_tools", "_content_read"),
        ),
        note=(
            "``ToolResult.degraded_sources`` is the carrier. It survives to the native "
            "card, which is the point: a degraded read must not render identically to a "
            "complete one."
        ),
    ),
    Responsibility(
        key="provider_routing",
        summary="Choosing and calling a model provider, with fallback and identity enforcement.",
        ownership=Ownership.OWNED,
        owners=(
            ("services.pulse_ai_provider_router", "generate_response"),
            ("services.pulse_ai_provider_router", "configured_providers_for_task"),
            ("services.pulse_ai_provider_router", "prepare_undx_model_request"),
            ("services.pulse_ai_provider_router", "undx_identity_violation"),
        ),
        note=(
            "Outside the ``undx_*`` namespace, which is why it was easy to miss when "
            "cataloguing. ``prepare_undx_model_request`` and ``undx_identity_violation`` "
            "put the identity guarantee at the provider boundary so it holds for every "
            "provider rather than once per call site."
        ),
    ),
    Responsibility(
        key="memory_isolation",
        summary="Per-owner memory scopes that cannot read across accounts.",
        ownership=Ownership.PARTIAL,
        owners=(
            ("services.undx_brain.memory", "MemoryKind"),
            ("services.undx_brain.memory", "CLASSES"),
            ("services.undx_brain.memory", "Scope"),
            ("services.undx_brain.memory", "open_scope"),
            ("services.undx_brain.memory", "read"),
            ("services.undx_brain.memory", "write"),
            ("services.undx_brain.memory", "forget"),
        ),
        gap=(
            "The rule now has an owner: ``memory`` names PART 7's seven kinds, binds the "
            "owner id itself rather than accepting one from the caller, and reads "
            "``UNDX_MEMORY_FAIL_CLOSED``. What is still partial is reach. The roughly "
            "one hundred existing hand-written ``WHERE user_id = ?`` clauses in "
            "``pulse_ai_service`` and ``undx_architecture`` are correct but have not "
            "been routed through ``open_scope``, so for those queries the guarantee "
            "still rests on the discipline of the call site. Closing this means "
            "migrating them, not writing more of this module."
        ),
        note=(
            "The isolation is structural rather than conventional: a caller writes the "
            "literal ``{owner}`` marker and never supplies its value, so there is no "
            "parameter through which a different account's id could be passed, and a "
            "statement without the marker is declined rather than run."
        ),
    ),
    # The seven memory classes named in PART 7. ``memory_isolation`` above covers the one
    # thing common to all of them — who may read a row. These seven cover what is
    # different about each: what is kept, what puts it there, what takes it away, and how
    # it is found again. Every one names a table that exists and is written today, so no
    # entry below is speculative; what varies, sharply, is whether anything reads it back
    # for the purpose the class exists to serve.
    Responsibility(
        key="memory_conversation",
        summary="Prior turns, so a reply is continuous rather than amnesiac.",
        ownership=Ownership.PARTIAL,
        owners=(
            ("services.undx_brain.memory", "MemoryKind"),
            ("services.undx_brain.memory", "BY_KIND"),
            ("services.pulse_ai_service", "_history_for_prompt"),
        ),
        gap=(
            "Turns are stored and recent ones are read back, and that is the whole of "
            "it. Recall is recency — the last N rows — not relevance, so a request that "
            "depends on something said twenty turns ago reaches nothing while carrying "
            "nineteen turns of irrelevant text into the context it does have. There is "
            "no consolidation: nothing turns a long thread into a durable summary, so "
            "the table grows without bound and the useful part of it becomes steadily "
            "harder to find. ``summarize_conversation`` exists in "
            "``messenger_intelligence_service`` and is a product feature over the "
            "*user's own* direct messages; it has nothing to do with this."
        ),
    ),
    Responsibility(
        key="memory_preference",
        summary="Standing preferences, the only class held indefinitely.",
        ownership=Ownership.PARTIAL,
        owners=(
            ("services.undx_brain.memory", "MemoryKind"),
            ("services.pulse_ai_service", "_user_memory"),
            ("services.pulse_ai_service", "correct_memory"),
            ("services.pulse_ai_service", "delete_memory"),
        ),
        gap=(
            "Every preference in this table got there because the person typed it. "
            "Nothing infers a preference from behaviour, which is the conservative "
            "choice and the right default — an inferred preference is a claim about "
            "somebody made without asking them — but it means the class only ever holds "
            "what was volunteered. The unhandled case is conflict: two preferences that "
            "cannot both be honoured are both stored, both returned, and resolved by "
            "whichever the reader happens to use first. Correction and deletion are "
            "sound, and ``forget`` reaches this table, so the removal half is done."
        ),
        note=(
            "This is the only class behind its own flag "
            "(``UNDX_MEMORY_USER_PREFERENCES_ENABLED``) as well as the master switch, "
            "because it is the only one whose contents shape an answer without ever "
            "appearing in it."
        ),
    ),
    Responsibility(
        key="memory_task_state",
        summary="What was intended and how far it got, so a resumed request is not restarted.",
        ownership=Ownership.OWNED,
        owners=(
            ("services.undx_brain.memory", "MemoryKind"),
            ("services.undx_architecture", "persist_plan"),
            ("services.undx_architecture", "resume_plan"),
            ("services.undx_architecture", "create_continuation"),
            ("services.undx_architecture", "burn_continuation"),
        ),
        note=(
            "The strongest of the seven, and the one already covered by "
            "``task_persistence`` above; it appears here so the seven classes are all "
            "present in one place rather than six being findable and one requiring the "
            "reader to know it is filed elsewhere. ``burn_continuation`` is why it is "
            "OWNED rather than PARTIAL: a resumable state that cannot be spent is a "
            "state that can be resumed twice."
        ),
    ),
    Responsibility(
        key="memory_fact",
        summary="Claims with a source and a confidence, retained so they can be cited.",
        ownership=Ownership.PARTIAL,
        owners=(
            ("services.undx_brain.memory", "MemoryKind"),
            ("services.undx_architecture", "record_fact"),
            ("services.undx_brain.truth", "TrustLevel"),
            ("services.undx_brain.truth", "rank"),
            ("services.undx_brain.facts", "read"),
            ("services.undx_brain.facts", "compare"),
            ("services.undx_brain.facts", "reconcile"),
            ("services.undx_brain.facts", "HORIZON_SECONDS"),
        ),
        gap=(
            "Both halves of this entry's original gap now have an owner, and neither is "
            "on the live path.\n\n"
            "``facts.read`` ages a stored claim against a horizon its trust level earns, "
            "so a reading past that horizon may be quoted only \"as of\" when it was "
            "taken, and one whose provenance was never recorded may not be quoted at "
            "all. ``facts.compare`` lines a new observation up against a stored one by "
            "*subject and value* rather than by claim text, and reports the "
            "disagreement — preferring the newer reading only when ``truth.meets`` says "
            "its trust is at least as strong, and disclosing the resolution in every "
            "case that is not agreement.\n\n"
            "Comparing subject and value rather than sentences is the whole of it, and "
            "the reason is visible in what the existing store does. ``record_fact`` "
            "returns a ``contradictions`` list built by matching *claim text* and "
            "keeping the rows whose ``source`` differs. Run against the real schema, "
            "recording \"btc alert threshold is 50000\" from a second source fills that "
            "list and sets ``status='review'`` — which is two sources agreeing — while "
            "recording \"btc alert threshold is 60000\" returns an empty list and "
            "``status='active'``, because a claim that disagrees is a different string "
            "and matches nothing. The one mechanism named for contradiction flags "
            "corroboration and lets the contradiction through.\n\n"
            "What is still not owned is reach on the live path. Two modules call "
            "``facts`` now — ``services.undx_brain.learning`` uses ``facts.read`` to "
            "time-qualify a finding and ``facts.parse_moment`` to read the timestamps "
            "it orders events by, and ``services.undx_brain.calibration`` uses "
            "``facts.read`` for the same reason, refusing to conclude a correctness "
            "rate it cannot date — and both are readers behind a flag calling a reader "
            "behind a flag, so neither moves anything closer to a request. Nothing on "
            "the live path calls either: ``facts`` is behind "
            "``UNDX_BRAIN_FACTS_ENABLED``, which defaults off, and with it off every "
            "entry point answers ``ok=False``. A fact only becomes comparable if its "
            "writer declared a subject — ``record_fact`` grew an optional ``metadata`` "
            "argument for exactly that, and its one caller outside tests is a bootstrap "
            "audit script — so every row written before now is uncomparable and is "
            "reported as such rather than guessed at. The ``confidence`` REAL column is "
            "deliberately not read as a trust level: it holds whatever float a caller "
            "passed, and mapping it onto the eight-level ordering would manufacture "
            "provenance out of an arbitrary decimal."
        ),
    ),
    Responsibility(
        key="memory_relationship",
        summary="Edges between entities the owner can see, each carrying its own access policy.",
        ownership=Ownership.PARTIAL,
        owners=(
            ("services.undx_brain.memory", "MemoryKind"),
            ("services.undx_architecture", "add_graph_edge"),
            ("services.undx_architecture", "graph_neighbors"),
        ),
        gap=(
            "Edges are written and one hop is readable. Multi-hop traversal has no owner "
            "and should not get one casually: the access policy lives on each edge, so a "
            "two-hop read is two policy decisions, and a traversal that checks the first "
            "and not the second is exactly how a private connection becomes visible "
            "through a public one. Until traversal is written with that in mind, one hop "
            "is the honest ceiling rather than an unfinished feature."
        ),
    ),
    Responsibility(
        key="memory_approval",
        summary="What was authorised, by whom, and whether it has been spent.",
        ownership=Ownership.OWNED,
        owners=(
            ("services.undx_brain.memory", "MemoryKind"),
            ("services.undx_architecture", "create_confirmation"),
            ("services.undx_architecture", "consume_confirmation"),
            ("services.undx_architecture", "revoke_confirmation"),
            ("services.undx_architecture", "approval_state"),
        ),
        note=(
            "Also covered by ``confirmation`` above, and listed again for the same "
            "reason as ``memory_task_state``. Worth noting that this class is the one "
            "where remembering *less* would be a defect: a spent confirmation is kept "
            "precisely so that the second attempt to redeem it can be refused with a "
            "reason rather than failing to find anything and minting a new one."
        ),
    ),
    Responsibility(
        key="memory_learning_event",
        summary="What happened, kept so a correction has something to attach to.",
        ownership=Ownership.PARTIAL,
        owners=(
            ("services.undx_brain.memory", "MemoryKind"),
            ("services.undx_brain.learning", "load"),
            ("services.undx_brain.learning", "distribution"),
            ("services.undx_brain.learning", "succession"),
            ("services.undx_brain.learning", "from_row"),
        ),
        note=(
            "This entry read UNOWNED until ``services.undx_brain.learning`` was "
            "written, and the finding that made it the sharpest one in the map is "
            "unchanged and still checkable. ``pulse_ai_learning_events`` is written by "
            "eleven call sites across ``pulse_ai_service`` — rate limits, safety "
            "refusals, agent actions, provider failures, answered messages, "
            "conversation resets, settings changes, memory corrections and deletions, "
            "feedback. Inside ``pulse_ai_service`` it is still read by exactly one: "
            "``admin_learning_dashboard`` runs ``SELECT COUNT(*)`` over it beside eight "
            "other tables, opening no row, reading no ``event_type``, no ``source`` and "
            "no ``metadata_json``, and filtering by no owner.\n\n"
            "``learning`` is the second reader and the first one that opens a row. It "
            "goes through ``memory`` rather than the table, so the owner clause is "
            "bound by the layer that exists to make forgetting it impossible, and it "
            "aggregates in Python over parsed rows, so every conclusion it reaches is "
            "reachable without a database. What it answers is what a count cannot: "
            "which capability accounts for most of an owner's agent actions, and "
            "whether one kind of event tends to be followed by another — the second "
            "measured against the consequent's rate across the whole window, because a "
            "conditional rate that merely matches the background is the most "
            "confident-sounding way to report nothing."
        ),
        gap=(
            "Three things are still missing, and one of them cannot be fixed here.\n\n"
            "Reach. Nothing calls ``learning``. It sits behind "
            "``UNDX_BRAIN_LEARNING_ENABLED``, which defaults off, and with it off every "
            "entry point answers ``ok=False`` so that a disabled reader is never "
            "mistaken for one that looked and found nothing. The admin dashboard still "
            "counts.\n\n"
            "Coverage of the questions. Two are answered — distribution over a "
            "dimension, and succession between two event types. Two named in the "
            "original gap are not: whether feedback correlates with anything, and "
            "whether a particular question shape reliably ends in a refusal. The second "
            "of those needs the message text, which is a different memory class under a "
            "different scope, and reaching across is exactly what ``memory`` refuses.\n\n"
            "Attribution, which is not a gap so much as a boundary. "
            "``_record_learning_event`` stores ``int(user_id or 0) or None``, so an "
            "event recorded for user ``0`` lands with a NULL ``user_id``. No "
            "owner-scoped read will ever return those rows, and ``memory.owner_id`` "
            "refuses ``0`` so no scope can be opened for them either. ``learning`` "
            "reports that as unknown rather than as zero; changing the writer to make "
            "them attributable would be a change to the live path and is not something "
            "this layer should do on its own."
        ),
    ),
    Responsibility(
        key="evidence_state_machine",
        summary="Explicit states for what is known about a request, with legal transitions.",
        ownership=Ownership.PARTIAL,
        owners=(
            ("services.undx_brain.truth", "EvidenceState"),
            ("services.undx_brain.truth", "transition"),
            ("services.undx_brain.truth", "state_supports_completion_claim"),
            ("services.undx_brain.evidence", "derive"),
            ("services.undx_brain.evidence", "Assessment"),
            ("services.undx_brain.evidence", "may_say_done"),
            ("services.undx_brain.evidence", "OUTCOME_FAMILY"),
            ("services.undx_agent_contracts", "VerificationState"),
        ),
        gap=(
            "The derivation exists now. ``evidence.derive`` maps the pair the runtime "
            "really carries — an ``AgentOutcome`` and a ``VerificationResult`` — onto an "
            "``EvidenceState``, always reading the verification, and its tests enumerate "
            "the gateway's own ``_status_for`` output rather than hand-written pairs, so "
            "the mapping cannot drift from what the gateway emits. What is still open is "
            "routing: nothing calls it on the live path. ``AgentReceipt`` still carries a "
            "``verification_state`` and the response layer still reads that, so for a real "
            "turn the guarantee continues to rest on the receipt's own "
            "``may_claim_completed``. Closing this means making the gateway settle through "
            "``derive`` and the responder read the resulting ``Assessment`` — not writing "
            "more of this module."
        ),
        note=(
            "The one dangerous disagreement is an outcome of ``verified_success`` beside a "
            "verification that is pending: read the outcome and you tell somebody their "
            "alert is paused; read the verification and you tell them you sent it and have "
            "not confirmed it. ``derive`` lets the outcome choose only the state *family* "
            "and lets the verification decide whether that family's strongest member is "
            "available. Verification is a veto, never a promotion."
        ),
    ),
    Responsibility(
        key="trust_model",
        summary="How well something is known, kept separate from what happened.",
        ownership=Ownership.OWNED,
        owners=(
            ("services.undx_brain.truth", "TrustLevel"),
            ("services.undx_brain.truth", "may_claim_live_state"),
            ("services.undx_brain.truth", "hedge_for"),
            ("services.undx_brain.truth", "meets"),
        ),
        note=(
            "New in this package, and the reason it exists. Trust and evidence are "
            "orthogonal axes; ``may_claim_live_state`` returns ``False`` at every trust "
            "level, which is the whole claim stated as code."
        ),
    ),
    Responsibility(
        key="corpus_governance",
        summary="The source corpus ingested as bounded, provenance-carrying, untrusted data.",
        ownership=Ownership.OWNED,
        owners=(
            ("services.undx_brain.corpus", "ingest"),
            ("services.undx_brain.corpus", "CorpusManifest"),
            ("services.undx_brain.corpus", "prompt_block"),
            ("services.undx_brain.knowledge", "retrieve"),
        ),
        note=(
            "Before this, ``undx_training_v6_source_corpus.yaml`` was generated, "
            "audited, committed, and imported by nothing."
        ),
    ),
    Responsibility(
        key="qa_gating",
        summary="Machine-checkable gates that must pass before a stage is trusted.",
        ownership=Ownership.PARTIAL,
        owners=(
            ("services.undx_capability_registry", "unregistered_tool_names"),
            ("services.undx_knowledge_map", "readiness_matrix"),
            ("services.undx_brain.rollout", "may_read"),
            ("services.undx_brain.rollout", "may_write"),
            ("services.undx_brain.rollout", "in_qa_cohort"),
            ("services.undx_brain.rollout", "surface"),
        ),
        gap=(
            "``UNDX_BRAIN_QA_ONLY`` decides something now. ``rollout`` reads it, reads "
            "both rollout percentages, reuses the existing ``UNDX_AGENT_QA_USER_IDS`` "
            "cohort rather than declaring a second one, and answers ``may_read`` and "
            "``may_write`` per account. What is still partial is that the *parity and "
            "readiness* checks remain reporting-only: ``unregistered_tool_names`` and "
            "``readiness_matrix`` fail a test when they drift, and nothing consults them "
            "at request time to withhold a capability whose verifier is missing. So "
            "reach is gated and maturity is not. Closing this means making skill "
            "availability depend on the readiness matrix, which is PART 6's work, not "
            "more of this one."
        ),
        note=(
            "Two properties in ``rollout`` are load-bearing and neither is the obvious "
            "implementation. The bucket is a SHA-256 digest because Python salts string "
            "hashing per process, so a ``hash()``-based bucket would move an account in "
            "and out of the rollout between workers. And the write percentage is capped "
            "by the read percentage, so the write cohort is always a subset of the read "
            "cohort — a wider write dial than read dial would otherwise hand people a "
            "Brain permitted to change their account but not to look at it first."
        ),
    ),
    Responsibility(
        key="homeostasis",
        summary="Staying within its own limits, and noticing when it is not.",
        ownership=Ownership.PARTIAL,
        owners=(
            ("services.undx_brain.bounds", "budget"),
            ("services.undx_brain.bounds", "admit"),
            ("services.undx_brain.bounds", "Ledger"),
            ("services.undx_brain.bounds", "Refusal"),
            ("services.undx_brain.workspace", "SLOTS"),
        ),
        gap=(
            "Half the word is done and it is the half that keeps the system safe. Every "
            "ceiling — steps, tool calls, retries, timeout, workspace slots, capability "
            "count, request length — is declared, resolved from the environment through "
            "``config.resolve``, and enforced by refusing rather than truncating, which "
            "is the property that stops an over-budget plan quietly becoming a "
            "different, smaller plan nobody asked for. ``Ledger`` spends and never "
            "refunds, so a grant cannot be requested twice.\n\n"
            "The other half — regulation toward a set point — has no owner at all. Every "
            "ceiling is a constant read once at the start of a request; nothing measures "
            "current load, latency, provider health or refusal rate, and nothing widens "
            "or narrows a limit in response to what it observes. There is no signal a "
            "regulator could read, which is the thing to build first: the limits are "
            "already in one place, so a measurement layer would have somewhere obvious "
            "to attach. Note also that the existing rate limits in ``pulse_ai_service`` "
            "and ``pulse_security_core`` are per-user quotas defending the product, not "
            "self-regulation by the Brain, and should not be mistaken for this."
        ),
        note=(
            "Refusing rather than truncating is the decision that makes this a boundary "
            "instead of a suggestion, and it is the less convenient one every time it "
            "fires. A system that trims to fit produces an answer, and the answer is "
            "built on whichever part of the material survived the trim — chosen by "
            "position in a list rather than by importance. A refusal is visible, "
            "attributable to a named ceiling, and can be raised deliberately by "
            "somebody who has read why it was set."
        ),
    ),
)


class FoundationError(AssertionError):
    """Raised when the map claims an owner that no longer exists."""


@dataclass(frozen=True)
class VerificationReport:
    """Result of checking the map against the code it describes."""

    checked: int = 0
    missing: tuple[str, ...] = ()
    unowned: tuple[str, ...] = ()
    partial: tuple[str, ...] = ()
    #: Modules that could not be imported because a *third-party* dependency is absent
    #: from this interpreter — not because the module is gone. Kept separate because
    #: conflating the two makes the map fail on any machine with a thin environment,
    #: and a check that fails for the wrong reason is a check people learn to ignore.
    unavailable: tuple[str, ...] = ()
    notes: tuple[str, ...] = field(default=(), repr=False)

    @property
    def ok(self) -> bool:
        """True when every claimed owner that could be checked exists.

        ``UNOWNED`` and ``PARTIAL`` entries do not make the report fail. They are
        accurate statements about an incomplete system, and a check that failed on them
        would create pressure to describe gaps as filled.
        """
        return not self.missing

    @property
    def complete(self) -> bool:
        """True when every owner was actually reachable, so ``ok`` means something.

        A test asserting only ``ok`` in an environment where half the modules failed to
        import is asserting almost nothing. CI should assert both.
        """
        return self.ok and not self.unavailable


def verify() -> VerificationReport:
    """Import every claimed owner and confirm the named symbol is present.

    Never raises for a missing owner — it returns them, so a caller can report all of
    them at once rather than one per test run. :func:`require` is the raising form.
    """
    missing: list[str] = []
    unowned: list[str] = []
    partial: list[str] = []
    unavailable: dict[str, None] = {}
    imported: dict[str, object] = {}
    checked = 0

    for item in FOUNDATION:
        if item.ownership is Ownership.UNOWNED:
            unowned.append(item.key)
            if item.owners:
                missing.append(
                    f"{item.key}: declared UNOWNED but names {len(item.owners)} owner(s)"
                )
            if not item.gap:
                missing.append(f"{item.key}: declared UNOWNED without stating the gap")
            continue
        if item.ownership is Ownership.PARTIAL:
            partial.append(item.key)
            if not item.gap:
                missing.append(f"{item.key}: declared PARTIAL without stating the gap")
        if not item.owners:
            missing.append(f"{item.key}: {item.ownership.value} but names no owner")
            continue
        for module_name, symbol in item.owners:
            checked += 1
            if module_name in unavailable:
                continue
            module = imported.get(module_name)
            if module is None:
                try:
                    module = importlib.import_module(module_name)
                except ModuleNotFoundError as exc:
                    absent = exc.name or ""
                    if absent == module_name or module_name.startswith(f"{absent}."):
                        # The owner itself is gone. That is exactly what this map exists
                        # to catch.
                        missing.append(f"{item.key}: module {module_name} does not exist")
                    else:
                        # Some third-party package the owner imports is absent from this
                        # interpreter. Says nothing about whether the owner still owns.
                        unavailable[module_name] = None
                    continue
                except Exception as exc:
                    missing.append(f"{item.key}: cannot import {module_name} ({exc!r})")
                    continue
                imported[module_name] = module
            if not hasattr(module, symbol):
                missing.append(f"{item.key}: {module_name} has no {symbol!r}")

    keys = [item.key for item in FOUNDATION]
    notes: list[str] = []
    if len(set(keys)) != len(keys):
        missing.append("duplicate responsibility keys in FOUNDATION")
    notes.append(f"{len(FOUNDATION)} responsibilities, {checked} owner symbols checked")
    if unavailable:
        notes.append(
            f"{len(unavailable)} module(s) unverifiable here (missing dependency): "
            + ", ".join(sorted(unavailable))
        )

    return VerificationReport(
        checked=checked,
        missing=tuple(missing),
        unowned=tuple(unowned),
        partial=tuple(partial),
        unavailable=tuple(sorted(unavailable)),
        notes=tuple(notes),
    )


def require() -> None:
    """Raise :class:`FoundationError` if any claimed owner has gone missing."""
    report = verify()
    if not report.ok:
        raise FoundationError(
            "Foundation map is out of date:\n  " + "\n  ".join(report.missing)
        )


def by_key(key: str) -> Responsibility | None:
    for item in FOUNDATION:
        if item.key == key:
            return item
    return None


def gaps() -> tuple[Responsibility, ...]:
    """Everything not fully owned — the next work, in one call."""
    return tuple(
        item for item in FOUNDATION if item.ownership is not Ownership.OWNED
    )


def owning_modules() -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for item in FOUNDATION:
        for module_name, _ in item.owners:
            seen.setdefault(module_name, None)
    return tuple(sorted(seen))


__all__ = [
    "FOUNDATION",
    "FoundationError",
    "Ownership",
    "Responsibility",
    "VerificationReport",
    "by_key",
    "gaps",
    "owning_modules",
    "require",
    "verify",
]
