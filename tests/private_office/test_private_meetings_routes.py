"""Private Meetings over HTTP — the authority chain, exercised end to end.

Run either way::

    python -m pytest tests/private_office/test_private_meetings_routes.py
    python tests/private_office/test_private_meetings_routes.py

What these tests defend
-----------------------
* **The gate order holds over HTTP.** No session is 401, no tier is 403, the
  kill switch is 404, a locked Office is 423 — before any meeting is touched.
  And unlike every older Office feature, the meetings switch defaults OFF: a
  deploy without ``PRIVATE_MEETINGS_ENABLED`` has no meetings surface (§54).
* **The waiting room is the absence of a capability, not a UI state.** A
  waiting occupant's projection contains no call id and no channel name, the
  token route answers 403, and the chat reads refuse — there is nothing on the
  wire that could reach media.
* **Admission opens exactly one door.** After admit, the token route mints a
  REAL Agora token (offline HMAC, dummy credentials) bound to this user id as
  the rtc uid and to the meeting's own channel.
* **Moderation closes it again.** Deny/remove make the meeting read as 404 and
  the token route refuse; code rotation makes the old code stop resolving.
* **Recording never lies.** Flag-gated; a REQUESTED recording stopped before
  it went active closes FAILED; ``recording_active`` is true only while a
  recording is genuinely ACTIVE.
* **No fabricated transcripts.** TRANSCRIPT_DERIVED artifacts are refused with
  409 while no transcription provider exists; USER_CONFIRMED saves fine and is
  owner-scoped.
* **Audit is metadata-only.** Meeting titles and message bodies never appear
  in audit rows.
"""

import os
import sqlite3
import sys
import tempfile
import types

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_meetings_routes_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
# Offline Agora credentials: RtcTokenBuilder is pure HMAC, so a real token is
# minted with dummy values — no network, no real project touched.
os.environ.setdefault("AGORA_APP_ID", "test-agora-app-id")
os.environ.setdefault("AGORA_APP_CERTIFICATE", "test-agora-app-certificate")

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

# --- stub the monolith BEFORE the route packs can import it -----------------
_stub = types.ModuleType("bot")
_stub._test_user = None


def _api_account_user():
    return _stub._test_user


def _require_admin_api(permission):
    return (None, ("DENIED", 403))


_stub.api_account_user = _api_account_user
_stub.require_admin_api = _require_admin_api
# The communications engine reaches its database through ``bot.db()``
# (pulse_communications_v2/service.py::_open_db) — give the stub the same
# connection the rest of the suite uses so the token route's engine leg runs
# for real against the temp database.
_stub.sqlite3 = sqlite3


def _stub_db():
    from services import db as _db
    return _db.connect()


_stub.db = _stub_db
sys.modules["bot"] = _stub

from flask import Flask  # noqa: E402
from flask.testing import FlaskClient  # noqa: E402

from services import db  # noqa: E402
from services.business_os.entitlements import service as svc  # noqa: E402
from services import private_office_routes as routes  # noqa: E402
from services import private_office_meetings_routes as mt_routes  # noqa: E402
from services.private_office import feature_matrix as matrix  # noqa: E402
from services.private_office import meetings as meetings_mod  # noqa: E402
from services.private_office import schema  # noqa: E402
from services.private_office import tiers  # noqa: E402

HOST = 9931       # meeting host
GUEST = 9932      # admitted guest
DENIED = 9933     # waits, is denied
LATE = 9934       # joins after code rotation
NO_TIER = 9935    # real session, no Private tier

#: Content that must never surface in audit rows.
_SECRETS = ("Q3 Succession Planning", "the-first-chat-line")

_FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    if condition:
        print(f"  PASS  {label}")
        return True
    _FAILURES.append(f"{label}{(' — ' + detail) if detail else ''}")
    print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")
    return False


_GRANTS: dict[int, str] = {}
PASSCODE = "462871"


