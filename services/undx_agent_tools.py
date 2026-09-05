"""Executors for the registered UNDX capabilities.

Every function here is the *only* code path between the agent and a real PulseSoc
service. They are deliberately dull: no confirmation logic, no auditing, no
retries, no policy. The gateway owns all of that, and keeping it out of here means
there is exactly one place to review when asking "can this action be reached
without approval?".

Two invariants hold throughout:

**Ownership is enforced by the service call, not by a preceding check.** Each
underlying function takes ``user_id`` and filters on it in SQL. A caller who
substitutes another account's ``alert_id`` gets "not found" from the database
rather than a permission check that could be forgotten or reordered.

**Nothing raw escapes.** Service responses are projected onto declared fields by
``_alert_record`` before travelling on. A crypto alert row can contain
user-authored strings, and if such a row were handed to the model verbatim then a
symbol named "ignore previous instructions" would arrive looking exactly like
system text. Whitelisting is what prevents that.
"""

from __future__ import annotations

import json
import time
from typing import Any, Callable

from services.undx_agent_contracts import (
    AgentError,
    AgentOutcome,
    ToolResult,
    clean,
)


# ---------------------------------------------------------------------------
# Normalisation of untrusted service output
# ---------------------------------------------------------------------------

#: Fields of an alert rule that may be shown to a user or a model. Anything not
#: named here — metadata blobs, source refs, delivery logs, internal ids — is
#: dropped rather than filtered, so a newly added column cannot leak by default.
#:
#: The drop-by-default rule is the right one, and it has a cost that has to be paid
#: deliberately: a rule shape the projection has not been taught is not rejected here,
#: it is quietly described as something simpler than it is. Compound conditions and
#: watchlist/portfolio scoping were added to ``alert_engine`` without this list being
#: extended, so every scoped compound rule reached the model as ``symbol: ""``,
#: ``threshold: None`` and the legacy fallback condition — a rule that reads as broken,
#: and that ``resolve_alert_reference`` then had to identify from an empty description.
_ALERT_FIELDS = ("id", "symbol", "condition", "threshold_value", "status", "active",
                 "alert_type", "created_at", "updated_at", "trigger_count",
                 # What the rule actually watches. ``condition_summary`` is rendered
                 # once, server-side, by ``alert_engine._public_rule`` precisely so the
                 # web UI, the native UI, the notification copy and this projection
                 # cannot describe one rule four different ways; it is carried, never
                 # re-derived. It is empty for a basic rule, whose ``condition`` and
                 # ``threshold_value`` already say everything there is to say.
                 "condition_summary", "is_advanced",
                 # Scope. A scoped rule has no symbol on purpose — it is about a list or
                 # about everything held, and naming one coin would be read as a claim
                 # that it watches only that coin.
                 "watchlist_id", "is_watchlist_rule", "is_portfolio_rule")


def _alert_record(rule: dict[str, Any] | None) -> dict[str, Any]:
    """Project one alert rule onto its safe, bounded public shape."""
    if not isinstance(rule, dict) or not rule:
        return {}
    channels = rule.get("channels") if isinstance(rule.get("channels"), dict) else {}
    record: dict[str, Any] = {}
    for key in _ALERT_FIELDS:
        value = rule.get(key)
        if isinstance(value, bool):
            record[key] = value
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            record[key] = value
        else:
            record[key] = clean(value, 80)
    record["alert_id"] = int(rule.get("id") or 0)
    record["threshold"] = rule.get("threshold_value")
    record["paused"] = clean(rule.get("status") or "active", 24) == "paused"
    # ``clean`` turns None into "", which is right for prose and wrong for a nullable
    # id: an unscoped rule would arrive carrying an empty watchlist rather than no
    # watchlist, and "" is a value a model will try to say something about.
    if rule.get("watchlist_id") is None:
        record["watchlist_id"] = None
    metadata = rule.get("metadata")
    if isinstance(metadata, str):
        try:
            metadata = json.loads(metadata)
        except (TypeError, ValueError):
            metadata = {}
    if not isinstance(metadata, dict):
        metadata = {}
    # A member's own note wins. Failing that the name is built from what the rule
    # watches, which for a scoped rule is not a coin: falling back to the symbol alone
    # named every portfolio and watchlist rule "Crypto alert", so an account holding
    # several of them offered the model a set of identically-named things to choose
    # between — and ``resolve_alert_reference`` is required to find exactly one.
    if record.get("is_portfolio_rule"):
        fallback = "Portfolio alert"
    elif record.get("is_watchlist_rule"):
        fallback = "Watchlist alert"
    else:
        fallback = f"{record.get('symbol') or 'Crypto'} alert"
    record["display_name"] = clean(metadata.get("note") or fallback, 80)
    record["channels"] = {
        name: bool(channels.get(name))
        for name in ("in_app", "push", "email", "sms", "telegram")
    }
    return record


