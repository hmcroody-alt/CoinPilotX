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

    __slots__ = ("receipt", "confirmation", "result", "verification", "is_write")

    def __init__(self, receipt: AgentReceipt, *, confirmation: ConfirmationRequest | None = None,
                 result: ToolResult | None = None, verification: VerificationResult | None = None,
                 is_write: bool = True) -> None:
        self.receipt = receipt
        self.confirmation = confirmation
        self.result = result
        self.verification = verification
        # Defaults to the stricter of the two readings. A caller that did not say what it
        # ran is held to the write rules, because assessing a mutation as a lookup is the
        # error that ends with somebody being told a change happened.
        self.is_write = bool(is_write)

    @property
    def status(self) -> str:
        return self.receipt.status

    @property
    def succeeded(self) -> bool:
        """The call did what it was asked to do.

        Deliberately *not* the same question as :attr:`may_claim_done`. A read that
        answered from the account succeeded, and there is still nothing completed to tell
        the person about. Keeping the two names apart is the point: collapsing them is how
        "the lookup worked" turns into "your change is done".

        **The two halves are asked differently because they are different questions.** A
        write succeeded when the receipt says so — status completed *and* an independent
        read-back that verified — which is :attr:`AgentReceipt.may_claim_completed`
        unchanged. A read has no such second condition available to it: a read-only
        capability declares no verifier, so :func:`_verify` returns ``impossible`` and the
        receipt's verification state says so truthfully. Holding a lookup to a write's
        read-back requirement made this property return ``False`` for every read that ever
        ran, in flat contradiction of the sentence above it, and the durable run queue —
        the one caller that reads this — recorded every successfully executed read as a
        failure. See ``scripts/undx_read_settlement_probe.py`` for the measurement.

        The asymmetry can only widen the *read* answer and cannot touch the write answer,
        which is what makes it safe. Nothing downstream turns a successful read into a
        completion claim either: :attr:`may_claim_done` stays narrow, because the Brain
        assesses a read as ``RETRIEVED`` no matter how well it went.
        """
        if not self.is_write:
            return self.receipt.status in AgentOutcome.COMPLETED
        return self.receipt.may_claim_completed

    @property
    def assessment(self) -> Any:
        """What :mod:`services.undx_brain.evidence` makes of this outcome, or ``None``.

        The Brain's evidence module has always been able to answer this and nothing on the
        live path ever asked it. This property is the ask. It re-derives the state from the
        same two fields the receipt was built from — the outcome and the independent
        read-back — using code that was written separately from the receipt's own rule, so
        the two are genuinely independent readings rather than one reading called twice.

        Imported lazily and guarded, like every other Brain call site, so that a missing or
        broken Brain package degrades this to ``None`` instead of taking the gateway down
        with it. ``None`` is not a permissive answer: :attr:`may_claim_done` treats it as
        "no second opinion available" and falls back to the receipt alone, which is exactly
        where the system already was.
        """
        try:
            from services.undx_brain import evidence as brain_evidence
        except Exception:  # pragma: no cover - Brain package absent
            return None
        try:
            # The receipt's own ``verification_state`` is the fallback, not ``None``, and
            # the distinction is not cosmetic. Several paths reach a settled receipt with
            # no ``VerificationResult`` object attached — an idempotent replay carries the
            # earlier operation's verdict, a refusal carries ``impossible`` — and passing
            # ``None`` there would make this read a different pair of facts than
            # ``may_claim_completed`` reads, which would turn every one of those turns into
            # a logged "divergence" that is really just the two looking at different
            # inputs. Two independent derivations are only worth comparing when they are
            # derived from the same thing.
            return brain_evidence.derive(
                self.receipt.status,
                self.verification if self.verification is not None
                else self.receipt.verification_state,
                is_write=self.is_write)
        except Exception:  # pragma: no cover - derive is documented never to raise
            logger.warning("evidence assessment failed; falling back to the receipt",
                           exc_info=True)
            return None

    @property
    def may_claim_done(self) -> bool:
        """Whether this turn may tell the person their change is complete.

        The conjunction of two independently written derivations of the same rule:
        :attr:`AgentReceipt.may_claim_completed`, which compares the outcome and the
        verification state directly, and :meth:`assessment`, which resolves the same pair
        through the Brain's state machine. Both must say yes.

        A conjunction can only ever *narrow*. That asymmetry is the reason this is safe to
        turn on unconditionally rather than behind a flag: the worst a defect in either
        derivation can do here is withhold a claim the system was entitled to make, and
        withholding a true "it's done" costs a person one extra look at their settings,
        while making a false one costs them their trust in every other thing UNDX says.

        The two disagree in exactly one reachable place, and they are supposed to. A
        successful *read* satisfies ``may_claim_completed`` — the status is
        ``verified_success`` and the read-back verified — while the evidence module assesses
        it as ``RETRIEVED``, which does not license a completion claim, because a lookup is
        not a change. Any *other* disagreement is a defect in one of the two, so it is
        logged rather than quietly resolved; the narrower answer is still returned, because
        an unexplained disagreement is not a reason to trust the wider one.
        """
        receipt_says = self.receipt.may_claim_completed
        found = self.assessment
        if found is None:
            return receipt_says
        brain_says = bool(found.may_claim_done)
        if receipt_says != brain_says and not (receipt_says and not self.is_write):
            logger.warning(
                "undx_evidence_divergence capability=%s status=%s verification=%s "
                "receipt=%s brain=%s state=%s",
                self.receipt.capability_id, self.receipt.status,
                getattr(self.verification, "state", None), receipt_says, brain_says,
                getattr(found, "state", None))
        return receipt_says and brain_says


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
    #
    # The test is the receipt's own rule, applied to the pair of facts this receipt is
    # about to be built from, rather than a re-statement of half of it. It used to read
    # ``status == VERIFIED_SUCCESS`` alone, which is the first of ``may_claim_completed``'s
    # two conditions and not the second, so any path reaching a completed status without
    # a read-back offered the person a button to reverse a change that may never have
    # happened. Undo is the highest-consequence affordance in the system — it is itself a
    # write, aimed at state whose value is in doubt — so it is the last place that should
    # get a weaker definition of success than the sentence beside it.
    verified_now = (
        status in AgentOutcome.COMPLETED
        and (verification.state if verification else VerificationState.IMPOSSIBLE)
        == VerificationState.VERIFIED
    )
    undo_arguments = (
        spec.undo_arguments(arguments or {}, list(canonical_ids or []))
        if verified_now
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
            token: str, confirmation_id: str = "") -> dict[str, Any] | None:
    """Redeem an approval for exactly this action, or return ``None``.

    Both bindings are asserted inside :func:`undx_architecture.consume_confirmation`,
    which checks them before burning the row, so a wrong-action redemption neither
    destroys a valid grant nor reveals that one existed.

    Two ways in, one set of rules. A client that holds the bearer token presents it; the
    conversational path, which never receives the plaintext, addresses the same row by
    the id :func:`undx_architecture.pending_approvals` handed it. The token is preferred
    when both arrive, because it is the stronger credential — possession of it proves the
    caller saw the card — and a caller holding one has no reason to fall back.

    The id route is not a weaker door. ``consume_approval`` applies the identical owner
    scope, pending-and-unexpired predicate, continuation-namespace guard, action and
    argument bindings, and single-use burn. What it does *not* require is a secret, and
    that is sound only because the id is never a credential on its own: it is read out of
    the caller's own rows, under their own user id, by a function that cannot see anybody
    else's. Authority comes from the authenticated session, exactly as it does for the
    revoke path this mirrors.
    """
    if clean(token, 500):
        return undx_architecture.consume_confirmation(
            cur, int(user_id), token,
            expect_action_id=spec.capability_id,
            expect_argument_hash=canonical_hash(arguments),
        )
    return undx_architecture.consume_approval(
        cur, int(user_id), clean(confirmation_id, 120),
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
                      history: tuple[str, ...] = (),
                      goal_shape: str = "") -> tuple[str, dict[str, Any]]:
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
            spec, status, result, verification, question=question, history=history,
            goal_shape=goal_shape)
        if text:
            return text, plan.to_dict()
    except Exception as exc:  # pragma: no cover - defensive
        logger.error("undx_response_intelligence_failed capability=%s error=%s",
                     spec.capability_id, exc.__class__.__name__)
    return _terse_explanation(spec, status, result, verification), {}