class _GrantClient(FlaskClient):
    def open(self, *args, **kwargs):
        user = _stub._test_user or {}
        token = _GRANTS.get(int(user.get("user_id") or 0), "")
        if token:
            headers = dict(kwargs.get("headers") or {})
            headers.setdefault(routes.GRANT_HEADER, token)
            kwargs["headers"] = headers
        return super().open(*args, **kwargs)


def _app():
    app = Flask(__name__)
    app.test_client_class = _GrantClient
    routes.register(app)
    mt_routes.register(app)
    return app


def _as(user_id):
    _stub._test_user = {"user_id": user_id, "account_status": "active",
                        "access_enabled": 1}


def _unlock(user_id):
    app = Flask(__name__)
    routes.register(app)
    client = app.test_client()
    _as(user_id)
    client.post("/api/private-office/security/setup",
                json={"passcode": PASSCODE, "confirm_passcode": PASSCODE})
    resp = client.post("/api/private-office/security/unlock",
                       json={"passcode": PASSCODE})
    token = (resp.get_json() or {}).get("grant_token") or ""
    if token:
        _GRANTS[int(user_id)] = token
    return token


def setup_environment():
    svc.ensure_schema()
    conn = db.connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "user_id INTEGER PRIMARY KEY, username TEXT DEFAULT '', "
            "display_name TEXT DEFAULT '', avatar_url TEXT DEFAULT '', "
            "account_status TEXT DEFAULT 'active', "
            "access_enabled INTEGER DEFAULT 1)"
        )
        conn.execute("DELETE FROM users")
        for uid in (HOST, GUEST, DENIED, LATE, NO_TIER):
            conn.execute(
                "INSERT INTO users (user_id, username, account_status, access_enabled) "
                "VALUES (?, ?, ?, 1)", (uid, f"member{uid}", "active"))
        cur = conn.cursor()
        schema.ensure_private_schema(cur, force=True)
        meetings_mod.ensure_meetings_schema(cur, force=True)
        conn.commit()
    finally:
        conn.close()
    for uid in (HOST, GUEST, DENIED, LATE):
        svc.grant_entitlement(uid, "private_office.access", source="admin")
    _GRANTS.clear()
    for uid in (HOST, GUEST, DENIED, LATE):
        _unlock(uid)
    os.environ["PRIVATE_MEETINGS_ENABLED"] = "1"
    _stub._test_user = None


def _query_all(sql, params=()):
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def _service(work):
    conn = db.connect()
    try:
        cur = conn.cursor()
        out = work(cur)
        conn.commit()
        return out
    finally:
        conn.close()


_STATE = {}  # ids threaded between stages


# ---------------------------------------------------------------------------
# Gate order + the fail-closed default
# ---------------------------------------------------------------------------

def stage_gates():
    print("\n[meetings routes: gates]")
    client = _app().test_client()

    _stub._test_user = None
    resp = client.get("/api/private-office/meetings")
    check("no session is 401", resp.status_code == 401, str(resp.status_code))

    _as(NO_TIER)
    resp = client.get("/api/private-office/meetings")
    body = resp.get_json() or {}
    check("no Private tier is 403 with a minimum tier",
          resp.status_code == 403 and body.get("minimum_tier"),
          f"{resp.status_code} {body}")

    # §54: ABSENT env var means OFF for meetings — not merely a "false" value.
    previous = os.environ.pop("PRIVATE_MEETINGS_ENABLED", None)
    try:
        _as(HOST)
        resp = client.get("/api/private-office/meetings")
        check("ABSENT flag is 404 for an entitled member (default OFF, §54)",
              resp.status_code == 404, str(resp.status_code))
        state = matrix.availability("private_meetings", tiers.TIER_PRIVATE)
        check("absent flag reads FEATURE_DISABLED, implementation IMPLEMENTED",
              state["availability"] == matrix.AVAIL_FEATURE_DISABLED
              and state["implementation"] == matrix.IMPL_IMPLEMENTED, str(state))
        os.environ["PRIVATE_MEETINGS_ENABLED"] = "false"
        resp = client.get("/api/private-office/meetings")
        check("explicit false is 404 too", resp.status_code == 404,
              str(resp.status_code))
    finally:
        if previous is None:
            os.environ.pop("PRIVATE_MEETINGS_ENABLED", None)
        else:
            os.environ["PRIVATE_MEETINGS_ENABLED"] = previous
    os.environ["PRIVATE_MEETINGS_ENABLED"] = "1"

    _as(HOST)
    resp = client.get("/api/private-office/meetings",
                      headers={routes.GRANT_HEADER: ""})
    check("no unlock grant is 423 Locked", resp.status_code == 423,
          str(resp.status_code))

    resp = client.get("/api/private-office/meetings")
    body = resp.get_json() or {}
    check("entitled + unlocked + flag on is 200 with buckets",
          resp.status_code == 200 and set(body.get("meetings") or {})
          == {"live", "upcoming", "recent"}, f"{resp.status_code} {body}")


