"""HTTP surface for the unified PulseSoc presence service.

Every client (web, iOS, Android, desktop) uses these endpoints. There is no
alternative presence path: subsystems that need to know whether a user is
online call services.presence_service directly on the server, and clients read
from here.

Transport note
--------------
Presence is carried over a REST heartbeat rather than a socket. PulseSoc's
realtime layer is SSE plus smart polling; there is no WebSocket server to
attach to. This is not a compromise on accuracy: correctness comes from the
heartbeat expiry being evaluated at read time, so a client that vanishes is
reported offline whether or not any socket noticed it drop.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request, session

from services import presence_service

LOGGER = logging.getLogger(__name__)

presence_blueprint = Blueprint("pulse_presence", __name__)

API_PREFIX = "/api/pulse/presence"

# Presence lookups are cheap but a viewer should not be able to enumerate the
# whole user table in one request.
MAX_BATCH_USERS = 100


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
    # Presence is never cacheable; a cached "online" is a fake "online".
    response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"
    return response, status


def _locale():
    header = (request.headers.get("Accept-Language") or "").strip()
    if not header:
        return "en"
    return header.split(",")[0].split(";")[0].strip() or "en"


def _device_label():
    explicit = (request.headers.get("X-Pulse-Device") or "").strip().lower()
    if explicit:
        return explicit[:40]
    try:
        return _bot().presence_device_label()
    except Exception:
        return "unknown"


def _with_db(handler):
    """Run handler(cur, conn) inside a committed transaction."""
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


def _session_key(user_id: int) -> str:
    return f"presence_session_{int(user_id)}"


# --------------------------------------------------------------------------
# Connection lifecycle
# --------------------------------------------------------------------------

@presence_blueprint.post(f"{API_PREFIX}/connect")
def presence_connect():
    """Open a presence session for this device."""
    user, denied = _require_user()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    user_id = int(user["user_id"])

    def run(cur, conn):
        return presence_service.connect(
            cur,
            user_id,
            device_id=payload.get("device_id") or "",
            device_label=payload.get("device_label") or _device_label(),
            platform=payload.get("platform") or "",
            invisible=bool(payload.get("invisible")),
            conn=conn,
        )

    try:
        result = _with_db(run)
    except Exception as exc:
        LOGGER.exception("PRESENCE_CONNECT_FAILED user_id=%s error=%s", user_id, exc.__class__.__name__)
        return _json({"ok": False, "message": "Could not open presence session."}, 500)

    # Remember the session server-side so a client that loses its copy (page
    # reload) resumes the same session instead of leaking a ghost.
    try:
        session[_session_key(user_id)] = result.get("session_id")
    except Exception:
        pass
    return _json(result)


@presence_blueprint.post(f"{API_PREFIX}/heartbeat")
def presence_heartbeat():
    """Extend this device's session. Called on the interval the server sets."""
    user, denied = _require_user()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    user_id = int(user["user_id"])
    session_id = payload.get("session_id") or session.get(_session_key(user_id)) or ""

    activity = payload.get("activity")
    activity_context = payload.get("activity_context") or ""

    def run(cur, conn):
        result = presence_service.heartbeat(
            cur,
            user_id,
            session_id,
            activity=activity,
            activity_context=activity_context,
            conn=conn,
        )
        # Session unknown (server restart, expiry, first call): transparently
        # re-establish so the client never sits in a dead state.
        if not result.get("ok") and result.get("reconnect"):
            result = presence_service.connect(
                cur,
                user_id,
                device_id=payload.get("device_id") or "",
                device_label=payload.get("device_label") or _device_label(),
                platform=payload.get("platform") or "",
                conn=conn,
            )
            result["reconnected"] = True
            if activity:
                presence_service.set_activity(
                    cur, user_id, result["session_id"], activity, activity_context, conn=conn
                )
        return result

    try:
        result = _with_db(run)
    except presence_service.PresenceError as exc:
        return _json({"ok": False, "message": str(exc)}, 400)
    except Exception as exc:
        LOGGER.exception("PRESENCE_HEARTBEAT_FAILED user_id=%s error=%s", user_id, exc.__class__.__name__)
        return _json({"ok": False, "message": "Heartbeat failed."}, 500)

    if result.get("reconnected"):
        try:
            session[_session_key(user_id)] = result.get("session_id")
        except Exception:
            pass
    return _json(result)


