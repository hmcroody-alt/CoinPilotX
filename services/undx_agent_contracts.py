"""Canonical typed contracts for the UNDX agent runtime.

This module is the vocabulary every other agent module speaks. It is deliberately
dependency-free (standard library only, no Flask, no database) so that the runtime,
the gateway, the verifier and their tests can be exercised without booting the web
application. Nothing here performs I/O or makes a policy decision; it only defines
the shapes that carry decisions between the modules that do.

Two rules drive the design:

1.  **No free-form tool payloads.** A model may propose an action, but the arguments
    it proposes are validated against a declared schema before anything executes.
    ``validate_arguments`` is the only sanctioned way to turn model-proposed values
    into a ``ToolCall``.
2.  **Success is a narrow claim.** ``AgentOutcome`` separates "the backend accepted
    this" from "we independently read the state back and it matched". Only the
    latter is allowed to be reported to a user as done.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Callable, Iterable


# ---------------------------------------------------------------------------
# Canonical enumerations
# ---------------------------------------------------------------------------


class AgentOutcome:
    """The complete set of terminal answers the agent may give about an action.

    Anything that is not one of these is a bug, not a new outcome. The runtime
    asserts membership before a receipt is persisted so a typo cannot silently
    invent a status the native client does not know how to render.
    """

    VERIFIED_SUCCESS = "verified_success"
    ACCEPTED_UNVERIFIED = "accepted_unverified"
    #: The request was accepted and moved to the worker. Nothing has been attempted yet.
    #:
    #: A separate value rather than a reuse of ``ACCEPTED_UNVERIFIED``, and the
    #: distinction is the entire reason durable runs are safe to have. That one means
    #: *the write was performed and could not be read back* — a real change with an
    #: unknown result. This one means *no executor has been entered*. Collapsing them
    #: would let a row sitting in a queue render under the client's receipt kicker, which
    #: is the precise failure the whole verification chain exists to prevent, reached by
    #: the one route the verification chain cannot see.
    #:
    #: It is deliberately absent from ``COMPLETED`` and from ``AWAITING_USER``. Not
    #: completed because nothing ran; not awaiting the user because the next thing that
    #: happens is a worker claiming it, and a client that treats this as an open question
    #: would hold a conversation open waiting for an answer nobody was asked for.
    ACCEPTED_QUEUED = "accepted_queued"
    CONFIRMATION_REQUIRED = "confirmation_required"
    #: The agent understood the request and is waiting to be told one more thing.
    #:
    #: Carried as ``terminal_failure`` until now, which was wrong in a way that cost
    #: something real: anything counting terminal failures counted every question the
    #: runtime asked as something breaking, so the metric got *worse* the more
    #: carefully the agent behaved. It is not a failure. Nothing was attempted, and
    #: the next message can complete it.
    #:
    #: This was deferred across four batches on the belief that a native client
    #: meeting an unknown enum value would render nothing at all. That belief was
    #: wrong, and reading the client rather than assuming is what settled it:
    #: ``kindOf`` in ``mobile-native/src/undx/actionCards.ts`` returns ``"failure"``
    #: for anything it does not recognise, which is exactly what a question renders as
    #: today. An old client is therefore no worse off, a new one is better off, and
    #: ``contractParity.test.ts`` already fails when the server adds a card the client
    #: has no home for — so the drift this deferral feared is the one case CI catches.
    CLARIFICATION_REQUIRED = "clarification_required"
    #: The person withdrew a staged action in words before it ran.
    #:
    #: Not a failure and not a refusal. Nothing broke, nobody was denied anything, and
    #: the outcome is exactly what was asked for — which is why it could not be carried
    #: as ``terminal_failure``. A cancellation drawn under "NOT DONE" tells someone who
    #: successfully changed their mind that something went wrong, and the whole reason
    #: this batch exists is that saying "never mind" was previously reported as nothing
    #: at all: the turn declined, the approval stayed live, and the button on screen
    #: still worked.
    #:
    #: Distinct from ``permission_denied`` in the direction that matters. That one says
    #: the *system* refused; this one says the *person* did.
    CANCELLED = "cancelled"
    PERMISSION_DENIED = "permission_denied"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    RECOVERABLE_FAILURE = "recoverable_failure"
    TERMINAL_FAILURE = "terminal_failure"

    ALL = frozenset({
        VERIFIED_SUCCESS,
        ACCEPTED_UNVERIFIED,
        ACCEPTED_QUEUED,
        CONFIRMATION_REQUIRED,
        CLARIFICATION_REQUIRED,
        CANCELLED,
        PERMISSION_DENIED,
        UNSUPPORTED_CAPABILITY,
        RECOVERABLE_FAILURE,
        TERMINAL_FAILURE,
    })

    #: Outcomes a user may be told represent a completed change.
    COMPLETED = frozenset({VERIFIED_SUCCESS})

    #: Outcomes where the runtime is holding a question open and the next message from
    #: this account may close it. Named as a set rather than tested inline because
    #: three places have to agree about it — the card the client draws, the metric that
    #: counts failures, and the continuation store that decides whether a reply is an
    #: answer — and those three going out of step is how a question stops being one.
    AWAITING_USER = frozenset({CONFIRMATION_REQUIRED, CLARIFICATION_REQUIRED})


class RiskLevel:
    """Server-owned risk classes. The model never assigns these."""

    READ_ONLY = "read_only"
    REVERSIBLE_WRITE = "reversible_write"
    CONSEQUENTIAL_WRITE = "consequential_write"
    HIGH_RISK = "high_risk"

    ALL = frozenset({READ_ONLY, REVERSIBLE_WRITE, CONSEQUENTIAL_WRITE, HIGH_RISK})

    #: Ordering used for "at least this risky" comparisons.
    ORDER = {READ_ONLY: 0, REVERSIBLE_WRITE: 1, CONSEQUENTIAL_WRITE: 2, HIGH_RISK: 3}

    @classmethod
    def is_write(cls, level: str) -> bool:
        return cls.ORDER.get(str(level), 0) >= cls.ORDER[cls.REVERSIBLE_WRITE]


class PermissionScope:
    """Whose data a capability is allowed to touch.

    This started life as documentation — a string on every capability that read
    ``self_account_only`` and was consulted by nothing. That is a dangerous kind of
    comment, because it reads like an enforced invariant: someone reviewing a new
    capability sees the field, believes ownership is handled, and does not check that
    the executor actually scoped its query. It was harmless while every target was a
    row belonging to the caller. It stops being harmless the moment a capability's
    target is *another user*, which is exactly what a social pack is.

    So the values are now load-bearing. The gateway refuses to execute a capability
    whose scope it has no enforcement rule for, and each rule is a real check:

    ``self_account_only``
        The capability may only reach rows owned by the caller. Enforced structurally:
        no declared field may name another actor, so there is nothing for a hostile
        argument to point at.
    ``other_user_target``
        The capability names another account on purpose. The target must be declared,
        must resolve, and must be authorised for this actor before execution.
    ``owned_content_target``
        The capability names a content item. It must resolve and be visible to this
        actor; ownership of the *content* is not required, but access is.
    """

    SELF_ACCOUNT_ONLY = "self_account_only"
    OTHER_USER_TARGET = "other_user_target"
    OWNED_CONTENT_TARGET = "owned_content_target"

    ALL = frozenset({SELF_ACCOUNT_ONLY, OTHER_USER_TARGET, OWNED_CONTENT_TARGET})


class ConfirmationPolicy:
    """When a capability needs a human approval bound to its exact arguments."""

    NEVER = "never"
    CONTEXTUAL = "contextual"
    ALWAYS = "always"

    ALL = frozenset({NEVER, CONTEXTUAL, ALWAYS})


class RunConfirmation:
    """The approval state recorded on a durable run row.

    Distinct from :class:`ConfirmationPolicy`, which says what a *capability* demands.
    This says what a particular queued run *has*, which is a fact about one row.

    It lives here, in the dependency-free contracts module, because two modules that
    must never disagree about it are otherwise on opposite sides of a heavy import.
    :mod:`services.undx_agent_runs` writes the column and decides on its strength
    whether a worker may claim the row; :mod:`services.undx_run_status` reads the same
    column and decides on its strength whether to tell somebody their request is waiting
    on them. Held as two literal sets in two modules, those drift, and the drift is
    silent in the worst direction: a run the projection calls "waiting for you" that the
    claim query happily picks up and executes anyway.

    :data:`PENDING_STATES` carries three spellings rather than one on purpose. Nothing in
    this repository writes them yet — ``enqueue`` refuses to create an unapproved write at
    all — so the set exists to be *recognised*, and a recogniser that only knows the
    spelling in use today fails open the first time another module picks a synonym.
    """

    #: The capability is read-only or ``confirmation=never``; no approval was ever needed.
    NOT_REQUIRED = "not_required"
    #: A person approved this exact capability, target and argument hash.
    GRANTED = "granted"

    PENDING = "pending"
    REQUIRED = "required"
    AWAITING = "awaiting"

    #: Parked on a person. A run in any of these states is not work a worker can advance,
    #: and Stage 18 of the durable-run design turns on that: claiming one would hold a
    #: lease and spend an attempt against an approval that does not exist yet.
    PENDING_STATES = frozenset({PENDING, REQUIRED, AWAITING})

    #: Every spelling this column is allowed to hold.
    ALL = frozenset({NOT_REQUIRED, GRANTED}) | PENDING_STATES


class VerificationState:
    """The verdict of reading state back independently of the mutation response."""

    VERIFIED = "verified"
    PENDING = "verification_pending"
    FAILED = "verification_failed"
    IMPOSSIBLE = "impossible_to_verify"

    ALL = frozenset({VERIFIED, PENDING, FAILED, IMPOSSIBLE})


class TaskStatus:
    PLANNING = "planning"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"

    ALL = frozenset({
        PLANNING, AWAITING_CONFIRMATION, EXECUTING, VERIFYING,
        SUCCEEDED, FAILED, CANCELLED, EXPIRED,
    })

    #: Statuses from which a task may never be resumed.
    CLOSED = frozenset({SUCCEEDED, FAILED, CANCELLED, EXPIRED})


class CardType:
    """Native result-card identifiers understood by the PulseSoc client."""

    SEARCH_RESULTS = "search_results"
    PROFILE_RESULT = "profile_result"
    CONTENT_RESULT = "content_result"
    CONVERSATION_RESULT = "conversation_result"
    MESSAGE_DRAFT_CONFIRMATION = "message_draft_confirmation"
    ACTION_CONFIRMATION = "action_confirmation"
    ACTION_PROGRESS = "action_progress"
    ACTION_SUCCESS_RECEIPT = "action_success_receipt"
    ACTION_FAILURE = "action_failure"
    SETTING_CHANGE_RECEIPT = "setting_change_receipt"
    CRYPTO_ALERT_CARD = "crypto_alert_card"
    RELATIONSHIP_CHANGE_RECEIPT = "relationship_change_receipt"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    PERMISSION_DENIED = "permission_denied"
    RETRY_ACTION = "retry_action"
    #: A question with a value to compose. "Which price?" — nothing to pick from.
    CLARIFICATION_REQUIRED = "clarification_required"
    #: A question with rows to pick from. Carries ``candidates``.
    #:
    #: Two card types rather than one because the client renders them differently and
    #: has to know which without inspecting the payload: a chooser is a list of
    #: tappable rows, a clarification is a prompt to type. Deciding that from
    #: ``candidates.length`` would put the branch in every renderer instead of in the
    #: contract, and a chooser that arrived with an empty list would silently become a
    #: prompt with nothing to prompt for.
    CHOICE_REQUIRED = "choice_required"
    #: A staged action the person called off in words, and the grant that is now dead.
    #:
    #: Its own component rather than ``ACTION_FAILURE`` for the same reason
    #: ``CHOICE_REQUIRED`` is not ``CRYPTO_ALERT_CARD``: the client draws from the
    #: component name, and every existing name would misdescribe this one. A
    #: cancellation is not a failure, and it is emphatically not a receipt — nothing
    #: was written. It reports that something which was about to happen now will not.
    ACTION_CANCELLED = "action_cancelled"

    ALL = frozenset({
        SEARCH_RESULTS, PROFILE_RESULT, CONTENT_RESULT, CONVERSATION_RESULT,
        MESSAGE_DRAFT_CONFIRMATION, ACTION_CONFIRMATION, ACTION_PROGRESS,
        ACTION_SUCCESS_RECEIPT, ACTION_FAILURE, SETTING_CHANGE_RECEIPT,
        CRYPTO_ALERT_CARD, RELATIONSHIP_CHANGE_RECEIPT, UNSUPPORTED_CAPABILITY,
        PERMISSION_DENIED, RETRY_ACTION, CLARIFICATION_REQUIRED, CHOICE_REQUIRED,
        ACTION_CANCELLED,
    })

    #: Cards that are open questions rather than reports of something attempted.
    QUESTIONS = frozenset({CLARIFICATION_REQUIRED, CHOICE_REQUIRED})


# ---------------------------------------------------------------------------
# Bounds. Every task is finite; none of these may be raised by a caller.
# ---------------------------------------------------------------------------


MAX_PLAN_STEPS = 6
MAX_TOOL_CALLS = 8
MAX_RETRIES = 2
MAX_EXECUTION_SECONDS = 20.0
MAX_TEXT_CHARS = 4000
MAX_ARGUMENT_CHARS = 2000
CONFIRMATION_TTL_SECONDS = 300


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class AgentError(Exception):
    """A typed, user-safe agent failure.

    ``code`` is a stable machine string for telemetry and client branching.
    ``message`` is shown to the user and must never contain a stack trace,
    credential, internal path, or another account's data. ``retryable`` tells the
    runtime whether re-attempting could plausibly succeed; it is set to ``False``
    for anything that already mutated state, so a retry cannot double-apply a write.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        outcome: str = AgentOutcome.TERMINAL_FAILURE,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = str(code or "agent_error")
        self.message = str(message or "UNDX could not complete that.")
        self.outcome = outcome if outcome in AgentOutcome.ALL else AgentOutcome.TERMINAL_FAILURE
        self.retryable = bool(retryable)
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "outcome": self.outcome,
            "retryable": self.retryable,
            "details": self.details,
        }


