"""HTTP surface for Private Meetings — PulseSoc is the authority, Agora is transport.

All routes live under ``/api/private-office/meetings`` and run the shared
Private Office entry: session auth (401), the server-side feature gate on
``private_meetings`` (503/404/403), and the Office second lock (423). The two
gates are different questions on purpose — "did this member pay for the room"
and "did the person holding the phone just prove they are the member" — and a
meeting containing another person's face and voice deserves both answers.

Meeting authority (lifecycle, waiting room, lock, roles, invites, chat,
recording metadata, artifacts) lives in ``services/private_office/meetings.py``.
The one route that touches media is ``POST .../<ref>/token``: it asks the
canonical communications engine for a join token against the meeting's
room-scope call. Least privilege there is structural, not decorative — the
engine refuses any caller without a live ``communication_call_participants``
row, and admission/removal in the meetings module is exactly the insertion and
revocation of that row. A waiting-room occupant has nothing to present.

Nothing here logs a token, a channel name, or message content. Refusals carry
machine codes; every response is ``no-store``.
"""

from __future__ import annotations

import logging

from flask import Blueprint, request

from services import private_office_routes as po_http
from services import pulsesoc_communications_engine as call_engine
from services.private_office import meetings as po_meetings

MEETINGS_FEATURE_ID = "private_meetings"

LOGGER = logging.getLogger(__name__)

private_office_meetings_blueprint = Blueprint(
    "private_office_meetings", __name__)

#: Truthful capability edges, repeated on the surface a client actually reads.
#: Screens render exactly this — no client invents a capability the server did
#: not assert (mission §28-§31: no fabricated captions, no pretend recording).
PROVIDER_STATUS = {
    "rtc_transport": "agora",
    "meeting_authority": "pulsesoc",
    "transcription": "not_configured",
    "screen_share": "not_implemented",
    "note": (
        "PulseSoc owns admission, roles, the waiting room and the lock; Agora "
        "carries media only. Transcription has no provider, so nothing in a "
        "meeting is ever presented as 'what was said'. Screen share requires "
        "a third governed publication path and is not built."
    ),
}


def _entry():
    """Auth + tier gate + second lock shared by every meetings route."""
    user = po_http._current_user()
    if not user:
        return None, po_http._no_store(
            {"ok": False, "message": "Login required."}, 401)
    resolved = po_http._resolve_for(user)
    refusal = po_http._gate(resolved, MEETINGS_FEATURE_ID)
    if refusal:
        return None, refusal
    locked = po_http._office_lock_gate(user)
    if locked:
        return None, locked
    return user, None


def _body() -> dict:
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _run(work, *, log_tag: str, fail_message: str):
    """Execute ``work(cur)`` through the shared commit-on-success cursor and
    translate the two failure families:

    * ``PrivateMeetingRejected`` already carries its HTTP mapping — the model
      decided, the route only renders. The machine ``code`` rides along so the
      client can distinguish "locked" from "meeting_over" without parsing prose.
    * Anything else is a 503 with ``state: "unavailable"`` — an infrastructure
      failure must never be dressed up as a permission answer.
    """
    try:
        result = po_http._with_cursor(work)
    except po_meetings.PrivateMeetingRejected as exc:
        return None, po_http._no_store(
            {"ok": False, "code": exc.code, "message": str(exc)}, exc.status)
    except Exception:  # noqa: BLE001
        LOGGER.exception(log_tag)
        return None, po_http._no_store(
            {"ok": False, "state": "unavailable", "message": fail_message}, 503)
    return result, None


# --- home: create / schedule / list -----------------------------------------


@private_office_meetings_blueprint.route(
    "/api/private-office/meetings", methods=["POST"])
