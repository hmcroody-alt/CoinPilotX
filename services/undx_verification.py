"""Independent read-back verification for every write capability.

A mutation's own response is the *claim*. This module produces the *evidence*. The
distinction matters because almost every way an agent lies to a user routes through
trusting the former: a service returns ``{"ok": true}`` while a filter silently
matched zero rows, a write lands on a replica that has not caught up, or a
capability updates an adjacent field and reports success for the one it was asked
about. In each case the mutation response is honest about what the code did and
wrong about what the user asked for.

So every verifier here re-reads state through a *separate* call and compares it to
what was requested. Verifiers never look at the tool's return value to decide
whether it worked; they take the requested arguments and go and look.

The four states are not interchangeable:

``verified``
    We read the state back and it matches. Only this permits "done".
``verification_failed``
    We read the state back and it did **not** match. Loud: something is wrong.
``verification_pending``
    We could not read it back right now, but a later read might succeed.
``impossible_to_verify``
    There is no read path for this property at all. Never a transient condition.
"""

from __future__ import annotations

from typing import Any, Callable

from services.undx_agent_contracts import (
    ToolResult,
    VerificationResult,
    VerificationState,
    clean,
)


def _alert_engine():
    from services import alert_engine

    return alert_engine


def _notifications():
    from services import pulsesoc_notification_system

    return pulsesoc_notification_system


def _unreadable(detail: str, expected: Any = None) -> VerificationResult:
    """A read-back that could not be performed, as distinct from one that failed."""
    return VerificationResult(
        state=VerificationState.PENDING,
        expected=expected,
        detail=clean(detail, 200),
    )


# ---------------------------------------------------------------------------
# Crypto alerts
# ---------------------------------------------------------------------------


#: Which persisted status each capability is expected to produce.
_EXPECTED_STATUS = {
    "crypto.alerts.pause": "paused",
    "crypto.alerts.resume": "active",
}


def crypto_alert_status(user_id: int, arguments: dict[str, Any], result: ToolResult) -> VerificationResult:
    """Confirm the alert really holds the status the capability requested."""
    expected = _EXPECTED_STATUS.get(result.capability_id, "")
    if not expected:
        return VerificationResult(
            state=VerificationState.IMPOSSIBLE,
            detail="No expected status is declared for this capability.",
        )
    alert_id = int(arguments.get("alert_id") or 0)
    try:
        rule = _alert_engine().get_alert_rule(alert_id, int(user_id))
    except Exception as exc:  # pragma: no cover - defensive
        return _unreadable(f"Alert read-back failed: {exc.__class__.__name__}", expected)
    if not rule:
        # The row is gone or was never ours. Either way the requested state is
        # demonstrably absent, which is a failure and not a pending read.
        return VerificationResult(
            state=VerificationState.FAILED,
            expected=expected,
            observed=None,
            detail="The alert could not be read back on this account.",
        )
    observed = clean(rule.get("status") or "active", 24)
    return VerificationResult(
        state=VerificationState.VERIFIED if observed == expected else VerificationState.FAILED,
        expected=expected,
        observed=observed,
        evidence={
            "canonical_resource_id": f"alert_rule:{alert_id}",
            "read_back": {"status": observed, "active": int(rule.get("active") or 0)},
            "source": "alert_engine.get_alert_rule",
        },
    )


def crypto_alert_exists(user_id: int, arguments: dict[str, Any], result: ToolResult) -> VerificationResult:
    """Confirm a newly created alert exists and carries the requested terms."""
    alert_id = int((result.data or {}).get("alert_id") or 0)
    if not alert_id:
        return VerificationResult(
            state=VerificationState.FAILED,
            expected="a created alert",
            observed=None,
            detail="PulseSoc did not return a canonical alert id.",
        )
    try:
        rule = _alert_engine().get_alert_rule(alert_id, int(user_id))
    except Exception as exc:  # pragma: no cover - defensive
        return _unreadable(f"Alert read-back failed: {exc.__class__.__name__}")
    if not rule:
        return VerificationResult(
            state=VerificationState.FAILED,
            expected="a created alert",
            observed=None,
            detail="The new alert could not be read back on this account.",
        )
    expected = {
        "symbol": clean(arguments.get("symbol"), 24).upper(),
        "condition": clean(arguments.get("condition"), 40),
        "threshold": float(arguments.get("threshold") or 0),
    }
    observed = {
        "symbol": clean(rule.get("symbol") or rule.get("asset_symbol"), 24).upper(),
        "condition": clean(rule.get("condition"), 40),
        "threshold": float(rule.get("threshold_value") or 0),
    }
    matches = (
        observed["symbol"] == expected["symbol"]
        and observed["condition"] == expected["condition"]
        and abs(observed["threshold"] - expected["threshold"]) < 1e-9
    )
    return VerificationResult(
        state=VerificationState.VERIFIED if matches else VerificationState.FAILED,
        expected=expected,
        observed=observed,
        evidence={
            "canonical_resource_id": f"alert_rule:{alert_id}",
            "read_back": observed,
            "source": "alert_engine.get_alert_rule",
        },
    )


