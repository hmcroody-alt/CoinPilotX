"""The agent runtime: turn one authenticated request into one governed outcome.

This module owns the sequence — understand, resolve, plan, execute, report — but it
owns no authority. Every decision that matters is delegated: the registry decides
what exists, :mod:`services.undx_agent_policy` decides what is permitted, and
:mod:`services.undx_tool_gateway` decides what actually runs. The runtime's job is
to arrange those calls correctly and to tell the truth about what came back.

Two design choices are worth stating plainly, because they are what make an LLM
safe to put in front of this.

**A proposal is just an argument.** :func:`handle` accepts a capability id and a
dictionary. It does not care whether a language model, a deterministic matcher, or
a unit test produced them. Nothing about being "chosen by the model" grants a
proposal any standing, so prompt injection can at worst cause a *different
authorised action* to be proposed — and that proposal still faces the registry, the
policy engine, the ownership filter and the confirmation card. This is why the
built-in matcher below is allowed to be simple: it is a convenience, not a control.

**Reference resolution is a first-class step, and it is allowed to refuse.**
"pause my Bitcoin alert" is only actionable if exactly one alert matches. Two
matches is not a coin flip to be resolved by picking the first row; it is a
question to ask. :func:`resolve_reference` returns the count, and the gateway
refuses ambiguity on the user's behalf.

The runtime never claims an action succeeded on the strength of the tool saying so.
That judgement belongs to the verification engine, arrives through the receipt, and
is reproduced here verbatim.
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any

from services import undx_agent_policy as policy
from services import undx_agent_tools, undx_tool_gateway
from services.undx_agent_contracts import (
    MAX_TEXT_CHARS,
    AgentError,
    AgentOutcome,
    AgentReceipt,
    CardType,
    RiskLevel,
    VerificationState,
    clean,
    new_id,
)
from services.undx_capability_registry import REGISTRY, CapabilitySpec, get, require

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Intent matching
# ---------------------------------------------------------------------------


#: Phrases that make an instruction *explicit* rather than exploratory. The
#: distinction decides whether a CONTEXTUAL capability may run without a card, so
#: these are matched against the user's own message only — never against retrieved
#: content, tool output, or anything else the user did not type.
_EXPLICIT_MARKERS = (
    "pause ", "resume ", "unpause ", "turn off ", "turn on ", "stop ", "start ",
    "delete ", "remove ", "mute ", "unmute ", "disable ", "enable ",
)

_HEDGES = ("should i", "can you", "could you", "what if", "maybe", "how do i",
           "is it possible", "what happens if", "would it")


def is_explicit(text: str) -> bool:
    """Whether the user issued an instruction rather than explored an option.

    A hedge anywhere in the message disqualifies it. "Should I pause my alert?"
    contains "pause " and is not an instruction, and treating it as one would mean
    acting on a question — the single most annoying way for an agent to be wrong.
    """
    lowered = clean(text, MAX_TEXT_CHARS).lower()
    if any(hedge in lowered for hedge in _HEDGES):
        return False
    return any(marker in lowered for marker in _EXPLICIT_MARKERS)


def _tokens(text: str) -> list[str]:
    """Words, lowercased, with a naive plural stripped.

    Crude on purpose. "alerts" and "alert" must be the same token or every intent
    phrase would need both spellings, and a registry that has to enumerate English
    morphology is a registry nobody will keep correct.
    """
    words = re.findall(r"[a-z0-9']+", clean(text, MAX_TEXT_CHARS).lower())
    return [word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word
            for word in words]


def _subsequence_score(phrase_tokens: list[str], message_tokens: list[str]) -> int:
    """How well an intent phrase matches, as matched characters, or 0 for no match.

    The phrase's words must all appear, in order, but need not be adjacent — which
    is what lets "pause alert" match "pause my Bitcoin alert". Requiring adjacency
    was the original mistake: real instructions almost always have a possessive or a
    qualifier wedged in the middle, so a contiguous matcher recognises the phrasing
    found in test fixtures and almost nothing a person actually types.

    Scoring by matched characters rather than word count means the more specific
    phrase wins a tie: "delete alert" outranks "alert" for the same message.
    """
    position = 0
    for token in phrase_tokens:
        try:
            position = message_tokens.index(token, position) + 1
        except ValueError:
            return 0
    return sum(len(token) for token in phrase_tokens)


def match_capability(text: str) -> CapabilitySpec | None:
    """Deterministic best-effort capability match, used when no planner supplied one.

    Returning ``None`` is a perfectly good answer, and the common one: the caller
    then falls back to a conversational reply rather than guessing at an action.
    A matcher that always finds something is a matcher that acts on small talk.
    """
    message_tokens = _tokens(text)
    if not message_tokens:
        return None
    best: tuple[int, str, CapabilitySpec] | None = None
    for spec in REGISTRY.values():
        for phrase in spec.intents:
            score = _subsequence_score(_tokens(phrase), message_tokens)
            # Ties broken by capability id so the choice is stable across runs rather
            # than dependent on dictionary ordering.
            if score and (best is None or (score, spec.capability_id) > (best[0], best[1])):
                best = (score, spec.capability_id, spec)
    return best[2] if best else None


# ---------------------------------------------------------------------------
# Reference resolution
# ---------------------------------------------------------------------------


def _read_permitted(user_id: int, capability_id: str) -> bool:
    """Whether this module may perform a supporting read on its own.

    Two paths here — reference resolution and the confirmation preview — call
    executors directly rather than through the gateway, because neither is an action
    the user asked for and neither should produce a receipt, an audit row or a ledger
    entry. That shortcut skipped the flags. An operator who disabled
    ``crypto.alerts.get``, or switched reads off during an incident, still had its
    data read and rendered into a confirmation card, which makes those switches mean
    less than their names promise.

    The gate is ``policy.evaluate`` rather than a hand-rolled flag check, so there is
    one definition of "may this capability run" and this path cannot drift from the
    gateway's. For a read-only spec that evaluation depends on nothing but the flags,
    the cohort and the capability id, which is why passing no arguments is sound.

    Failing closed here is cheap: resolution reports that it could not read, and the
    preview renders a confirmation with an unknown current value. Neither invents one.
    """
    spec = get(capability_id)
    if spec is None or spec.is_write:
        return False
    return not policy.evaluate(int(user_id), spec, {}).denied


#: How many alerts reference resolution will compare before declining to. Matches the
#: maximum ``crypto.alerts.list`` accepts, so this reads the largest window the
#: capability permits rather than the executor's conversational default of 20.
_MAX_REFERENCE_SCAN = 50

_SYMBOL_ALIASES = {
    "bitcoin": "BTC", "btc": "BTC", "ethereum": "ETH", "eth": "ETH",
    "solana": "SOL", "sol": "SOL", "cardano": "ADA", "ada": "ADA",
    "dogecoin": "DOGE", "doge": "DOGE", "ripple": "XRP", "xrp": "XRP",
}


class Reference:
    """The outcome of turning a phrase like "my Bitcoin alert" into an id.

    ``count`` is deliberately reported even when it is zero or greater than one,
    because those are the cases the caller must not paper over.
    """

    __slots__ = ("count", "resource_id", "candidates", "detail")

    def __init__(self, count: int, resource_id: int = 0,
                 candidates: list[dict[str, Any]] | None = None, detail: str = "") -> None:
        self.count = int(count)
        self.resource_id = int(resource_id)
        self.candidates = candidates or []
        self.detail = detail

    @property
    def unique(self) -> bool:
        return self.count == 1 and self.resource_id > 0


def resolve_alert_reference(user_id: int, text: str, *, explicit_id: Any = None) -> Reference:
    """Find the single alert a message refers to, or report that there isn't one.

    An id supplied directly is still checked for ownership by reading it back
    through the owner-scoped service call, so a caller cannot smuggle in another
    account's row by naming it precisely.
    """
    if not _read_permitted(user_id, "crypto.alerts.get"):
        return Reference(0, detail="Your alerts could not be read just now.")

    if explicit_id:
        result = undx_agent_tools.crypto_alerts_get(int(user_id), {"alert_id": int(explicit_id)})
        if result.ok:
            return Reference(1, int(explicit_id))
        return Reference(0, detail="That alert is not on your account.")

    # The largest page the capability permits, not the executor's default. Resolution
    # asks "is there exactly one?", which is a question about the whole account; asking
    # it of a 20-row window silently redefines it as "exactly one on page one".
    listing = undx_agent_tools.crypto_alerts_list(int(user_id), {"limit": _MAX_REFERENCE_SCAN})
    if not listing.ok:
        return Reference(0, detail="Your alerts could not be read just now.")
    if (listing.data or {}).get("truncated"):
        # More alerts exist than were read, so uniqueness cannot be established — there
        # may be a second match just past the edge of the page. Refusing here costs one
        # clarifying question; guessing costs the user a change to the wrong alert, made
        # under a confirmation card that named a different one as the only candidate.
        return Reference(
            2, 0, [],
            detail="You have more alerts than UNDX can compare at once. Open your alerts and tell me which one.",
        )
    # Only live alerts are candidates. Matching a deleted alert and then refusing to
    # act on it would report "ambiguous" for a set the user considers to have one item.
    alerts = [item for item in listing.records if str(item.get("status") or "") != "deleted"]

    lowered = clean(text, MAX_TEXT_CHARS).lower()
    wanted = {code for word, code in _SYMBOL_ALIASES.items() if re.search(rf"\b{word}\b", lowered)}
    for item in alerts:
        symbol = str(item.get("symbol") or "").upper()
        if symbol and re.search(rf"\b{re.escape(symbol.lower())}\b", lowered):
            wanted.add(symbol)
    if wanted:
        alerts = [item for item in alerts if str(item.get("symbol") or "").upper() in wanted]

    if len(alerts) == 1:
        return Reference(1, int(alerts[0].get("alert_id") or 0), alerts)
    return Reference(
        len(alerts), 0, alerts,
        detail=("You do not have an alert matching that." if not alerts
                else "More than one of your alerts matches that description."),
    )


def resolve_notification_arguments(text: str, arguments: dict[str, Any]) -> dict[str, Any] | None:
    """Derive ``category`` and ``push`` from a sentence, or report that they aren't there.

    This delegates to ``undx_architecture.notification_action_from_text`` rather than
    reimplementing the parse. That function is what the shipped V4/V5 path uses, and
    while both paths exist they must read an identical sentence identically — a second
    parser would eventually disagree with the first, and the disagreement would show up
    as the agent changing a different setting than the one the user was shown.

    ``None`` means the direction was not stated. "Change my notifications" names no
    value to change it to, and guessing the opposite of the current setting would be
    inventing an instruction rather than following one.
    """
    if "push" in arguments and arguments.get("category"):
        return dict(arguments)
    from services import undx_architecture

    parsed = undx_architecture.notification_action_from_text(text)
    if not parsed:
        return None
    derived = dict(parsed.get("arguments") or {})
    if "category" not in derived or "push" not in derived:
        return None
    # An explicitly supplied value still wins: a planner that names the category is
    # more specific than a phrase match, and the gateway validates either way.
    derived.update({key: value for key, value in arguments.items() if value is not None})
    return derived


def resolve_saved_arguments(text: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Narrow Saved-library reads from the user's own product wording."""
    resolved = dict(arguments)
    if clean(resolved.get("content_type"), 40) not in {"", "all"}:
        return resolved
    lowered = clean(text, MAX_TEXT_CHARS).lower()
    types = (
        ("reel", ("reel", "reels")),
        ("post", ("post", "posts")),
        ("status", ("status", "statuses")),
        ("marketplace", ("listing", "listings", "marketplace")),
        ("video", ("video", "videos")),
    )
    for content_type, words in types:
        if any(re.search(rf"\b{word}\b", lowered) for word in words):
            resolved["content_type"] = content_type
            break
    resolved.setdefault("content_type", "all")
    return resolved


