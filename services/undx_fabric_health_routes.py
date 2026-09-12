"""One unauthenticated GET reporting whether the model fabric's guarantees hold.

Sibling of :mod:`services.undx_run_health_routes` and built to the same rules,
because it is the same kind of thing: per-deployment rather than per-account,
secret-free, and therefore reachable without a session. A health check that
needs a session cannot be used by the thing that restarts the service.

The difference from ``/health/undx/runs`` is what it watches. That route asks
whether the run worker is keeping up. This one asks whether the four controls
built around provider calls are actually in force — whether a provider the
dashboard calls healthy still has a key, whether the month's spend is a total
or a floor, whether any configuration has drifted out from under the
assumptions the router makes.

**200 when it can answer.** The status code reports whether the *check* ran;
``ok`` in the body reports the news. Same convention as ``/health/undx/runs``
and the same reason: this is a scraped surface, and a scraper that gets a 503
records an outage of the scrape — so the state it was collecting goes missing
at exactly the moment it mattered.

**It contacts no provider.** Model availability is
:mod:`services.undx_model_audit`, it spends real money, and it is an operator
action run from one place. An endpoint anyone can GET on a 30-second interval
is the last place to put a paid call, and anyone who found the URL could bill
us on purpose.

Registered through ``_load_route_pack`` in ``bot.py`` like every other pack.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify

from services import undx_fabric_health

LOGGER = logging.getLogger(__name__)

undx_fabric_health_blueprint = Blueprint("undx_fabric_health", __name__)

ROUTE_PATH = "/health/undx/fabric"

#: Shares the gate with ``/health/undx`` and ``/health/undx/runs`` rather than
#: adding a third flag. Three switches for "expose the secret-free health
#: surface" is two more than anybody will remember to set, and the failure mode
#: of forgetting is a health route that is quietly dark.
GATE_FLAG = "UNDX_HEALTH_ENDPOINT_ENABLED"


def _json(payload, status=200):
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
    return response, status


def _enabled() -> bool:
    try:
        from services.undx_brain import config as brain_config  # noqa: PLC0415

        return bool(brain_config.flags().get(GATE_FLAG, True))
    except Exception:
        # Fails open, like its sibling: a health surface that disappears because
        # its own configuration lookup broke is a health surface that hides the
        # outage it exists to report.
        LOGGER.debug("fabric health flag read failed", exc_info=True)
        return True


@undx_fabric_health_blueprint.get(ROUTE_PATH)
def undx_fabric_health_check():
    """Provider states, month-to-date spend, and live configuration drift."""
    if not _enabled():
        return _json({"ok": False, "surface": undx_fabric_health.SURFACE,
                      "reason": "disabled"}, 404)
    try:
        payload = undx_fabric_health.snapshot()
    except Exception as exc:
        LOGGER.exception("UNDX_FABRIC_HEALTH_FAILED error=%s", exc.__class__.__name__)
        # The exception class, not the exception. A database error can carry a
        # statement and a statement can carry a value, and this route has no
        # session behind it.
        return _json({"ok": False, "surface": undx_fabric_health.SURFACE,
                      "reason": "unavailable"}, 503)
    return _json(payload, 200)


def register(app) -> None:
    app.register_blueprint(undx_fabric_health_blueprint)