def api_private_meetings_create():
    """``{"instant": true}`` starts a meeting now; otherwise
    ``scheduled_start_at`` (ISO) + optional ``duration_minutes`` schedules one."""
    user, refusal = _entry()
    if refusal:
        return refusal
    body = _body()

    def work(cur):
        return po_meetings.create_meeting(
            cur,
            owner_user_id=user["user_id"],
            title=str(body.get("title") or ""),
            scheduled_start_at=str(body.get("scheduled_start_at") or ""),
            duration_minutes=int(body.get("duration_minutes") or 0),
            waiting_room_enabled=bool(body.get("waiting_room_enabled", True)),
            instant=bool(body.get("instant")),
        )

    meeting, err = _run(work, log_tag="PRIVATE_MEETINGS_CREATE_FAILED",
                        fail_message="We could not create the meeting just now.")
    if err:
        return err
    return po_http._no_store(
        {"ok": True, "meeting": meeting, "provider_status": PROVIDER_STATUS}, 201)


@private_office_meetings_blueprint.route(
    "/api/private-office/meetings", methods=["GET"])
def api_private_meetings_list():
    user, refusal = _entry()
    if refusal:
        return refusal
    try:
        limit = int(request.args.get("limit") or 20)
    except (TypeError, ValueError):
        limit = 20

    def work(cur):
        # Lazy zombie sweep before reading (mission §41): a stale LIVE row is
        # finalized here rather than served, so no worker outage can leave the
        # home screen advertising a meeting nobody is in.
        po_meetings.sweep_meetings(cur)
        return po_meetings.list_meetings(cur, user_id=user["user_id"],
                                         limit=limit)

    buckets, err = _run(work, log_tag="PRIVATE_MEETINGS_LIST_FAILED",
                        fail_message="We could not load your meetings just now.")
    if err:
        return err
    return po_http._no_store({
        "ok": True,
        "meetings": buckets,
        "capabilities": po_meetings.capability_states(),
        "provider_status": PROVIDER_STATUS,
    })


@private_office_meetings_blueprint.route(
    "/api/private-office/meetings/<meeting_ref>", methods=["GET"])
def api_private_meetings_get(meeting_ref: str):
    user, refusal = _entry()
    if refusal:
        return refusal

    def work(cur):
        po_meetings.sweep_meetings(cur)
        return po_meetings.get_meeting(
            cur, user_id=user["user_id"], meeting_ref=meeting_ref)

    meeting, err = _run(work, log_tag="PRIVATE_MEETINGS_GET_FAILED",
                        fail_message="We could not load this meeting just now.")
    if err:
        return err
    return po_http._no_store(
        {"ok": True, "meeting": meeting, "provider_status": PROVIDER_STATUS})


# --- lifecycle ---------------------------------------------------------------


def _simple_meeting_action(meeting_ref: str, runner, *, log_tag: str,
                           fail_message: str, key: str = "meeting"):
    user, refusal = _entry()
    if refusal:
        return refusal

    def work(cur):
        return runner(cur, user)

    result, err = _run(work, log_tag=log_tag, fail_message=fail_message)
    if err:
        return err
    return po_http._no_store({"ok": True, key: result})


@private_office_meetings_blueprint.route(
    "/api/private-office/meetings/<meeting_ref>/start", methods=["POST"])
def api_private_meetings_start(meeting_ref: str):
    return _simple_meeting_action(
        meeting_ref,
        lambda cur, user: po_meetings.start_meeting(
            cur, actor_user_id=user["user_id"], meeting_ref=meeting_ref),
        log_tag="PRIVATE_MEETINGS_START_FAILED",
        fail_message="We could not start the meeting just now.")


@private_office_meetings_blueprint.route(
    "/api/private-office/meetings/<meeting_ref>/cancel", methods=["POST"])
def api_private_meetings_cancel(meeting_ref: str):
    return _simple_meeting_action(
        meeting_ref,
        lambda cur, user: po_meetings.cancel_meeting(
            cur, actor_user_id=user["user_id"], meeting_ref=meeting_ref),
        log_tag="PRIVATE_MEETINGS_CANCEL_FAILED",
        fail_message="We could not cancel the meeting just now.")