def crypto_alert_threshold(user_id: int, arguments: dict[str, Any], result: ToolResult) -> VerificationResult:
    """Confirm an edited alert now holds the requested threshold."""
    alert_id = int(arguments.get("alert_id") or 0)
    try:
        rule = _alert_engine().get_alert_rule(alert_id, int(user_id))
    except Exception as exc:  # pragma: no cover - defensive
        return _unreadable(f"Alert read-back failed: {exc.__class__.__name__}")
    if not rule:
        return VerificationResult(
            state=VerificationState.FAILED,
            expected=arguments.get("threshold"),
            observed=None,
            detail="The alert could not be read back on this account.",
        )
    expected = float(arguments.get("threshold") or 0)
    observed = float(rule.get("threshold_value") or 0)
    return VerificationResult(
        state=VerificationState.VERIFIED if abs(observed - expected) < 1e-9 else VerificationState.FAILED,
        expected=expected,
        observed=observed,
        evidence={
            "canonical_resource_id": f"alert_rule:{alert_id}",
            "read_back": {"threshold_value": observed},
            "source": "alert_engine.get_alert_rule",
        },
    )


def crypto_alert_deleted(user_id: int, arguments: dict[str, Any], result: ToolResult) -> VerificationResult:
    """Confirm the alert is really gone.

    Deletion is a soft delete, so "absent from the owner-scoped read" and "present
    with status deleted" are both correct outcomes; anything else is not.
    """
    alert_id = int(arguments.get("alert_id") or 0)
    try:
        rule = _alert_engine().get_alert_rule(alert_id, int(user_id))
    except Exception as exc:  # pragma: no cover - defensive
        return _unreadable(f"Alert read-back failed: {exc.__class__.__name__}", "deleted")
    observed = "absent" if not rule else clean(rule.get("status") or "active", 24)
    deleted = observed in {"absent", "deleted"}
    return VerificationResult(
        state=VerificationState.VERIFIED if deleted else VerificationState.FAILED,
        expected="deleted",
        observed=observed,
        evidence={
            "canonical_resource_id": f"alert_rule:{alert_id}",
            "read_back": {"status": observed},
            "source": "alert_engine.get_alert_rule",
        },
    )


# ---------------------------------------------------------------------------
# Notification preferences
# ---------------------------------------------------------------------------


def notification_preference_value(user_id: int, arguments: dict[str, Any], result: ToolResult) -> VerificationResult:
    """Confirm the stored push flag now equals the requested value."""
    from services.undx_agent_tools import read_push_value, resolve_category

    category = clean(arguments.get("category") or "global", 40)
    stored = resolve_category(category)
    expected = bool(arguments.get("push"))
    try:
        after = _notifications().get_preferences(int(user_id)) or {}
    except Exception as exc:  # pragma: no cover - defensive
        return _unreadable(f"Preference read-back failed: {exc.__class__.__name__}", expected)
    observed = read_push_value(after, category)
    return VerificationResult(
        state=VerificationState.VERIFIED if observed == expected else VerificationState.FAILED,
        expected=expected,
        observed=observed,
        evidence={
            "canonical_resource_id": f"user:{int(user_id)}:{stored}",
            "read_back": {"category": category, "stored_category": stored, "push": observed},
            "source": "pulsesoc_notification_system.get_preferences",
        },
    )


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

VERIFIERS: dict[str, Callable[[int, dict[str, Any], ToolResult], VerificationResult]] = {
    "crypto_alert_status": crypto_alert_status,
    "crypto_alert_exists": crypto_alert_exists,
    "crypto_alert_threshold": crypto_alert_threshold,
    "crypto_alert_deleted": crypto_alert_deleted,
    "notification_preference_value": notification_preference_value,
}


def verify(name: str, user_id: int, arguments: dict[str, Any], result: ToolResult) -> VerificationResult:
    """Run one declared verifier.

    A missing verifier yields ``impossible_to_verify`` rather than an exception or a
    silent pass. The action still happened; what is unavailable is the evidence,
    and the receipt should say exactly that.
    """
    verifier = VERIFIERS.get(clean(name, 80))
    if verifier is None:
        return VerificationResult(
            state=VerificationState.IMPOSSIBLE,
            detail="No read-back path is available for this action.",
        )
    return verifier(int(user_id), arguments or {}, result)


__all__ = ["VERIFIERS", "verify"]