def stage_flag_defaults():
    print("\n[meetings routes: flag defaults]")
    # The new default-off machinery must not have moved any historical row.
    briefings = matrix.availability("private_briefings", tiers.TIER_PRIVATE)
    check("historical rows keep absent-env == enabled (briefings ENTITLED)",
          briefings["availability"] == matrix.AVAIL_ENTITLED, str(briefings))
    rec = matrix.availability("private_meetings.recording", tiers.TIER_PRIVATE)
    check("recording row defaults OFF as well",
          rec["availability"] == matrix.AVAIL_FEATURE_DISABLED, str(rec))
    tx = matrix.availability("private_meetings.transcription", tiers.TIER_PRIVATE)
    check("transcription is NOT_IMPLEMENTED (provider required) for everyone",
          tx["availability"] == matrix.AVAIL_NOT_IMPLEMENTED, str(tx))
    ss = matrix.availability("private_meetings.screen_share", tiers.TIER_PRIVATE)
    check("screen share is NOT_IMPLEMENTED for everyone",
          ss["availability"] == matrix.AVAIL_NOT_IMPLEMENTED, str(ss))


# ---------------------------------------------------------------------------
# Create / schedule
# ---------------------------------------------------------------------------

def stage_create():
    print("\n[meetings routes: create]")
    client = _app().test_client()
    _as(HOST)

    resp = client.post("/api/private-office/meetings",
                       json={"instant": True, "title": _SECRETS[0]})
    body = resp.get_json() or {}
    meeting = body.get("meeting") or {}
    _STATE["public_id"] = meeting.get("public_id") or ""
    _STATE["code"] = meeting.get("meeting_code") or ""
    _STATE["channel"] = meeting.get("channel_name") or ""
    check("instant meeting is 201 and LIVE",
          resp.status_code == 201 and meeting.get("status") == "LIVE",
          f"{resp.status_code} {meeting.get('status')}")
    check("host projection carries the call id and channel",
          bool(meeting.get("call_public_id")) and bool(_STATE["channel"]),
          str(meeting.get("call_public_id")))
    code = _STATE["code"]
    check("meeting code is NNN-NNN-NNN",
          len(code) == 11 and code.count("-") == 2
          and code.replace("-", "").isdigit(), code)
    caps = meeting.get("capabilities") or {}
    check("capabilities are truthful in the payload",
          caps.get("screen_share", {}).get("available") is False
          and caps.get("captions", {}).get("available") is False, str(caps))

    resp = client.post("/api/private-office/meetings",
                       json={"title": "Planning sync",
                             "scheduled_start_at": "2026-09-08T15:00:00+00:00",
                             "duration_minutes": 45})
    scheduled = (resp.get_json() or {}).get("meeting") or {}
    _STATE["scheduled_id"] = scheduled.get("public_id") or ""
    check("scheduled meeting is 201 and SCHEDULED",
          resp.status_code == 201 and scheduled.get("status") == "SCHEDULED",
          f"{resp.status_code} {scheduled.get('status')}")

    resp = client.post("/api/private-office/meetings",
                       json={"scheduled_start_at": "not-a-time"})
    check("invalid schedule is 400", resp.status_code == 400,
          str(resp.status_code))


