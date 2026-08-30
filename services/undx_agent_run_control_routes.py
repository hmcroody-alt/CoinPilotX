"""Changing your mind about a queued run. One route, one verb, one thing it can do.

This is a separate module from :mod:`services.undx_agent_run_routes` for a reason that is
worth stating rather than assuming. That module is the surface a client polls every couple
of seconds, and its guarantee — asserted by a test over the Flask URL map, not by reading
its source — is that it registers two ``GET`` routes and nothing else. A poll that can
mutate is how a poll becomes a mutation nobody meant to send; a ``POST`` added there would
still be a ``POST``, but the property that made the claim checkable would be gone.

So cancellation lives here, on its own route, with its own authorisation, and the read
pack keeps its proof.

**What cancellation is allowed to mean.** Exactly one thing: a run that no worker has
taken yet stops and never runs. It is not a recall. There is no message that reaches
inside :func:`services.undx_tool_gateway.execute` and un-sends a request already in
flight, and the failure mode of pretending otherwise is specific and bad — a person told
"cancelled" whose block, payment or deletion then lands anyway. Telling somebody they were
a second too late is a smaller harm than telling them something untrue about their own
account, so a claimed run is refused and they are pointed back at its status.

The refusal is made by :func:`services.undx_agent_runs.cancel_for_user`, in a
compare-and-swap that carries both the owner and ``status='queued'`` in its ``WHERE``
clause. Nothing in this module decides who owns the run or whether it is too late; it
turns four stable codes into four HTTP answers.

**Nothing about identity comes from the client.** The owner is
:func:`bot.api_account_user`, from the session. The run id in the path is the only value
taken from the request, and it goes to a statement that already carries the owner.

Registered through ``_load_route_pack`` in ``bot.py`` like every other pack.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify

from services import undx_agent_runs
from services import undx_run_status

LOGGER = logging.getLogger(__name__)

undx_agent_run_control_blueprint = Blueprint("undx_agent_run_control", __name__)

API_PREFIX = "/api/undx/runs"

#: The four answers :func:`services.undx_agent_runs.cancel_for_user` can give, as the
#: status code and sentence each one deserves.
#:
#: ``not_found`` is a 404 and says the same thing a foreign run id says, because
#: :func:`services.undx_agent_runs.get_for_user` cannot tell them apart and must not: a
#: cancel endpoint that distinguished "no such run" from "not yours" would confirm the
#: existence of other people's runs to anybody willing to guess ids.
#:
#: ``already_settled`` and ``in_flight`` are both 409 — the request was understood, the run
#: exists, and its state does not permit this — but they are different sentences because
#: they call for different things from the person: one is over, the other is worth
#: watching.
_ANSWERS: dict[str, tuple[int, str]] = {
    undx_agent_runs.CANCEL_DONE: (200, "Cancelled. That request will not run."),
    undx_agent_runs.CANCEL_NOT_FOUND: (404, "No such request."),
    undx_agent_runs.CANCEL_ALREADY_SETTLED: (
        409, "That request already finished. Cancelling it would not undo anything."),
    undx_agent_runs.CANCEL_IN_FLIGHT: (
        409, "That request is already running and cannot be called back. "
             "Check its status to see how it finishes."),
}


def _bot():
    import bot

    return bot


def _require_user():
    try:
        user = _bot().api_account_user()
    except Exception:
        user = None
    if not user:
        return None, (jsonify({"ok": False, "message": "Login required."}), 401)
    return user, None


def _json(payload, status=200):
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
    return response, status


def _with_db(handler):
    """Run handler(cur, conn) inside a committed transaction.

    Unlike the read pack's copy of this, the commit here is load-bearing rather than a
    side effect of ``ensure_schema`` issuing DDL: the cancellation is the write.
    """
    conn = _bot().db()
    try:
        cur = conn.cursor()
        result = handler(cur, conn)
        conn.commit()
        return result
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


@undx_agent_run_control_blueprint.post(f"{API_PREFIX}/<run_id>/cancel")
def undx_run_cancel(run_id: str):
    """Stop a queued run of this account's. Idempotent in the way that matters.

    Cancelling an already-cancelled run answers 409 ``already_settled`` rather than 200,
    which is the honest reading: the second request did not cancel anything. A client that
    wants "make sure this is stopped" gets that from either answer, because both mean the
    run will not execute.
    """
    user, denied = _require_user()
    if denied:
        return denied
    user_id = int(user["user_id"])
    # Bounded before it reaches a statement, same as the detail route. A run id is
    # ``run_`` plus a uuid4 hex, so anything longer is not one.
    wanted = str(run_id or "").strip()[:80]

    def run(cur, conn):
        code = undx_agent_runs.cancel_for_user(cur, user_id, wanted)
        # Read back inside the same transaction so the status returned is the one the
        # cancel just produced, rather than one a second connection might race with.
        return code, undx_agent_runs.get_for_user(cur, user_id, wanted)

    try:
        code, row = _with_db(run)
    except Exception as exc:
        LOGGER.exception("UNDX_RUN_CANCEL_FAILED user_id=%s error=%s",
                         user_id, exc.__class__.__name__)
        return _json({"ok": False, "message": "Could not cancel that request."}, 500)

    status_code, message = _ANSWERS.get(
        code, (500, "Could not cancel that request."))
    payload = {"ok": status_code == 200, "message": message, "result": code}
    if row:
        # The run's own state travels with the answer so a client does not have to make a
        # second round trip to the poll endpoint to find out what it is now looking at —
        # and so that "already running" comes with the evidence for that claim.
        projection = undx_run_status.project(row)
        payload["run_id"] = str(row.get("run_id") or "")
        payload["status"] = projection.status
        payload["status_detail"] = projection.description
        payload["terminal"] = projection.terminal
    LOGGER.info("UNDX_RUN_CANCEL user_id=%s run_id=%s result=%s",
                user_id, wanted, code)
    return _json(payload, status_code)


def register(app) -> None:
    app.register_blueprint(undx_agent_run_control_blueprint)
