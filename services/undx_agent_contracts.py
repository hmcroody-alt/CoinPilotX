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
    CONFIRMATION_REQUIRED = "confirmation_required"
    PERMISSION_DENIED = "permission_denied"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    RECOVERABLE_FAILURE = "recoverable_failure"
    TERMINAL_FAILURE = "terminal_failure"

    ALL = frozenset({
        VERIFIED_SUCCESS,
        ACCEPTED_UNVERIFIED,
        CONFIRMATION_REQUIRED,
        PERMISSION_DENIED,
        UNSUPPORTED_CAPABILITY,
        RECOVERABLE_FAILURE,
        TERMINAL_FAILURE,
    })

    #: Outcomes a user may be told represent a completed change.
    COMPLETED = frozenset({VERIFIED_SUCCESS})


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

    ALL = frozenset({
        SEARCH_RESULTS, PROFILE_RESULT, CONTENT_RESULT, CONVERSATION_RESULT,
        MESSAGE_DRAFT_CONFIRMATION, ACTION_CONFIRMATION, ACTION_PROGRESS,
        ACTION_SUCCESS_RECEIPT, ACTION_FAILURE, SETTING_CHANGE_RECEIPT,
        CRYPTO_ALERT_CARD, RELATIONSHIP_CHANGE_RECEIPT, UNSUPPORTED_CAPABILITY,
        PERMISSION_DENIED, RETRY_ACTION,
    })


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
    "AgentOutcome", "RiskLevel", "PermissionScope", "ConfirmationPolicy", "VerificationState",
    "TaskStatus", "CardType", "AgentError", "FieldSpec", "validate_arguments",
    "AgentRequest", "ToolCall", "ToolResult", "VerificationResult",
    "ConfirmationRequest", "AgentReceipt", "AgentTask", "NativeCard",
    "AgentResponse", "now_iso", "clean", "canonical_hash", "new_id",
    "MAX_PLAN_STEPS", "MAX_TOOL_CALLS", "MAX_RETRIES", "MAX_EXECUTION_SECONDS",
    "CONFIRMATION_TTL_SECONDS",
]