# ---------------------------------------------------------------------------
# Waiting room = no capability
# ---------------------------------------------------------------------------

def stage_waiting_room():
    print("\n[meetings routes: waiting room]")
    client = _app().test_client()
    _as(GUEST)

    resp = client.post(f"/api/private-office/meetings/{_STATE['code']}/join")
    meeting = (resp.get_json() or {}).get("meeting") or {}
    me = meeting.get("me") or {}
    check("joining by code lands in the waiting room",
          resp.status_code == 200 and me.get("state") == "WAITING_ROOM",
          f"{resp.status_code} {me.get('state')}")
    check("waiting projection has no call id, channel, or meeting code",
          "call_public_id" not in meeting and "channel_name" not in meeting
          and "meeting_code" not in meeting, str(sorted(meeting)))

    resp = client.post(
        f"/api/private-office/meetings/{_STATE['public_id']}/token")
    body = resp.get_json() or {}
    check("waiting occupant's token request is 403 not_admitted",
          resp.status_code == 403 and body.get("code") == "not_admitted",
          f"{resp.status_code} {body}")

    resp = client.get(
        f"/api/private-office/meetings/{_STATE['public_id']}/messages")
    check("waiting occupant cannot read chat", resp.status_code == 403,
          str(resp.status_code))


# ---------------------------------------------------------------------------
# Admission opens exactly one door: a real bound token
# ---------------------------------------------------------------------------

def stage_admit_and_token():
    print("\n[meetings routes: admit + token]")
    client = _app().test_client()

    _as(GUEST)
    resp = client.post(
        f"/api/private-office/meetings/{_STATE['public_id']}/admit",
        json={"user_id": GUEST})
    check("a participant cannot admit themselves", resp.status_code == 403,
          str(resp.status_code))

    _as(HOST)
    resp = client.post(
        f"/api/private-office/meetings/{_STATE['public_id']}/admit",
        json={"user_id": GUEST})
    participant = (resp.get_json() or {}).get("participant") or {}
    check("host admits the guest",
          resp.status_code == 200 and participant.get("state") == "ADMITTED",
          f"{resp.status_code} {participant.get('state')}")

    _as(GUEST)
    resp = client.post(
        f"/api/private-office/meetings/{_STATE['public_id']}/token")
    body = resp.get_json() or {}
    join = body.get("join") or {}
    check("admitted guest mints a real token", resp.status_code == 200
          and len(str(join.get("token") or "")) > 40,
          f"{resp.status_code} {str(join.get('token'))[:24]}")
    check("token is bound to the meeting's own channel",
          join.get("channel_name") == _STATE["channel"], str(join.get("channel_name")))
    check("rtc uid IS the PulseSoc user id", join.get("uid") == GUEST,
          str(join.get("uid")))
    check("token has an expiry and publish scope",
          bool(join.get("expires_at")) and join.get("can_publish") is True,
          str(join))
    me = body.get("me") or {}
    check("presence flips to JOINED on mint", me.get("state") == "JOINED",
          str(me.get("state")))


# ---------------------------------------------------------------------------
# Chat
# ---------------------------------------------------------------------------