@private_office_meetings_blueprint.route(
    "/api/private-office/meetings/<meeting_ref>/join", methods=["POST"])
def api_private_meetings_join(meeting_ref: str):
    """Join by public id or by meeting code. The response is the viewer's own
    projection: a waiting-room occupant gets a meeting without a call id, so
    there is nothing on the client that could even name the media channel."""
    return _simple_meeting_action(
        meeting_ref,
        lambda cur, user: po_meetings.join_meeting(
            cur, user_id=user["user_id"], meeting_ref=meeting_ref),
        log_tag="PRIVATE_MEETINGS_JOIN_FAILED",
        fail_message="We could not join the meeting just now.")


@private_office_meetings_blueprint.route(
    "/api/private-office/meetings/<meeting_ref>/leave", methods=["POST"])
def api_private_meetings_leave(meeting_ref: str):
    return _simple_meeting_action(
        meeting_ref,
        lambda cur, user: po_meetings.leave_meeting(
            cur, user_id=user["user_id"], meeting_ref=meeting_ref),
        log_tag="PRIVATE_MEETINGS_LEAVE_FAILED",
        fail_message="We could not record your leave just now.")


@private_office_meetings_blueprint.route(
    "/api/private-office/meetings/<meeting_ref>/end", methods=["POST"])
def api_private_meetings_end(meeting_ref: str):
    """End for everyone — moderator only, idempotent, ends the room call."""
    return _simple_meeting_action(
        meeting_ref,
        lambda cur, user: po_meetings.end_meeting(
            cur, actor_user_id=user["user_id"], meeting_ref=meeting_ref),
        log_tag="PRIVATE_MEETINGS_END_FAILED",
        fail_message="We could not end the meeting just now.")


@private_office_meetings_blueprint.route(
    "/api/private-office/meetings/<meeting_ref>/lock", methods=["POST"])
def api_private_meetings_lock(meeting_ref: str):
    body = _body()
    return _simple_meeting_action(
        meeting_ref,
        lambda cur, user: po_meetings.set_locked(
            cur, actor_user_id=user["user_id"], meeting_ref=meeting_ref,
            locked=bool(body.get("locked", True))),
        log_tag="PRIVATE_MEETINGS_LOCK_FAILED",
        fail_message="We could not change the meeting lock just now.")


@private_office_meetings_blueprint.route(
    "/api/private-office/meetings/<meeting_ref>/rotate-code", methods=["POST"])
def api_private_meetings_rotate_code(meeting_ref: str):
    """Host-only. The old code stops resolving the moment this commits."""
    return _simple_meeting_action(
        meeting_ref,
        lambda cur, user: po_meetings.rotate_code(
            cur, actor_user_id=user["user_id"], meeting_ref=meeting_ref),
        log_tag="PRIVATE_MEETINGS_ROTATE_FAILED",
        fail_message="We could not rotate the meeting code just now.")


# --- moderation --------------------------------------------------------------


@private_office_meetings_blueprint.route(
    "/api/private-office/meetings/<meeting_ref>/admit", methods=["POST"])
def api_private_meetings_admit(meeting_ref: str):
    body = _body()
    return _simple_meeting_action(
        meeting_ref,
        lambda cur, user: po_meetings.admit_participant(
            cur, actor_user_id=user["user_id"], meeting_ref=meeting_ref,
            user_id=int(body.get("user_id") or 0)),
        log_tag="PRIVATE_MEETINGS_ADMIT_FAILED",
        fail_message="We could not admit that participant just now.",
        key="participant")


@private_office_meetings_blueprint.route(
    "/api/private-office/meetings/<meeting_ref>/deny", methods=["POST"])
