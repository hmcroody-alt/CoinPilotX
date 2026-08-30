"""One unauthenticated GET that reports whether the run worker is doing its job.

Separate from :mod:`services.undx_agent_run_routes` and
:mod:`services.undx_agent_run_control_routes` because it is a different kind of thing
from either: those two are per-account and require a session, this one is per-deployment
and requires nothing. Folding an unauthenticated route into a pack whose stated guarantee
is "every route here is scoped to the logged-in owner" would destroy that guarantee even
though the new route is harmless, and the guarantee is worth more than the file count.

**Unauthenticated, and therefore counts only.** This route is reachable by anyone who can
reach the service, which is the point — a health check that needs a session cannot be
used by the thing that restarts the service. Everything it can say is bounded by
:mod:`services.undx_run_health`, which publishes integers, booleans, durations, a git sha
and a fixed status vocabulary, and specifically not ``last_error``. See that module's
docstring for why that one field is excluded rather than truncated.

**It returns 200 when it can answer.** The status code reports whether the *check* ran,
not whether the news is good; ``ok`` in the body reports the news. This is the opposite
of the convention ``/health/undx`` uses, and the difference is deliberate: that endpoint
is a liveness probe whose 503 is meant to be acted on by infrastructure, while this one
is an observability surface meant to be scraped and graphed. A scraper that gets a 503
records an outage of the scrape, and the queue depth it was collecting goes missing at
exactly the moment it mattered.

Registered through ``_load_route_pack`` in ``bot.py`` like every other pack.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify

from services import undx_run_health
from services.undx_brain import config as brain_config

LOGGER = logging.getLogger(__name__)

undx_run_health_blueprint = Blueprint("undx_run_health", __name__)

ROUTE_PATH = "/health/undx/runs"

#: Shares the gate with ``/health/undx`` rather than adding a second flag. Two switches
#: for "expose the secret-free health surface" is one more than anybody will remember to
#: set, and the failure mode of forgetting is a health route that is quietly dark.
GATE_FLAG = "UNDX_HEALTH_ENDPOINT_ENABLED"


def _bot():
    import bot

    return bot


def _json(payload, status=200):
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
    return response, status


def _enabled() -> bool:
    try:
        return bool(brain_config.flags().get(GATE_FLAG, True))
    except Exception:
        # The flag fails open in the catalog, and a health surface that disappears
        # because its own configuration lookup broke is a health surface that hides the
        # outage it exists to report.
        LOGGER.debug("run health flag read failed", exc_info=True)
        return True


def _with_db(handler):
    """Read-only, and says so by never committing.

    There is no ``ensure_schema`` call here either. This route reads tables the web
    service and the worker both create on boot; a health check that creates schema would
    be a health check that reports success on a deployment where boot failed.
    """
    conn = _bot().db()
    try:
        return handler(conn.cursor())
    finally:
        try:
            conn.close()
        except Exception:
            LOGGER.debug("run health connection close failed", exc_info=True)


@undx_run_health_blueprint.get(ROUTE_PATH)
def undx_run_health_check():
    """Queue depth, worker liveness, and whether the two deploys agree on a sha."""
    if not _enabled():
        return _json({"ok": False, "surface": "undx-run-health-1",
                      "reason": "disabled"}, 404)
    try:
        payload = _with_db(undx_run_health.snapshot)
    except Exception as exc:
        LOGGER.exception("UNDX_RUN_HEALTH_FAILED error=%s", exc.__class__.__name__)
        # The exception class, not the exception. A database error can carry a statement
        # and a statement can carry a value, and this route has no session behind it.
        return _json({"ok": False, "surface": "undx-run-health-1",
                      "reason": "unavailable"}, 503)
    return _json(payload, 200)


def register(app) -> None:
    app.register_blueprint(undx_run_health_blueprint)