def stage_chat():
    print("\n[meetings routes: chat]")
    client = _app().test_client()
    ref = _STATE["public_id"]

    _as(GUEST)
    resp = client.post(f"/api/private-office/meetings/{ref}/messages",
                       json={"body": _SECRETS[1]})
    message = (resp.get_json() or {}).get("message") or {}
    _STATE["first_message_id"] = int(message.get("id") or 0)
    check("an admitted guest can post", resp.status_code == 200
          and message.get("body") == _SECRETS[1], str(resp.status_code))

    resp = client.post(f"/api/private-office/meetings/{ref}/messages",
                       json={"body": "🔥", "kind": "reaction"})
    check("reactions post as their own kind", resp.status_code == 200
          and ((resp.get_json() or {}).get("message") or {}).get("kind")
          == "reaction", str(resp.status_code))

    resp = client.post(f"/api/private-office/meetings/{ref}/messages",
                       json={"body": "   "})
    check("empty body is 400", resp.status_code == 400, str(resp.status_code))

    _as(HOST)
    resp = client.get(f"/api/private-office/meetings/{ref}/messages")
    rows = (resp.get_json() or {}).get("messages") or []
    check("the host reads the persisted thread",
          resp.status_code == 200 and len(rows) == 2, str(len(rows)))
    resp = client.get(
        f"/api/private-office/meetings/{ref}/messages"
        f"?since_id={_STATE['first_message_id']}")
    rows = (resp.get_json() or {}).get("messages") or []
    check("since_id pages incrementally", len(rows) == 1
          and rows[0].get("kind") == "reaction", str(rows))

    _as(GUEST)
    resp = client.post(f"/api/private-office/meetings/{ref}/hand",
                       json={"raised": True})
    participant = (resp.get_json() or {}).get("participant") or {}
    check("raise hand persists", resp.status_code == 200
          and bool(participant.get("raised_hand_at")), str(participant))


# ---------------------------------------------------------------------------
# Moderation: deny, roles, code rotation
# ---------------------------------------------------------------------------

def stage_moderation():
    print("\n[meetings routes: moderation]")
    client = _app().test_client()
    ref = _STATE["public_id"]

    _as(DENIED)
    client.post(f"/api/private-office/meetings/{_STATE['code']}/join")
    _as(HOST)
    resp = client.post(f"/api/private-office/meetings/{ref}/deny",
                       json={"user_id": DENIED})
    check("host denies the waiting occupant", resp.status_code == 200,
          str(resp.status_code))
    _as(DENIED)
    resp = client.get(f"/api/private-office/meetings/{ref}")
    check("a denied person reads the meeting as 404",
          resp.status_code == 404, str(resp.status_code))
    resp = client.post(f"/api/private-office/meetings/{ref}/token")
    check("and cannot mint a token", resp.status_code in (403, 404),
          str(resp.status_code))

    _as(GUEST)
    resp = client.post(f"/api/private-office/meetings/{ref}/role",
                       json={"user_id": GUEST, "role": "CO_HOST"})
    check("role changes are host-only", resp.status_code == 403,
          str(resp.status_code))
    _as(HOST)
    resp = client.post(f"/api/private-office/meetings/{ref}/role",
                       json={"user_id": GUEST, "role": "CO_HOST"})
    check("host promotes the guest to co-host", resp.status_code == 200
          and ((resp.get_json() or {}).get("participant") or {}).get("role")
          == "CO_HOST", str(resp.status_code))

    _as(GUEST)
    resp = client.post(f"/api/private-office/meetings/{ref}/lock",
                       json={"locked": True})
    meeting = (resp.get_json() or {}).get("meeting") or {}
    check("a co-host can lock the meeting", resp.status_code == 200
          and meeting.get("locked") is True, str(resp.status_code))
    _as(HOST)
    client.post(f"/api/private-office/meetings/{ref}/lock",
                json={"locked": False})

    old_code = _STATE["code"]
    resp = client.post(f"/api/private-office/meetings/{ref}/rotate-code")
    rotated = (resp.get_json() or {}).get("meeting") or {}
    new_code = rotated.get("meeting_code") or ""
    check("host rotates the code", resp.status_code == 200
          and new_code and new_code != old_code, new_code)
    _STATE["code"] = new_code

    _as(LATE)
    resp = client.post(f"/api/private-office/meetings/{old_code}/join")
    check("the old code no longer resolves", resp.status_code == 404,
          str(resp.status_code))
    resp = client.post(f"/api/private-office/meetings/{new_code}/join")
    me = ((resp.get_json() or {}).get("meeting") or {}).get("me") or {}
    check("the new code admits to the waiting room",
          resp.status_code == 200 and me.get("state") == "WAITING_ROOM",
          f"{resp.status_code} {me.get('state')}")