@presence_blueprint.post(f"{API_PREFIX}/disconnect")
def presence_disconnect():
    """Close this device's session (tab close, backgrounding, sign-out).

    Accepts sendBeacon, which cannot set a JSON content type.
    """
    user, denied = _require_user()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    user_id = int(user["user_id"])
    session_id = payload.get("session_id") or session.get(_session_key(user_id)) or ""
    everywhere = bool(payload.get("all"))

    def run(cur, conn):
        if everywhere:
            return presence_service.disconnect_all(cur, user_id, conn=conn)
        return presence_service.disconnect(cur, user_id, session_id, conn=conn)

    try:
        result = _with_db(run)
    except Exception as exc:
        LOGGER.exception("PRESENCE_DISCONNECT_FAILED user_id=%s error=%s", user_id, exc.__class__.__name__)
        return _json({"ok": False, "message": "Disconnect failed."}, 500)

    try:
        session.pop(_session_key(user_id), None)
    except Exception:
        pass
    return _json(result)


# --------------------------------------------------------------------------
# Activity indicators
# --------------------------------------------------------------------------

@presence_blueprint.post(f"{API_PREFIX}/activity")
def presence_activity():
    """Set typing / recording / uploading / in-call / live state.

    Transient activities expire on their own, so a client that dies mid-typing
    cannot leave a stuck indicator on someone else's screen.
    """
    user, denied = _require_user()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    user_id = int(user["user_id"])
    session_id = payload.get("session_id") or session.get(_session_key(user_id)) or ""

    def run(cur, conn):
        return presence_service.set_activity(
            cur,
            user_id,
            session_id,
            payload.get("activity") or "idle",
            payload.get("activity_context") or "",
            conn=conn,
        )

    try:
        result = _with_db(run)
    except presence_service.PresenceError:
        return _json({"ok": False, "message": "Unsupported activity."}, 400)
    except Exception as exc:
        LOGGER.exception("PRESENCE_ACTIVITY_FAILED user_id=%s error=%s", user_id, exc.__class__.__name__)
        return _json({"ok": False, "message": "Could not update activity."}, 500)
    return _json(result)


# --------------------------------------------------------------------------
# Reads
# --------------------------------------------------------------------------

def _parse_ids(raw) -> list[int]:
    if isinstance(raw, (list, tuple)):
        candidates = raw
    else:
        candidates = str(raw or "").replace(" ", "").split(",")
    ids = []
    for item in candidates:
        try:
            value = int(item)
        except (TypeError, ValueError):
            continue
        if value > 0:
            ids.append(value)
    return sorted(set(ids))[:MAX_BATCH_USERS]


@presence_blueprint.get(f"{API_PREFIX}/query")
def presence_query():
    """Read presence for a batch of users, as this viewer may see it."""
    user, denied = _require_user()
    if denied:
        return denied
    viewer_id = int(user["user_id"])
    ids = _parse_ids(request.args.get("user_ids") or request.args.get("ids") or "")
    if not ids:
        return _json({"ok": True, "presence": [], "count": 0})

    def run(cur, conn):
        return presence_service.presence_for(cur, viewer_id, ids, conn=conn, locale=_locale())

    try:
        mapping = _with_db(run)
    except Exception as exc:
        LOGGER.exception("PRESENCE_QUERY_FAILED viewer=%s error=%s", viewer_id, exc.__class__.__name__)
        return _json({"ok": False, "message": "Could not read presence."}, 500)

    return _json({
        "ok": True,
        "presence": [mapping[uid] for uid in ids if uid in mapping],
        "count": len(mapping),
        "heartbeat_interval_seconds": presence_service.HEARTBEAT_INTERVAL_SECONDS,
    })


