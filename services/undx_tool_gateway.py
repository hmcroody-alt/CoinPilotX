"""The single chokepoint between a proposed action and a real mutation.

Every capability invocation passes through :func:`execute`. Nothing else calls an
executor, and executors themselves contain no policy — that separation is the point.
It means the question "can this action happen without approval?" has exactly one
place to be answered, and the answer can be read in one sitting.

The order of the checks is itself a security property, so it is fixed and
documented rather than incidental:

1.  **Authentication.** No user id, no gateway. Established before anything else so
    that no later check can be reached by an unauthenticated caller.
2.  **Capability allowlisting.** The ``capability_id`` is resolved against the
    registry. An unknown id stops here as ``unsupported_capability`` — a model that
    hallucinates ``crypto.alerts.wire_funds`` gets a typed refusal, not a lookup
    failure deeper in the stack. The capability's declared ownership scope is then
    checked against the rules this gateway can actually apply; one it cannot enforce
    is refused rather than executed.
3.  **Schema validation.** Arguments are coerced against the declared spec and
    undeclared keys are dropped. What reaches the executor is a known shape.
4.  **Policy evaluation.** Flags, cohort, risk class and confirmation policy, all
    from :mod:`services.undx_agent_policy`, none of it from message text.
5.  **Confirmation redemption.** A capability that requires approval executes only
    on a token redeemed against *this* user, *this* capability and *this* argument
    hash. Redemption happens BEFORE execution and burns the token.
6.  **Idempotency.** A replayed key returns the previous receipt instead of acting
    twice.
7.  **Execution**, bounded by a wall clock and wrapped so that no service exception
    escapes as anything other than a typed outcome.
8.  **Verification.** An independent read-back, run for every write.
9.  **Audit.** One row per operation, recording the redeemed grant and the
    read-back verdict — never the caller's claims about either.

A note on what is deliberately absent: there is no parameter, no flag and no
argument value that lets a caller skip steps 4-6. The only way to reach execution
of a confirmed capability is to hold a token that the server minted, and the only
way to obtain one is to have been shown a confirmation card.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from services import undx_agent_policy as policy
from services import (
    undx_agent_tools,
    undx_architecture,
    undx_response_intelligence,
    undx_verification,
)
from services.undx_agent_contracts import (
    MAX_EXECUTION_SECONDS,
    AgentError,
    AgentOutcome,
    AgentReceipt,
    ConfirmationRequest,
    PermissionScope,
    RiskLevel,
    ToolResult,
    VerificationResult,
    VerificationState,
    canonical_hash,
    clean,
    validate_arguments,
)
from services.undx_capability_registry import CapabilitySpec, require

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Outcome plumbing
# ---------------------------------------------------------------------------


class GatewayOutcome:
    """What one gateway call produced.

    Carries a receipt in every case, including refusals. A refusal is a real
    outcome the user is owed an explanation for, not an absence of one, and giving
    denials the same shape as successes means the caller cannot accidentally treat
    "nothing came back" as "it worked".
    """

    __slots__ = ("receipt", "confirmation", "result", "verification")

    def __init__(self, receipt: AgentReceipt, *, confirmation: ConfirmationRequest | None = None,
                 result: ToolResult | None = None, verification: VerificationResult | None = None) -> None:
        self.receipt = receipt
        self.confirmation = confirmation
        self.result = result
        self.verification = verification

    @property
    def status(self) -> str:
        return self.receipt.status

    @property
    def succeeded(self) -> bool:
        return self.receipt.may_claim_completed


def _receipt(spec: CapabilitySpec, *, user_id: int, request_id: str, task_id: str,
             status: str, explanation: str, arguments: dict[str, Any] | None = None,
             verification: VerificationResult | None = None,
             canonical_ids: list[str] | None = None,
             evidence: dict[str, Any] | None = None,
             retry_count: int = 0) -> AgentReceipt:
    # Undo is offered only on a change the system independently read back, and only
    # when the reversing call can actually be parameterised. Those are separate
    # conditions and both have to hold: an unverified write might not have happened,
    # and a verified creation whose row id never made it into the result gives an
    # undo with nothing to delete. Either way the affordance is withheld together
    # with the arguments, so the client never sees a capability id it cannot invoke.
    undo_arguments = (
        spec.undo_arguments(arguments or {}, list(canonical_ids or []))
        if status == AgentOutcome.VERIFIED_SUCCESS
        else None
    )
    return AgentReceipt(
        task_id=task_id,
        request_id=request_id,
        capability_id=spec.capability_id,
        action=spec.description,
        status=status,
        owner_user_id=int(user_id),
        canonical_resource_ids=list(canonical_ids or []),
        verification_state=(verification.state if verification else VerificationState.IMPOSSIBLE),
        evidence=dict(evidence or {}),
        native_deep_link=spec.deep_link(arguments or {}),
        undo_capability_id=spec.undo_capability_id if undo_arguments is not None else "",
        undo_arguments=dict(undo_arguments or {}),
        # Bounded by the response layer's own limit rather than by a local number. The
        # previous 400 truncated a detailed answer mid-clause, which turns an honest
        # explanation of a partial result into a sentence that stops before the part
        # the person needed.
        user_explanation=clean(explanation, undx_response_intelligence.MAX_EXPLANATION_CHARS),
        risk_level=spec.risk,
        retry_count=int(retry_count),
    )


def _last_resort_receipt(spec: CapabilitySpec, *, user_id: int, request_id: str,
                         task_id: str, status: str, explanation: str,
                         evidence: dict[str, Any]) -> AgentReceipt:
    """A receipt built without using anything that might be what just failed.

    :func:`execute` used to build its final, defensive receipt by calling
    :func:`_receipt` — the same function that may be the reason it is in the handler
    at all. Fault injection showed that plainly: making ``_receipt`` raise made
    ``execute`` raise too, *after* the executor had run, with the user's alert already
    paused in the database. That is the one thing the design says cannot happen, and
    the comment claiming the tail was wrapped "so that a defect in the defences is not
    itself the leak" was describing an intention rather than the code. A handler that
    re-enters the failing call is not a handler.

    So this constructs the dataclass directly and touches nothing that can throw. No
    ``deep_link()``, no ``undo_arguments()``, no ``clean()``, no attribute lookups
    across module boundaries — only plain reads off a frozen spec and literals. The
    status and verification state are module constants rather than computed values, so
    ``AgentReceipt.__post_init__`` cannot reject them either.

    What this gives up is real: no deep link, no undo affordance, no verification
    detail, an explanation that says less than the situation deserves. All of that is
    the correct trade. A thin receipt reaches the person as "something happened and I
    could not describe it, go and look" — an exception reaches them as a chatbot
    cheerfully discussing something else while their data has already changed.
    """
    return AgentReceipt(
        task_id=str(task_id or ""),
        request_id=str(request_id or ""),
        capability_id=spec.capability_id,
        action=spec.description,
        status=status,
        owner_user_id=int(user_id),
        verification_state=VerificationState.IMPOSSIBLE,
        evidence=dict(evidence),
        user_explanation=explanation,
        risk_level=spec.risk,
    )


# ---------------------------------------------------------------------------
# Confirmation
# ---------------------------------------------------------------------------


def _checkpoint(cur) -> None:
    """Make the confirmation bookkeeping written so far durable, right now.

    This exists for two independent reasons, and both of them matter.

    *Correctness of the grant.* A minted approval that is still inside an open
    transaction is a promise the database has not made. If the surrounding request
    then fails, the user has been shown a confirmation card holding a token that no
    longer exists. Symmetrically, a redemption must be durable **before** the write
    it authorises runs: if redemption and execution were rolled back together, a
    replayed token would authorise a second real mutation. Burning the token first
    means the worst case is a wasted approval, which is recoverable, rather than a
    duplicated action, which is not.

    *Liveness.* The service layer opens its own connection. On SQLite, holding an
    open write transaction here while an executor writes through a second connection
    deadlocks until the busy timeout expires and then fails — which is exactly what
    happens without this call, and it surfaces as a spurious "the action could not
    be completed" on an action that was fully authorised.
    """
    owner = getattr(cur, "_owner", None) or getattr(cur, "connection", None)
    if owner is not None and hasattr(owner, "commit"):
        owner.commit()


#: The same call, under a name the runtime is allowed to say.
#:
#: The runtime now writes a row of its own before replying — the remembered question
#: behind a clarification — and it needs exactly the guarantee documented above: a
#: promise shown to the person must be one the database has already made. Reaching for
#: the underscored name across a module boundary would work and would be a lie about
#: the interface; copying the four lines would put the reasoning in two places and
#: leave one of them to rot. So the function is published instead.
checkpoint = _checkpoint


def _mint_confirmation(cur, user_id: int, spec: CapabilitySpec, arguments: dict[str, Any],
                       *, task_id: str, current_value: Any, proposed_value: Any,
                       risk_summary: str, resource_label: str = "") -> ConfirmationRequest:
    """Create the approval a confirmation card is built from.

    The token is bound to the capability id and to the hash of the *validated*
    arguments. Binding to validated rather than proposed arguments is what stops an
    approval shown for "pause alert 12" being redeemed for "pause alert 99": the
    hash the user approved and the hash the gateway later presents are computed
    from the same normalised dictionary.
    """
    # The target comes from the capability's own declaration, not from a hardcoded
    # list of argument names. A gateway that knows the words ``alert_id`` and
    # ``category`` shows an unnamed card — "approve this change to *what*?" — the
    # first time a pack arrives whose target is called something else.
    #
    # That fixed "approve *what*" for the machine and not for the person: the target
    # it produces for an alert is the row id, which is correct as identity and empty
    # as description. ``resource_label`` carries the description, supplied by the
    # caller because reading the row is the caller's job — this function is on the
    # authorisation path and must not acquire a data dependency it could fail on.
    target = spec.canonical_target(arguments)
    grant = undx_architecture.create_confirmation(
        cur, int(user_id),
        {
            "action_id": spec.capability_id,
            "action_version": "agent.1",
            "target_id": target[:160],
            "arguments": arguments,
        },
    )
    return ConfirmationRequest(
        confirmation_id=grant["confirmation_id"],
        confirmation_token=grant["confirmation_token"],
        capability_id=spec.capability_id,
        action_name=spec.description,
        target=target[:160],
        current_value=current_value,
        proposed_value=proposed_value,
        risk_summary=clean(risk_summary, 240),
        expires_at=grant["expires_at"],
        argument_hash=canonical_hash(arguments),
        task_id=task_id,
        resource_label=clean(resource_label, 160),
    )


def _redeem(cur, user_id: int, spec: CapabilitySpec, arguments: dict[str, Any],
            token: str) -> dict[str, Any] | None:
    """Redeem an approval for exactly this action, or return ``None``.

    Both bindings are asserted inside :func:`undx_architecture.consume_confirmation`,
    which checks them before burning the row, so a wrong-action redemption neither
    destroys a valid grant nor reveals that one existed.
    """
    return undx_architecture.consume_confirmation(
        cur, int(user_id), token,
        expect_action_id=spec.capability_id,
        expect_argument_hash=canonical_hash(arguments),
    )


# ---------------------------------------------------------------------------
# Permission scope
# ---------------------------------------------------------------------------


#: Argument names that would let a caller nominate whose data is touched. A capability
#: scoped to the caller's own account must not declare any of them: every executor
#: takes the acting user id as a separate parameter that no argument can reach, and a
#: field like this is the one shape that could quietly undo that.
_ACTOR_NAMING_FIELDS = frozenset({
    "user_id", "owner_id", "actor_id", "account_id", "target_user_id",
    "on_behalf_of", "as_user", "profile_id", "member_id",
})


def _enforce_permission_scope(
    spec: CapabilitySpec,
    user_id: int,
    arguments: dict[str, Any],
) -> None:
    """Refuse any capability whose ownership rule this gateway cannot actually apply.

    ``permission`` spent its first life as a comment: a string on every capability
    reading ``self_account_only``, consulted by nothing. That is a worse state than
    having no field at all, because it reads like an enforced invariant — a reviewer
    adding a capability sees ownership "handled" and does not check that the executor
    scoped its own query.

    So it fails closed. Only ``self_account_only`` has an enforcement rule today, and
    it is a structural one that cannot be got wrong at runtime: a capability may not
    declare a field naming *whose* data to touch, so there is nothing for a hostile
    argument to point at. The scopes for acting on another user or on a content item
    need a resolver that authorises the target before execution; until Stage 6 and 8
    build one, a capability declaring them is refused rather than executed under an
    ownership check that does not exist yet.
    """
    if spec.permission == PermissionScope.SELF_ACCOUNT_ONLY:
        named = sorted(item.name for item in spec.fields if item.name in _ACTOR_NAMING_FIELDS)
        if named:
            raise AgentError(
                "capability_scope_violation",
                "UNDX cannot do that.",
                outcome=AgentOutcome.PERMISSION_DENIED,
                details={"capability_id": spec.capability_id, "actor_naming_fields": named},
            )
        return
    if spec.permission == PermissionScope.OTHER_USER_TARGET:
        declared = {item.name for item in spec.fields}
        if "target_user_id" not in declared:
            raise AgentError(
                "capability_scope_unenforceable",
                "UNDX cannot do that yet.",
                outcome=AgentOutcome.UNSUPPORTED_CAPABILITY,
                details={"capability_id": spec.capability_id, "permission": spec.permission},
            )
        target_id = int(arguments.get("target_user_id") or 0)
        from services.social_relationship_service import is_following

        if target_id <= 0 or is_following(int(user_id), target_id) is None:
            raise AgentError(
                "invalid_user_target",
                "UNDX could not find an eligible PulseSoc account for that action.",
                outcome=AgentOutcome.PERMISSION_DENIED,
                details={"capability_id": spec.capability_id},
            )
        return
    raise AgentError(
        "capability_scope_unenforceable",
        "UNDX cannot do that yet.",
        outcome=AgentOutcome.UNSUPPORTED_CAPABILITY,
        details={"capability_id": spec.capability_id, "permission": spec.permission},
    )


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def _previous_operation(cur, user_id: int, tool_name: str, idempotency_key: str) -> dict[str, Any] | None:
    """Find a completed operation with this exact key, if one exists.

    The uniqueness constraint on ``(user_id, tool_name, idempotency_key)`` already
    prevents a duplicate audit row. This look-up exists so the duplicate never
    reaches the executor at all: the constraint protects the ledger, but only an
    up-front check protects the user's data from a second real mutation.
    """
    try:
        cur.execute(
            """SELECT operation_id, status, canonical_entity_id, verification_json
            FROM pulse_ai_tool_operations
            WHERE user_id=? AND tool_name=? AND idempotency_key=? LIMIT 1""",
            (int(user_id), tool_name, idempotency_key),
        )
        row = cur.fetchone()
    except Exception:  # pragma: no cover - defensive; a missing table is not a mutation
        return None
    return dict(row) if row else None


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------


def _run_executor(spec: CapabilitySpec, user_id: int, arguments: dict[str, Any]) -> ToolResult:
    """Call one executor, converting every failure mode into a typed result.

    A service raising is normal; a service raising *through* the gateway is not.
    The exception type is recorded, but its message is not forwarded to the user,
    because service exception text routinely carries schema fragments, file paths
    and identifiers belonging to other rows.
    """
    executor = undx_agent_tools.resolve(spec.executor)
    started = time.monotonic()
    try:
        result = executor(int(user_id), dict(arguments))
    except AgentError as exc:
        # Deliberately converted rather than re-raised. An ``AgentError`` from inside an
        # executor is raised *after* the executor was entered, so it may follow a partial
        # or even complete mutation. Letting it propagate would carry it out of
        # ``execute`` past the point of no return, and the caller reads any exception as
        # "the agent did not act". It is a typed refusal, so it survives as one — with
        # its own code and message, which an executor's refusals are safe to show.
        return ToolResult(
            ok=False, tool_name=spec.tool_name, capability_id=spec.capability_id,
            error_code=clean(getattr(exc, "code", "") or "executor_refused", 60),
            error_message=clean(str(exc) or "The action could not be completed.", 240),
            retryable=False, latency_ms=int((time.monotonic() - started) * 1000),
        )
    except Exception as exc:  # pragma: no cover - defensive
        return ToolResult(
            ok=False, tool_name=spec.tool_name, capability_id=spec.capability_id,
            error_code="executor_exception",
            error_message=f"The action could not be completed ({exc.__class__.__name__}).",
            retryable=True, latency_ms=int((time.monotonic() - started) * 1000),
        )
    elapsed = time.monotonic() - started
    if elapsed > MAX_EXECUTION_SECONDS:
        # The work may well have landed, so this is not reported as a failure to act.
        # Verification decides what actually happened; that is precisely the case the
        # read-back exists for.
        result.error_code = result.error_code or "slow_execution"
    return result


def _verify(spec: CapabilitySpec, user_id: int, arguments: dict[str, Any],
            result: ToolResult) -> VerificationResult:
    if not spec.verifier:
        return VerificationResult(
            state=VerificationState.IMPOSSIBLE,
            detail="This capability declares no read-back path.",
        )
    return undx_verification.verify(spec.verifier, int(user_id), arguments, result)


def _status_for(spec: CapabilitySpec, result: ToolResult, verification: VerificationResult) -> str:
    """Map a tool result plus its read-back onto one of the seven canonical outcomes.

    The asymmetry here is intentional. ``verified`` is the only path to
    ``verified_success``; a pending or impossible read-back yields
    ``accepted_unverified``, which reads as "we did it and cannot prove it" rather
    than as success. A *failed* read-back is never softened into "accepted" — it is
    a terminal failure, because the one thing worse than not knowing is telling the
    user something happened when the state says otherwise.

    Reads carry the same asymmetry. A read used to reach ``verified_success`` merely
    by not raising, which meant a query that failed, was caught, and returned an
    empty list was recorded as verified — the audit trail agreeing, with full
    confidence, that nothing happened. A read that reports degraded sources is
    therefore ``accepted_unverified``: the rows it did return are real, but it
    cannot claim to be the whole answer.
    """
    if not result.ok:
        return AgentOutcome.RECOVERABLE_FAILURE if result.retryable else AgentOutcome.TERMINAL_FAILURE
    if not spec.is_write:
        return (AgentOutcome.ACCEPTED_UNVERIFIED if result.degraded_sources
                else AgentOutcome.VERIFIED_SUCCESS)
    if verification.state == VerificationState.VERIFIED:
        return AgentOutcome.VERIFIED_SUCCESS
    if verification.state == VerificationState.FAILED:
        return AgentOutcome.TERMINAL_FAILURE
    return AgentOutcome.ACCEPTED_UNVERIFIED


def _compose_response(spec: CapabilitySpec, status: str, result: ToolResult,
                      verification: VerificationResult, *, question: str = "",
                      history: tuple[str, ...] = ()) -> tuple[str, dict[str, Any]]:
    """Build the sentence and the plan that justifies it.

    This is where the old failure mode lived. Every earlier version chose its wording
    from the *outcome code*, so four rows, one row, zero rows and zero-rows-because-a-
    table-was-unreachable all produced the same five words — the runtime's hardest-won
    distinctions dying one inch from the reader. The response layer composes from the
    evidence instead, and audits its own output before handing it back.

    It is also the only place a rendering fault could turn a completed action into an
    exception, so it must not raise. A failure here degrades to a terse but true
    sentence and an empty plan; it never costs the user their receipt.
    """
    try:
        text, plan = undx_response_intelligence.compose(
            spec, status, result, verification, question=question, history=history)
        if text:
            return text, plan.to_dict()
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("undx_response_intelligence_failed capability=%s error=%s",
                     spec.capability_id, exc.__class__.__name__)
    return _terse_explanation(spec, status, result, verification), {}


def _explain(spec: CapabilitySpec, status: str, result: ToolResult,
             verification: VerificationResult, *, question: str = "",
             history: tuple[str, ...] = ()) -> str:
    """Plain language that matches the evidence, including when the evidence is thin."""
    return _compose_response(spec, status, result, verification,
                            question=question, history=history)[0]


def _terse_explanation(spec: CapabilitySpec, status: str, result: ToolResult,
                       verification: VerificationResult) -> str:
    """The last-resort sentence. Every branch is true without consulting the evidence."""
    if status == AgentOutcome.VERIFIED_SUCCESS:
        if not spec.is_write:
            return "I read that from your account."
        return "Done, and I read the new state back to confirm it."
    if status == AgentOutcome.ACCEPTED_UNVERIFIED:
        if not spec.is_write:
            missing = len(result.degraded_sources)
            return ("I could not reach "
                    f"{'one part of' if missing == 1 else f'{missing} parts of'} your data, "
                    "so treat this as incomplete rather than as the whole answer.")
        return ("PulseSoc accepted the change, but I could not read it back to confirm it. "
                "Please check the screen before relying on it.")
    if status == AgentOutcome.RECOVERABLE_FAILURE:
        return clean(result.error_message or "That did not go through. It is worth trying again.", 240)
    if verification.state == VerificationState.FAILED:
        return ("The action reported success but the verified state does not match, "
                "so I have not marked it as done.")
    return clean(result.error_message or "That did not work.", 240)


# ---------------------------------------------------------------------------
# The gateway
# ---------------------------------------------------------------------------


def execute(
    cur,
    *,
    user_id: int,
    capability_id: str,
    proposed_arguments: dict[str, Any],
    request_id: str,
    task_id: str = "",
    client_request_id: str = "",
    correlation_id: str = "",
    confirmation_token: str = "",
    explicit_request: bool = False,
    resolved_resource_count: int = 1,
    current_value: Any = None,
    proposed_value: Any = None,
    resource_label: str = "",
    question: str = "",
    recent_replies: tuple[str, ...] = (),
) -> GatewayOutcome:
    """Run one capability under full governance. The only entry point to a mutation.

    ``cur`` is an open database cursor owned by the caller, so the audit row, the
    confirmation redemption and the caller's own transaction commit or roll back
    together. A gateway that opened its own connection could leave an approval
    burned by a request that then failed to record anything.

    ``resource_label`` is presentation and is treated as such: it is copied onto the
    approval and never consulted. It cannot select a capability, change a target, or
    alter the argument hash the token is bound to — a card that named the wrong row
    would still be an approval for the row the arguments name, which is why the label
    has to be read back from that row by the caller rather than composed here.

    ``question`` and ``recent_replies`` reach only the response layer, and only after
    every decision has been made. They cannot select a capability, widen a permission,
    or change an outcome — the last of the nine checks has already run by the time
    either is read. What they can do is make the answer the right length and stop it
    repeating the previous one, which is worth having and worth confining.
    """
    task_id = clean(task_id or request_id, 120)
    question = clean(question, 400)
    history = tuple(clean(reply, undx_response_intelligence.MAX_EXPLANATION_CHARS)
                    for reply in list(recent_replies or ())[-undx_response_intelligence.HISTORY_WINDOW:])

    # 1. Authentication. Not a formality: every subsequent check is scoped by user id,
    #    so an unauthenticated call must not reach any of them.
    if int(user_id or 0) <= 0:
        raise AgentError("unauthenticated", "Sign in to let UNDX do that.",
                         outcome=AgentOutcome.PERMISSION_DENIED)

    # 2. Capability allowlisting. Raises unsupported_capability for anything unknown.
    spec = require(capability_id)

    # 3. Schema validation. Undeclared keys are dropped rather than rejected, so a
    #    hostile string that smuggles plausible extra parameters can neither steer the
    #    tool nor deny service by making the whole call invalid.
    arguments = validate_arguments(spec.fields, proposed_arguments or {})

    # 3b. Ownership scope runs over the validated argument set. A resolver must never
    #     authorise a target from a key the capability did not declare.
    _enforce_permission_scope(spec, int(user_id), arguments)

    # 4. Deterministic policy. Nothing here reads message text.
    decision = policy.evaluate(
        int(user_id), spec, arguments,
        explicit_request=bool(explicit_request),
        resolved_resource_count=int(resolved_resource_count),
    )
    if decision.denied:
        return GatewayOutcome(_receipt(
            spec, user_id=user_id, request_id=request_id, task_id=task_id,
            status=decision.outcome or AgentOutcome.PERMISSION_DENIED,
            explanation=decision.message, arguments=arguments,
            evidence={"reason": decision.reason, **decision.details},
        ))

    # 5. Confirmation. A required approval is either redeemed now or requested now;
    #    there is no third branch in which execution proceeds anyway.
    grant: dict[str, Any] | None = None
    if decision.needs_confirmation:
        if not clean(confirmation_token, 500):
            request = _mint_confirmation(
                cur, int(user_id), spec, arguments, task_id=task_id,
                current_value=current_value, proposed_value=proposed_value,
                risk_summary=spec.description,
                resource_label=resource_label,
            )
            _checkpoint(cur)
            return GatewayOutcome(
                _receipt(spec, user_id=user_id, request_id=request_id, task_id=task_id,
                         status=AgentOutcome.CONFIRMATION_REQUIRED,
                         explanation="I need you to confirm this before I make the change.",
                         arguments=arguments,
                         evidence={"confirmation_id": request.confirmation_id,
                                   "expires_at": request.expires_at}),
                confirmation=request,
            )
        grant = _redeem(cur, int(user_id), spec, arguments, confirmation_token)
        # Durable before the authorised write runs. See :func:`_checkpoint`.
        _checkpoint(cur)
        if not grant:
            # Expired, already used, minted for another action, or never existed. These
            # are deliberately indistinguishable to the caller: telling an attacker
            # which one applies turns the token into an oracle.
            return GatewayOutcome(_receipt(
                spec, user_id=user_id, request_id=request_id, task_id=task_id,
                status=AgentOutcome.CONFIRMATION_REQUIRED,
                explanation="That confirmation is no longer valid. Ask me again and I will re-confirm.",
                arguments=arguments, evidence={"reason": "grant_not_redeemable"},
            ))

    # 6. Idempotency, keyed on the caller's request id and the canonical target.
    canonical_target = spec.canonical_target(arguments)
    prepared = undx_architecture.prepare_tool_operation(
        int(user_id), spec.tool_name,
        clean(client_request_id or request_id, 120), canonical_target,
    )
    if spec.is_write:
        previous = _previous_operation(cur, int(user_id), spec.tool_name, prepared["idempotency_key"])
        if previous:
            # A row in ``pending`` or ``needs_reconciliation`` means an earlier attempt
            # reached the executor and its outcome was never recorded. That is precisely
            # the case where repeating is most tempting and most dangerous, so it is
            # reported honestly instead of retried: the user is told the outcome is
            # unknown rather than told it succeeded or having it done to them twice.
            prior_status = str(previous.get("status") or "")
            unsettled = prior_status in {"pending", "needs_reconciliation"}
            replayed = ToolResult(
                ok=prior_status in {"verified", "ok"},
                tool_name=spec.tool_name, capability_id=spec.capability_id,
                canonical_resource_id=clean(previous.get("canonical_entity_id"), 180),
                idempotent_replay=True,
            )
            return GatewayOutcome(
                _receipt(spec, user_id=user_id, request_id=request_id, task_id=task_id,
                         status=(AgentOutcome.VERIFIED_SUCCESS if prior_status == "verified"
                                 else AgentOutcome.ACCEPTED_UNVERIFIED),
                         explanation=("I started that once already and could not confirm how it "
                                      "finished, so I have not run it again. Check it before retrying."
                                      if unsettled else
                                      "I had already done that; I have not repeated it."),
                         arguments=arguments,
                         canonical_ids=[replayed.canonical_resource_id] if replayed.canonical_resource_id else [],
                         evidence={"idempotent_replay": True,
                                   "prior_status": prior_status,
                                   "needs_reconciliation": unsettled,
                                   "operation_id": clean(previous.get("operation_id"), 60)}),
                result=replayed,
            )

    # 7. Reserve the ledger row before the mutation, and make it durable. This is the
    #    only ordering under which "every executed action has an audit trail" survives a
    #    crash: a row written afterwards is a row that may never be written at all, and
    #    the evidence of a real change to a user's data would be lost with it. The
    #    reservation is a short transaction of its own — it is committed immediately and
    #    no lock is held across the service call.
    if spec.is_write:
        try:
            undx_architecture.begin_tool_operation(
                cur, int(user_id), prepared, clean(correlation_id or request_id, 120))
        except Exception:  # pragma: no cover - a reservation failure must not block the user
            # Losing the reservation costs crash-safety for this one action; refusing the
            # action would cost the user a capability they are authorised to use. The
            # post-execution audit write still runs and is still the record of truth.
            logger.warning("undx_audit_reservation_failed tool=%s user=%s", spec.tool_name, int(user_id))

    # 8. Execution. Checkpoint first regardless of how we got here: the executor writes
    #    through its own connection, and any transaction still open on ours would block
    #    it. A caller that did unrelated writes before calling us must not turn an
    #    authorised action into a lock timeout.
    _checkpoint(cur)
    arguments_for_executor = dict(arguments)
    arguments_for_executor["_idempotency_key"] = prepared["idempotency_key"]
    result = _run_executor(spec, int(user_id), arguments_for_executor)

    # --- The point of no return -------------------------------------------------
    # The executor has run. Whatever it did to the user's data is done, through the
    # service layer's own connection, and cannot be taken back by anything below.
    # From here, ``execute`` must return a receipt — never raise. A raise would
    # unwind into ``pulse_ai_service``, whose handler treats "the agent did not
    # handle this turn" as licence to fall through to the language model, and the
    # model would then answer a question about an action it has no idea occurred.
    # Every step below is therefore individually defended, and the whole tail is
    # wrapped as well so that a defect in the defences is not itself the leak.
    try:
        return _settle(
            cur, spec=spec, user_id=int(user_id), arguments=arguments, result=result,
            prepared=prepared, grant=grant, request_id=request_id, task_id=task_id,
            correlation_id=correlation_id, question=question, history=history,
        )
    except Exception as exc:  # pragma: no cover - defensive
        try:
            logger.critical("undx_gateway_settle_failed tool=%s user=%s error=%s",
                            spec.tool_name, int(user_id), exc.__class__.__name__)
        except Exception:
            # Logging is the least important thing happening here and the only step
            # that talks to something outside the process. It must not be the reason
            # the receipt never gets built.
            pass
        # Built by :func:`_last_resort_receipt`, not :func:`_receipt`. The seam that
        # raised may be inside ``_receipt`` itself — verified by injecting a fault
        # there and watching ``execute`` propagate it with the row already mutated.
        return GatewayOutcome(
            _last_resort_receipt(
                spec, user_id=int(user_id), request_id=request_id, task_id=task_id,
                status=(AgentOutcome.ACCEPTED_UNVERIFIED if result.ok
                        else AgentOutcome.TERMINAL_FAILURE),
                explanation=("PulseSoc accepted the change, but something went wrong while I was "
                             "recording and checking it. Please look at the screen before "
                             "relying on it." if result.ok else
                             "That did not work, and I could not record why."),
                evidence={"settle_error": exc.__class__.__name__,
                          "needs_reconciliation": bool(spec.is_write and result.ok),
                          "operation_id": str(prepared.get("operation_id") or "")}),
            result=result,
        )


def _settle(cur, *, spec: CapabilitySpec, user_id: int, arguments: dict[str, Any],
            result: ToolResult, prepared: dict[str, Any], grant: dict[str, Any] | None,
            request_id: str, task_id: str, correlation_id: str,
            question: str = "", history: tuple[str, ...] = ()) -> GatewayOutcome:
    """Verify, audit and describe an execution that has already happened.

    Split out of :func:`execute` so the boundary between "nothing has changed yet"
    and "something has changed" is a function call rather than a comment halfway
    down a long body. Everything in :func:`execute` above the call may raise, and
    raising there is correct — no mutation has occurred. Nothing here may.
    """
    # 9. Independent verification, before any claim of success is formed.
    verification = _verify(spec, int(user_id), arguments, result) if result.ok else VerificationResult(
        state=VerificationState.IMPOSSIBLE, detail="The action did not run, so there is nothing to verify.",
    )
    status = _status_for(spec, result, verification)

    # 10. Audit. ``canonical_verified`` carries the real read-back verdict so the ledger
    #    records evidence rather than the structural guess undx_architecture would
    #    otherwise fall back to.
    audit: dict[str, Any] = {}
    try:
        audit = undx_architecture.record_tool_result(
            cur, int(user_id), prepared,
            {
                "success": bool(result.ok),
                "canonical_entity_id": result.canonical_resource_id,
                "error_code": result.error_code,
                "latency_ms": result.latency_ms,
                "degraded_sources": list(result.degraded_sources),
            },
            clean(correlation_id or request_id, 120),
            confirmation=grant,
            expect_action_id=spec.capability_id,
            # A read used to pass ``None`` here and be filed as verified regardless of
            # whether its queries ran. The audit trail is the record we would reach for
            # to answer "was UNDX right that day", so it has to distinguish a read that
            # saw everything from one that saw part and returned quietly.
            canonical_verified=(verification.state == VerificationState.VERIFIED
                                if spec.is_write else not result.degraded_sources),
        )
    except Exception as exc:  # pragma: no cover - defensive
        # The mutation already happened and its verdict could not be recorded. Repeating
        # the mutation would be the wrong repair, and so would rolling back — the change
        # is real either way. Mark the reserved row so the disagreement is findable, keep
        # the idempotency key so nothing retries, and carry the fact into the receipt.
        audit = {"status": "audit_failed", "error": exc.__class__.__name__}
        logger.critical("undx_audit_write_failed tool=%s user=%s error=%s",
                        spec.tool_name, int(user_id), exc.__class__.__name__)
        if spec.is_write:
            try:
                audit["reconciliation"] = undx_architecture.flag_operation_for_reconciliation(
                    cur, int(user_id), prepared, exc.__class__.__name__)
            except Exception as flag_exc:  # pragma: no cover - defensive
                # The database is refusing writes on two consecutive attempts, so the
                # ledger cannot be told about this. Say so in the receipt rather than
                # letting a second failure inside the first failure's handler destroy
                # the only remaining description of a mutation that really happened.
                audit["reconciliation"] = {"status": "flag_failed",
                                           "error": flag_exc.__class__.__name__}
                logger.critical("undx_reconciliation_flag_failed tool=%s user=%s error=%s",
                                spec.tool_name, int(user_id), flag_exc.__class__.__name__)
    try:
        _checkpoint(cur)
    except Exception:  # pragma: no cover - defensive
        logger.critical("undx_audit_checkpoint_failed tool=%s user=%s", spec.tool_name, int(user_id))
    # The mutation itself already committed through the service layer's own connection,
    # so this row can no longer be "undone" by rolling it back — rolling it back would
    # only delete the evidence of something that really happened. Making it durable here
    # is what keeps "every executed action has an audit row" true even if the surrounding
    # request fails afterwards.

    explanation, response_plan = _compose_response(
        spec, status, result, verification, question=question, history=history)
    receipt = _receipt(
        spec, user_id=user_id, request_id=request_id, task_id=task_id,
        status=status, explanation=explanation,
        arguments=arguments, verification=verification,
        canonical_ids=[result.canonical_resource_id] if result.canonical_resource_id else [],
        evidence={
            # The plan travels with the receipt so that the sentence a user read can be
            # audited against the facts it was allowed to use. Without it, "why did UNDX
            # say that?" is answerable only by re-running the turn.
            "response_plan": response_plan,
            "verification": {
                "state": verification.state,
                "expected": verification.expected,
                "observed": verification.observed,
                "detail": verification.detail,
                **verification.evidence,
            },
            "audit": {"status": audit.get("status", ""),
                      "confirmation_state": audit.get("confirmation_state", ""),
                      "needs_reconciliation": audit.get("status") == "audit_failed"},
            "operation_id": prepared["operation_id"],
            "risk": spec.risk,
        },
    )
    return GatewayOutcome(receipt, result=result, verification=verification)


__all__ = ["GatewayOutcome", "execute"]
