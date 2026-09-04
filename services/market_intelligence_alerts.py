"""Derived-intelligence readings, shaped as alert metrics.

The alert engine already knows how to watch a number: it latches, it dedupes
with a crossing key, it respects a cooldown, and it treats a missing value as
undecidable rather than false. None of that needed rebuilding to alert on
"entry quality improved" — it needed a number.

So this module is a translator, nothing more. It runs the same
``services.market_intelligence`` analysis the screen shows and flattens the parts
of it that are genuinely ordered into plain floats on the asset dict the engine
already passes around. Everything downstream — comparators, edge detection,
notification copy, history — is the existing code path.

Two properties matter and are load-bearing:

**Absent stays absent.** Any reading the analysis could not compute comes back
``None``, which the engine already routes to "undecidable": check the rule,
leave the latch alone, notify nobody. This is the difference between "risk did
not become high" and "we could not measure risk", and an alert that fired on the
second would be worse than no alert.

**Nothing here fetches.** It reads the shared board, which the alert worker has
already loaded for this very cycle. A hundred intelligence alerts across a
hundred symbols still ride on one board request.
"""

from __future__ import annotations

import logging
from typing import Any

from services import crypto_alert_conditions as conditions

LOGGER = logging.getLogger(__name__)


def _score(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def intelligence_metrics(symbol: str) -> dict[str, Any]:
    """The derived readings for one symbol, keyed by alert field name.

    Every key in ``conditions.INTELLIGENCE_METRIC_FIELDS`` is present in the
    result, with ``None`` where the reading could not be computed. Returning the
    full set of keys — rather than only the ones that worked — is what lets the
    engine tell "we looked and it is not true" apart from "we could not look",
    because a missing key and a null value would otherwise be indistinguishable
    at the comparison site.
    """
    blank = {field: None for field in conditions.INTELLIGENCE_METRIC_FIELDS.values()}
    try:
        from services import market_pulse

        payload = market_pulse.asset_intelligence(symbol)
    except Exception as exc:  # noqa: BLE001 - an analysis failure is undecidable, not false
        LOGGER.info("Intelligence metrics unavailable for %s: %s", symbol, exc)
        return blank

    action = (payload.get("action") or {}).get("state")
    setup = payload.get("setup") or {}
    structure = payload.get("structure") or {}
    levels = structure.get("levels") or {}
    volume = payload.get("volume") or {}

    support_distance = _score(levels.get("supportDistancePct"))
    resistance_distance = _score(levels.get("resistanceDistancePct"))

    metrics = dict(blank)
    metrics["intel_opportunity"] = _score((payload.get("opportunity") or {}).get("score"))
    metrics["intel_entry"] = _score((payload.get("entry") or {}).get("score"))
    metrics["intel_risk_ordinal"] = conditions.RISK_ORDINALS.get((payload.get("risk") or {}).get("level"))
    metrics["intel_action_posture"] = conditions.ACTION_POSTURE.get(action)

    # 1/0 flags rather than booleans, so a "became true" rule is an ordinary
    # crossing above 0.5 and needs no new comparator.
    if setup.get("type"):
        metrics["intel_setup_ready"] = 1.0 if setup.get("status") == "READY" else 0.0
        # Confirmed means the trigger has happened: price is through the level
        # the breakout setup named, not merely pressing against it. A setup
        # still marked PENDING has by definition not been confirmed.
        metrics["intel_breakout_confirmed"] = (
            1.0 if setup.get("type") == "breakout" and setup.get("status") not in {"PENDING", None} else 0.0
        )
    if support_distance is not None:
        # "At support" is a position, not an event: within 2% of the nearest
        # support pivot. The crossing comparator turns it into the event.
        metrics["intel_at_support"] = 1.0 if support_distance <= 2.0 else 0.0
    if resistance_distance is None and support_distance is None:
        metrics["intel_at_support"] = None
    metrics["intel_volume_ratio"] = _score(volume.get("ratio"))
    return metrics


def enrich_asset(asset: dict[str, Any] | None, symbol: str,
                 spec: Any = None) -> dict[str, Any]:
    """Return ``asset`` plus derived metrics, but only if the rule asks for them.

    Called from the engine's advanced-rule path. When the rule reads nothing
    derived, this returns the asset untouched and does no work at all — which is
    the property that keeps existing alerts exactly as cheap as they were.
    """
    base = dict(asset or {})
    if spec is not None and not conditions.spec_uses_intelligence(spec):
        return base
    base.update(intelligence_metrics(symbol))
    return base
