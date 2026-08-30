"""The read side of durable agent runs: what happened to the thing you asked for.

Until now a run could be created and executed but never looked at. The request that
queued it answered ``accepted_queued`` and the conversation moved on; the worker did the
work and wrote the result into a table no client could reach. That is worse than not
having queued it, because the person was told their request was accepted and then given
no way to find out whether it was carried out.

These two endpoints close that. They are the only production surface over
:func:`services.undx_agent_runs.for_user` and :func:`services.undx_agent_runs.get_for_user`.

**They read. They do not act.** Both handlers are ``GET``, both open the database through
:func:`_with_db` and neither writes. Cancellation, confirmation and any other state change
belong on their own explicit routes with their own authorisation, and putting them here —
where a client already polls every couple of seconds — is how a poll turns into a mutation
nobody meant to send.

**Nothing about identity comes from the client.** The owner is
:func:`bot.api_account_user`, from the session. The run id is the only value taken from
the request, and it is passed to a statement that already carries the owner in its
``WHERE`` clause, so a valid id belonging to another account loads nothing rather than
loading a row that is then filtered. There is no ``user_id`` parameter, no
``capability_id`` parameter and no ``target_id`` parameter anywhere in this module — not
rejected, absent, which is the difference between a filter and a design.

**The status is projected, never passed through.** The stored status is a claim-query
artifact; ``succeeded`` there means the worker returned without raising. What a client is
shown comes from :func:`services.undx_run_status.project`, and the completion claim inside
it comes from the Brain. This module contains no comparison against ``"verified_success"``
and must not grow one.

Registered through ``_load_route_pack`` in ``bot.py`` like every other pack, which is what
keeps the integration to a single additive line and leaves ``bot.py`` otherwise untouched.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from services import undx_agent_runs
from services import undx_run_status

LOGGER = logging.getLogger(__name__)

undx_agent_run_blueprint = Blueprint("undx_agent_runs", __name__)

API_PREFIX = "/api/undx/runs"

#: How many runs one request may read. The floor exists because a caller sending ``0``
#: means "I did not set this", not "return nothing"; the ceiling because a list endpoint
#: should not be a way to pull an account's whole action history in one round trip.
#: ``for_user`` clamps too — deliberately, so that a second caller reaching it from
#: somewhere other than here is bounded by the module that owns the table rather than by
#: whichever route remembered to.
DEFAULT_LIMIT = 20
MAX_LIMIT = 50


def _bot():
    import bot

    return bot


def _current_user():
    try:
        return _bot().api_account_user()
    except Exception:
        return None


def _require_user():
    user = _current_user()
    if not user:
        return None, (jsonify({"ok": False, "message": "Login required."}), 401)
    return user, None


def _json(payload, status=200):
    response = jsonify(payload)
    # A cached run status is a stale run status, and a stale run status is the specific
    # lie this whole surface exists to avoid — "queued" served from a cache for a run that
    # finished and failed reads as work still in progress.
    response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
    return response, status


def _with_db(handler):
    """Run handler(cur, conn) inside a committed transaction.

    Committed even though both handlers only read, because
    :func:`services.undx_agent_runs.ensure_schema` runs on every read and may issue DDL on
    a database that has not seen this table yet. Leaving that uncommitted would recreate
    it on every request.
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


def _limit() -> int:
    raw = (request.args.get("limit") or "").strip()
    if not raw:
        return DEFAULT_LIMIT
    try:
        value = int(raw)
    except ValueError:
        # A malformed limit is a client bug, not a reason to refuse the person their own
        # run list. The default is the safe reading of "I could not tell you what I want".
        return DEFAULT_LIMIT
    return max(1, min(value, MAX_LIMIT))