def resolve_relationship_arguments(text: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Distinguish inbound followers from accounts the caller follows."""
    resolved = dict(arguments)
    if clean(resolved.get("direction"), 20) in {"followers", "following"}:
        return resolved
    lowered = clean(text, MAX_TEXT_CHARS).lower()
    resolved["direction"] = (
        "following"
        if any(phrase in lowered for phrase in ("am i following", "i follow", "my following"))
        else "followers"
    )
    return resolved


def resolve_saved_post_write_arguments(text: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Extract an explicit post id and desired state; never infer a toggle."""
    resolved = dict(arguments)
    lowered = clean(text, MAX_TEXT_CHARS).lower()
    if not resolved.get("post_id"):
        match = re.search(r"\bpost(?:\s+#?|\s+number\s+)(\d+)\b", lowered)
        if match:
            resolved["post_id"] = int(match.group(1))
    if "saved" not in resolved:
        removing = any(
            phrase in lowered
            for phrase in ("unsave", "remove from saved", "remove post from saved")
        )
        resolved["saved"] = not removing
    return resolved


def resolve_user_target_arguments(text: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """Extract an explicit numeric QA-safe user target; names require disambiguation."""
    resolved = dict(arguments)
    if not resolved.get("target_user_id"):
        match = re.search(r"\b(?:user|account|member)\s+#?(\d+)\b", clean(text, MAX_TEXT_CHARS).lower())
        if match:
            resolved["target_user_id"] = int(match.group(1))
    return resolved


# ---------------------------------------------------------------------------
# Native result cards
# ---------------------------------------------------------------------------


def _card_type(spec: CapabilitySpec, status: str) -> str:
    """Choose the card by outcome first, capability second.

    A failed setting change is a failure card, not a settings receipt. Rendering a
    receipt-shaped card for something that did not happen is how an interface ends
    up implying success that the data does not support.
    """
    if status == AgentOutcome.CONFIRMATION_REQUIRED:
        return CardType.ACTION_CONFIRMATION
    if status == AgentOutcome.PERMISSION_DENIED:
        return CardType.PERMISSION_DENIED
    if status == AgentOutcome.UNSUPPORTED_CAPABILITY:
        return CardType.UNSUPPORTED_CAPABILITY
    if status == AgentOutcome.RECOVERABLE_FAILURE:
        return CardType.RETRY_ACTION
    if status == AgentOutcome.TERMINAL_FAILURE:
        return CardType.ACTION_FAILURE
    return spec.result_card


def build_card(spec: CapabilitySpec, outcome: undx_tool_gateway.GatewayOutcome) -> dict[str, Any]:
    """The structured payload the native client renders.

    Contains no prose the client must parse and no HTML. Everything the UI needs to
    decide what to draw — the outcome, the verification state, whether an undo
    exists — is a field, so a client can render this correctly without inspecting
    the human-readable sentence that accompanies it.
    """
    receipt = outcome.receipt
    card: dict[str, Any] = {
        "component": _card_type(spec, receipt.status),
        "capability_id": spec.capability_id,
        "status": receipt.status,
        "verification_state": receipt.verification_state,
        "verified": receipt.verification_state == VerificationState.VERIFIED,
        "title": spec.description,
        "message": receipt.user_explanation,
        "risk": spec.risk,
        "deep_link": receipt.native_deep_link,
        "canonical_resource_ids": list(receipt.canonical_resource_ids),
        "task_id": receipt.task_id,
        "undo_capability_id": receipt.undo_capability_id,
        # The client needs both halves to offer Undo. Sending the capability id alone
        # would leave the app to guess the arguments, and the obvious guess — replay
        # what was just sent — is the one that re-applies a preference change instead
        # of reversing it. The gateway clears both together, so `can_undo` reading only
        # the id is not a shortcut: an id without arguments cannot occur.
        "undo_arguments": dict(receipt.undo_arguments),
        "can_undo": bool(receipt.undo_capability_id),
        "timestamp": receipt.timestamp,
    }
    if outcome.confirmation is not None:
        grant = outcome.confirmation
        card.update({
            "confirmation_id": grant.confirmation_id,
            "confirmation_token": grant.confirmation_token,
            "expires_at": grant.expires_at,
            "action_name": grant.action_name,
            "target": grant.target,
            "current_value": grant.current_value,
            "proposed_value": grant.proposed_value,
            "risk_summary": grant.risk_summary,
        })
    if outcome.result is not None:
        if outcome.result.records:
            card["records"] = outcome.result.records
            card["record_count"] = len(outcome.result.records)
        if outcome.result.data:
            card["data"] = outcome.result.data
        if outcome.result.idempotent_replay:
            card["idempotent_replay"] = True
        if outcome.result.degraded_sources:
            # A field rather than prose, so the client can visibly mark the answer
            # partial. Without it a degraded read renders identically to a complete
            # one and an empty section reads as "there is nothing here".
            card["complete"] = False
            card["degraded_sources"] = list(outcome.result.degraded_sources)
    if outcome.verification is not None and outcome.verification.state != VerificationState.VERIFIED:
        # Surfaced so the client can visibly downgrade its own confidence rather than
        # rendering an unverified change identically to a confirmed one.
        card["verification_detail"] = outcome.verification.detail
    return card


# ---------------------------------------------------------------------------
# The runtime entry point
# ---------------------------------------------------------------------------


class AgentResponse:
    """One complete agent turn: what happened, the card, and what to say."""

    __slots__ = ("handled", "receipt", "card", "reply", "capability_id", "latency_ms")

    def __init__(self, *, handled: bool, receipt: AgentReceipt | None = None,
                 card: dict[str, Any] | None = None, reply: str = "",
                 capability_id: str = "", latency_ms: int = 0) -> None:
        self.handled = handled
        self.receipt = receipt
        self.card = card
        self.reply = reply
        self.capability_id = capability_id
        self.latency_ms = latency_ms

    def __bool__(self) -> bool:
        """An unhandled turn is falsy, so ``handle(...) or None`` falls through.

        Without this a caller writing the natural ``if response:`` would treat "this
        was ordinary conversation" as "the agent answered", and reply to the user with
        an empty string. Truthiness tracks ``handled`` and nothing else — a handled
        refusal is still a real answer and must stay truthy.
        """
        return bool(self.handled)

    @property
    def status(self) -> str:
        return self.receipt.status if self.receipt else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "handled": self.handled,
            "capability_id": self.capability_id,
            "status": self.status,
            "reply": self.reply,
            "card": self.card,
            "latency_ms": self.latency_ms,
            "receipt": (self.receipt.to_dict() if self.receipt and hasattr(self.receipt, "to_dict")
                        else None),
        }


#: What each write capability will leave the resource in, for the confirmation card.
_PROPOSED_STATE = {
    "crypto.alerts.pause": "paused",
    "crypto.alerts.resume": "active",
    "crypto.alerts.delete": "deleted",
}


def preview(user_id: int, spec: CapabilitySpec, arguments: dict[str, Any]) -> tuple[Any, Any]:
    """Read the current value so the confirmation card can show before and after.

    A card that says "confirm this change" without naming the change is not consent,
    it is a habituation exercise — the user learns to press the button without
    reading it. So the current value is fetched with a real read, not inferred from
    what the agent believes it set earlier.

    This is strictly read-only and best-effort. If the read fails the card still
    appears with an unknown current value, because failing to render a confirmation
    is worse than rendering one with a gap in it: the alternative is either acting
    unconfirmed or refusing an action the user is entitled to take.
    """
    try:
        if (spec.capability_id.startswith("crypto.alerts.") and arguments.get("alert_id")
                and _read_permitted(user_id, "crypto.alerts.get")):
            result = undx_agent_tools.crypto_alerts_get(int(user_id), {"alert_id": int(arguments["alert_id"])})
            alert = result.data or {}
            if spec.capability_id == "crypto.alerts.update":
                return alert.get("threshold"), arguments.get("threshold")
            return alert.get("status"), _PROPOSED_STATE.get(spec.capability_id)
        if (spec.capability_id == "notifications.preference.update"
                and _read_permitted(user_id, "notifications.preference.read")):
            category = clean(arguments.get("category") or "global", 40)
            result = undx_agent_tools.notification_preferences_read(int(user_id), {"category": category})
            return (result.data or {}).get("push"), bool(arguments.get("push"))
        if spec.capability_id == "saved.post.set" and arguments.get("post_id"):
            from services.saved_content_service import get_post_saved

            state = get_post_saved(int(user_id), int(arguments["post_id"])) or {}
            return state.get("saved"), bool(arguments.get("saved"))
        if spec.capability_id in {"social.follow", "social.unfollow"} and arguments.get("target_user_id"):
            from services.social_relationship_service import is_following

            before = is_following(int(user_id), int(arguments["target_user_id"]))
            return before, spec.capability_id == "social.follow"
        if spec.capability_id in {"feed.posts.like", "feed.posts.unlike"} and arguments.get("post_id"):
            from services.feed_intelligence_service import get_post_like

            before = get_post_like(int(user_id), int(arguments["post_id"]))
            return before, spec.capability_id == "feed.posts.like"
    except Exception:  # pragma: no cover - a preview must never block the action
        return None, None
    return None, None


def _bare_receipt(spec: CapabilitySpec, *, user_id: int, request_id: str, status: str,
                  explanation: str, evidence: dict[str, Any] | None = None) -> AgentReceipt:
    """A receipt for an outcome that never reached the gateway.

    Refusals get the same shape as successes so no caller can mistake a refusal for
    an absence of one — and so the client renders every outcome through one path.
    """
    return AgentReceipt(
        task_id=request_id, request_id=request_id, capability_id=spec.capability_id,
        action=spec.description, status=status, owner_user_id=int(user_id),
        verification_state=VerificationState.IMPOSSIBLE,
        evidence=dict(evidence or {}), native_deep_link=spec.deep_link({}),
        user_explanation=clean(explanation, 400), risk_level=spec.risk,
    )


def _unresolved_response(spec: CapabilitySpec, reference: Reference, request_id: str,
                         user_id: int, started: float) -> AgentResponse:
    """Ask which one, instead of picking one.

    The candidate list travels with the card so the client can render a chooser and
    the next turn arrives with an explicit id. Nothing has been mutated at this
    point, which is the property that makes asking cheap and guessing expensive.
    """
    receipt = _bare_receipt(
        spec, user_id=user_id, request_id=request_id,
        status=AgentOutcome.TERMINAL_FAILURE,
        explanation=(reference.detail or "UNDX needs to know which one you mean."),
        evidence={"resolved_matches": reference.count},
    )
    card = {
        "component": CardType.ACTION_FAILURE if not reference.candidates else spec.result_card,
        "capability_id": spec.capability_id,
        "status": receipt.status,
        "verification_state": receipt.verification_state,
        "verified": False,
        "title": spec.description,
        "message": receipt.user_explanation,
        "risk": spec.risk,
        "deep_link": receipt.native_deep_link,
        "task_id": request_id,
        "needs_disambiguation": reference.count > 1,
        "candidates": reference.candidates,
        "record_count": len(reference.candidates),
        "timestamp": receipt.timestamp,
    }
    return AgentResponse(handled=True, receipt=receipt, card=card, reply=receipt.user_explanation,
                         capability_id=spec.capability_id,
                         latency_ms=int((time.monotonic() - started) * 1000))


def _error_response(spec: CapabilitySpec, exc: AgentError, request_id: str,
                    user_id: int, started: float) -> AgentResponse:
    status = getattr(exc, "outcome", "") or AgentOutcome.TERMINAL_FAILURE
    receipt = _bare_receipt(
        spec, user_id=user_id, request_id=request_id, status=status,
        explanation=str(exc), evidence={"code": getattr(exc, "code", "")},
    )
    return AgentResponse(
        handled=True, receipt=receipt,
        card={
            "component": _card_type(spec, status),
            "capability_id": spec.capability_id,
            "status": status,
            "verification_state": receipt.verification_state,
            "verified": False,
            "title": spec.description,
            "message": receipt.user_explanation,
            "risk": spec.risk,
            "deep_link": receipt.native_deep_link,
            "task_id": request_id,
            "timestamp": receipt.timestamp,
        },
        reply=receipt.user_explanation, capability_id=spec.capability_id,
        latency_ms=int((time.monotonic() - started) * 1000),
    )


def available(user_id: int) -> bool:
    """Whether the agent should be consulted for this account at all.

    Checked before any work so that a disabled agent costs nothing and, more
    importantly, so that conversation continues to function normally when the agent
    is switched off. The agent is an enhancement to UNDX, not a prerequisite for it.
    """
    return policy.user_enabled(user_id)


def handle(
    cur,
    *,
    user_id: int,
    text: str,
    request_id: str = "",
    conversation_id: int = 0,
    capability_id: str = "",
    arguments: dict[str, Any] | None = None,
    confirmation_token: str = "",
    client_request_id: str = "",
    correlation_id: str = "",
) -> AgentResponse:
    """Run one agent turn.

    ``capability_id`` and ``arguments`` are the planner's proposal. When they are
    absent the built-in matcher tries to derive them from ``text``; when they are
    present the text is still used for reference resolution and for judging whether
    the instruction was explicit. Neither route confers any privilege — both end up
    at the same gateway call.
    """
    started = time.monotonic()
    request_id = clean(request_id or new_id("undx_req"), 120)
    text = clean(text, MAX_TEXT_CHARS)
    arguments = dict(arguments or {})

    if int(user_id or 0) <= 0:
        raise AgentError("unauthenticated", "Sign in to let UNDX do that.",
                         outcome=AgentOutcome.PERMISSION_DENIED)

    if not available(int(user_id)):
        return AgentResponse(handled=False, reply="", latency_ms=int((time.monotonic() - started) * 1000))

    if capability_id:
        # An explicitly named capability that does not exist is a refusal, not a
        # conversation. Falling through would let a planner — or an attacker who has
        # shaped one — probe for capability names and receive a chatty answer instead
        # of a typed ``unsupported_capability``. ``require`` raises that outcome.
        spec = require(capability_id)
    else:
        spec = match_capability(text)
        if spec is None:
            # Nothing actionable was recognised. Not a failure: the normal case for the
            # overwhelming majority of messages, which want a conversation.
            return AgentResponse(handled=False, latency_ms=int((time.monotonic() - started) * 1000))

    # Resolve which resource the request is about. A capability that names an
    # ``alert_id`` field needs exactly one, and the count travels to the gateway so
    # that ambiguity is refused there rather than guessed at here.
    resolved_count = 1
    if any(item.name == "alert_id" for item in spec.fields):
        reference = resolve_alert_reference(int(user_id), text, explicit_id=arguments.get("alert_id"))
        resolved_count = reference.count
        if reference.unique:
            arguments["alert_id"] = reference.resource_id
        else:
            # Answered here rather than passed on. The gateway also refuses ambiguity,
            # but only after schema validation, which would reject the still-missing id
            # with a message about a required field — technically true and useless to
            # the person, who asked a reasonable question about a real alert. Resolving
            # references is the runtime's job, so explaining a failed resolution is too.
            return _unresolved_response(spec, reference, request_id, int(user_id), started)

    if any(item.name == "push" for item in spec.fields):
        derived = resolve_notification_arguments(text, arguments)
        if derived is None:
            # The sentence named a setting but not a direction. Answered here, for the
            # same reason ambiguous alert references are: the gateway would reject this
            # as a missing required field, which is true and unhelpful.
            return _unresolved_response(
                spec,
                Reference(0, detail="Tell UNDX whether to turn that on or off."),
                request_id, int(user_id), started)
        arguments = derived
    if spec.capability_id == "saved.items.list":
        arguments = resolve_saved_arguments(text, arguments)
    if spec.capability_id == "saved.post.set":
        arguments = resolve_saved_post_write_arguments(text, arguments)
    if spec.capability_id == "social.followers.list":
        arguments = resolve_relationship_arguments(text, arguments)
    if spec.capability_id in {"social.follow", "social.unfollow"}:
        arguments = resolve_user_target_arguments(text, arguments)
    if spec.capability_id in {
        "messages.list", "messages.search", "conversations.summarize",
        "messages.suggest", "messages.draft",
    } and not arguments.get("conversation_id"):
        match = re.search(r"\bconversation\s*(?:id\s*)?#?\s*(\d+)\b", text, re.IGNORECASE)
        if match:
            arguments["conversation_id"] = int(match.group(1))
    if spec.capability_id == "messages.search" and not arguments.get("query"):
        match = re.search(r"(?:where|for)\s+(.+?)(?:\s+in\s+conversation\s+\d+)?[.!?]?$", text, re.IGNORECASE)
        if match:
            arguments["query"] = clean(match.group(1), 120)
    if spec.capability_id == "messages.draft" and not arguments.get("body"):
        match = re.search(r"(?:saying|say|body)\s+(.+?)(?:\s+to\s+conversation\s+\d+)?[.!?]?$", text, re.IGNORECASE)
        if match:
            arguments["body"] = clean(match.group(1), 2000)
    if spec.capability_id in {
        "feed.posts.get", "comments.list", "feed.posts.like", "feed.posts.unlike",
        "feed.post.performance.summary", "feed.comments.summary",
    } and not arguments.get("post_id"):
        match = re.search(r"\bpost\s*(?:id\s*)?#?\s*(\d+)\b", text, re.IGNORECASE)
        if match:
            arguments["post_id"] = int(match.group(1))
    if spec.capability_id.startswith("reels.") and spec.capability_id != "reels.search" and not arguments.get("reel_id"):
        match = re.search(r"\breel\s*(?:id\s*)?#?\s*(\d+)\b", text, re.IGNORECASE)
        if match:
            arguments["reel_id"] = int(match.group(1))
    if spec.capability_id.startswith("status.") and spec.capability_id != "status.list" and not arguments.get("status_id"):
        match = re.search(r"\bstatus\s*(?:id\s*)?#?\s*(\d+)\b", text, re.IGNORECASE)
        if match:
            arguments["status_id"] = int(match.group(1))
    if spec.capability_id == "profile.preferences.update" and not arguments.get("preferred_language"):
        lowered = text.lower()
        for label, code in (("english", "en"), ("spanish", "es"), ("french", "fr")):
            if label in lowered:
                arguments["preferred_language"] = code
                break
    if spec.capability_id == "feed.posts.list":
        lowered = text.lower()
        if "my latest" in lowered or "my posts" in lowered:
            arguments["feed"] = "my_posts"
        elif "trending" in lowered:
            arguments["feed"] = "trending"
        elif "following" in lowered:
            arguments["feed"] = "following"
    if any(item.name == "query" for item in spec.fields) and not arguments.get("query"):
        # Search phrases are filters, never authority. Keep the extraction bounded
        # and let the owner-scoped domain service decide which records are visible.
        match = re.search(
            r"(?:about|for|named)\s+(.+?)[.!?]?$",
            text, re.IGNORECASE,
        )
        if match:
            arguments["query"] = clean(match.group(1), 120)
    for field_name, noun in (
        ("notification_id", "notification"),
        ("listing_id", "listing"),
        ("order_id", "order"),
        ("live_id", "live"),
    ):
        if any(item.name == field_name for item in spec.fields) and not arguments.get(field_name):
            match = re.search(rf"\b{noun}\s*(?:id\s*)?#?\s*(\d+)\b", text, re.IGNORECASE)
            if match:
                arguments[field_name] = int(match.group(1))
    if spec.capability_id == "settings.explain" and not arguments.get("section"):
        lowered = text.lower()
        arguments["section"] = next(
            (section for section in ("privacy", "notifications", "language", "accessibility")
             if section in lowered),
            "all",
        )

    # Read the before/after pair now, while nothing has changed, so a confirmation
    # card can state plainly what it is asking permission for.
    current_value, proposed_value = preview(int(user_id), spec, arguments) if spec.is_write else (None, None)

    try:
        outcome = undx_tool_gateway.execute(
            cur,
            user_id=int(user_id),
            capability_id=spec.capability_id,
            proposed_arguments=arguments,
            current_value=current_value,
            proposed_value=proposed_value,
            request_id=request_id,
            task_id=request_id,
            client_request_id=client_request_id,
            correlation_id=correlation_id,
            confirmation_token=confirmation_token,
            explicit_request=is_explicit(text),
            resolved_resource_count=resolved_count,
        )
    except AgentError as exc:
        # A typed refusal from validation or the registry. It is already a canonical
        # outcome; it should reach the user as one rather than as a 500. Every raising
        # path inside ``execute`` lies *before* the executor runs — the gateway settles
        # its own tail — so arriving here always means nothing was changed.
        return _error_response(spec, exc, request_id, int(user_id), started)

    # Past this line the action may have really happened, so the turn must be reported
    # as handled no matter what. Card construction is presentation: a defect in it is a
    # missing card, not a reason to hand the turn back to a language model that would
    # then answer as though nothing had occurred.
    try:
        card = build_card(spec, outcome)
    except Exception as exc:  # pragma: no cover - defensive
        logger.critical("undx_card_build_failed capability=%s user=%s error=%s",
                        spec.capability_id, int(user_id), exc.__class__.__name__)
        card = None
    return AgentResponse(
        handled=True,
        receipt=outcome.receipt,
        card=card,
        reply=outcome.receipt.user_explanation,
        capability_id=spec.capability_id,
        latency_ms=int((time.monotonic() - started) * 1000),
    )


__all__ = [
    "AgentResponse", "Reference", "available", "handle", "build_card",
    "match_capability", "is_explicit", "resolve_alert_reference",
    "resolve_notification_arguments",
    "resolve_saved_arguments",
    "resolve_relationship_arguments",
    "resolve_saved_post_write_arguments",
]