# ---------------------------------------------------------------------------
# Small helpers shared by every contract
# ---------------------------------------------------------------------------


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    """A correlation identifier for one request or task.

    Generated with :mod:`secrets` rather than a counter or a timestamp. These ids
    appear in audit rows and in client payloads, and a guessable id invites a caller
    to reference a task that is not theirs.
    """
    return f"{clean(prefix, 40) or 'undx'}_{secrets.token_hex(10)}"


def clean(value: Any, limit: int = 240) -> str:
    """Collapse any value into a bounded single-line string."""
    text = " ".join(str(value if value is not None else "").split())
    return text[:max(0, int(limit))]


def format_amount(value: Any) -> str:
    """A threshold as a person would write it, or "" if it is not a number.

    ``90000.0`` is what the column holds and not what anybody types. Rendering it
    raw on a card is a small thing that reads as a machine talking to itself, and
    the card exists to be read.
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return ""
    if float(value) == int(value):
        return f"{int(value):,}"
    return f"{float(value):,.8f}".rstrip("0").rstrip(".")


def describe_alert(record: Any) -> str:
    """Name one alert the way the chooser named it, from a row that was read.

    Deliberately built from the *record* and from nothing else. The caller has the
    request text in hand and must not pass it: the defect this exists to expose is
    an id that resolved to an alert the request did not describe, and a label that
    could draw on the request would describe that alert correctly right up until the
    moment it mattered.

    The composition matches ``choiceRowsOf`` in the client — the chooser's name line
    followed by its detail line, same separator. A person who picked row 2 out of a
    list and then reads the confirmation should be looking at the same words, because
    "is this the one I chose?" is the question the card has to answer and comparing
    two different renderings of the same row is not a fair test of it.

    It lives here, in the module that depends on nothing, for a reason that was paid
    for live. Batch 16 taught the *confirmation* card to name its subject and left the
    *result* sentence saying "the current value is paused" — the same failure one
    screen later, because the naming lived in the runtime and the sentence is written
    in :mod:`services.undx_response_intelligence`, which cannot import it. Two copies
    would drift; a person comparing "is this the one I approved?" against a card that
    words it differently is doing exactly the comparison this composition exists to
    make fair. One function, imported by both, is the only arrangement in which the
    two screens cannot disagree.
    """
    if not isinstance(record, dict) or not record:
        return ""
    symbol = clean(record.get("symbol"), 24)
    name = clean(record.get("display_name"), 80) or (f"{symbol} alert" if symbol else "")
    parts = [part for part in (name,
                               clean(record.get("condition"), 24),
                               format_amount(record.get("threshold"))) if part]
    return clean(" · ".join(parts), 160)


def describe_post(record: Any) -> str:
    """Name one post the way a person would recognise it, from a row that was read.

    Same discipline as :func:`describe_alert` and here for the same reason, one step
    further along: a post's id is never something a person recognises, so a card that
    carries only the id is unreadable even when it is right. Where an alert at least
    has a symbol the person typed, "post 2" has nothing.

    Built from the record and never from the request. The failure this guards is the
    one ``resolve_recent_post`` introduces by existing — the runtime picking a row the
    sentence only described — and a label drawn from the request would say "your most
    recent post" over whichever row was actually chosen, which is precisely the
    reassurance a wrong choice needs in order to go unnoticed.

    Author first because the same body text on two accounts is a real case (a repost,
    a quote, a duplicate), and whose post it is decides whether liking it is what the
    person meant. The date disambiguates the recency claim itself, which on this path
    is the claim being checked. The snippet is last and is the part that is allowed to
    be lossy: it identifies, it does not reproduce.
    """
    if not isinstance(record, dict) or not record:
        return ""
    created = clean(record.get("created_at"), 40).split("T")[0]
    snippet = clean(record.get("title"), 90) or clean(record.get("body"), 90)
    parts = [part for part in (clean(record.get("author_name"), 80),
                               created,
                               f"“{snippet}”" if snippet else "") if part]
    return clean(" · ".join(parts), 160)


def canonical_hash(value: Any) -> str:
    """Stable fingerprint of a JSON-serialisable value.

    Uses the same normalisation as ``undx_architecture.argument_hash`` so a hash
    computed here binds to a confirmation minted there.
    """
    return hashlib.sha256(
        json.dumps(value or {}, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


# ---------------------------------------------------------------------------
# Argument schemas
# ---------------------------------------------------------------------------


_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")


@dataclass(frozen=True)
class FieldSpec:
    """One declared, validated argument.

    A capability declares its full argument surface as ``FieldSpec`` objects.
    Anything a model proposes that is not declared here is dropped rather than
    forwarded, which is what stops an injected instruction from smuggling an extra
    parameter (``{"user_id": 999}``) into a tool the user did authorise.
    """

    name: str
    kind: str  # "str" | "int" | "float" | "bool" | "enum" | "identifier"
    required: bool = True
    choices: tuple[Any, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    max_length: int = 240
    default: Any = None

    def coerce(self, raw: Any) -> Any:
        """Convert one raw value, raising ``AgentError`` when it cannot be trusted."""
        if self.kind == "bool":
            if isinstance(raw, bool):
                return raw
            text = clean(raw, 12).lower()
            if text in {"true", "1", "yes", "on", "enable", "enabled"}:
                return True
            if text in {"false", "0", "no", "off", "disable", "disabled"}:
                return False
            raise self._invalid("expected a true/false value")
        if self.kind in {"int", "float"}:
            try:
                number = float(raw)
            except (TypeError, ValueError):
                raise self._invalid("expected a number") from None
            if number != number or number in (float("inf"), float("-inf")):
                raise self._invalid("expected a finite number")
            if self.minimum is not None and number < self.minimum:
                raise self._invalid(f"must be at least {self.minimum}")
            if self.maximum is not None and number > self.maximum:
                raise self._invalid(f"must be at most {self.maximum}")
            return int(number) if self.kind == "int" else float(number)
        text = clean(raw, self.max_length)
        if not text:
            raise self._invalid("must not be empty")
        if self.kind == "identifier" and not _IDENTIFIER.match(text):
            raise self._invalid("is not a valid identifier")
        if self.kind == "enum" and text not in {str(choice) for choice in self.choices}:
            raise self._invalid("is not an allowed value")
        return text

    def _invalid(self, reason: str) -> AgentError:
        return AgentError(
            "invalid_arguments",
            f"UNDX could not use the value given for '{self.name}': it {reason}.",
            outcome=AgentOutcome.TERMINAL_FAILURE,
            details={"field": self.name, "reason": reason},
        )


def validate_arguments(specs: Iterable[FieldSpec], proposed: Any) -> dict[str, Any]:
    """Turn model-proposed values into a validated argument dict.

    Undeclared keys are **dropped, not rejected**. A hostile string retrieved from a
    post may well contain plausible-looking extra parameters; rejecting the whole
    call would let that string deny service, while forwarding it would let it steer
    a tool. Dropping is the only option that neither breaks nor obeys.
    """
    source = proposed if isinstance(proposed, dict) else {}
    if len(json.dumps(source, default=str)) > MAX_ARGUMENT_CHARS:
        raise AgentError(
            "invalid_arguments",
            "That request carried more argument data than UNDX accepts.",
            details={"reason": "argument_payload_too_large"},
        )
    validated: dict[str, Any] = {}
    for spec in specs:
        if spec.name in source and source[spec.name] is not None:
            validated[spec.name] = spec.coerce(source[spec.name])
        elif spec.required and spec.default is None:
            raise AgentError(
                "missing_arguments",
                f"UNDX still needs '{spec.name}' before it can do that.",
                details={"field": spec.name},
            )
        elif spec.default is not None:
            validated[spec.name] = spec.default
    return validated


# ---------------------------------------------------------------------------
# Request / context
# ---------------------------------------------------------------------------


@dataclass
class AgentRequest:
    """One authenticated turn addressed to the agent."""

    request_id: str
    conversation_id: int
    user_id: int
    text: str
    locale: str = "en"
    timezone: str = "UTC"
    client: str = "unknown"
    app_version: str = ""
    device_context: dict[str, Any] = field(default_factory=dict)
    ui_context: dict[str, Any] = field(default_factory=dict)
    attachments: list[dict[str, Any]] = field(default_factory=list)
    resumed_task_id: str = ""
    client_message_id: str = ""

    def __post_init__(self) -> None:
        self.request_id = clean(self.request_id, 120)
        self.user_id = int(self.user_id or 0)
        self.conversation_id = int(self.conversation_id or 0)
        self.text = clean(self.text, MAX_TEXT_CHARS)
        self.locale = clean(self.locale, 16) or "en"
        self.timezone = clean(self.timezone, 64) or "UTC"
        self.client = clean(self.client, 40) or "unknown"
        self.app_version = clean(self.app_version, 40)
        self.resumed_task_id = clean(self.resumed_task_id, 120)
        self.client_message_id = clean(self.client_message_id, 120)
        if self.user_id <= 0:
            # An agent turn without a resolved account has no owner to bind tools
            # to, so it can never be made safe by later checks.
            raise AgentError(
                "unauthenticated",
                "UNDX needs you to be signed in before it can do that.",
                outcome=AgentOutcome.PERMISSION_DENIED,
            )


@dataclass
class ToolCall:
    """A validated, owner-bound intention to invoke exactly one registered tool."""

    tool_name: str
    capability_id: str
    owner_user_id: int
    arguments: dict[str, Any]
    request_id: str
    task_id: str
    idempotency_key: str
    canonical_resource_id: str = ""
    confirmation_token: str = ""

    def argument_hash(self) -> str:
        return canonical_hash(self.arguments)


@dataclass
class ToolResult:
    """The normalised outcome of one tool invocation.

    ``raw`` is deliberately absent. Tool output is untrusted input: it is summarised
    into declared fields by the gateway and the original object never travels
    onward, so instruction-like text inside a record cannot reach the model as if it
    were a directive.
    """

    ok: bool
    tool_name: str
    capability_id: str
    canonical_resource_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    records: list[dict[str, Any]] = field(default_factory=list)
    error_code: str = ""
    error_message: str = ""
    retryable: bool = False
    latency_ms: int = 0
    idempotent_replay: bool = False
    #: Reads that failed while producing this result. Non-empty means the records
    #: are a partial view: ``ok`` is True but the answer is not complete. This field
    #: exists because a failed read returns an empty list, which is otherwise
    #: identical to a genuinely empty result — the case where a wrong answer gets
    #: delivered with full authority. Anything here blocks ``verified_success``.
    degraded_sources: list[str] = field(default_factory=list)


@dataclass
class VerificationResult:
    """An independent read-back verdict on a mutation."""

    state: str
    expected: Any = None
    observed: Any = None
    evidence: dict[str, Any] = field(default_factory=dict)
    checked_at: str = field(default_factory=now_iso)
    detail: str = ""

    def __post_init__(self) -> None:
        if self.state not in VerificationState.ALL:
            raise AgentError("invalid_verification_state", "UNDX produced an unknown verification state.")

    @property
    def is_verified(self) -> bool:
        return self.state == VerificationState.VERIFIED


@dataclass
class ConfirmationRequest:
    """A pending approval bound to one user, capability and argument set."""

    confirmation_id: str
    confirmation_token: str
    capability_id: str
    action_name: str
    target: str
    current_value: Any
    proposed_value: Any
    risk_summary: str
    expires_at: str
    argument_hash: str = ""
    task_id: str = ""
    #: The resource in words the person has actually seen, read back from the row
    #: this approval would change.
    #:
    #: ``target`` is the canonical identifier and cannot be this. It is what the
    #: idempotency key and the audit row are built from, so it must stay stable and
    #: machine-shaped — for an alert it is a bare row id, a number the person has
    #: never been shown anywhere in the app. A card whose only distinguishing field
    #: is that id says the same thing about every alert on the account, which means
    #: approving it is not consent to anything in particular.
    #:
    #: Empty when the row could not be read. A blank label renders as no label; a
    #: label guessed from the request would be worse than none, because the one
    #: mistake this field exists to catch is a resolved id that does not match what
    #: the person asked for, and a label built from their words would agree with
    #: them precisely when it should not.
    resource_label: str = ""


@dataclass
class AgentReceipt:
    """The persisted, auditable record of what the agent actually did.

    This is the object of record. The natural-language reply is generated *from*
    it, which is the mechanism that stops the model from narrating a success the
    system never observed.
    """

    task_id: str
    request_id: str
    capability_id: str
    action: str
    status: str
    owner_user_id: int
    canonical_resource_ids: list[str] = field(default_factory=list)
    verification_state: str = VerificationState.IMPOSSIBLE
    evidence: dict[str, Any] = field(default_factory=dict)
    native_deep_link: str = ""
    undo_capability_id: str = ""
    #: The arguments that would reverse this action, when one can be built.
    #:
    #: Kept beside ``undo_capability_id`` because the two are only meaningful
    #: together. Naming the capability that reverses a change says nothing about how
    #: to invoke it, and the two capabilities most in need of an undo are precisely
    #: the two where replaying this call's arguments would be wrong: one undoes
    #: itself with the value flipped, the other undoes with a delete keyed on a row
    #: id that did not exist when the call was made.
    undo_arguments: dict[str, Any] = field(default_factory=dict)
    user_explanation: str = ""
    risk_level: str = RiskLevel.READ_ONLY
    timestamp: str = field(default_factory=now_iso)
    retry_count: int = 0

    def __post_init__(self) -> None:
        if self.status not in AgentOutcome.ALL:
            raise AgentError("invalid_outcome", "UNDX produced an unknown action outcome.")
        if self.verification_state not in VerificationState.ALL:
            raise AgentError("invalid_verification_state", "UNDX produced an unknown verification state.")

    @property
    def may_claim_completed(self) -> bool:
        """Whether user-facing copy is allowed to say the change is done.

        Both conditions are required. A verified read-back after a failed write, or
        a successful write we could not read back, are each insufficient.
        """
        return (
            self.status in AgentOutcome.COMPLETED
            and self.verification_state == VerificationState.VERIFIED
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AgentTask:
    """Durable state for one agent objective, resumable across app restarts."""

    task_id: str
    owner_user_id: int
    conversation_id: int
    capability_id: str
    risk_level: str
    status: str
    request_id: str = ""
    current_step: int = 0
    completed_steps: list[str] = field(default_factory=list)
    pending_confirmation_id: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    expires_at: str = ""
    retry_count: int = 0
    final_receipt: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in TaskStatus.ALL:
            raise AgentError("invalid_task_status", "UNDX produced an unknown task status.")

    @property
    def is_resumable(self) -> bool:
        return self.status not in TaskStatus.CLOSED


@dataclass
class NativeCard:
    """One structured payload the native client renders instead of raw text."""

    component: str
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.component not in CardType.ALL:
            raise AgentError("invalid_card_type", "UNDX produced an unknown result card.")

    def to_dict(self) -> dict[str, Any]:
        return {"component": self.component, **self.payload}


@dataclass
class AgentResponse:
    """What the runtime hands back to the transport layer for one turn."""

    outcome: str
    text: str
    cards: list[NativeCard] = field(default_factory=list)
    receipt: AgentReceipt | None = None
    task_id: str = ""
    confirmation: ConfirmationRequest | None = None
    error: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "text": self.text,
            "response_components": [card.to_dict() for card in self.cards],
            "task_id": self.task_id,
            "receipt": self.receipt.to_dict() if self.receipt else None,
            "confirmation": asdict(self.confirmation) if self.confirmation else None,
            "error": self.error,
        }


__all__ = [
    "AgentOutcome", "RiskLevel", "PermissionScope", "ConfirmationPolicy", "RunConfirmation",
    "VerificationState",
    "TaskStatus", "CardType", "AgentError", "FieldSpec", "validate_arguments",
    "AgentRequest", "ToolCall", "ToolResult", "VerificationResult",
    "ConfirmationRequest", "AgentReceipt", "AgentTask", "NativeCard",
    "AgentResponse", "now_iso", "clean", "canonical_hash", "new_id",
    "describe_alert", "format_amount",
    "MAX_PLAN_STEPS", "MAX_TOOL_CALLS", "MAX_RETRIES", "MAX_EXECUTION_SECONDS",
    "CONFIRMATION_TTL_SECONDS",
]