def api_private_meetings_deny(meeting_ref: str):
    body = _body()
    return _simple_meeting_action(
        meeting_ref,
        lambda cur, user: po_meetings.deny_participant(
            cur, actor_user_id=user["user_id"], meeting_ref=meeting_ref,
            user_id=int(body.get("user_id") or 0)),
        log_tag="PRIVATE_MEETINGS_DENY_FAILED",
        fail_message="We could not deny that participant just now.",
        key="participant")


@private_office_meetings_blueprint.route(
    "/api/private-office/meetings/<meeting_ref>/remove", methods=["POST"])
def api_private_meetings_remove(meeting_ref: str):
    body = _body()
    return _simple_meeting_action(
        meeting_ref,
        lambda cur, user: po_meetings.remove_participant(
            cur, actor_user_id=user["user_id"], meeting_ref=meeting_ref,
            user_id=int(body.get("user_id") or 0)),
        log_tag="PRIVATE_MEETINGS_REMOVE_FAILED",
        fail_message="We could not remove that participant just now.",
        key="participant")


@private_office_meetings_blueprint.route(
    "/api/private-office/meetings/<meeting_ref>/role", methods=["POST"])
def api_private_meetings_role(meeting_ref: str):
    body = _body()
    return _simple_meeting_action(
        meeting_ref,
        lambda cur, user: po_meetings.set_role(
            cur, actor_user_id=user["user_id"], meeting_ref=meeting_ref,
            user_id=int(body.get("user_id") or 0),
            role=str(body.get("role") or "")),
        log_tag="PRIVATE_MEETINGS_ROLE_FAILED",
        fail_message="We could not change that role just now.",
        key="participant")


# --- invites -----------------------------------------------------------------


@private_office_meetings_blueprint.route(
    "/api/private-office/meetings/<meeting_ref>/invites", methods=["POST"])
def api_private_meetings_invite(meeting_ref: str):
    user, refusal = _entry()
    if refusal:
        return refusal
    body = _body()
    raw_ids = body.get("user_ids")
    user_ids = [int(v) for v in raw_ids if str(v).strip().isdigit()] \
        if isinstance(raw_ids, list) else []

    def work(cur):
        return po_meetings.invite_users(
            cur, actor_user_id=user["user_id"], meeting_ref=meeting_ref,
            user_ids=user_ids, message=str(body.get("message") or ""))

    result, err = _run(work, log_tag="PRIVATE_MEETINGS_INVITE_FAILED",
                       fail_message="We could not send those invites just now.")
    if err:
        return err
    return po_http._no_store({"ok": True, **result})


@private_office_meetings_blueprint.route(
    "/api/private-office/meetings/<meeting_ref>/invites/respond",
    methods=["POST"])
def api_private_meetings_invite_respond(meeting_ref: str):
    body = _body()
    return _simple_meeting_action(
        meeting_ref,
        lambda cur, user: po_meetings.respond_invite(
            cur, user_id=user["user_id"], meeting_ref=meeting_ref,
            accept=bool(body.get("accept"))),
        log_tag="PRIVATE_MEETINGS_RESPOND_FAILED",
        fail_message="We could not record your response just now.",
        key="result")


# --- in-meeting: chat, reactions, raise hand --------------------------------


@private_office_meetings_blueprint.route(
    "/api/private-office/meetings/<meeting_ref>/messages", methods=["POST"])
def api_private_meetings_message(meeting_ref: str):
    body = _body()
    return _simple_meeting_action(
        meeting_ref,
        lambda cur, user: po_meetings.post_message(
            cur, user_id=user["user_id"], meeting_ref=meeting_ref,
            body=str(body.get("body") or ""),
            kind=str(body.get("kind") or "text")),
        log_tag="PRIVATE_MEETINGS_MESSAGE_FAILED",
        fail_message="We could not send that message just now.",
        key="message")


@private_office_meetings_blueprint.route(
    "/api/private-office/meetings/<meeting_ref>/messages", methods=["GET"])