# ---------------------------------------------------------------------------
# Recording: flag-gated, truthful states
# ---------------------------------------------------------------------------

def stage_recording():
    print("\n[meetings routes: recording]")
    client = _app().test_client()
    ref = _STATE["public_id"]
    _as(HOST)

    resp = client.post(f"/api/private-office/meetings/{ref}/recording/start")
    check("recording without its flag is 403 recording_disabled",
          resp.status_code == 403
          and (resp.get_json() or {}).get("code") == "recording_disabled",
          str(resp.status_code))

    os.environ["PRIVATE_MEETINGS_RECORDING_ENABLED"] = "1"
    try:
        resp = client.post(f"/api/private-office/meetings/{ref}/recording/start")
        recording = (resp.get_json() or {}).get("recording") or {}
        check("recording starts as REQUESTED", resp.status_code == 200
              and recording.get("status") == "REQUESTED",
              str(recording.get("status")))
        resp = client.post(f"/api/private-office/meetings/{ref}/recording/stop")
        recording = (resp.get_json() or {}).get("recording") or {}
        check("stopping a never-active recording closes FAILED, not COMPLETED",
              recording.get("status") == "FAILED", str(recording.get("status")))

        resp = client.post(f"/api/private-office/meetings/{ref}/recording/start")
        recording = (resp.get_json() or {}).get("recording") or {}
        rec_id = int(recording.get("id") or 0)
        _service(lambda cur: meetings_mod.mark_recording_active(
            cur, meeting_ref=ref, recording_id=rec_id,
            provider_resource_id="res-1", provider_sid="sid-1"))
        resp = client.get(f"/api/private-office/meetings/{ref}")
        meeting = (resp.get_json() or {}).get("meeting") or {}
        check("recording_active shows in the projection while ACTIVE",
              meeting.get("recording_active") is True, str(meeting.get("recording_active")))
        resp = client.post(f"/api/private-office/meetings/{ref}/recording/stop")
        recording = (resp.get_json() or {}).get("recording") or {}
        check("an active recording stops COMPLETED",
              recording.get("status") == "COMPLETED", str(recording.get("status")))
    finally:
        os.environ.pop("PRIVATE_MEETINGS_RECORDING_ENABLED", None)


# ---------------------------------------------------------------------------
# Artifacts: provenance honesty
# ---------------------------------------------------------------------------

def stage_artifacts():
    print("\n[meetings routes: artifacts]")
    client = _app().test_client()
    ref = _STATE["public_id"]
    _as(HOST)

    resp = client.post(
        f"/api/private-office/meetings/{ref}/artifacts",
        json={"artifact_type": "SUMMARY", "provenance": "TRANSCRIPT_DERIVED",
              "title": "Summary", "content": "What was said"})
    body = resp.get_json() or {}
    check("TRANSCRIPT_DERIVED is refused while no transcription exists",
          resp.status_code == 409 and body.get("code") == "transcript_unavailable",
          f"{resp.status_code} {body}")

    resp = client.post(
        f"/api/private-office/meetings/{ref}/artifacts",
        json={"artifact_type": "DECISION", "provenance": "USER_CONFIRMED",
              "title": "Ship it", "content": "We agreed to ship on Friday."})
    artifact = (resp.get_json() or {}).get("artifact") or {}
    check("USER_CONFIRMED saves", resp.status_code == 200
          and artifact.get("provenance") == "USER_CONFIRMED",
          str(resp.status_code))

    _as(GUEST)
    resp = client.get(f"/api/private-office/meetings/{ref}/artifacts")
    rows = (resp.get_json() or {}).get("artifacts") or []
    check("artifacts are owner-scoped — the guest sees none of the host's",
          resp.status_code == 200 and rows == [], str(rows))