def _timed(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


def _fail(tool: str, capability: str, code: str, message: str, *, retryable: bool = False,
          started: float = 0.0) -> ToolResult:
    return ToolResult(
        ok=False, tool_name=tool, capability_id=capability,
        error_code=code, error_message=message, retryable=retryable,
        latency_ms=_timed(started) if started else 0,
    )


# ---------------------------------------------------------------------------
# Crypto alerts — backed by services.alert_engine
# ---------------------------------------------------------------------------


def _alert_engine():
    from services import alert_engine

    return alert_engine


def crypto_alerts_list(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    """List the caller's alerts, and say plainly when the list is not all of them.

    One row more than the limit is requested on purpose. Without it, a full page and a
    complete set are indistinguishable — and that distinction is load-bearing a long
    way from here. ``undx_agent_runtime.resolve_alert_reference`` decides "exactly one
    of your alerts matches this description" from this list, and a *truncated* list can
    contain exactly one match while the account holds several. The user would then be
    shown, and would approve, a change to whichever alert happened to fall on page one.

    ``symbol`` narrows the page itself. Passing it means ``truncated`` describes the
    coin the person named rather than the account as a whole, which is the difference
    between "you have more Bitcoin alerts than I can compare" and "you have more alerts
    than I can compare" — the second having been said, until this argument existed, to
    people holding exactly one Bitcoin alert and fifty of something else.
    """
    started = time.perf_counter()
    engine = _alert_engine()
    limit = int(arguments.get("limit") or 20)
    symbol = str(arguments.get("symbol") or "").strip().upper()
    fetched = [_alert_record(rule)
               for rule in ((engine.list_alert_rules(int(user_id), limit=limit + 1,
                                                    symbol=symbol or None) or {})
                            .get("alerts") or [])]
    records = fetched[:limit]
    return ToolResult(
        ok=True,
        tool_name="pulsesoc.crypto_alerts.list",
        capability_id="crypto.alerts.list",
        records=records,
        data={"count": len(records), "truncated": len(fetched) > limit},
        latency_ms=_timed(started),
    )


def crypto_alerts_get(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    engine = _alert_engine()
    alert_id = int(arguments["alert_id"])
    # Owner-scoped read. A rule belonging to another account is indistinguishable
    # from one that does not exist, which is the correct disclosure boundary.
    rule = engine.get_alert_rule(alert_id, int(user_id))
    if not rule:
        return _fail("pulsesoc.crypto_alerts.get", "crypto.alerts.get",
                     "resource_not_found", "UNDX could not find that alert on your account.",
                     started=started)
    record = _alert_record(rule)
    return ToolResult(
        ok=True,
        tool_name="pulsesoc.crypto_alerts.get",
        capability_id="crypto.alerts.get",
        canonical_resource_id=f"alert_rule:{alert_id}",
        records=[record],
        data=record,
        latency_ms=_timed(started),
    )


def _set_alert_state(user_id: int, alert_id: int, *, capability: str, tool: str,
                     call: Callable[[int, int], dict[str, Any]]) -> ToolResult:
    started = time.perf_counter()
    engine = _alert_engine()
    existing = engine.get_alert_rule(alert_id, int(user_id))
    if not existing:
        return _fail(tool, capability, "resource_not_found",
                     "UNDX could not find that alert on your account.", started=started)
    if clean(existing.get("status") or "active", 24) == "deleted":
        return _fail(tool, capability, "resource_deleted",
                     "That alert has already been deleted.", started=started)
    outcome = call(alert_id, int(user_id)) or {}
    if not outcome.get("ok"):
        return _fail(tool, capability, "write_rejected",
                     clean(outcome.get("message") or "PulseSoc did not accept that change.", 200),
                     retryable=True, started=started)
    return ToolResult(
        ok=True,
        tool_name=tool,
        capability_id=capability,
        canonical_resource_id=f"alert_rule:{alert_id}",
        data={"alert_id": alert_id, "requested_status": clean(outcome.get("status"), 24)},
        latency_ms=_timed(started),
    )


def crypto_alerts_pause(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    engine = _alert_engine()
    return _set_alert_state(
        user_id, int(arguments["alert_id"]),
        capability="crypto.alerts.pause", tool="pulsesoc.crypto_alerts.pause",
        call=engine.pause_alert,
    )


def crypto_alerts_resume(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    engine = _alert_engine()
    return _set_alert_state(
        user_id, int(arguments["alert_id"]),
        capability="crypto.alerts.resume", tool="pulsesoc.crypto_alerts.resume",
        call=engine.resume_alert,
    )


def crypto_alerts_delete(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    engine = _alert_engine()
    return _set_alert_state(
        user_id, int(arguments["alert_id"]),
        capability="crypto.alerts.delete", tool="pulsesoc.crypto_alerts.delete",
        call=engine.delete_alert,
    )


def crypto_alerts_create(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    engine = _alert_engine()
    condition = clean(arguments["condition"], 40)
    alert_type = "move_24h" if condition in {"moves_up_percent", "moves_down_percent", "volatility_above"} else "coin_price"
    outcome = engine.create_alert_rule(
        int(user_id),
        alert_type=alert_type,
        symbol=clean(arguments["symbol"], 24),
        condition=condition,
        threshold=float(arguments["threshold"]),
        channels={"in_app": True, "push": True},
        source="undx_agent",
        # The idempotency key travels into the row so a duplicate submission is
        # detectable after the fact, not only at the gateway.
        source_ref=clean(arguments.get("_idempotency_key") or "", 160),
    ) or {}
    if not outcome.get("ok"):
        return _fail("pulsesoc.crypto_alerts.create", "crypto.alerts.create",
                     "write_rejected",
                     clean(outcome.get("message") or "PulseSoc did not accept that alert.", 200),
                     started=started)
    alert_id = int(outcome.get("alert_id") or 0)
    record = _alert_record(outcome.get("alert") or {})
    return ToolResult(
        ok=True,
        tool_name="pulsesoc.crypto_alerts.create",
        capability_id="crypto.alerts.create",
        canonical_resource_id=f"alert_rule:{alert_id}",
        records=[record] if record else [],
        data={"alert_id": alert_id},
        latency_ms=_timed(started),
    )


def crypto_alerts_update(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    engine = _alert_engine()
    alert_id = int(arguments["alert_id"])
    existing = engine.get_alert_rule(alert_id, int(user_id))
    if not existing:
        return _fail("pulsesoc.crypto_alerts.update", "crypto.alerts.update",
                     "resource_not_found", "UNDX could not find that alert on your account.",
                     started=started)
    payload: dict[str, Any] = {"targetValue": float(arguments["threshold"])}
    if arguments.get("condition"):
        payload["condition"] = clean(arguments["condition"], 40)
    outcome = engine.update_alert_rule(alert_id, int(user_id), payload) or {}
    if not outcome.get("ok"):
        return _fail("pulsesoc.crypto_alerts.update", "crypto.alerts.update",
                     "write_rejected",
                     clean(outcome.get("message") or "PulseSoc did not accept that change.", 200),
                     started=started)
    return ToolResult(
        ok=True,
        tool_name="pulsesoc.crypto_alerts.update",
        capability_id="crypto.alerts.update",
        canonical_resource_id=f"alert_rule:{alert_id}",
        data={"alert_id": alert_id, "threshold": float(arguments["threshold"])},
        latency_ms=_timed(started),
    )


# ---------------------------------------------------------------------------
# Crypto intelligence — backed by services.portfolio_intelligence,
# services.market_observations and services.alert_engine, premium-gated by
# services.crypto_premium_gate
# ---------------------------------------------------------------------------

#: Whitelisted fields for a single portfolio holding. Anything the valuation
#: service adds beyond these names is dropped, never forwarded.
_HOLDING_FIELDS = ("asset_id", "symbol", "name", "quantity", "amount", "balance",
                   "price", "price_usd", "value", "value_usd", "allocation",
                   "allocation_pct", "weight", "change_24h", "change_24h_pct")

#: Whitelisted fields for one point of the portfolio-history series.
_HISTORY_POINT_FIELDS = ("captured_at", "observed_at", "timestamp", "period_start",
                         "total_value", "value", "change", "change_pct")

#: Whitelisted fields for an alert trigger event (services.alert_engine
#: ``alert_events`` rows). ``message`` is deliberately absent: it can embed
#: user-authored strings.
_ALERT_EVENT_FIELDS = ("id", "alert_rule_id", "symbol", "condition", "threshold_value",
                       "observed_value", "status", "delivery_status", "created_at")

#: Whitelisted fields for one sampled market observation.
_OBSERVATION_FIELDS = ("asset_id", "symbol", "observed_at", "price", "volume_24h",
                       "market_cap")

#: services.market_observations is being built concurrently and its reader name
#: is not part of the contract. Try the plausible names in order; if none exist
#: the tool reports an honest "unavailable" rather than inventing data.
_OBSERVATION_READERS = ("get_observation_series", "list_observations", "get_observations",
                        "recent_observations", "observation_series")


def _project(source: Any, fields: tuple[str, ...], *, str_limit: int = 80) -> dict[str, Any]:
    """Project one untrusted service row onto a declared, bounded shape."""
    if not isinstance(source, dict) or not source:
        return {}
    record: dict[str, Any] = {}
    for key in fields:
        if key not in source:
            continue
        value = source.get(key)
        if isinstance(value, bool):
            record[key] = value
        elif isinstance(value, (int, float)):
            record[key] = value
        elif value is None:
            record[key] = None
        else:
            record[key] = clean(value, str_limit)
    return record


def _scalars(source: Any, *, max_keys: int = 12, str_limit: int = 80) -> dict[str, Any]:
    """Bound an open-shaped dict (concentration stats, premium payloads) to
    scalar values only. Nested structures are dropped, not serialised."""
    if not isinstance(source, dict):
        return {}
    bounded: dict[str, Any] = {}
    for key, value in source.items():
        if len(bounded) >= max_keys:
            break
        name = clean(key, 60)
        if not name:
            continue
        if isinstance(value, bool) or isinstance(value, (int, float)):
            bounded[name] = value
        elif value is None:
            bounded[name] = None
        elif isinstance(value, str):
            bounded[name] = clean(value, str_limit)
    return bounded


def _premium_payload(gate: Any, cap: Any) -> dict[str, Any]:
    """The upsell payload shown when a premium capability is locked, projected
    so the gate service cannot smuggle arbitrary structure to the model."""
    try:
        raw = gate.premium_required_response(cap)
    except Exception:
        raw = {}
    payload = _scalars(raw, max_keys=12, str_limit=200)
    payload["premium_required"] = True
    payload.setdefault("capability", clean(cap, 80))
    return payload


def _premium_denial(user_id: int, cap_attr: str, *, tool: str, capability: str,
                    started: float) -> ToolResult | None:
    """Return a denial ToolResult if the caller lacks the premium capability,
    or ``None`` when access is allowed.

    Fail closed on every uncertainty: if the gate module is missing or errors,
    the feature is treated as locked and the result says so honestly rather
    than guessing in either direction.
    """
    try:
        from services import crypto_premium_gate as gate
    except ImportError:
        return _fail(tool, capability, "premium_gate_unavailable",
                     "UNDX could not verify premium access for that feature, "
                     "so it is treating it as locked.",
                     started=started)
    cap = getattr(gate, cap_attr, cap_attr)
    try:
        if gate.has_crypto_capability(int(user_id), cap):
            return None
    except Exception:
        return _fail(tool, capability, "premium_gate_error",
                     "UNDX could not verify premium access for that feature, "
                     "so it is treating it as locked.",
                     started=started)
    payload = _premium_payload(gate, cap)
    return ToolResult(
        ok=False,
        tool_name=tool,
        capability_id=capability,
        error_code="premium_required",
        error_message=clean(
            payload.get("message") or "That feature needs a PulseSoc premium crypto plan.",
            200,
        ),
        data=payload,
        latency_ms=_timed(started),
    )


def crypto_portfolio_summary(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    """Current portfolio valuation for the caller. Premium (CAP_CRYPTO_PORTFOLIO)."""
    started = time.perf_counter()
    tool, capability = "pulsesoc.crypto_portfolio.summary", "crypto.portfolio.summary"
    denial = _premium_denial(user_id, "CAP_CRYPTO_PORTFOLIO",
                             tool=tool, capability=capability, started=started)
    if denial is not None:
        return denial
    try:
        from services import portfolio_intelligence
    except ImportError:
        return _fail(tool, capability, "portfolio_service_unavailable",
                     "UNDX cannot read portfolio data right now: the portfolio "
                     "service is not available.",
                     started=started)
    outcome = portfolio_intelligence.compute_portfolio_valuation(int(user_id)) or {}
    if not isinstance(outcome, dict) or not outcome.get("ok"):
        message = ""
        if isinstance(outcome, dict):
            message = outcome.get("error") or outcome.get("message") or ""
        return _fail(tool, capability, "portfolio_read_failed",
                     clean(message or "PulseSoc could not compute your portfolio right now.", 200),
                     retryable=True, started=started)
    holdings = [_project(row, _HOLDING_FIELDS)
                for row in outcome.get("holdings") or [] if isinstance(row, dict)]
    total_value = outcome.get("total_value")
    data: dict[str, Any] = {
        "total_value": float(total_value) if isinstance(total_value, (int, float))
        and not isinstance(total_value, bool) else None,
        "holding_count": len(holdings),
        "concentration": _scalars(outcome.get("concentration")),
    }
    if outcome.get("currency"):
        data["currency"] = clean(outcome.get("currency"), 12)
    if outcome.get("as_of"):
        data["as_of"] = clean(outcome.get("as_of"), 40)
    return ToolResult(
        ok=True,
        tool_name=tool,
        capability_id=capability,
        canonical_resource_id=f"user:{int(user_id)}:portfolio",
        records=holdings,
        data=data,
        latency_ms=_timed(started),
    )


def crypto_portfolio_history(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    """Portfolio value over a period. Premium (CAP_CRYPTO_PORTFOLIO)."""
    started = time.perf_counter()
    tool, capability = "pulsesoc.crypto_portfolio.history", "crypto.portfolio.history"
    denial = _premium_denial(user_id, "CAP_CRYPTO_PORTFOLIO",
                             tool=tool, capability=capability, started=started)
    if denial is not None:
        return denial
    try:
        from services import portfolio_intelligence
    except ImportError:
        return _fail(tool, capability, "portfolio_service_unavailable",
                     "UNDX cannot read portfolio history right now: the portfolio "
                     "service is not available.",
                     started=started)
    period = clean(arguments.get("period") or "30d", 8)
    outcome = portfolio_intelligence.get_portfolio_history(int(user_id), period)
    if isinstance(outcome, dict) and not outcome.get("ok", True):
        message = outcome.get("error") or outcome.get("message") or ""
        return _fail(tool, capability, "portfolio_read_failed",
                     clean(message or "PulseSoc could not read your portfolio history.", 200),
                     retryable=True, started=started)
    if isinstance(outcome, dict):
        series = (outcome.get("points") or outcome.get("history")
                  or outcome.get("snapshots") or [])
    elif isinstance(outcome, list):
        series = outcome
    else:
        series = []
    points = [_project(row, _HISTORY_POINT_FIELDS)
              for row in series if isinstance(row, dict)]
    return ToolResult(
        ok=True,
        tool_name=tool,
        capability_id=capability,
        canonical_resource_id=f"user:{int(user_id)}:portfolio-history",
        records=points,
        data={"period": period, "point_count": len(points)},
        latency_ms=_timed(started),
    )


def crypto_alerts_activity(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    """The caller's alert rules plus recent trigger history.

    The rule listing is free — it is the same read ``crypto.alerts.list``
    already grants. The trigger-event detail is the premium half
    (CAP_CRYPTO_ADVANCED_ALERTS): when the gate denies, the rules still come
    back and ``trigger_history`` carries the honest premium payload instead of
    the events, so the model can explain exactly what is locked and why.
    """
    started = time.perf_counter()
    tool, capability = "pulsesoc.crypto_alerts.activity", "crypto.alerts.activity"
    engine = _alert_engine()
    limit = int(arguments.get("limit") or 20)
    alert_id = int(arguments.get("alert_id") or 0)
    outcome = engine.list_alert_rules(int(user_id), limit=limit + 1) or {}
    if not outcome.get("ok"):
        return _fail(tool, capability, "read_failed",
                     "PulseSoc could not read your alerts right now.",
                     retryable=True, started=started)
    fetched = [_alert_record(rule) for rule in outcome.get("alerts") or []]
    records = fetched[:limit]
    denial = _premium_denial(user_id, "CAP_CRYPTO_ADVANCED_ALERTS",
                             tool=tool, capability=capability, started=started)
    if denial is None:
        events_outcome = engine.list_alert_events(
            int(user_id), limit=limit, alert_id=alert_id or None) or {}
        if events_outcome.get("ok"):
            events = [_project(event, _ALERT_EVENT_FIELDS)
                      for event in events_outcome.get("events") or []
                      if isinstance(event, dict)]
            history: dict[str, Any] = {"available": True, "events": events,
                                       "count": len(events)}
        else:
            history = {"available": False, "error_code": "read_failed"}
    else:
        history = {"available": False, "error_code": clean(denial.error_code, 80)}
        if denial.error_code == "premium_required" and isinstance(denial.data, dict):
            history.update(denial.data)
    return ToolResult(
        ok=True,
        tool_name=tool,
        capability_id=capability,
        canonical_resource_id=f"user:{int(user_id)}:crypto-alert-activity",
        records=records,
        data={
            "alert_count": len(records),
            "truncated": len(fetched) > limit,
            "trigger_history": history,
        },
        latency_ms=_timed(started),
    )


def crypto_market_observations(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    """Recent sampled market observations for one asset. Free tier."""
    started = time.perf_counter()
    tool, capability = "pulsesoc.crypto_market.observations", "crypto.market.observations"
    try:
        from services import market_observations
    except ImportError:
        return _fail(tool, capability, "market_observations_unavailable",
                     "UNDX cannot read market observations right now: the "
                     "observation service is not available.",
                     started=started)
    asset_id = clean(arguments.get("asset_id"), 60)
    if not asset_id:
        return _fail(tool, capability, "invalid_arguments",
                     "An asset id is required.", started=started)
    limit = int(arguments.get("limit") or 24)
    reader = None
    for name in _OBSERVATION_READERS:
        candidate = getattr(market_observations, name, None)
        if callable(candidate):
            reader = candidate
            break
    if reader is None:
        return _fail(tool, capability, "market_observations_unavailable",
                     "UNDX cannot read market observations right now: the "
                     "observation service exposes no series reader.",
                     started=started)
    try:
        try:
            outcome = reader(asset_id, limit=limit)
        except TypeError:
            outcome = reader(asset_id)
    except Exception:
        return _fail(tool, capability, "market_observation_read_failed",
                     "PulseSoc could not read observations for that asset right now.",
                     retryable=True, started=started)
    if isinstance(outcome, dict):
        if not outcome.get("ok", True):
            message = outcome.get("error") or outcome.get("message") or ""
            return _fail(tool, capability, "market_observation_read_failed",
                         clean(message or "PulseSoc could not read observations for that asset.", 200),
                         retryable=True, started=started)
        series = (outcome.get("observations") or outcome.get("series")
                  or outcome.get("points") or [])
    elif isinstance(outcome, list):
        series = outcome
    else:
        series = []
    rows = [_project(item, _OBSERVATION_FIELDS)
            for item in series if isinstance(item, dict)][:max(1, limit)]
    return ToolResult(
        ok=True,
        tool_name=tool,
        capability_id=capability,
        canonical_resource_id=f"asset:{asset_id}",
        records=rows,
        data={"asset_id": asset_id, "observation_count": len(rows)},
        latency_ms=_timed(started),
    )


# ---------------------------------------------------------------------------
# Live market reads — backed by services.undx_market_context (Market Pulse)
# ---------------------------------------------------------------------------
#
# The context bridge in action for the agent path. ``symbol`` may be empty:
# a person who arrived from an asset screen already told the app which coin
# "it" is, and that envelope is stored server-side per account. Resolution
# never guesses — no symbol and no context is an honest ask-back, not a
# default to Bitcoin.
#
# Entitlement first, context second. These reads are the agent-path twins of
# the ``/api/crypto/market-pulse``, asset-detail, asset-history and market-board
# routes, every one of which bot.py gates on ``premium.crypto.intelligence``.
# Leaving the agent path open would have meant an expired member could not open
# Market Pulse but could still ask UNDX for the same numbers.
#
# The gate runs BEFORE ``_resolve_market_symbol`` on purpose. The parked market
# context says WHICH coin the person means; it is not a grant. Checking it first
# would have let a context envelope — parked while the membership was still
# live, and outliving it — decide entitlement.


def _market_context():
    from services import undx_market_context

    return undx_market_context


def _resolve_market_symbol(user_id: int, arguments: dict[str, Any]) -> tuple[str, str]:
    """(symbol, via) — the argument if given, else the active screen context."""
    symbol = clean(arguments.get("symbol"), 24).upper()
    if symbol:
        return symbol, "argument"
    context = _market_context().active_context_for_user(int(user_id))
    if context:
        return clean((context.get("asset") or {}).get("symbol"), 24).upper(), "context"
    return "", ""


_NO_SYMBOL_MESSAGE = ("Tell me which coin you mean — for example \"price of BTC\" — "
                      "or open it in Market Pulse and ask again.")


def crypto_market_quote(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    tool, capability = "pulsesoc.crypto_market.quote", "crypto.market.quote"
    denial = _premium_denial(user_id, "CAP_CRYPTO_INTELLIGENCE",
                             tool=tool, capability=capability, started=started)
    if denial is not None:
        return denial
    symbol, via = _resolve_market_symbol(user_id, arguments)
    if not symbol:
        return _fail(tool, capability, "missing_arguments", _NO_SYMBOL_MESSAGE, started=started)
    try:
        record = _market_context().quote(symbol)
    except Exception:
        return _fail(tool, capability, "market_read_failed",
                     "PulseSoc could not read live market data right now.",
                     retryable=True, started=started)
    if not record:
        return _fail(tool, capability, "resource_not_found",
                     f"UNDX could not find {symbol} on the live market board.",
                     started=started)
    return ToolResult(
        ok=True, tool_name=tool, capability_id=capability,
        canonical_resource_id=f"asset:{symbol}",
        records=[record],
        data={"symbol": symbol, "resolved_via": via,
              "freshness": record.get("freshness") or {}},
        latency_ms=_timed(started),
    )


def crypto_market_history(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    tool, capability = "pulsesoc.crypto_market.history", "crypto.market.history"
    denial = _premium_denial(user_id, "CAP_CRYPTO_INTELLIGENCE",
                             tool=tool, capability=capability, started=started)
    if denial is not None:
        return denial
    symbol, via = _resolve_market_symbol(user_id, arguments)
    if not symbol:
        return _fail(tool, capability, "missing_arguments", _NO_SYMBOL_MESSAGE, started=started)
    context_module = _market_context()
    range_key = context_module.normalize_range(arguments.get("range")) or "24H"
    try:
        pack = context_module.history_pack(symbol, range_key)
    except Exception:
        return _fail(tool, capability, "market_read_failed",
                     "PulseSoc could not read price history right now.",
                     retryable=True, started=started)
    if not pack.get("ok"):
        return _fail(tool, capability, "history_unavailable",
                     clean(pack.get("warning") or f"No {range_key} history is available for {symbol}.", 200),
                     retryable=True, started=started)
    return ToolResult(
        ok=True, tool_name=tool, capability_id=capability,
        canonical_resource_id=f"asset:{symbol}:{range_key}",
        records=[pack],
        data={**pack, "resolved_via": via},
        latency_ms=_timed(started),
    )


def crypto_market_compare(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    tool, capability = "pulsesoc.crypto_market.compare", "crypto.market.compare"
    denial = _premium_denial(user_id, "CAP_CRYPTO_INTELLIGENCE",
                             tool=tool, capability=capability, started=started)
    if denial is not None:
        return denial
    symbol, via = _resolve_market_symbol(user_id, arguments)
    versus = clean(arguments.get("versus"), 24).upper()
    if not symbol or not versus:
        return _fail(tool, capability, "missing_arguments",
                     _NO_SYMBOL_MESSAGE if not symbol else "Name the coin to compare against.",
                     started=started)
    if symbol == versus:
        return _fail(tool, capability, "invalid_arguments",
                     "Those are the same asset — name two different coins to compare.",
                     started=started)
    context_module = _market_context()
    try:
        left, right = context_module.quote(symbol), context_module.quote(versus)
    except Exception:
        return _fail(tool, capability, "market_read_failed",
                     "PulseSoc could not read live market data right now.",
                     retryable=True, started=started)
    missing = [name for name, rec in ((symbol, left), (versus, right)) if not rec]
    if missing:
        return _fail(tool, capability, "resource_not_found",
                     f"UNDX could not find {' and '.join(missing)} on the live market board.",
                     started=started)
    return ToolResult(
        ok=True, tool_name=tool, capability_id=capability,
        canonical_resource_id=f"asset:{symbol}:vs:{versus}",
        records=[left, right],
        data={"symbol": symbol, "versus": versus, "resolved_via": via,
              "freshness": left.get("freshness") or {}},
        latency_ms=_timed(started),
    )


def crypto_market_overview(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    tool, capability = "pulsesoc.crypto_market.overview", "crypto.market.overview"
    denial = _premium_denial(user_id, "CAP_CRYPTO_INTELLIGENCE",
                             tool=tool, capability=capability, started=started)
    if denial is not None:
        return denial
    try:
        metrics = _market_context().overview()
    except Exception:
        return _fail(tool, capability, "market_read_failed",
                     "PulseSoc could not read the market overview right now.",
                     retryable=True, started=started)
    if not metrics.get("available"):
        return _fail(tool, capability, "market_read_failed",
                     "The live market overview is unavailable right now.",
                     retryable=True, started=started)
    return ToolResult(
        ok=True, tool_name=tool, capability_id=capability,
        canonical_resource_id="market:global",
        records=[metrics],
        data=metrics,
        latency_ms=_timed(started),
    )


# ---------------------------------------------------------------------------
# Notification preferences — backed by services.pulsesoc_notification_system
# ---------------------------------------------------------------------------


def _notifications():
    from services import pulsesoc_notification_system

    return pulsesoc_notification_system


# The words a person uses are not the column names PulseSoc stores. "Reels" is a
# surface in the app; the notification category behind it is ``likes``. Without this
# map a write to "reels" creates a category the notification pipeline never consults,
# and — worse — the read-back of a category that does not exist returns False, so
# UNDX would report reel notifications as already off for a user who never touched
# them. Every category the registry offers must appear here, and
# ``test_notification_categories_are_real`` asserts each target really exists.
CATEGORY_ALIASES: dict[str, str] = {
    "global": "global",     # the master switch, stored under ``experience``
    "posts": "social",
    "reels": "likes",
    "messages": "messages",
    "calls": "calls",
    "alerts": "crypto",
}


def resolve_category(category: str) -> str:
    """Translate a UNDX-facing category into the one the notification store uses."""
    name = clean(category, 40).lower()
    return CATEGORY_ALIASES.get(name, name)


def read_push_value(preferences: dict[str, Any], category: str) -> bool:
    """Extract one push flag from the preferences document.

    The global switch lives under ``experience`` while per-category switches live
    under ``preferences``; both the executor and the verifier read through this one
    function so a mutation and its read-back can never disagree merely because they
    parsed the same document differently.
    """
    resolved = resolve_category(category)
    if resolved == "global":
        return bool((preferences.get("experience") or {}).get("enable_push_notifications"))
    return bool(((preferences.get("preferences") or {}).get(resolved) or {}).get("push"))


def notification_preferences_read(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    system = _notifications()
    category = clean(arguments.get("category") or "global", 40)
    preferences = system.get_preferences(int(user_id)) or {}
    value = read_push_value(preferences, category)
    return ToolResult(
        ok=True,
        tool_name="pulsesoc.notification_preferences.read",
        capability_id="notifications.preference.read",
        canonical_resource_id=f"user:{int(user_id)}:{resolve_category(category)}",
        data={"category": category, "push": value},
        records=[{"category": category, "push": value}],
        latency_ms=_timed(started),
    )


def notification_preferences_update(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    system = _notifications()
    category = clean(arguments["category"], 40)
    proposed = bool(arguments["push"])
    before = system.get_preferences(int(user_id)) or {}
    observed_before = read_push_value(before, category)

    expected_before = arguments.get("_expected_current_push")
    if expected_before is not None and bool(expected_before) != observed_before:
        # The world moved between approval and execution. Applying the write anyway
        # would silently overwrite whatever changed it, so the operation stops and
        # asks for a fresh decision instead.
        return _fail("pulsesoc.notification_preferences.update",
                     "notifications.preference.update",
                     "stale_state",
                     "That setting changed after UNDX prepared this action. Review it and confirm again.",
                     started=started)

    resolved = resolve_category(category)
    if resolved == "global":
        payload: dict[str, Any] = {"enable_push_notifications": proposed}
    else:
        current_category = dict((before.get("preferences") or {}).get(resolved) or {})
        payload = {resolved: {**current_category, "push": proposed}}
    system.update_preferences(int(user_id), payload)
    return ToolResult(
        ok=True,
        tool_name="pulsesoc.notification_preferences.update",
        capability_id="notifications.preference.update",
        canonical_resource_id=f"user:{int(user_id)}:{resolved}",
        data={"category": category, "push": proposed, "previous": observed_before},
        latency_ms=_timed(started),
    )


# ---------------------------------------------------------------------------
# Saved content — backed by services.saved_content_service
# ---------------------------------------------------------------------------


def saved_items_list(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services import saved_content_service

    records = saved_content_service.list_saved_items(
        int(user_id),
        content_type=clean(arguments.get("content_type") or "all", 40),
        query=clean(arguments.get("query"), 120),
        limit=int(arguments.get("limit") or 20),
    )
    return ToolResult(
        ok=True,
        tool_name="pulsesoc.saved_items.list",
        capability_id="saved.items.list",
        canonical_resource_id=f"user:{int(user_id)}:saved",
        data={"count": len(records), "content_type": clean(arguments.get("content_type") or "all", 40)},
        records=records,
        latency_ms=_timed(started),
    )


def saved_post_set(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.saved_content_service import set_post_saved

    post_id = int(arguments.get("post_id") or 0)
    desired = bool(arguments.get("saved"))
    outcome = set_post_saved(int(user_id), post_id, saved=desired)
    if not outcome.get("ok"):
        return _fail(
            "pulsesoc.saved_posts.set",
            "saved.post.set",
            clean(outcome.get("error") or "write_rejected", 80),
            "UNDX could not find that post or change its Saved state.",
            started=started,
        )
    return ToolResult(
        ok=True,
        tool_name="pulsesoc.saved_posts.set",
        capability_id="saved.post.set",
        canonical_resource_id=f"post:{int(outcome['post_id'])}",
        data={
            "post_id": int(outcome["post_id"]),
            "saved": bool(outcome["saved"]),
            "changed": bool(outcome.get("changed")),
        },
        latency_ms=_timed(started),
    )


def social_relationships_list(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.social_relationship_service import list_relationships

    direction = clean(arguments.get("direction") or "followers", 20).lower()
    records = list_relationships(
        int(user_id),
        direction=direction,
        query=clean(arguments.get("query"), 120),
        limit=int(arguments.get("limit") or 20),
    )
    return ToolResult(
        ok=True,
        tool_name="pulsesoc.relationships.list",
        capability_id="social.followers.list",
        canonical_resource_id=f"user:{int(user_id)}:{direction}",
        records=records,
        data={"direction": direction, "record_count": len(records)},
        latency_ms=_timed(started),
    )


def _set_following(
    user_id: int,
    arguments: dict[str, Any],
    *,
    following: bool,
    capability_id: str,
    tool_name: str,
) -> ToolResult:
    started = time.perf_counter()
    from services.social_relationship_service import set_following

    target_id = int(arguments.get("target_user_id") or 0)
    outcome = set_following(int(user_id), target_id, following=following)
    if not outcome.get("ok"):
        return _fail(
            tool_name,
            capability_id,
            clean(outcome.get("error") or "write_rejected", 80),
            "UNDX could not change that follow relationship.",
            started=started,
        )
    return ToolResult(
        ok=True,
        tool_name=tool_name,
        capability_id=capability_id,
        canonical_resource_id=f"follow:{int(user_id)}:{target_id}",
        data={
            "target_user_id": target_id,
            "following": bool(outcome["following"]),
            "changed": bool(outcome.get("changed")),
        },
        latency_ms=_timed(started),
    )


def social_follow(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    return _set_following(
        user_id, arguments, following=True,
        capability_id="social.follow", tool_name="pulsesoc.relationships.follow",
    )


def social_unfollow(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    return _set_following(
        user_id, arguments, following=False,
        capability_id="social.unfollow", tool_name="pulsesoc.relationships.unfollow",
    )


def conversations_list(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.messenger_intelligence_service import list_my_conversations

    records = list_my_conversations(
        int(user_id),
        conversation_type=clean(arguments.get("conversation_type") or "all", 40),
        limit=int(arguments.get("limit") or 20),
    )
    return ToolResult(
        ok=True,
        tool_name="pulsesoc.conversations.list",
        capability_id="conversations.list",
        canonical_resource_id=f"user:{int(user_id)}:conversations",
        records=records,
        data={"record_count": len(records)},
        latency_ms=_timed(started),
    )


def messages_list(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.messenger_intelligence_service import list_conversation_messages

    conversation_id = int(arguments.get("conversation_id") or 0)
    records = list_conversation_messages(
        int(user_id), conversation_id, limit=int(arguments.get("limit") or 30),
    )
    return ToolResult(
        ok=True,
        tool_name="pulsesoc.messages.list",
        capability_id="messages.list",
        canonical_resource_id=f"conversation:{conversation_id}:messages",
        records=records,
        data={"record_count": len(records), "conversation_id": conversation_id},
        latency_ms=_timed(started),
    )

def messages_search(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.messenger_intelligence_service import search_messages

    conversation_id = int(arguments.get("conversation_id") or 0)
    records = search_messages(
        int(user_id), clean(arguments.get("query"), 120),
        conversation_id=conversation_id, limit=int(arguments.get("limit") or 30),
    )
    return ToolResult(
        ok=True, tool_name="pulsesoc.messages.search", capability_id="messages.search",
        canonical_resource_id=f"user:{int(user_id)}:message-search",
        records=records,
        data={"record_count": len(records), "conversation_id": conversation_id},
        latency_ms=_timed(started),
    )


def conversation_summarize(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.messenger_intelligence_service import summarize_conversation

    conversation_id = int(arguments.get("conversation_id") or 0)
    record = summarize_conversation(
        int(user_id), conversation_id, limit=int(arguments.get("limit") or 50),
    )
    if not record:
        return _fail(
            "pulsesoc.conversations.summarize", "conversations.summarize", "not_found",
            "UNDX could not summarize a conversation you are allowed to view.", started=started,
        )
    return ToolResult(
        ok=True, tool_name="pulsesoc.conversations.summarize",
        capability_id="conversations.summarize",
        canonical_resource_id=f"conversation:{conversation_id}",
        records=[record], data=record, latency_ms=_timed(started),
    )


def messages_suggest(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.messenger_intelligence_service import suggested_responses

    conversation_id = int(arguments.get("conversation_id") or 0)
    records = suggested_responses(int(user_id), conversation_id)
    return ToolResult(
        ok=True, tool_name="pulsesoc.messages.suggest", capability_id="messages.suggest",
        canonical_resource_id=f"conversation:{conversation_id}:suggestions",
        records=records, data={"conversation_id": conversation_id, "record_count": len(records)},
        latency_ms=_timed(started),
    )


def message_draft(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.messenger_intelligence_service import prepare_reply_draft

    conversation_id = int(arguments.get("conversation_id") or 0)
    record = prepare_reply_draft(int(user_id), conversation_id, clean(arguments.get("body"), 2000))
    if not record:
        return _fail(
            "pulsesoc.messages.draft", "messages.draft", "not_found",
            "UNDX could not prepare a draft for that conversation.", started=started,
        )
    return ToolResult(
        ok=True, tool_name="pulsesoc.messages.draft", capability_id="messages.draft",
        canonical_resource_id=clean(record.get("draft_id"), 100),
        records=[record], data=record, latency_ms=_timed(started),
    )


def feed_posts_list(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.feed_intelligence_service import list_posts

    records = list_posts(
        int(user_id),
        feed=clean(arguments.get("feed") or "for_you", 40),
        query=clean(arguments.get("query"), 80),
        limit=int(arguments.get("limit") or 20),
    )
    return ToolResult(
        ok=True, tool_name="pulsesoc.feed.posts.list", capability_id="feed.posts.list",
        canonical_resource_id=f"user:{int(user_id)}:feed", records=records,
        data={"record_count": len(records)}, latency_ms=_timed(started),
    )


def feed_posts_get(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.feed_intelligence_service import get_post

    post_id = int(arguments.get("post_id") or 0)
    record = get_post(int(user_id), post_id)
    if not record:
        return _fail("pulsesoc.feed.posts.get", "feed.posts.get", "not_found",
                     "UNDX could not find a post you are allowed to view.", started=started)
    return ToolResult(
        ok=True, tool_name="pulsesoc.feed.posts.get", capability_id="feed.posts.get",
        canonical_resource_id=f"post:{post_id}", records=[record], data=record,
        latency_ms=_timed(started),
    )


def feed_comments_list(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.feed_intelligence_service import list_post_comments

    post_id = int(arguments.get("post_id") or 0)
    records = list_post_comments(
        int(user_id), post_id, limit=int(arguments.get("limit") or 40),
    )
    return ToolResult(
        ok=True, tool_name="pulsesoc.feed.comments.list", capability_id="comments.list",
        canonical_resource_id=f"post:{post_id}:comments", records=records,
        data={"post_id": post_id, "record_count": len(records)}, latency_ms=_timed(started),
    )

def feed_post_performance_summary(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.feed_intelligence_service import post_performance_summary

    post_id = int(arguments.get("post_id") or 0)
    record = post_performance_summary(int(user_id), post_id)
    if not record:
        return _fail(
            "pulsesoc.feed.post.performance.summary", "feed.post.performance.summary",
            "not_found", "UNDX could not find one of your posts with that ID.", started=started,
        )
    return ToolResult(
        ok=True, tool_name="pulsesoc.feed.post.performance.summary",
        capability_id="feed.post.performance.summary",
        canonical_resource_id=f"post:{post_id}", records=[record], data=record,
        latency_ms=_timed(started),
    )


def feed_comments_summary(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.feed_intelligence_service import summarize_post_comments

    post_id = int(arguments.get("post_id") or 0)
    record = summarize_post_comments(
        int(user_id), post_id, limit=int(arguments.get("limit") or 40),
    )
    if not record:
        return _fail(
            "pulsesoc.feed.comments.summary", "feed.comments.summary",
            "not_found", "UNDX could not summarize comments for a post you own.", started=started,
        )
    return ToolResult(
        ok=True, tool_name="pulsesoc.feed.comments.summary",
        capability_id="feed.comments.summary",
        canonical_resource_id=f"post:{post_id}:comment-summary",
        records=[record], data=record, latency_ms=_timed(started),
    )

def _set_post_like(
    user_id: int,
    arguments: dict[str, Any],
    *,
    liked: bool,
    capability_id: str,
    tool_name: str,
) -> ToolResult:
    started = time.perf_counter()
    from services.feed_intelligence_service import set_post_like

    post_id = int(arguments.get("post_id") or 0)
    outcome = set_post_like(int(user_id), post_id, liked=liked)
    if not outcome.get("ok"):
        return _fail(
            tool_name, capability_id, clean(outcome.get("error") or "write_rejected", 80),
            "UNDX could not change your reaction on that post.", started=started,
        )
    return ToolResult(
        ok=True, tool_name=tool_name, capability_id=capability_id,
        canonical_resource_id=f"post:{post_id}",
        data={
            "post_id": post_id,
            "liked": bool(outcome["liked"]),
            "changed": bool(outcome.get("changed")),
        },
        latency_ms=_timed(started),
    )


def feed_post_like(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    return _set_post_like(
        user_id, arguments, liked=True,
        capability_id="feed.posts.like", tool_name="pulsesoc.feed.posts.like",
    )


def feed_post_unlike(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    return _set_post_like(
        user_id, arguments, liked=False,
        capability_id="feed.posts.unlike", tool_name="pulsesoc.feed.posts.unlike",
    )


def feed_post_delete(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.pulse_feed_engine import delete_owned_post

    post_id = int(arguments.get("post_id") or 0)
    outcome = delete_owned_post(int(user_id), post_id)
    if not outcome.get("ok"):
        return _fail(
            "pulsesoc.feed.posts.delete", "feed.posts.delete",
            clean(outcome.get("error") or "write_rejected", 80),
            "UNDX could not delete a matching post owned by your account.",
            started=started,
        )
    return ToolResult(
        ok=True,
        tool_name="pulsesoc.feed.posts.delete",
        capability_id="feed.posts.delete",
        canonical_resource_id=f"post:{post_id}",
        data={"post_id": post_id, "deleted": True, "changed": bool(outcome.get("changed"))},
        latency_ms=_timed(started),
    )


def _content_read(user_id: int, arguments: dict[str, Any], capability: str, function: str,
                  canonical_key: str) -> ToolResult:
    started = time.perf_counter()
    from services import content_graph_intelligence_service as graph
    call = getattr(graph, function)
    kwargs = {key: value for key, value in arguments.items() if not key.startswith("_")}
    value = call(int(user_id), **kwargs)
    records = value if isinstance(value, list) else ([value] if value else [])
    if value is None:
        return _fail(f"pulsesoc.{capability}", capability, "not_found",
                     "UNDX could not find an authorized matching item.", started=started)
    target = 0
    if records and canonical_key:
        target = records[0].get(canonical_key) or 0
    return ToolResult(
        ok=True, tool_name=f"pulsesoc.{capability}", capability_id=capability,
        canonical_resource_id=f"{canonical_key.removesuffix('_id')}:{target}" if target else f"user:{int(user_id)}",
        records=records, data=value if isinstance(value, dict) else {"count": len(records)},
        latency_ms=_timed(started),
    )


def reels_search(u, a): return _content_read(u, a, "reels.search", "list_reels", "reel_id")
def reels_get(u, a): return _content_read(u, a, "reels.get", "get_reel", "reel_id")
def reels_performance(u, a): return _content_read(u, a, "reels.performance.summary", "reel_performance", "reel_id")
def reels_comments_summary(u, a): return _content_read(u, a, "reels.comments.summary", "reel_comment_summary", "reel_id")
def statuses_list(u, a): return _content_read(u, a, "status.list", "list_statuses", "status_id")
def statuses_get(u, a): return _content_read(u, a, "status.get", "get_status", "status_id")
def status_viewers(u, a): return _content_read(u, a, "status.viewer.summary", "status_viewer_summary", "status_id")
def status_reactions(u, a): return _content_read(u, a, "status.reaction.summary", "status_reaction_summary", "status_id")
def profile_get(u, a): return _content_read(u, a, "profile.get", "get_profile", "user_id")
def profile_activity(u, a): return _content_read(u, a, "profile.activity.summary", "profile_activity_summary", "user_id")
def profile_relationships(u, a): return _content_read(u, a, "profile.relationship.summary", "profile_relationship_summary", "user_id")


def profile_preferences_update(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.content_graph_intelligence_service import update_profile_preferences
    outcome = update_profile_preferences(
        int(user_id), preferred_language=clean(arguments.get("preferred_language"), 8),
    )
    if not outcome.get("ok"):
        return _fail("pulsesoc.profile.preferences.update", "profile.preferences.update",
                     clean(outcome.get("error"), 80), "UNDX could not update that preference.",
                     started=started)
    return ToolResult(
        ok=True, tool_name="pulsesoc.profile.preferences.update",
        capability_id="profile.preferences.update", canonical_resource_id=f"user:{int(user_id)}",
        data=outcome, latency_ms=_timed(started),
    )


def _reel_write(user_id: int, arguments: dict[str, Any], capability: str, function: str,
                desired: bool) -> ToolResult:
    started = time.perf_counter()
    from services import content_graph_intelligence_service as graph
    reel_id = int(arguments.get("reel_id") or 0)
    outcome = getattr(graph, function)(int(user_id), reel_id, **{
        "saved" if "save" in capability else "liked": desired,
    })
    if not outcome.get("ok"):
        return _fail(f"pulsesoc.{capability}", capability, clean(outcome.get("error"), 80),
                     "UNDX could not update that Reel.", started=started)
    return ToolResult(
        ok=True, tool_name=f"pulsesoc.{capability}", capability_id=capability,
        canonical_resource_id=f"reel:{reel_id}",
        data={"reel_id": reel_id, "saved" if "save" in capability else "liked": desired,
              "changed": bool(outcome.get("changed"))}, latency_ms=_timed(started),
    )


def reels_save(u, a): return _reel_write(u, a, "reels.save", "set_reel_saved", True)
def reels_unsave(u, a): return _reel_write(u, a, "reels.unsave", "set_reel_saved", False)
def reels_like(u, a): return _reel_write(u, a, "reels.like", "set_reel_liked", True)
def reels_unlike(u, a): return _reel_write(u, a, "reels.unlike", "set_reel_liked", False)


# ---------------------------------------------------------------------------
# Phase 3B personal intelligence (read-only)
# ---------------------------------------------------------------------------

def _personal_read(user_id: int, arguments: dict[str, Any], capability: str,
                   function: str, canonical_key: str = "") -> ToolResult:
    started = time.perf_counter()
    from services import undx_personal_intelligence_service as personal
    kwargs = {key: value for key, value in arguments.items() if not key.startswith("_")}
    # Every personal read runs inside a degradation collector, not only the read
    # models that opened one of their own. A read whose SQL raised returns [] and is
    # otherwise indistinguishable from a genuinely empty result, so without this the
    # gateway would stamp a broken query 'verified' and UNDX would report an
    # authoritative nothing. The names collected here travel on the result and are
    # what stop the gateway calling a degraded read verified.
    with personal.collecting() as degraded:
        value = getattr(personal, function)(int(user_id), **kwargs)
        degraded_sources = sorted(degraded)
    if value is None:
        return _fail(f"pulsesoc.{capability}", capability, "not_found",
                     "UNDX could not find an authorized matching item.", started=started)
    records = value if isinstance(value, list) else (
        list(value.get("items") or value.get("facts") or value.get("campaigns") or [])
        if isinstance(value, dict) else []
    )
    canonical = f"user:{int(user_id)}"
    if canonical_key and isinstance(value, dict):
        target = value.get(canonical_key) or (value.get("data") or {}).get(canonical_key)
        if target:
            canonical = f"{canonical_key.removesuffix('_id')}:{target}"
    data = value if isinstance(value, dict) else {"count": len(records), "items": records}
    if degraded_sources:
        # Written unconditionally rather than merged, so a read model that computed
        # its own optimistic 'complete' cannot outvote an observed failure.
        data = dict(data)
        data["complete"] = False
        data["degraded_sources"] = degraded_sources
    return ToolResult(
        ok=True, tool_name=f"pulsesoc.{capability}", capability_id=capability,
        canonical_resource_id=canonical, records=records,
        data=data, degraded_sources=degraded_sources,
        latency_ms=_timed(started),
    )


def activity_daily_summary(u, a): return _personal_read(u, a, "activity.daily_summary", "activity_daily_summary")
def notifications_inbox_list(u, a): return _personal_read(u, a, "notifications.inbox.list", "notifications_inbox")
def notifications_explain(u, a): return _personal_read(u, a, "notifications.explain", "notification_explain", "notification_id")
def notifications_group_summary(u, a): return _personal_read(u, a, "notifications.group_summary", "notification_group_summary")
def search_global(u, a): return _personal_read(u, a, "search.global", "search_global")
def search_people(u, a): return _personal_read(u, a, "search.people", "search_people")
def search_content(u, a): return _personal_read(u, a, "search.content", "search_content")
def search_messages(u, a): return _personal_read(u, a, "search.messages", "search_messages")
def search_activity(u, a): return _personal_read(u, a, "search.activity", "search_activity")
def settings_inspect(u, a): return _personal_read(u, a, "settings.inspect", "settings_inspect")
def settings_explain(u, a): return _personal_read(u, a, "settings.explain", "settings_explain")
def settings_recommend(u, a): return _personal_read(u, a, "settings.recommend", "settings_recommend")
def security_sessions_list(u, a): return _personal_read(u, a, "security.sessions.list", "security_sessions")
def security_activity_summary(u, a): return _personal_read(u, a, "security.activity.summary", "security_activity_summary")
def security_device_list(u, a): return _personal_read(u, a, "security.device.list", "security_devices")
def marketplace_search(u, a): return _personal_read(u, a, "marketplace.search", "marketplace_search")
def marketplace_listing_summary(u, a): return _personal_read(u, a, "marketplace.listing.summary", "marketplace_listing_summary", "listing_id")
def marketplace_order_status(u, a): return _personal_read(u, a, "marketplace.order.status", "marketplace_order_status", "order_id")
def premium_status(u, a): return _personal_read(u, a, "premium.status", "premium_status")
def premium_entitlements(u, a): return _personal_read(u, a, "premium.entitlements", "premium_entitlements")
def ads_performance_summary(u, a): return _personal_read(u, a, "ads.performance.summary", "ads_performance_summary")
def live_search(u, a): return _personal_read(u, a, "live.search", "live_search")
def live_summary(u, a): return _personal_read(u, a, "live.summary", "live_summary", "live_id")
def live_performance(u, a): return _personal_read(u, a, "live.performance", "live_performance", "live_id")
def learning_search(u, a): return _personal_read(u, a, "learning.search", "learning_search")
def learning_progress(u, a): return _personal_read(u, a, "learning.progress", "learning_progress")
def memory_activity_inspect(u, a): return _personal_read(u, a, "memory.activity.inspect", "memory_activity_inspect")
def groups_list(u, a): return _personal_read(u, a, "groups.list", "groups_list")
def groups_search(u, a): return _personal_read(u, a, "groups.search", "groups_search")
def events_upcoming(u, a): return _personal_read(u, a, "events.upcoming", "events_upcoming")
def music_search(u, a): return _personal_read(u, a, "music.search", "music_search")
def account_health_summary(u, a): return _personal_read(u, a, "account.health.summary", "account_health_summary")
def verification_status(u, a): return _personal_read(u, a, "verification.status", "verification_status")
def support_tickets_list(u, a): return _personal_read(u, a, "support.tickets.list", "support_tickets_list")
def creator_analytics_summary(u, a): return _personal_read(u, a, "creator.analytics.summary", "creator_analytics_summary")
def localization_preferences(u, a): return _personal_read(u, a, "localization.preferences", "localization_preferences")
# No `crypto_portfolio_summary` delegate here. The real one is defined above and
# opens with `_premium_denial(user_id, "CAP_CRYPTO_PORTFOLIO")`; a one-line
# `_personal_read` delegate at this point in the file would rebind the name and
# win, because both dispatch dicts below are built after this line. The result
# is not a crash or a failing test — it is a paid capability answering for free,
# which is the one failure mode this module cannot show you.
def crypto_market_window(u, a): return _personal_read(u, a, "crypto.market.window", "crypto_market_window")


def translation_content_translate(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.content_translation import TranslationError, translate_content

    content_ref = int(arguments.get("content_ref") or 0)
    try:
        outcome = translate_content(
            int(user_id),
            content_type=clean(arguments.get("content_type"), 24),
            content_ref=content_ref,
            text="",  # The service resolves canonical text and ignores caller text.
            source_language=clean(arguments.get("source_language") or "auto", 16),
            target_language=clean(arguments.get("target_language"), 16),
            force=True,
        )
    except TranslationError as exc:
        return _fail(
            "pulsesoc.translation.content.translate", "translation.content.translate",
            clean(exc.code, 80), clean(str(exc), 200), started=started,
        )
    return ToolResult(
        ok=True,
        tool_name="pulsesoc.translation.content.translate",
        capability_id="translation.content.translate",
        canonical_resource_id=f"{clean(arguments.get('content_type'), 24)}:{content_ref}",
        data={key: outcome.get(key) for key in (
            "status", "original_text", "translated_text", "source_language",
            "target_language", "provider", "provider_model", "content_version", "cached",
        )},
        latency_ms=_timed(started),
    )
def presence_privacy_status(u, a): return _personal_read(u, a, "presence.privacy.status", "presence_privacy_status")


# ---------------------------------------------------------------------------
# Stage 6 agentic actions
#
# Every executor below reaches a service function that already scopes its own SQL
# by the acting user id. None of them accepts a field naming whose data to touch —
# the gateway refuses a ``self_account_only`` capability that declares one — so the
# account acted on is the authenticated one and no argument can move it.
# ---------------------------------------------------------------------------


def _portfolio():
    from services import portfolio_service

    return portfolio_service


def _symbol(arguments: dict[str, Any]) -> str:
    return clean(arguments.get("symbol"), 16).upper()


def crypto_watchlist_list(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    symbols = sorted(_portfolio().watchlist_symbols(int(user_id)))
    return ToolResult(
        ok=True, tool_name="pulsesoc.crypto.watchlist.list",
        capability_id="crypto.watchlist.list",
        canonical_resource_id=f"user:{int(user_id)}",
        records=[{"symbol": symbol} for symbol in symbols],
        data={"count": len(symbols), "symbols": symbols},
        latency_ms=_timed(started),
    )


def crypto_watchlist_add(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    symbol = _symbol(arguments)
    outcome = _portfolio().add_watchlist_item(int(user_id), symbol) or {}
    if not outcome.get("ok"):
        # A plan limit refusal arrives here. It is a real product rule, not a fault,
        # so it is reported with the service's own sentence rather than a generic
        # failure — "you are at your watchlist limit" is actionable and "that did not
        # work" is not.
        return _fail("pulsesoc.crypto.watchlist.add", "crypto.watchlist.add",
                     clean(outcome.get("code") or "write_rejected", 60),
                     clean(outcome.get("message") or "PulseSoc did not accept that change.", 200),
                     started=started)
    return ToolResult(
        ok=True, tool_name="pulsesoc.crypto.watchlist.add",
        capability_id="crypto.watchlist.add",
        canonical_resource_id=f"watchlist:{symbol}",
        data={"symbol": symbol}, latency_ms=_timed(started),
    )


def crypto_watchlist_remove(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    symbol = _symbol(arguments)
    portfolio = _portfolio()
    # Symbol to row id happens here, against this account, rather than the caller
    # passing an id. A model that proposed ``item_id`` could name a row belonging to
    # anyone; a model that proposes ``BTC`` can only ever name one of the caller's own.
    item_id = portfolio.watchlist_item_id(int(user_id), symbol)
    if not item_id:
        if symbol in portfolio.watchlist_symbols(int(user_id)):
            return _fail("pulsesoc.crypto.watchlist.remove", "crypto.watchlist.remove",
                         "legacy_row_unremovable",
                         "That coin is on an older watchlist record UNDX cannot remove. "
                         "Remove it in PulseSoc instead.", started=started)
        return _fail("pulsesoc.crypto.watchlist.remove", "crypto.watchlist.remove",
                     "resource_not_found", "That coin is not on your watchlist.",
                     started=started)
    outcome = portfolio.delete_watchlist_item(int(user_id), item_id) or {}
    if not outcome.get("ok"):
        return _fail("pulsesoc.crypto.watchlist.remove", "crypto.watchlist.remove",
                     "write_rejected",
                     clean(outcome.get("message") or "PulseSoc did not accept that change.", 200),
                     retryable=True, started=started)
    return ToolResult(
        ok=True, tool_name="pulsesoc.crypto.watchlist.remove",
        capability_id="crypto.watchlist.remove",
        canonical_resource_id=f"watchlist:{symbol}",
        data={"symbol": symbol}, latency_ms=_timed(started),
    )


_HOLDING_FIELDS = ("symbol", "coin_name", "amount", "average_buy_price", "notes")


def crypto_portfolio_holdings_list(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    rows = _portfolio().list_portfolio_items(int(user_id))
    records = [_project(row, ("id",) + _HOLDING_FIELDS, str_limit=120) for row in rows]
    return ToolResult(
        ok=True, tool_name="pulsesoc.crypto.portfolio.holdings.list",
        capability_id="crypto.portfolio.holdings.list",
        canonical_resource_id=f"user:{int(user_id)}",
        records=records, data={"count": len(records), "items": records},
        latency_ms=_timed(started),
    )


def crypto_portfolio_holding_add(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    symbol = _symbol(arguments)
    portfolio = _portfolio()
    outcome = portfolio.add_portfolio_item(
        int(user_id), symbol,
        coin_name=symbol,
        amount=float(arguments.get("amount") or 0),
        average_buy_price=float(arguments.get("average_buy_price") or 0),
    ) or {}
    if not outcome.get("ok"):
        return _fail("pulsesoc.crypto.portfolio.holding.add", "crypto.portfolio.holding.add",
                     clean(outcome.get("code") or "write_rejected", 60),
                     clean(outcome.get("message") or "PulseSoc did not accept that holding.", 200),
                     started=started)
    # The service does not return the row it created, so the id is read back here.
    # Without it the receipt would carry no canonical resource id, and the Undo
    # affordance would be offered with nothing to delete — which is worse than
    # offering no Undo at all.
    created = next((row for row in portfolio.list_portfolio_items(int(user_id))
                    if str(row.get("symbol") or "").upper() == symbol), None)
    return ToolResult(
        ok=True, tool_name="pulsesoc.crypto.portfolio.holding.add",
        capability_id="crypto.portfolio.holding.add",
        canonical_resource_id=f"portfolio_item:{int((created or {}).get('id') or 0)}",
        data={"symbol": symbol, "amount": float(arguments.get("amount") or 0),
              "average_buy_price": float(arguments.get("average_buy_price") or 0)},
        latency_ms=_timed(started),
    )


def crypto_portfolio_holding_update(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    item_id = int(arguments.get("item_id") or 0)
    portfolio = _portfolio()
    if portfolio.get_portfolio_item(int(user_id), item_id) is None:
        # A holding owned by somebody else and a holding that does not exist give the
        # same refusal, so the capability cannot be used to discover whose rows exist.
        return _fail("pulsesoc.crypto.portfolio.holding.update",
                     "crypto.portfolio.holding.update", "resource_not_found",
                     "UNDX could not find that holding on your account.", started=started)
    patch = {key: arguments[key] for key in ("amount", "average_buy_price") if key in arguments}
    outcome = portfolio.update_portfolio_item(int(user_id), item_id, patch) or {}
    if not outcome.get("ok"):
        return _fail("pulsesoc.crypto.portfolio.holding.update",
                     "crypto.portfolio.holding.update", "write_rejected",
                     clean(outcome.get("message") or "PulseSoc did not accept that change.", 200),
                     retryable=True, started=started)
    return ToolResult(
        ok=True, tool_name="pulsesoc.crypto.portfolio.holding.update",
        capability_id="crypto.portfolio.holding.update",
        canonical_resource_id=f"portfolio_item:{item_id}",
        data={"item_id": item_id, **patch}, latency_ms=_timed(started),
    )


def crypto_portfolio_holding_delete(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    item_id = int(arguments.get("item_id") or 0)
    portfolio = _portfolio()
    if portfolio.get_portfolio_item(int(user_id), item_id) is None:
        return _fail("pulsesoc.crypto.portfolio.holding.delete",
                     "crypto.portfolio.holding.delete", "resource_not_found",
                     "UNDX could not find that holding on your account.", started=started)
    outcome = portfolio.delete_portfolio_item(int(user_id), item_id) or {}
    if not outcome.get("ok"):
        return _fail("pulsesoc.crypto.portfolio.holding.delete",
                     "crypto.portfolio.holding.delete", "write_rejected",
                     clean(outcome.get("message") or "PulseSoc did not accept that deletion.", 200),
                     retryable=True, started=started)
    return ToolResult(
        ok=True, tool_name="pulsesoc.crypto.portfolio.holding.delete",
        capability_id="crypto.portfolio.holding.delete",
        canonical_resource_id=f"portfolio_item:{item_id}",
        data={"item_id": item_id}, latency_ms=_timed(started),
    )


def notifications_mark_read(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    notifications = _notifications()
    notification_id = int(arguments.get("notification_id") or 0)
    if notifications.get_notification(int(user_id), notification_id) is None:
        return _fail("pulsesoc.notifications.mark_read", "notifications.mark_read",
                     "resource_not_found",
                     "UNDX could not find that notification on your account.", started=started)
    outcome = notifications.mark_read(int(user_id), notification_id) or {}
    if not outcome.get("ok"):
        return _fail("pulsesoc.notifications.mark_read", "notifications.mark_read",
                     "write_rejected", "PulseSoc did not accept that change.",
                     retryable=True, started=started)
    return ToolResult(
        ok=True, tool_name="pulsesoc.notifications.mark_read",
        capability_id="notifications.mark_read",
        canonical_resource_id=f"notification:{notification_id}",
        data={"notification_id": notification_id,
              "unread_count": int(outcome.get("unread_count") or 0)},
        latency_ms=_timed(started),
    )


def notifications_mark_all_read(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    category = resolve_category(clean(arguments.get("category") or "global", 24))
    # ``global`` is this registry's word for "everything"; the notification service
    # spells the same idea ``all``. Translating here rather than widening the field's
    # enum keeps one vocabulary in front of the user.
    scope = "all" if category == "global" else category
    outcome = _notifications().mark_all_read(int(user_id), scope) or {}
    if not outcome.get("ok"):
        return _fail("pulsesoc.notifications.mark_all_read", "notifications.mark_all_read",
                     "write_rejected", "PulseSoc did not accept that change.",
                     retryable=True, started=started)
    return ToolResult(
        ok=True, tool_name="pulsesoc.notifications.mark_all_read",
        capability_id="notifications.mark_all_read",
        canonical_resource_id=f"user:{int(user_id)}:{category}",
        data={"category": category, "updated": int(outcome.get("updated") or 0),
              "unread_count": int(outcome.get("unread_count") or 0)},
        latency_ms=_timed(started),
    )


def presence_privacy_update(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services import db as db_service
    from services import presence_service

    setting = clean(arguments.get("setting"), 32)
    enabled = bool(arguments.get("enabled"))
    conn = db_service.connect()
    try:
        outcome = presence_service.set_privacy(
            conn.cursor(), int(user_id),
            **{setting: enabled}, conn=conn)
        conn.commit()
    except Exception as exc:  # pragma: no cover - defensive
        conn.rollback()
        return _fail("pulsesoc.presence.privacy.update", "presence.privacy.update",
                     "write_rejected",
                     f"PulseSoc did not accept that change ({exc.__class__.__name__}).",
                     retryable=True, started=started)
    finally:
        conn.close()
    return ToolResult(
        ok=True, tool_name="pulsesoc.presence.privacy.update",
        capability_id="presence.privacy.update",
        canonical_resource_id=f"user:{int(user_id)}:{setting}",
        data={"setting": setting, "enabled": bool(outcome.get(setting))},
        latency_ms=_timed(started),
    )


def localization_region_update(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.pulse_region_preferences import RegionPreferenceError, update_preferences

    setting = clean(arguments.get("setting"), 32)
    value = clean(arguments.get("value"), 64)
    try:
        update_preferences(int(user_id), {setting: value})
    except RegionPreferenceError as exc:
        return _fail("pulsesoc.localization.region.update", "localization.region.update",
                     clean(getattr(exc, "code", "") or "write_rejected", 60),
                     clean(str(exc) or "PulseSoc did not accept that region preference.", 200),
                     started=started)
    return ToolResult(
        ok=True, tool_name="pulsesoc.localization.region.update",
        capability_id="localization.region.update",
        canonical_resource_id=f"user:{int(user_id)}:{setting}",
        data={"setting": setting, "value": value}, latency_ms=_timed(started),
    )


def localization_translation_update(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services.content_translation import TranslationError, set_preference

    target = clean(arguments.get("target_language"), 16)
    policy = clean(arguments.get("policy"), 16)
    try:
        set_preference(int(user_id), "auto", target, policy)
    except TranslationError as exc:
        return _fail("pulsesoc.localization.translation.update",
                     "localization.translation.update",
                     clean(getattr(exc, "code", "") or "write_rejected", 60),
                     clean(str(exc) or "PulseSoc did not accept that translation preference.", 200),
                     started=started)
    return ToolResult(
        ok=True, tool_name="pulsesoc.localization.translation.update",
        capability_id="localization.translation.update",
        canonical_resource_id=f"user:{int(user_id)}:{target}",
        data={"target_language": target, "policy": policy}, latency_ms=_timed(started),
    )


#: Preference groups an agent capability may write. ``security`` is absent by
#: construction and must stay absent: two-factor, biometric unlock and the
#: "require password for sensitive changes" switch all live there, and the mission
#: brief puts credential and MFA changes outside what any agent may do. A capability
#: that reached them would be a privilege escalation wearing a settings receipt.
SETTINGS_WRITABLE_GROUPS: frozenset[str] = frozenset({"appearance", "privacy"})


def _settings_patch(user_id: int, group: str, patch: dict[str, Any], *,
                    capability: str, started: float) -> ToolResult:
    """Merge one bounded patch into the stored preference document.

    Runs the same three steps as the HTTP route — load, merge, save — through the
    same functions, so the normalisation and side effects a person's own settings
    write goes through are the ones an agent write goes through too. The group
    allowlist is checked here as well as by the field enum, because the enum
    protects the argument and this protects the call.
    """
    from services import db as db_service
    from services.pulse_settings_routes import (
        load_preferences, merge_preferences, save_preferences,
    )

    if group not in SETTINGS_WRITABLE_GROUPS:
        return _fail(f"pulsesoc.{capability}", capability, "group_not_writable",
                     "UNDX cannot change that group of settings.", started=started)
    conn = db_service.connect()
    try:
        cur = conn.cursor()
        stored, revision, _ = load_preferences(cur, int(user_id))
        merged = merge_preferences(stored, {group: patch})
        save_preferences(cur, int(user_id), merged, revision)
        conn.commit()
    except Exception as exc:  # pragma: no cover - defensive
        conn.rollback()
        return _fail(f"pulsesoc.{capability}", capability, "write_rejected",
                     f"PulseSoc did not accept that change ({exc.__class__.__name__}).",
                     retryable=True, started=started)
    finally:
        conn.close()
    return ToolResult(
        ok=True, tool_name=f"pulsesoc.{capability}", capability_id=capability,
        canonical_resource_id=f"user:{int(user_id)}:{group}",
        data={"group": group, **patch}, latency_ms=_timed(started),
    )


def settings_privacy_audience_update(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    setting = clean(arguments.get("setting"), 40)
    audience = clean(arguments.get("audience"), 24)
    return _settings_patch(int(user_id), "privacy", {setting: audience},
                           capability="settings.privacy.audience.update", started=started)


def settings_appearance_theme_update(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    theme = clean(arguments.get("theme"), 16)
    return _settings_patch(int(user_id), "appearance", {"theme": theme},
                           capability="settings.appearance.theme.update", started=started)


# ---------------------------------------------------------------------------
# Feed — viewer-scoped hide
# ---------------------------------------------------------------------------


def feed_post_hide(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    """Hide one other account's post from this account's Home feed.

    Viewer-scoped by construction: ``hide_post`` writes ``pulse_post_hides`` keyed
    on (viewer, post) and refuses the caller's own post with a 400. Nothing about
    the post itself changes, and no other account's feed is touched — which is why
    this is a reversible-class preference and not a moderation action.
    """
    started = time.perf_counter()
    from services.pulse_feed_engine import hide_post

    post_id = int(arguments.get("post_id") or 0)
    payload, status = hide_post(int(user_id), post_id)
    if int(status) != 200 or not payload.get("ok"):
        code = {400: "write_rejected", 404: "not_found"}.get(int(status), "write_rejected")
        return _fail(
            "pulsesoc.feed.posts.hide", "feed.posts.hide", code,
            clean(payload.get("message") or "UNDX could not hide that post.", 200),
            started=started,
        )
    return ToolResult(
        ok=True, tool_name="pulsesoc.feed.posts.hide", capability_id="feed.posts.hide",
        canonical_resource_id=f"post:{post_id}",
        data={"post_id": post_id, "hidden": True},
        latency_ms=_timed(started),
    )


# ---------------------------------------------------------------------------
# Messaging — backed by pulse_communications_v2.service
# ---------------------------------------------------------------------------


def _comm_v2():
    from pulse_communications_v2 import service as comm_service

    return comm_service


def _comm_failure(payload: dict[str, Any], tool: str, capability: str, *,
                  fallback: str, started: float) -> ToolResult:
    """Translate a comm_v2 error envelope without leaking who exists.

    ``_conversation_access`` already collapses "no such conversation" and "not
    yours" into the same 404 upstream; this keeps that property by carrying the
    service's own code and message rather than inventing a more specific one.
    """
    status = int(payload.get("http_status") or 0)
    code = clean(payload.get("status") or "", 60)
    if payload.get("status") == "disabled":
        code = "feature_disabled"
    elif not code or code == "error":
        code = {404: "not_found", 403: "forbidden"}.get(status, "write_rejected")
    return _fail(tool, capability, code,
                 clean(payload.get("message") or fallback, 200), started=started)


def messages_mark_read(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    conversation_id = int(arguments.get("conversation_id") or 0)
    outcome = _comm_v2().mark_read(int(user_id), conversation_id)
    if not outcome.get("ok"):
        return _comm_failure(outcome, "pulsesoc.messages.mark_read", "messages.mark_read",
                             fallback="UNDX could not mark that conversation as read.",
                             started=started)
    return ToolResult(
        ok=True, tool_name="pulsesoc.messages.mark_read", capability_id="messages.mark_read",
        canonical_resource_id=f"conversation:{conversation_id}",
        data={
            "conversation_id": conversation_id,
            "unread_count": 0,
            "last_read_message_id": int(outcome.get("last_read_message_id") or 0),
        },
        latency_ms=_timed(started),
    )


def messages_send(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    """Send one message into a conversation the caller is already a member of.

    Text only, and no attachment arguments are forwarded even if present: the
    capability's field list does not declare them, and widening the payload here
    would let an argument the policy never inspected reach the writer.

    No ``client_message_id`` is supplied. Sending the same words twice is a thing
    a person legitimately does, so there is no content-derived key that is safe to
    deduplicate on; the boundary that stops one approval becoming two messages is
    the single-use confirmation row, which is a SQL property of
    ``pulse_ai_confirmations`` rather than anything this function can assert.

    Membership is checked with a *read* first, and the first sentence of this
    docstring is why. ``comm_v2.send_message`` calls ``_conversation_access`` with
    ``join_public=True``, which for a public room the caller is not in does not
    refuse — it calls ``_add_participant`` and joins them, then sends. So a send
    aimed at a public conversation would quietly enrol the person in it: a
    membership change nobody previewed, nobody confirmed, and nobody can tell
    happened from a receipt that says only "message sent". The confirmation card
    shows the words being sent; it does not show "and you will be joined to this
    room", and an action must not do a second thing its approval never described.
    ``get_conversation_read_state`` is membership-scoped in SQL, so a foreign,
    departed, or non-existent conversation all read as ``None`` and all refuse
    identically — which also keeps this from becoming an existence oracle for
    conversations the caller cannot see.
    """
    from services.messenger_intelligence_service import get_conversation_read_state

    started = time.perf_counter()
    conversation_id = int(arguments.get("conversation_id") or 0)
    body = clean(arguments.get("body"), 2000)
    if not body:
        return _fail("pulsesoc.messages.send", "messages.send", "empty_message",
                     "UNDX will not send an empty message.", started=started)
    if get_conversation_read_state(int(user_id), conversation_id) is None:
        return _fail("pulsesoc.messages.send", "messages.send", "not_found",
                     "UNDX could not find that conversation.", started=started)
    payload: dict[str, Any] = {"body": body, "message_type": "text"}
    outcome = _comm_v2().send_message(int(user_id), conversation_id, payload)
    if not outcome.get("ok"):
        return _comm_failure(outcome, "pulsesoc.messages.send", "messages.send",
                             fallback="UNDX could not send that message.",
                             started=started)
    message_id = int(outcome.get("message_id") or 0)
    return ToolResult(
        ok=True, tool_name="pulsesoc.messages.send", capability_id="messages.send",
        canonical_resource_id=f"message:{message_id}",
        data={
            "conversation_id": conversation_id,
            "message_id": message_id,
            "body": body,
            "idempotent": bool(outcome.get("idempotent")),
        },
        latency_ms=_timed(started),
    )


# ---------------------------------------------------------------------------
# Business OS — advertising operations and seller profile
# ---------------------------------------------------------------------------


def _campaign_transition(user_id: int, arguments: dict[str, Any], *, capability: str,
                         verb: str, started: float) -> ToolResult:
    """Pause or resume one owned campaign.

    Ownership is checked with a *read* first. ``pause_campaign`` reads
    ``campaign.get("advertiser_user_id")`` without a ``None`` guard, so a campaign
    the caller does not own raises ``AttributeError`` and surfaces as a 500 rather
    than the 404 the module documents. Pre-checking through
    ``get_operational_view(..., requester_user_id=...)`` — which enforces ownership
    correctly — turns that into a clean refusal without changing the service.

    This moves no money and buys no delivery: ``operational_status`` authorizes a
    future delivery worker, and the funding and review states are untouched.
    """
    from services.business_os.advertising import operations as ops
    from services.business_os.advertising.service import AdvertisingError

    tool = f"pulsesoc.{capability}"
    campaign_id = clean(arguments.get("campaign_id"), 120)
    if not campaign_id:
        return _fail(tool, capability, "invalid_request",
                     "A campaign is required.", started=started)
    try:
        ops.get_operational_view(campaign_id, requester_user_id=int(user_id))
    except AdvertisingError as exc:
        return _fail(tool, capability, clean(getattr(exc, "code", "") or "not_found", 60),
                     clean(str(exc) or "UNDX could not find that campaign on your account.", 200),
                     started=started)
    except Exception as exc:  # pragma: no cover - defensive
        return _fail(tool, capability, "read_failed",
                     f"UNDX could not read that campaign ({exc.__class__.__name__}).",
                     retryable=True, started=started)
    try:
        view = getattr(ops, f"{verb}_campaign")(campaign_id, requester_user_id=int(user_id))
    except AdvertisingError as exc:
        return _fail(tool, capability, clean(getattr(exc, "code", "") or "write_rejected", 60),
                     clean(str(exc) or "PulseSoc did not accept that campaign change.", 200),
                     started=started)
    except Exception as exc:  # pragma: no cover - defensive
        return _fail(tool, capability, "write_rejected",
                     f"PulseSoc did not accept that campaign change ({exc.__class__.__name__}).",
                     retryable=True, started=started)
    return ToolResult(
        ok=True, tool_name=tool, capability_id=capability,
        canonical_resource_id=f"campaign:{campaign_id}",
        data={
            "campaign_id": campaign_id,
            "operational_status": clean(view.get("operational_status") or "", 40),
        },
        latency_ms=_timed(started),
    )


def business_campaign_pause(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    return _campaign_transition(int(user_id), arguments,
                                capability="business.campaign.pause", verb="pause",
                                started=time.perf_counter())


def business_campaign_resume(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    return _campaign_transition(int(user_id), arguments,
                                capability="business.campaign.resume", verb="resume",
                                started=time.perf_counter())


#: Seller-profile fields an agent may write. Every one is free text that the
#: business itself authors and that appears flat in ``owner_profile``, so each has
#: an exact read-back. Deliberately absent: ``business_category``, contact details
#: and their visibility, ``preferred_contact``, ``languages``, ``accessibility``,
#: ``hours_mode`` and the public location — those change how PulseSoc routes
#: customers to a business or what it discloses about it, which is a decision for
#: the owner rather than a description an assistant can redraft.
BUSINESS_PROFILE_WRITABLE_FIELDS: tuple[str, ...] = (
    "about", "what_you_sell", "service_area", "shipping_summary",
    "return_summary", "response_expectations", "response_hours",
)


def business_profile_update(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    """Write one seller-profile text field.

    ``update_profile`` is a partial save that returns 200 even when a field was
    rejected or held for verification review, so the envelope is inspected rather
    than the status: a rejected or queued field is a failure here, because an
    agent that reported success for a change sitting in a review queue would be
    claiming an outcome the account cannot see.

    A field whose value already matches is skipped by the service and appears in
    none of the three buckets. That is a success with ``changed`` false — the
    requested state holds, and verification will confirm it independently.
    """
    started = time.perf_counter()
    from services.business_os.profile import api as profile_api

    field = clean(arguments.get("field"), 40)
    value = clean(arguments.get("value"), 600)
    if field not in BUSINESS_PROFILE_WRITABLE_FIELDS:
        return _fail("pulsesoc.business.profile.update", "business.profile.update",
                     "field_not_writable",
                     "UNDX cannot change that part of your business profile.",
                     started=started)
    status, body = profile_api.update_profile(int(user_id), {field: value})
    if int(status) != 200 or not body.get("ok"):
        return _fail("pulsesoc.business.profile.update", "business.profile.update",
                     clean(body.get("code") or "write_rejected", 60),
                     clean(body.get("error") or "PulseSoc did not accept that profile change.", 200),
                     started=started)
    rejected = body.get("rejected") or {}
    if field in rejected:
        return _fail("pulsesoc.business.profile.update", "business.profile.update",
                     "field_rejected",
                     clean(str(rejected.get(field)) or "PulseSoc rejected that value.", 200),
                     started=started)
    if field in (body.get("queued_for_review") or []):
        return _fail("pulsesoc.business.profile.update", "business.profile.update",
                     "queued_for_review",
                     "That change was sent for verification review and is not live yet.",
                     started=started)
    saved = body.get("saved") or {}
    return ToolResult(
        ok=True, tool_name="pulsesoc.business.profile.update",
        capability_id="business.profile.update",
        canonical_resource_id=f"user:{int(user_id)}:{field}",
        data={"field": field, "value": value, "changed": field in saved},
        latency_ms=_timed(started),
    )


# ---------------------------------------------------------------------------
# Consumer social graph, profile, reels and moderation
# ---------------------------------------------------------------------------
#
# Every executor below is a thin adapter over a shared service that the HTTP
# routes also call. None of them opens a connection, writes SQL, or decides who
# may act on what: if the rule is not in the service, it does not exist, and a
# rule enforced here would be a second authority by definition.


def _service_error_result(exc, tool: str, capability: str, started: float) -> ToolResult:
    """Translate a service's own refusal into a tool failure, preserving its code.

    The service already decided *why* the write was refused and phrased it for a
    person. Re-deriving either here would let the two drift, so both are carried
    through unchanged. Only a 5xx is marked retryable — retrying a 403 just asks
    the same forbidden question twice.
    """
    status = int(getattr(exc, "http_status", 400) or 400)
    return _fail(
        tool, capability,
        clean(getattr(exc, "code", "") or "write_rejected", 80),
        clean(str(exc), 240) or "PulseSoc did not accept that change.",
        retryable=status >= 500,
        started=started,
    )


def _social_graph():
    from services import pulse_social_graph_service

    return pulse_social_graph_service


def _set_block(user_id: int, arguments: dict[str, Any], *, blocked: bool,
               capability_id: str, tool_name: str) -> ToolResult:
    started = time.perf_counter()
    service = _social_graph()
    target_user_id = int(arguments.get("target_user_id") or 0)
    try:
        if blocked:
            outcome = service.block_user(int(user_id), target_user_id, surface="undx")
        else:
            outcome = service.unblock_user(int(user_id), target_user_id, surface="undx")
    except service.SocialGraphError as exc:
        return _service_error_result(exc, tool_name, capability_id, started)
    except Exception as exc:  # pragma: no cover - defensive
        return _fail(tool_name, capability_id, "write_rejected",
                     f"PulseSoc did not accept that change ({exc.__class__.__name__}).",
                     retryable=True, started=started)
    return ToolResult(
        ok=True, tool_name=tool_name, capability_id=capability_id,
        canonical_resource_id=f"user:{target_user_id}",
        data={
            "target_user_id": target_user_id,
            "blocked": bool(outcome.get("blocked")),
            "changed": bool(outcome.get("changed")),
            "correlation_id": outcome.get("correlation_id") or "",
        },
        latency_ms=_timed(started),
    )


def profile_block(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    return _set_block(user_id, arguments, blocked=True,
                      capability_id="profile.block", tool_name="pulsesoc.profile.block")


def profile_unblock(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    return _set_block(user_id, arguments, blocked=False,
                      capability_id="profile.unblock", tool_name="pulsesoc.profile.unblock")


def profile_bio_update(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    """Change only the bio.

    ``update_profile_bio`` passes every other field as unset, so a bio edit
    cannot carry a stale display name back over one the user changed elsewhere.
    That is the whole reason the service takes partial input.
    """
    started = time.perf_counter()
    from services import pulse_profile_service

    bio = clean(arguments.get("bio"), pulse_profile_service.BIO_MAX)
    try:
        outcome = pulse_profile_service.update_profile_bio(int(user_id), bio, surface="undx")
    except pulse_profile_service.ProfileError as exc:
        return _service_error_result(exc, "pulsesoc.profile.bio.update", "profile.bio.update", started)
    except Exception as exc:  # pragma: no cover - defensive
        return _fail("pulsesoc.profile.bio.update", "profile.bio.update", "write_rejected",
                     f"PulseSoc did not accept that change ({exc.__class__.__name__}).",
                     retryable=True, started=started)
    return ToolResult(
        ok=True, tool_name="pulsesoc.profile.bio.update", capability_id="profile.bio.update",
        canonical_resource_id=f"user:{int(user_id)}:bio",
        data={
            "bio": outcome.get("bio") or "",
            "changed": bool(outcome.get("changed")),
            "fields_changed": list(outcome.get("fields_changed") or ()),
        },
        latency_ms=_timed(started),
    )


def reels_delete(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services import pulse_feed_engine

    reel_id = int(arguments.get("reel_id") or 0)
    outcome = pulse_feed_engine.delete_owned_reel(int(user_id), reel_id, surface="undx")
    if not outcome.get("ok"):
        return _fail(
            "pulsesoc.reels.delete", "reels.delete",
            clean(outcome.get("error") or "write_rejected", 80),
            clean(outcome.get("message") or "", 240)
            or "UNDX could not delete a matching Reel owned by your account.",
            started=started,
        )
    return ToolResult(
        ok=True, tool_name="pulsesoc.reels.delete", capability_id="reels.delete",
        canonical_resource_id=f"reel:{reel_id}",
        data={
            "reel_id": reel_id,
            "post_id": int(outcome.get("post_id") or 0),
            "deleted": True,
            "changed": bool(outcome.get("changed")),
            "correlation_id": outcome.get("correlation_id") or "",
        },
        latency_ms=_timed(started),
    )


def reels_comment_create(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    """Comment on a Reel.

    The Reel is resolved to its post through the viewer-scoped reel read, not by
    joining the tables here: that read is what enforces whether this account may
    see the Reel at all, and a Reel the user cannot see is one they cannot
    comment on. ``add_comment`` then applies moderation to the body.
    """
    started = time.perf_counter()
    from services import content_graph_intelligence_service as graph
    from services import pulse_feed_engine

    reel_id = int(arguments.get("reel_id") or 0)
    body = clean(arguments.get("body"), 2200)
    record = graph.get_reel(int(user_id), reel_id)
    if not record:
        return _fail("pulsesoc.reels.comment.create", "reels.comment.create", "not_found",
                     "UNDX could not find that Reel.", started=started)
    post_id = int(record.get("post_id") or 0)
    outcome, status = pulse_feed_engine.add_comment(int(user_id), post_id, body)
    if not outcome.get("ok"):
        return _fail(
            "pulsesoc.reels.comment.create", "reels.comment.create",
            "moderation_rejected" if int(status or 0) == 400 else "write_rejected",
            clean(outcome.get("message") or "", 240) or "PulseSoc did not accept that comment.",
            started=started,
        )
    comment_id = int(outcome.get("comment_id") or 0)
    return ToolResult(
        ok=True, tool_name="pulsesoc.reels.comment.create", capability_id="reels.comment.create",
        canonical_resource_id=f"comment:{comment_id}",
        data={"comment_id": comment_id, "reel_id": reel_id, "post_id": post_id, "body": body},
        latency_ms=_timed(started),
    )


def reels_comment_update(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services import pulse_feed_engine

    comment_id = int(arguments.get("comment_id") or 0)
    body = clean(arguments.get("body"), 2200)
    outcome = pulse_feed_engine.update_comment(int(user_id), comment_id, body, surface="undx")
    if not outcome.get("ok"):
        return _fail(
            "pulsesoc.reels.comment.update", "reels.comment.update",
            clean(outcome.get("error") or "write_rejected", 80),
            clean(outcome.get("message") or "", 240) or "PulseSoc did not accept that edit.",
            started=started,
        )
    return ToolResult(
        ok=True, tool_name="pulsesoc.reels.comment.update", capability_id="reels.comment.update",
        canonical_resource_id=f"comment:{comment_id}",
        data={
            "comment_id": comment_id,
            "post_id": int(outcome.get("post_id") or 0),
            "body": outcome.get("body") or "",
            "changed": bool(outcome.get("changed")),
        },
        latency_ms=_timed(started),
    )


def reels_comment_delete(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services import pulse_feed_engine

    comment_id = int(arguments.get("comment_id") or 0)
    outcome = pulse_feed_engine.delete_comment(int(user_id), comment_id, surface="undx")
    if not outcome.get("ok"):
        return _fail(
            "pulsesoc.reels.comment.delete", "reels.comment.delete",
            clean(outcome.get("error") or "write_rejected", 80),
            clean(outcome.get("message") or "", 240) or "PulseSoc did not accept that deletion.",
            started=started,
        )
    return ToolResult(
        ok=True, tool_name="pulsesoc.reels.comment.delete", capability_id="reels.comment.delete",
        canonical_resource_id=f"comment:{comment_id}",
        data={
            "comment_id": comment_id,
            "post_id": int(outcome.get("post_id") or 0),
            "deleted": True,
            "changed": bool(outcome.get("changed")),
            # Surfaced so the receipt can say "removed from your Reel" rather
            # than "deleted your comment" when those are different acts.
            "moderated_by_owner": bool(outcome.get("moderated_by_owner")),
        },
        latency_ms=_timed(started),
    )


def feed_report(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    from services import pulse_feed_engine

    content_type = clean(arguments.get("content_type"), 24).lower()
    content_id = int(arguments.get("content_id") or 0)
    reason = clean(arguments.get("reason"), 500)
    outcome = pulse_feed_engine.report_content(
        int(user_id), content_type, content_id, reason, surface="undx")
    if not outcome.get("ok"):
        return _fail(
            "pulsesoc.feed.report", "feed.report",
            clean(outcome.get("error") or "write_rejected", 80),
            clean(outcome.get("message") or "", 240) or "PulseSoc did not accept that report.",
            started=started,
        )
    return ToolResult(
        ok=True, tool_name="pulsesoc.feed.report", capability_id="feed.report",
        canonical_resource_id=f"{content_type}:{content_id}",
        data={
            "content_type": content_type,
            "content_id": content_id,
            "report_id": outcome.get("report_id"),
            "reported": True,
            "changed": bool(outcome.get("changed")),
        },
        latency_ms=_timed(started),
    )


# ---------------------------------------------------------------------------
# Marketplace listings — backed by services.business_os.marketplace.service
# ---------------------------------------------------------------------------
#
# The marketplace service is reused exactly as it stands. It already enforces
# the feature flag, the approved-seller requirement, the account hold, product
# ownership and the legal status transitions, and it already writes its own
# ``business_os_mkt_audit`` trail. Nothing in this section re-decides any of
# that; these functions translate arguments in and results out.
#
# ``marketplace.listing.delete`` maps to the service's ``archive`` transition.
# There is no hard delete in the product and this is not the place to invent
# one: orders reference products, and a row that vanishes from under a buyer's
# receipt is a support incident, not a feature.


def _marketplace():
    from services.business_os.marketplace import service as marketplace_service

    return marketplace_service


def marketplace_listing_create(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    started = time.perf_counter()
    service = _marketplace()
    # 160, not a tighter round number: it is what `marketplace.service.TITLE_MAX`
    # accepts and what the capability spec declares. Clipping shorter here would
    # accept a 150-character title, silently store 140, and then fail the
    # verifier that compares the two — a truncation the seller never asked for,
    # reported back to them as their own listing being wrong.
    title = clean(arguments.get("title"), 160)
    price_cents = int(arguments.get("price_cents") or 0)
    try:
        product = service.create_product(
            int(user_id),
            title=title,
            price_cents=price_cents,
            description=clean(arguments.get("description"), 2000) or None,
            fulfillment_type=clean(arguments.get("fulfillment_type"), 24) or "physical",
        )
    except service.MarketplaceError as exc:
        return _service_error_result(exc, "pulsesoc.marketplace.listing.create",
                                     "marketplace.listing.create", started)
    except Exception as exc:  # pragma: no cover - defensive
        return _fail("pulsesoc.marketplace.listing.create", "marketplace.listing.create",
                     "write_rejected",
                     f"PulseSoc did not accept that listing ({exc.__class__.__name__}).",
                     retryable=True, started=started)
    listing_id = clean(product.get("product_id"), 120)
    return ToolResult(
        ok=True, tool_name="pulsesoc.marketplace.listing.create",
        capability_id="marketplace.listing.create",
        canonical_resource_id=f"listing:{listing_id}",
        data={
            "listing_id": listing_id,
            "title": product.get("title") or title,
            "price_cents": int(product.get("price_cents") or price_cents),
            # Created listings start as drafts. Saying so on the receipt is the
            # difference between the seller knowing they still have to publish
            # and wondering why nobody can buy it.
            "status": product.get("status") or "draft",
        },
        latency_ms=_timed(started),
    )


def marketplace_listing_update(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    """Edit one mutable field on an owned listing.

    One field per call, deliberately. The service accepts a dict, but a
    capability that took several would need the confirmation card to describe
    several simultaneous changes, and a person approving "update my listing"
    should be told exactly which value moves.
    """
    started = time.perf_counter()
    service = _marketplace()
    listing_id = clean(arguments.get("listing_id"), 120)
    field = clean(arguments.get("field"), 40)
    raw = arguments.get("value")
    value: Any = clean(raw, 2000)
    if field in {"price_cents", "inventory_qty"}:
        try:
            value = int(float(raw))
        except (TypeError, ValueError):
            return _fail("pulsesoc.marketplace.listing.update", "marketplace.listing.update",
                         "invalid_value", f"{field} must be a whole number.", started=started)
    try:
        product = service.update_product(int(user_id), listing_id, fields={field: value})
    except service.MarketplaceError as exc:
        return _service_error_result(exc, "pulsesoc.marketplace.listing.update",
                                     "marketplace.listing.update", started)
    except Exception as exc:  # pragma: no cover - defensive
        return _fail("pulsesoc.marketplace.listing.update", "marketplace.listing.update",
                     "write_rejected",
                     f"PulseSoc did not accept that change ({exc.__class__.__name__}).",
                     retryable=True, started=started)
    return ToolResult(
        ok=True, tool_name="pulsesoc.marketplace.listing.update",
        capability_id="marketplace.listing.update",
        canonical_resource_id=f"listing:{listing_id}",
        data={"listing_id": listing_id, "field": field, "value": product.get(field),
              "status": product.get("status") or ""},
        latency_ms=_timed(started),
    )


def _marketplace_transition(user_id: int, arguments: dict[str, Any], *, action: str,
                            capability_id: str, tool_name: str) -> ToolResult:
    started = time.perf_counter()
    service = _marketplace()
    listing_id = clean(arguments.get("listing_id"), 120)
    try:
        product = service.transition_product(int(user_id), listing_id, action)
    except service.MarketplaceError as exc:
        return _service_error_result(exc, tool_name, capability_id, started)
    except Exception as exc:  # pragma: no cover - defensive
        return _fail(tool_name, capability_id, "write_rejected",
                     f"PulseSoc did not accept that change ({exc.__class__.__name__}).",
                     retryable=True, started=started)
    return ToolResult(
        ok=True, tool_name=tool_name, capability_id=capability_id,
        canonical_resource_id=f"listing:{listing_id}",
        data={"listing_id": listing_id, "status": product.get("status") or "",
              "action": action},
        latency_ms=_timed(started),
    )


def marketplace_listing_pause(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    return _marketplace_transition(user_id, arguments, action="pause",
                                   capability_id="marketplace.listing.pause",
                                   tool_name="pulsesoc.marketplace.listing.pause")


def marketplace_listing_resume(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    return _marketplace_transition(user_id, arguments, action="resume",
                                   capability_id="marketplace.listing.resume",
                                   tool_name="pulsesoc.marketplace.listing.resume")


def marketplace_listing_delete(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    """Retire a listing by archiving it. See the section note on why not a delete."""
    return _marketplace_transition(user_id, arguments, action="archive",
                                   capability_id="marketplace.listing.delete",
                                   tool_name="pulsesoc.marketplace.listing.delete")


# ---------------------------------------------------------------------------
# Private Office — the member's own private fact store
# ---------------------------------------------------------------------------

#: The highest sensitivity UNDX may read out of the private store.
#:
#: The owner reading their own screen gets everything; the agent does not, and
#: the difference is not timidity. Anything this executor returns can end up in
#: a chat transcript, in a model's context window, and — if a hostile string
#: elsewhere in that context gets its way — in a summary the member did not ask
#: for. A ceiling is the one control that limits the blast radius of every such
#: failure at once, and it costs nothing while the categories above it hold the
#: things a person would least like read aloud.
#:
#: Raising this is a deliberate decision with its own review, not a default.
UNDX_SENSITIVITY_CEILING = "CONFIDENTIAL"

#: Bound on rows returned in one call, independent of the registry's field
#: maximum. The registry bound stops a hostile argument; this one stops a
#: well-formed argument from turning one question into a bulk export.
UNDX_MAX_FACTS = 25


def _private_office():
    from services import db
    from services.private_office import access, facts, office, schema, tiers

    return db, access, facts, office, schema, tiers


def _office_locked_result(tool: str, capability: str, started: float) -> ToolResult:
    """The one sentence UNDX may say about a locked Office.

    No fact names, no counts, no domains, no "you have 3 insurance policies" —
    the refusal itself must not leak what the lock protects. The message is the
    Stage 17 wording verbatim so the agent's transcript renders an instruction,
    not a summary.
    """
    return _fail(tool, capability, "PRIVATE_OFFICE_LOCKED",
                 "Unlock Private Office to access that information.",
                 started=started)


def private_facts_list(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    """Read the caller's own private facts, gated on the canonical tier truth.

    Three properties are worth stating because each is enforced structurally
    rather than by a check that could be reordered away:

    **There is no owner argument.** The capability declares ``domain`` and
    ``limit`` and nothing else, and ``list_facts`` takes ``owner_user_id`` as a
    required keyword that goes into every ``WHERE`` clause. A model that
    proposes ``{"owner_user_id": 999}`` has that key dropped by the field
    validator before this function is entered, and even if it were not, there is
    nowhere here for it to go. Cross-owner reads are not refused; they are
    unrepresentable.

    **A record belonging to somebody else is indistinguishable from no record.**
    The reader filters rather than checks, so UNDX cannot report "that exists
    but is not yours" — which is the disclosure this boundary exists to prevent.
    Stage 8 asks that UNDX not reveal that another member's record exists, and
    the reason it cannot is that it never sees one.

    **The gate is the same one the screen uses.** ``access.decide`` is shared
    with the HTTP surface, so the agent cannot open a capability the product
    still calls unavailable, and — the direction that actually bites — cannot
    refuse one the member is looking at.
    """
    started = time.perf_counter()
    tool = "pulsesoc.private_facts.list"
    capability = "private.facts.list"
    db, access, facts, office, schema, tiers = _private_office()

    owner = int(user_id or 0)
    if owner <= 0:
        return _fail(tool, capability, "authentication_required",
                     "UNDX needs you signed in to read your Private Office.",
                     started=started)

    try:
        resolved = tiers.resolve_tier(owner)
    except Exception:  # noqa: BLE001 - a resolver fault is not a denial
        resolved = {}
    decision = access.decide(resolved, "private_facts")
    verdict = decision["decision"]

    if verdict == access.UNAVAILABLE:
        # Retryable, and deliberately not phrased as a refusal. "We could not
        # look" told as "you may not have this" is the one error that lands on
        # the member who paid.
        return _fail(tool, capability, "entitlement_unavailable",
                     "UNDX could not confirm your Private Office access just now.",
                     retryable=True, started=started)
    if verdict in (access.NOT_IMPLEMENTED, access.FEATURE_DISABLED):
        return _fail(tool, capability, "capability_not_available",
                     "Private facts are not available yet.", started=started)
    if verdict == access.NOT_ENTITLED:
        return _fail(tool, capability, "not_entitled",
                     "Your plan does not include the Private Office.",
                     started=started)

    domain = clean(arguments.get("domain") or "", 32).upper()
    limit = max(1, min(int(arguments.get("limit") or 10), UNDX_MAX_FACTS))

    connection = db.connect()
    try:
        cursor = connection.cursor()
        schema.ensure_private_schema(cursor)

        # The second lock (Stage 17). The tier said the member may have the
        # room; this asks whether the person holding the device just proved
        # they are the member. Same validator, same request bindings as the
        # HTTP surface — the agent can never read what the screen would show
        # locked. Fails closed, including when there is no request context.
        from services.private_office import security as office_security
        if not office_security.request_is_unlocked(cursor, owner).get("ok"):
            return _office_locked_result(tool, capability, started)

        rows = facts.list_facts(
            cursor,
            owner_user_id=owner,
            domains=[domain] if domain else None,
            sensitivity_ceiling=UNDX_SENSITIVITY_CEILING,
            limit=limit + 1,
        )
    except Exception:  # noqa: BLE001
        return _fail(tool, capability, "private_store_unavailable",
                     "UNDX could not read your Private Office just now.",
                     retryable=True, started=started)
    finally:
        connection.close()

    # One row past the limit, for the same reason ``crypto_alerts_list`` does it:
    # a full page and a complete set are otherwise indistinguishable, and a
    # confident "that is everything you have recorded" said over a truncated
    # page is a wrong answer delivered with full authority.
    truncated = len(rows) > limit
    # ``project_facts`` is the same allowlist the HTTP surface returns, so the
    # locator of a source document — and any column added later — stays behind it.
    records = office.project_facts(rows[:limit])
    return ToolResult(
        ok=True,
        tool_name=tool,
        capability_id=capability,
        records=records,
        data={
            "count": len(records),
            "truncated": truncated,
            "domain": domain or "ALL",
            # Named so a caller reading an unexpectedly short list can tell a
            # ceiling from an empty store rather than guessing.
            "sensitivity_ceiling": UNDX_SENSITIVITY_CEILING,
        },
        latency_ms=_timed(started),
    )


def _private_records_executor(capability_id: str) -> Callable[[int, dict[str, Any]], ToolResult]:
    """One record-view executor, bound to its capability at registration time.

    Six capabilities share this implementation, but the gateway hands an
    executor only ``(user_id, arguments)`` — nothing at call time says which
    capability was invoked — so each view binds its own closure rather than
    sharing a name. The properties are ``private_facts_list``'s: no owner
    argument exists, the gate is the same ``access.decide`` the screen uses,
    and the second lock fails closed. The read itself goes through
    ``undx_records_spec.execute_view`` → ``retrieval.retrieve_records``, whose
    general-intent policy caps the agent at the GENERAL domain and an INTERNAL
    ceiling — narrower than the member's own screen, and the result carries
    ``sensitivity_ceiling`` so a short list is legible as a ceiling rather than
    an empty office.
    """

    def _list_records(user_id: int, arguments: dict[str, Any]) -> ToolResult:
        started = time.perf_counter()
        from services.private_office import undx_records_spec as records_spec
        tool = records_spec.tool_name(capability_id)
        db, access, facts, office, schema, tiers = _private_office()

        owner = int(user_id or 0)
        if owner <= 0:
            return _fail(tool, capability_id, "authentication_required",
                         "UNDX needs you signed in to read your Private Office.",
                         started=started)

        try:
            resolved = tiers.resolve_tier(owner)
        except Exception:  # noqa: BLE001 - a resolver fault is not a denial
            resolved = {}
        decision = access.decide(resolved, "private_office.operations")
        verdict = decision["decision"]

        if verdict == access.UNAVAILABLE:
            return _fail(tool, capability_id, "entitlement_unavailable",
                         "UNDX could not confirm your Private Office access just now.",
                         retryable=True, started=started)
        if verdict in (access.NOT_IMPLEMENTED, access.FEATURE_DISABLED):
            return _fail(tool, capability_id, "capability_not_available",
                         "That part of the Private Office is not available yet.",
                         started=started)
        if verdict == access.NOT_ENTITLED:
            return _fail(tool, capability_id, "not_entitled",
                         "Your plan does not include the Private Office.",
                         started=started)

        connection = db.connect()
        try:
            cursor = connection.cursor()
            schema.ensure_private_schema(cursor)

            from services.private_office import security as office_security
            if not office_security.request_is_unlocked(cursor, owner).get("ok"):
                return _office_locked_result(tool, capability_id, started)

            result = records_spec.execute_view(
                cursor, capability_id=capability_id, owner_user_id=owner,
                arguments=dict(arguments or {}))
            # The retrieval audit row must survive the read.
            connection.commit()
        except Exception:  # noqa: BLE001
            return _fail(tool, capability_id, "private_store_unavailable",
                         "UNDX could not read your Private Office just now.",
                         retryable=True, started=started)
        finally:
            connection.close()

        if not result.get("ok"):
            return _fail(tool, capability_id, "records_denied",
                         "UNDX could not read that part of your Private Office.",
                         started=started)

        return ToolResult(
            ok=True,
            tool_name=tool,
            capability_id=capability_id,
            records=list(result.get("records") or []),
            data={
                "count": int(result.get("counts", {}).get("returned") or 0),
                "truncated": bool(result.get("truncated")),
                "view": result.get("view") or "",
                # Named so a caller reading an unexpectedly short list can tell
                # a ceiling from an empty office rather than guessing.
                "sensitivity_ceiling": result.get("sensitivity_ceiling") or "",
            },
            latency_ms=_timed(started),
        )

    return _list_records


def private_capital_portfolio(user_id: int, arguments: dict[str, Any]) -> ToolResult:
    """Read the caller's own projected Portfolio holdings, priced live.

    ``private_facts_list``'s properties hold here, plus two of its own:

    **No arguments at all.** The capability declares zero fields, so there is
    nothing a model could widen — no owner, no symbol filter that might later
    grow into a cross-account lookup. ``arguments`` is accepted and ignored.

    **Honest numbers or none.** ``totals.value`` arrives ``null`` whenever any
    holding lacks a live quote; this executor relays that refusal untouched.
    The agent may say "1 of 2 holdings priced", never invent the missing total.
    """
    started = time.perf_counter()
    from services.private_office import undx_capital_spec as capital_spec
    tool = capital_spec.tool_name(capital_spec.CAPABILITY_ID)
    capability = capital_spec.CAPABILITY_ID
    db, access, facts, office, schema, tiers = _private_office()

    owner = int(user_id or 0)
    if owner <= 0:
        return _fail(tool, capability, "authentication_required",
                     "UNDX needs you signed in to read your Private Office.",
                     started=started)

    try:
        resolved = tiers.resolve_tier(owner)
    except Exception:  # noqa: BLE001 - a resolver fault is not a denial
        resolved = {}
    decision = access.decide(resolved, capital_spec.FEATURE_ID)
    verdict = decision["decision"]

    if verdict == access.UNAVAILABLE:
        return _fail(tool, capability, "entitlement_unavailable",
                     "UNDX could not confirm your Private Office access just now.",
                     retryable=True, started=started)
    if verdict in (access.NOT_IMPLEMENTED, access.FEATURE_DISABLED):
        return _fail(tool, capability, "capability_not_available",
                     "The Capital Graph is not available yet.", started=started)
    if verdict == access.NOT_ENTITLED:
        return _fail(tool, capability, "not_entitled",
                     "Your plan does not include the Private Office.",
                     started=started)

    connection = db.connect()
    try:
        cursor = connection.cursor()
        schema.ensure_private_schema(cursor)

        from services.private_office import security as office_security
        if not office_security.request_is_unlocked(cursor, owner).get("ok"):
            return _office_locked_result(tool, capability, started)

        result = capital_spec.execute(cursor, owner_user_id=owner)
        # The outbox sweep and audit rows must survive the read.
        connection.commit()
    except Exception:  # noqa: BLE001
        return _fail(tool, capability, "private_store_unavailable",
                     "UNDX could not read your Private Office just now.",
                     retryable=True, started=started)
    finally:
        connection.close()

    if not result.get("ok"):
        return _fail(tool, capability, "records_denied",
                     "UNDX could not read your Capital Graph.",
                     started=started)

    return ToolResult(
        ok=True,
        tool_name=tool,
        capability_id=capability,
        records=list(result.get("records") or []),
        data={
            "count": int(result.get("counts", {}).get("returned") or 0),
            # Relayed whole: ``totals.value`` is null unless every holding was
            # priced live, and the agent must repeat that refusal, not fill it.
            "totals": result.get("totals") or {},
            "prices": result.get("prices") or {},
            "sync": result.get("sync") or {},
        },
        latency_ms=_timed(started),
    )


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------

EXECUTORS: dict[str, Callable[[int, dict[str, Any]], ToolResult]] = {
    "private_facts_list": private_facts_list,
    "profile_block": profile_block,
    "profile_unblock": profile_unblock,
    "profile_bio_update": profile_bio_update,
    "reels_delete": reels_delete,
    "reels_comment_create": reels_comment_create,
    "reels_comment_update": reels_comment_update,
    "reels_comment_delete": reels_comment_delete,
    "feed_report": feed_report,
    "marketplace_listing_create": marketplace_listing_create,
    "marketplace_listing_update": marketplace_listing_update,
    "marketplace_listing_pause": marketplace_listing_pause,
    "marketplace_listing_resume": marketplace_listing_resume,
    "marketplace_listing_delete": marketplace_listing_delete,
    "crypto_alerts_list": crypto_alerts_list,
    "crypto_alerts_get": crypto_alerts_get,
    "crypto_alerts_pause": crypto_alerts_pause,
    "crypto_alerts_resume": crypto_alerts_resume,
    "crypto_alerts_create": crypto_alerts_create,
    "crypto_alerts_update": crypto_alerts_update,
    "crypto_alerts_delete": crypto_alerts_delete,
    "crypto_portfolio_summary": crypto_portfolio_summary,
    "crypto_portfolio_history": crypto_portfolio_history,
    "crypto_alerts_activity": crypto_alerts_activity,
    "crypto_market_observations": crypto_market_observations,
    "notification_preferences_read": notification_preferences_read,
    "notification_preferences_update": notification_preferences_update,
    "saved_items_list": saved_items_list,
    "saved_post_set": saved_post_set,
    "social_relationships_list": social_relationships_list,
    "social_follow": social_follow,
    "social_unfollow": social_unfollow,
    "conversations_list": conversations_list,
    "messages_list": messages_list,
    "messages_search": messages_search,
    "conversation_summarize": conversation_summarize,
    "messages_suggest": messages_suggest,
    "message_draft": message_draft,
    "feed_posts_list": feed_posts_list,
    "feed_posts_get": feed_posts_get,
    "feed_comments_list": feed_comments_list,
    "feed_post_performance_summary": feed_post_performance_summary,
    "feed_comments_summary": feed_comments_summary,
    "feed_post_like": feed_post_like,
    "feed_post_unlike": feed_post_unlike,
    "feed_post_delete": feed_post_delete,
    "feed_post_hide": feed_post_hide,
    "messages_mark_read": messages_mark_read,
    "messages_send": messages_send,
    "business_campaign_pause": business_campaign_pause,
    "business_campaign_resume": business_campaign_resume,
    "business_profile_update": business_profile_update,
    "reels_search": reels_search,
    "reels_get": reels_get,
    "reels_performance": reels_performance,
    "reels_comments_summary": reels_comments_summary,
    "reels_save": reels_save,
    "reels_unsave": reels_unsave,
    "reels_like": reels_like,
    "reels_unlike": reels_unlike,
    "statuses_list": statuses_list,
    "statuses_get": statuses_get,
    "status_viewers": status_viewers,
    "status_reactions": status_reactions,
    "profile_get": profile_get,
    "profile_activity": profile_activity,
    "profile_relationships": profile_relationships,
    "profile_preferences_update": profile_preferences_update,
    "activity_daily_summary": activity_daily_summary,
    "notifications_inbox_list": notifications_inbox_list,
    "notifications_explain": notifications_explain,
    "notifications_group_summary": notifications_group_summary,
    "search_global": search_global,
    "search_people": search_people,
    "search_content": search_content,
    "search_messages": search_messages,
    "search_activity": search_activity,
    "settings_inspect": settings_inspect,
    "settings_explain": settings_explain,
    "settings_recommend": settings_recommend,
    "security_sessions_list": security_sessions_list,
    "security_activity_summary": security_activity_summary,
    "security_device_list": security_device_list,
    "marketplace_search": marketplace_search,
    "marketplace_listing_summary": marketplace_listing_summary,
    "marketplace_order_status": marketplace_order_status,
    "premium_status": premium_status,
    "premium_entitlements": premium_entitlements,
    "ads_performance_summary": ads_performance_summary,
    "live_search": live_search,
    "live_summary": live_summary,
    "live_performance": live_performance,
    "learning_search": learning_search,
    "learning_progress": learning_progress,
    "memory_activity_inspect": memory_activity_inspect,
    "groups_list": groups_list,
    "groups_search": groups_search,
    "events_upcoming": events_upcoming,
    "music_search": music_search,
    "account_health_summary": account_health_summary,
    "verification_status": verification_status,
    "support_tickets_list": support_tickets_list,
    "creator_analytics_summary": creator_analytics_summary,
    "localization_preferences": localization_preferences,
    "crypto_portfolio_summary": crypto_portfolio_summary,
    "crypto_market_window": crypto_market_window,
    "translation_content_translate": translation_content_translate,
    "presence_privacy_status": presence_privacy_status,
    "crypto_watchlist_list": crypto_watchlist_list,
    "crypto_watchlist_add": crypto_watchlist_add,
    "crypto_watchlist_remove": crypto_watchlist_remove,
    "crypto_portfolio_holdings_list": crypto_portfolio_holdings_list,
    "crypto_portfolio_holding_add": crypto_portfolio_holding_add,
    "crypto_portfolio_holding_update": crypto_portfolio_holding_update,
    "crypto_portfolio_holding_delete": crypto_portfolio_holding_delete,
    "notifications_mark_read": notifications_mark_read,
    "notifications_mark_all_read": notifications_mark_all_read,
    "presence_privacy_update": presence_privacy_update,
    "localization_region_update": localization_region_update,
    "localization_translation_update": localization_translation_update,
    "settings_privacy_audience_update": settings_privacy_audience_update,
    "settings_appearance_theme_update": settings_appearance_theme_update,
}


# The six Batch C record-view executors, bound from the spec module so the
# names here and in the capability registry agree by construction.
def _register_private_record_executors() -> None:
    from services.private_office import undx_records_spec as _po_spec

    for _entry in _po_spec.CAPABILITIES:
        _cid = _entry["capability_id"]
        EXECUTORS[_po_spec.executor_name(_cid)] = _private_records_executor(_cid)


_register_private_record_executors()


# The Capital Graph read, bound from its spec module for the same reason: the
# name here and the name the registry derives are the same function call.
def _register_private_capital_executor() -> None:
    from services.private_office import undx_capital_spec as _cap_spec

    EXECUTORS[_cap_spec.executor_name(_cap_spec.CAPABILITY_ID)] = private_capital_portfolio


_register_private_capital_executor()


def resolve(name: str) -> Callable[[int, dict[str, Any]], ToolResult]:
    executor = EXECUTORS.get(clean(name, 80))
    if executor is None:
        # A registry entry naming a non-existent executor is a deployment defect.
        # Surfacing it as "unsupported" keeps the user safe while the audit trail
        # records the real cause.
        raise AgentError(
            "executor_missing",
            "UNDX cannot do that yet.",
            outcome=AgentOutcome.UNSUPPORTED_CAPABILITY,
            details={"executor": clean(name, 80)},
        )
    return executor


__all__ = [
    "EXECUTORS", "resolve", "read_push_value", "SETTINGS_WRITABLE_GROUPS",
    "BUSINESS_PROFILE_WRITABLE_FIELDS",
]