def api_private_meetings_messages(meeting_ref: str):
    user, refusal = _entry()
    if refusal:
        return refusal
    try:
        since_id = int(request.args.get("since_id") or 0)
    except (TypeError, ValueError):
        since_id = 0
    try:
        limit = int(request.args.get("limit") or 50)
    except (TypeError, ValueError):
        limit = 50

    def work(cur):
        return po_meetings.list_messages(
            cur, user_id=user["user_id"], meeting_ref=meeting_ref,
            since_id=since_id, limit=limit)

    rows, err = _run(work, log_tag="PRIVATE_MEETINGS_MESSAGES_FAILED",
                     fail_message="We could not load messages just now.")
    if err:
        return err
    return po_http._no_store({"ok": True, "messages": rows})


@private_office_meetings_blueprint.route(
    "/api/private-office/meetings/<meeting_ref>/hand", methods=["POST"])
def api_private_meetings_hand(meeting_ref: str):
    body = _body()
    return _simple_meeting_action(
        meeting_ref,
        lambda cur, user: po_meetings.set_raised_hand(
            cur, user_id=user["user_id"], meeting_ref=meeting_ref,
            raised=bool(body.get("raised", True))),
        log_tag="PRIVATE_MEETINGS_HAND_FAILED",
        fail_message="We could not update your raised hand just now.",
        key="participant")


# --- presence + media token --------------------------------------------------


@private_office_meetings_blueprint.route(
    "/api/private-office/meetings/<meeting_ref>/token", methods=["POST"])
def api_private_meetings_token(meeting_ref: str):
    """Mint the media join token for an ADMITTED participant.

    Two authorities agree before any token exists, and both fail closed:

    1. The meetings projection — ``call_public_id`` is only present on the
       viewer's projection when their participant state is ADMITTED or better.
       A waiting-room occupant's projection simply has no call to name.
    2. The engine — ``join_token`` re-checks the room-scope
       ``communication_call_participants`` row (absent for waiting room,
       ``removed`` after moderation) against THIS user id, and binds the minted
       token to user + channel + role + expiry server-side. The rtc uid IS the
       PulseSoc user id, so the token cannot be replayed as someone else.

    The token appears only in this response body. It is never logged, never
    audited beyond the join metadata the model already wrote, never cached
    (``no-store`` like everything here).
    """
    user, refusal = _entry()
    if refusal:
        return refusal

    def load(cur):
        return po_meetings.get_meeting(
            cur, user_id=user["user_id"], meeting_ref=meeting_ref)

    meeting, err = _run(load, log_tag="PRIVATE_MEETINGS_TOKEN_LOAD_FAILED",
                        fail_message="We could not load this meeting just now.")
    if err:
        return err

    call_public_id = str(meeting.get("call_public_id") or "")
    if not call_public_id:
        # Structural refusal: not admitted (waiting room / not a participant)
        # or the meeting has no live call. Either way there is no channel to
        # hand out, so the answer is the same fail-closed 403.
        return po_http._no_store(
            {"ok": False, "code": "not_admitted",
             "message": "You are not admitted to this meeting."}, 403)
    if meeting.get("status") not in ("LIVE", "STARTING"):
        return po_http._no_store(
            {"ok": False, "code": "not_live",
             "message": "This meeting is not live."}, 409)

    try:
        token = call_engine.join_token(int(user["user_id"]), call_public_id)
    except Exception:  # noqa: BLE001
        LOGGER.exception("PRIVATE_MEETINGS_TOKEN_MINT_FAILED")
        return po_http._no_store(
            {"ok": False, "state": "unavailable",
             "message": "We could not prepare the media token just now."}, 503)
    if not token.get("ok"):
        status = int(token.get("http_status") or 403)
        return po_http._no_store(
            {"ok": False, "code": str(token.get("status") or "forbidden"),
             "message": str(token.get("message") or "Media access refused.")},
            status)

    def mark(cur):
        return po_meetings.mark_joined(
            cur, user_id=user["user_id"], meeting_ref=meeting_ref)

    me, err = _run(mark, log_tag="PRIVATE_MEETINGS_MARK_JOINED_FAILED",
                   fail_message="We could not record your join just now.")
    if err:
        return err

    return po_http._no_store({
        "ok": True,
        "call": token.get("call"),
        "join": token.get("join"),
        "me": me,
    })