def _int(value) -> int:
    """A counter as a number, or ``0``.

    A run row read from either backend can hand back a string, a ``Decimal`` or ``None``
    for the same column. None of those are worth failing a status read over.
    """
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _present(row) -> dict:
    """One stored row as the object a client is given.

    Assembled field by field rather than by copying the row, so that a column added to
    ``undx_agent_runs`` appears here only when somebody decides it should. The
    ``SELECT`` lists in :mod:`services.undx_agent_runs` are the first gate on that; this
    is the second, and two gates is the right number for a table that holds authorisation
    state.
    """
    projection = undx_run_status.project(row)
    return {
        "run_id": str(row.get("run_id") or ""),
        "capability_id": str(row.get("capability_id") or ""),
        # The identity of the thing acted on. Already the person's own — the row exists
        # under their user id and the resolver produced this target from their words —
        # so echoing it back tells them *which* alert or post this run is about, which is
        # most of what makes a list of runs readable at all.
        "target_id": str(row.get("canonical_target_id") or ""),
        "status": projection.status,
        "status_detail": projection.description,
        "terminal": projection.terminal,
        # The two fields a client must branch on before saying anything final, and they
        # are not the same question. ``may_claim_completed`` is the Brain's "may a change
        # be reported as done" — false for a healthy read, because a lookup completes
        # nothing. ``requires_disclosure`` is "must the sentence carry a hedge" — false
        # for that same healthy read, and true for a write that ran unconfirmed. A client
        # holding only the first would hedge every successful read.
        "may_claim_completed": projection.may_claim_completed,
        "requires_disclosure": projection.requires_disclosure,
        # The raw gateway answer, alongside the projection rather than instead of it. A
        # client that only knows the projection is fine; a client debugging a support
        # ticket needs to see what the gateway actually concluded.
        "outcome": str(row.get("outcome") or ""),
        "confirmation_state": str(row.get("confirmation_state") or ""),
        "dispatch_reason": str(row.get("dispatch_reason") or ""),
        "attempt": _int(row.get("attempt_count")),
        "max_attempts": _int(row.get("max_attempts")),
        # Not the arguments. The hash identifies the body the approval was bound to
        # without handing the body back out through a boundary it did not come in by.
        "arguments_hash": str(row.get("arguments_hash") or ""),
        "created_at": str(row.get("created_at") or ""),
        "updated_at": str(row.get("updated_at") or ""),
        "completed_at": str(row.get("completed_at") or ""),
        "expires_at": str(row.get("expires_at") or ""),
        # An error string the worker recorded. Already truncated at write time and never
        # an exception message — :func:`services.undx_agent_runs.clean_code` reduces a
        # raised exception to a stable code before it reaches the row.
        "error": str(row.get("last_error") or ""),
    }


@undx_agent_run_blueprint.get(API_PREFIX)
def undx_runs_list():
    """This account's durable runs, newest first."""
    user, denied = _require_user()
    if denied:
        return denied
    user_id = int(user["user_id"])
    limit = _limit()

    def run(cur, conn):
        return undx_agent_runs.for_user(cur, user_id, limit=limit)

    try:
        rows = _with_db(run)
    except Exception as exc:
        LOGGER.exception("UNDX_RUNS_LIST_FAILED user_id=%s error=%s",
                         user_id, exc.__class__.__name__)
        return _json({"ok": False, "message": "Could not read your requests."}, 500)

    return _json({
        "ok": True,
        "runs": [_present(row) for row in rows],
        "limit": limit,
        # The vocabulary itself, so a client can be written against the full status model
        # rather than against whichever statuses happened to be in the first response it
        # saw. Cheap to send and it makes an unrecognised status a client bug the client
        # can detect.
        "statuses": list(undx_run_status.ALL_STATUSES),
    })


@undx_agent_run_blueprint.get(f"{API_PREFIX}/<run_id>")
def undx_run_detail(run_id: str):
    """One run of this account's, or 404.

    A run that does not exist and a run belonging to somebody else are the same answer on
    purpose. Separating them would confirm whether an arbitrary run id is real, and the
    only party that benefits from that distinction is one enumerating ids.
    """
    user, denied = _require_user()
    if denied:
        return denied
    user_id = int(user["user_id"])
    # Bounded before it reaches a statement. Run ids are ``run_`` plus a uuid4 hex, so
    # anything long is not one, and a length cap keeps an oversized parameter out of the
    # database and out of the log line below.
    wanted = str(run_id or "").strip()[:80]

    def run(cur, conn):
        return undx_agent_runs.get_for_user(cur, user_id, wanted)

    try:
        row = _with_db(run)
    except Exception as exc:
        LOGGER.exception("UNDX_RUN_DETAIL_FAILED user_id=%s error=%s",
                         user_id, exc.__class__.__name__)
        return _json({"ok": False, "message": "Could not read that request."}, 500)

    if not row:
        return _json({"ok": False, "message": "No such request."}, 404)
    return _json({"ok": True, "run": _present(row)})


def register(app) -> None:
    app.register_blueprint(undx_agent_run_blueprint)