@presence_blueprint.get(f"{API_PREFIX}/user/<int:target_user_id>")
def presence_user(target_user_id: int):
    """Read one user's presence, as this viewer may see it."""
    user, denied = _require_user()
    if denied:
        return denied
    viewer_id = int(user["user_id"])

    def run(cur, conn):
        return presence_service.presence_of(cur, viewer_id, int(target_user_id), conn=conn, locale=_locale())

    try:
        result = _with_db(run)
    except Exception as exc:
        LOGGER.exception("PRESENCE_USER_FAILED viewer=%s error=%s", viewer_id, exc.__class__.__name__)
        return _json({"ok": False, "message": "Could not read presence."}, 500)
    return _json({"ok": True, "presence": result})


@presence_blueprint.get(f"{API_PREFIX}/me")
def presence_me():
    """The caller's own presence, live devices, and privacy settings."""
    user, denied = _require_user()
    if denied:
        return denied
    user_id = int(user["user_id"])

    def run(cur, conn):
        return {
            "presence": presence_service.presence_of(cur, user_id, user_id, conn=conn, locale=_locale()),
            "devices": presence_service.active_sessions(cur, user_id, conn=conn),
            "privacy": presence_service.get_privacy(cur, user_id, conn=conn),
        }

    try:
        result = _with_db(run)
    except Exception as exc:
        LOGGER.exception("PRESENCE_ME_FAILED user_id=%s error=%s", user_id, exc.__class__.__name__)
        return _json({"ok": False, "message": "Could not read presence."}, 500)

    result["ok"] = True
    result["heartbeat_interval_seconds"] = presence_service.HEARTBEAT_INTERVAL_SECONDS
    result["grace_period_seconds"] = presence_service.GRACE_PERIOD_SECONDS
    return _json(result)


# --------------------------------------------------------------------------
# Privacy
# --------------------------------------------------------------------------

@presence_blueprint.post(f"{API_PREFIX}/privacy")
def presence_privacy():
    """Update Hide Last Seen / Invisible Mode for the calling user."""
    user, denied = _require_user()
    if denied:
        return denied
    payload = request.get_json(silent=True) or {}
    user_id = int(user["user_id"])

    hide_last_seen = payload.get("hide_last_seen")
    invisible_mode = payload.get("invisible_mode")
    if hide_last_seen is None and invisible_mode is None:
        return _json({"ok": False, "message": "Nothing to update."}, 400)

    def run(cur, conn):
        return presence_service.set_privacy(
            cur,
            user_id,
            hide_last_seen=None if hide_last_seen is None else bool(hide_last_seen),
            invisible_mode=None if invisible_mode is None else bool(invisible_mode),
            conn=conn,
        )

    try:
        result = _with_db(run)
    except Exception as exc:
        LOGGER.exception("PRESENCE_PRIVACY_FAILED user_id=%s error=%s", user_id, exc.__class__.__name__)
        return _json({"ok": False, "message": "Could not update privacy."}, 500)
    return _json(result)


# --------------------------------------------------------------------------
# Diagnostics
# --------------------------------------------------------------------------

@presence_blueprint.get(f"{API_PREFIX}/health")
def presence_health():
    """Real presence counters. Values are measured, never synthesised."""
    def run(cur, conn):
        return presence_service.health_snapshot(cur, conn=conn)

    try:
        snapshot = _with_db(run)
    except Exception as exc:
        LOGGER.exception("PRESENCE_HEALTH_FAILED error=%s", exc.__class__.__name__)
        return _json({"ok": False, "message": "Could not read presence health."}, 500)
    snapshot["ok"] = True
    return _json(snapshot)


def register(app) -> None:
    app.register_blueprint(presence_blueprint)