# ---------------------------------------------------------------------------
# Presence + end for everyone
# ---------------------------------------------------------------------------

def stage_presence_and_end():
    print("\n[meetings routes: presence + end]")
    client = _app().test_client()
    ref = _STATE["public_id"]

    _as(GUEST)
    resp = client.post(f"/api/private-office/meetings/{ref}/reconnecting")
    participant = (resp.get_json() or {}).get("participant") or {}
    check("a client can truthfully report RECONNECTING",
          resp.status_code == 200 and participant.get("state") == "RECONNECTING",
          str(participant.get("state")))
    resp = client.post(f"/api/private-office/meetings/{ref}/token")
    me = (resp.get_json() or {}).get("me") or {}
    check("re-minting after reconnect rejoins the same logical participant",
          resp.status_code == 200 and me.get("state") == "JOINED",
          f"{resp.status_code} {me.get('state')}")

    resp = client.post(f"/api/private-office/meetings/{ref}/end")
    check("end-for-everyone is moderator-scoped, and a co-host may",
          resp.status_code == 200, str(resp.status_code))
    _as(HOST)
    resp = client.get(f"/api/private-office/meetings/{ref}")
    meeting = (resp.get_json() or {}).get("meeting") or {}
    check("the meeting reads ENDED", meeting.get("status") == "ENDED",
          str(meeting.get("status")))

    _as(GUEST)
    resp = client.post(f"/api/private-office/meetings/{ref}/token")
    check("no token after the end", resp.status_code in (403, 409),
          str(resp.status_code))
    resp = client.post(f"/api/private-office/meetings/{ref}/join")
    check("joining an ended meeting is 410", resp.status_code == 410,
          str(resp.status_code))

    _as(HOST)
    resp = client.get("/api/private-office/meetings")
    buckets = (resp.get_json() or {}).get("meetings") or {}
    recents = [m.get("public_id") for m in buckets.get("recent") or []]
    check("the ended meeting lands in the recent bucket", ref in recents,
          str(recents))


# ---------------------------------------------------------------------------
# Isolation + audit truthfulness
# ---------------------------------------------------------------------------

def stage_isolation_and_audit():
    print("\n[meetings routes: isolation + audit]")
    client = _app().test_client()

    _as(GUEST)
    resp = client.post("/api/private-office/meetings", json={"instant": True})
    other = (resp.get_json() or {}).get("meeting") or {}
    _as(HOST)
    resp = client.get(f"/api/private-office/meetings/{other.get('public_id')}")
    check("a non-participant reads another member's meeting as 404",
          resp.status_code == 404, str(resp.status_code))

    rows = _query_all(
        f"SELECT * FROM {schema.AUDIT_TABLE} WHERE action LIKE 'PRIVATE_MEETING%'")
    check("meeting actions were audited", len(rows) >= 10, str(len(rows)))
    blob = " ".join(str(v) for row in rows for v in row.values())
    leaked = [secret for secret in _SECRETS if secret in blob]
    check("audit rows carry metadata only — no titles, no chat bodies",
          not leaked, str(leaked))
    check("audit object ids are MEETING:<id> refs",
          all(str(r.get("object_id") or "").startswith("MEETING:")
              for r in rows), str(rows[:1]))


STAGES = (
    stage_gates,
    stage_flag_defaults,
    stage_create,
    stage_waiting_room,
    stage_admit_and_token,
    stage_chat,
    stage_moderation,
    stage_recording,
    stage_artifacts,
    stage_presence_and_end,
    stage_isolation_and_audit,
)


def test_everything():
    setup_environment()
    for stage in STAGES:
        stage()
    assert not _FAILURES, "\n".join(_FAILURES)


def main() -> int:
    setup_environment()
    for stage in STAGES:
        stage()
    print()
    if _FAILURES:
        print(f"FAILURES ({len(_FAILURES)}):")
        for failure in _FAILURES:
            print(f"  - {failure}")
        return 1
    print("All private meetings route checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