@private_office_meetings_blueprint.route(
    "/api/private-office/meetings/<meeting_ref>/reconnecting", methods=["POST"])
def api_private_meetings_reconnecting(meeting_ref: str):
    """Presence truthfulness: the tile shows RECONNECTING because the client
    said so, and the same logical participant row absorbs the rejoin."""
    return _simple_meeting_action(
        meeting_ref,
        lambda cur, user: po_meetings.mark_reconnecting(
            cur, user_id=user["user_id"], meeting_ref=meeting_ref),
        log_tag="PRIVATE_MEETINGS_RECONNECTING_FAILED",
        fail_message="We could not update your presence just now.",
        key="participant")


# --- recording (metadata authority; provider capture is a separate concern) --


@private_office_meetings_blueprint.route(
    "/api/private-office/meetings/<meeting_ref>/recording/start",
    methods=["POST"])
def api_private_meetings_recording_start(meeting_ref: str):
    return _simple_meeting_action(
        meeting_ref,
        lambda cur, user: po_meetings.start_recording(
            cur, actor_user_id=user["user_id"], meeting_ref=meeting_ref),
        log_tag="PRIVATE_MEETINGS_REC_START_FAILED",
        fail_message="We could not start the recording just now.",
        key="recording")


@private_office_meetings_blueprint.route(
    "/api/private-office/meetings/<meeting_ref>/recording/stop",
    methods=["POST"])
def api_private_meetings_recording_stop(meeting_ref: str):
    return _simple_meeting_action(
        meeting_ref,
        lambda cur, user: po_meetings.stop_recording(
            cur, actor_user_id=user["user_id"], meeting_ref=meeting_ref),
        log_tag="PRIVATE_MEETINGS_REC_STOP_FAILED",
        fail_message="We could not stop the recording just now.",
        key="recording")


# --- artifacts (UNDX save-to-office lands here; provenance is mandatory) -----


@private_office_meetings_blueprint.route(
    "/api/private-office/meetings/<meeting_ref>/artifacts", methods=["POST"])
def api_private_meetings_artifact_save(meeting_ref: str):
    body = _body()
    return _simple_meeting_action(
        meeting_ref,
        lambda cur, user: po_meetings.save_artifact(
            cur, user_id=user["user_id"], meeting_ref=meeting_ref,
            artifact_type=str(body.get("artifact_type") or ""),
            provenance=str(body.get("provenance") or ""),
            title=str(body.get("title") or ""),
            content=str(body.get("content") or ""),
            evidence_refs=str(body.get("evidence_refs") or "")),
        log_tag="PRIVATE_MEETINGS_ARTIFACT_FAILED",
        fail_message="We could not save that just now.",
        key="artifact")


@private_office_meetings_blueprint.route(
    "/api/private-office/meetings/<meeting_ref>/artifacts", methods=["GET"])
def api_private_meetings_artifact_list(meeting_ref: str):
    user, refusal = _entry()
    if refusal:
        return refusal
    try:
        limit = int(request.args.get("limit") or 20)
    except (TypeError, ValueError):
        limit = 20

    def work(cur):
        return po_meetings.list_artifacts(
            cur, user_id=user["user_id"], meeting_ref=meeting_ref, limit=limit)

    rows, err = _run(work, log_tag="PRIVATE_MEETINGS_ARTIFACTS_FAILED",
                     fail_message="We could not load artifacts just now.")
    if err:
        return err
    return po_http._no_store({"ok": True, "artifacts": rows})


def register(app) -> None:
    app.register_blueprint(private_office_meetings_blueprint)