def _explain(spec: CapabilitySpec, status: str, result: ToolResult,
             verification: VerificationResult, *, question: str = "",
             history: tuple[str, ...] = (), goal_shape: str = "") -> str:
    """Plain language that matches the evidence, including when the evidence is thin."""
    return _compose_response(spec, status, result, verification,
                            question=question, history=history,
                            goal_shape=goal_shape)[0]


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
    confirmation_id: str = "",
    explicit_request: bool = False,
    resolved_resource_count: int = 1,
    target_chosen_by_agent: bool = False,
    current_value: Any = None,
    proposed_value: Any = None,
    resource_label: str = "",
    question: str = "",
    goal_shape: str = "",
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

    ``question``, ``goal_shape`` and ``recent_replies`` reach only the response layer,
    and only after every decision has been made. They cannot select a capability, widen
    a permission, or change an outcome — the last of the nine checks has already run by
    the time any of them is read. What they can do is make the answer the right length,
    make it answer the question that was actually asked, and stop it repeating the
    previous one, which is worth having and worth confining.

    ``goal_shape`` in particular is a string the caller supplies, not a privilege. The
    worst a wrong one can do is produce a longer answer about the same evidence; there
    is no shape that unlocks a capability, because by this point the capability has
    already run.
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
        target_chosen_by_agent=bool(target_chosen_by_agent),
    )
    if decision.denied:
        return GatewayOutcome(_receipt(
            spec, user_id=user_id, request_id=request_id, task_id=task_id,
            status=decision.outcome or AgentOutcome.PERMISSION_DENIED,
            explanation=decision.message, arguments=arguments,
            evidence={"reason": decision.reason, **decision.details},
        ), is_write=spec.is_write)

    # 5. Confirmation. Two independent questions, and conflating them was a real bug.
    #
    #    "Is an approval needed?" is the policy engine's question, and it is asked of
    #    the request. "Is an approval being spent?" is this function's question, and it
    #    is answered by whether a token arrived. Redemption used to hang off the first,
    #    which meant a presented token was simply ignored whenever the policy happened
    #    to conclude that no card was needed.
    #
    #    That is not hypothetical. ``_agent_confirm`` replays an approval with
    #    ``explicit_request=True`` — truthfully, since pressing Confirm is explicit —
    #    and for a ``CONTEXTUAL`` capability the policy engine then returns ``ALLOW``
    #    via ``explicit_single_resource``. The write ran, was verified, and was audited
    #    as ``confirmation_state="not_required"`` with ``confirmation_evidence="no_grant"``
    #    while the approval row sat at ``pending`` for the rest of its TTL — replayable,
    #    and never reaching the ``consumed`` state whose message tells a person the
    #    change already happened. Pressing Confirm twice performed the write twice.
    #    ``tests/undx_agent/test_spent_approval.py`` holds that case.
    #
    #    So: a token that was presented is redeemed, whatever the policy concluded. A
    #    token that cannot be redeemed refuses the call rather than falling through to
    #    an execution that the presented approval no longer authorises.
    #
    #    "Presented" means an approval was *named*, by either of the two handles that can
    #    name one. A tapped Confirm carries the token; a typed "yes" carries the id, and
    #    it has to carry something, or the sentence would fall through to the mint branch
    #    and stage a second card in answer to the approval of the first. That was the
    #    whole of the execution gap: the conversational path could raise a confirmation
    #    and had no way to spend one.
    grant: dict[str, Any] | None = None
    presented = clean(confirmation_token, 500) or clean(confirmation_id, 120)
    if decision.needs_confirmation and not presented:
        request = _mint_confirmation(
            cur, int(user_id), spec, arguments, task_id=task_id,
            current_value=current_value, proposed_value=proposed_value,
            risk_summary=spec.description,
            resource_label=resource_label,
        )
        _checkpoint(cur)
        # The label, when the preview read one back, rather than the bare ask. A card
        # renders the target in its own right; this string is what a text transcript
        # has instead of a card, and "confirm this before I make the change" over a row
        # the person never named asks them to approve something they cannot see. The
        # fallback is the old sentence, not a guess: a preview that read nothing has no
        # name to offer, and inventing one here would describe the request rather than
        # the row — the one substitution this whole path exists to prevent.
        label = clean(resource_label, 160)
        return GatewayOutcome(
            _receipt(spec, user_id=user_id, request_id=request_id, task_id=task_id,
                     status=AgentOutcome.CONFIRMATION_REQUIRED,
                     # Title then label, the same two parts in the same order the card
                     # draws them, so a person who reads one and then the other is not
                     # comparing two renderings of the same approval. The description is
                     # not conjugated into the sentence — it is a registry noun phrase
                     # and reads as one; splicing it after "I will" produces prose that
                     # is grammatical and slightly wrong, which is worse here than plain.
                     explanation=(f"{spec.description}: {label}. Confirm and I will make "
                                  "the change." if label
                                  else "I need you to confirm this before I make the change."),
                     arguments=arguments,
                     evidence={"confirmation_id": request.confirmation_id,
                               "expires_at": request.expires_at}),
            confirmation=request,
            is_write=spec.is_write,
        )
    if presented:
        grant = _redeem(cur, int(user_id), spec, arguments, confirmation_token,
                        confirmation_id)
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
            ), is_write=spec.is_write)

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
            # The ledger's ``verified`` is a read-back verdict that was actually taken —
            # by the earlier attempt, against the canonical store, and recorded. Carrying
            # it forward as a real :class:`VerificationResult` is what lets this turn's
            # receipt reach ``may_claim_completed`` honestly, and what stops every reader
            # downstream from having to invent a rule for the replay case.
            #
            # It is deliberately *not* extended to ``ok``. ``ok`` means the executor
            # returned without error and nothing ever looked; the status below is
            # ``accepted_unverified`` and the verification stays ``impossible``, so the
            # prose guard in :func:`~services.undx_agent_runtime.build_card` catches "I
            # had already done that" and replaces it. Two ledger words that were treated
            # identically here now diverge exactly where they always differed in meaning.
            replay_verification = (
                VerificationResult(
                    state=VerificationState.VERIFIED,
                    evidence={"source": "audit_ledger",
                              "operation_id": clean(previous.get("operation_id"), 60)},
                    detail="an earlier attempt was read back and recorded as verified",
                )
                if prior_status == "verified" else None
            )
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
                         verification=replay_verification,
                         canonical_ids=[replayed.canonical_resource_id] if replayed.canonical_resource_id else [],
                         evidence={"idempotent_replay": True,
                                   "prior_status": prior_status,
                                   "needs_reconciliation": unsettled,
                                   "operation_id": clean(previous.get("operation_id"), 60)}),
                result=replayed,
                verification=replay_verification,
                is_write=spec.is_write,
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
                cur, int(user_id), prepared, clean(correlation_id or request_id, 120),
                # What this operation was authorised by, not what the tool usually
                # needs. Without it a contextual capability that a person explicitly
                # approved audits identically to one nobody was asked about.
                confirmed=bool(grant))
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
            goal_shape=goal_shape,
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
            is_write=spec.is_write,
        )


def _settle(cur, *, spec: CapabilitySpec, user_id: int, arguments: dict[str, Any],
            result: ToolResult, prepared: dict[str, Any], grant: dict[str, Any] | None,
            request_id: str, task_id: str, correlation_id: str,
            question: str = "", history: tuple[str, ...] = (),
            goal_shape: str = "") -> GatewayOutcome:
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
        spec, status, result, verification, question=question, history=history,
        goal_shape=goal_shape)
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
    return GatewayOutcome(receipt, result=result, verification=verification,
                          is_write=spec.is_write)


__all__ = ["GatewayOutcome", "execute"]
