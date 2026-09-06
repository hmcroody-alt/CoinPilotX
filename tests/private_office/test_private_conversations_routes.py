"""Private Conversations over HTTP — the gate chain and the delegation boundary.

Run either way::

    python -m pytest tests/private_office/test_private_conversations_routes.py
    python tests/private_office/test_private_conversations_routes.py

What these tests defend
-----------------------
* **The gate order holds over HTTP, in this order.** No session is 401, no
  Private tier is 403, the kill switch is 404, a locked Office is 423 — each
  one decided before a single conversation row is read. The order matters:
  answering 423 to a member with no tier would tell them the feature exists and
  invite them to try a passcode against a surface they can never reach.
* **The flag is fail-closed.** An ABSENT ``PRIVATE_CONVERSATIONS_ENABLED`` is
  404, not 200. That is the opposite of the feature matrix's historical
  absent-means-on convention, so it is asserted rather than assumed.
* **No second message ledger, proven across the HTTP surface.** The full route
  cycle — create, send, list, read — is driven through the blueprint, and then
  the database is inspected: the only tables this package created are its two,
  neither has a message-body column, and the message that was sent is in
  ``comm_v2_messages``. A future ``private_office_messages`` table fails here.
* **The route pack has no membership opinion of its own.** A stranger with full
  entitlement, an unlocked Office and the flag on is still refused — because
  the canonical service says they are not a participant, not because this pack
  ran a second participant query.
* **Failure is never an empty list.** When the read path raises, the list route
  answers 503 with ``state: "unavailable"`` and no ``conversations`` key. A
  screen cannot render "no conversations yet" over a failed fetch, because
  there is nothing there to render as empty.
* **No surface claims encryption.** The capabilities route reports
  ``end_to_end_encrypted: False``, and no route response anywhere in the cycle
  contains a true value under that key.
"""

import json
import os
import sqlite3
import sys
import tempfile
import types

_TMP_DB = os.path.join(
    tempfile.mkdtemp(prefix="private_conversations_routes_"), "test.db"
)
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

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
_stub.sqlite3 = sqlite3


def _stub_db():
    # The canonical communications engine reaches its database through
    # ``bot.db()``. Handing it the same connector the rest of the suite uses is
    # what makes the delegation in these tests real: the message rows the
    # assertions look for are written by pulse_communications_v2 itself.
    from services import db as _db

    return _db.connect()


_stub.db = _stub_db
sys.modules["bot"] = _stub

import pytest  # noqa: E402
from flask import Flask  # noqa: E402
from flask.testing import FlaskClient  # noqa: E402

from pulse_communications_v2 import service as comm_service  # noqa: E402
from services import db  # noqa: E402
from services import private_office_conversations_routes as convo_routes  # noqa: E402
from services import private_office_routes as routes  # noqa: E402
from services.business_os.entitlements import service as svc  # noqa: E402
from services.private_office import conversations as convo  # noqa: E402
from services.private_office import feature_matrix as matrix  # noqa: E402
from services.private_office import schema  # noqa: E402
from services.private_office import tiers  # noqa: E402

OWNER = 8801     # creates the thread
GUEST = 8802     # added participant
STRANGER = 8803  # entitled, unlocked, flag on — and still not a participant
NO_TIER = 8804   # real session, no Private tier

PASSCODE = "913574"
_GRANTS: dict[int, str] = {}

BASE = convo_routes.BASE


class _GrantClient(FlaskClient):
    """Attach the caller's unlock grant automatically.

    Every route here sits behind the Office second lock, so without this every
    single request would answer 423 and the interesting assertions would never
    run. The lock itself is still tested — by deliberately sending an empty
    grant header.
    """

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
    convo_routes.register(app)
    return app


def _as(user_id):
    _stub._test_user = {
        "user_id": user_id,
        "account_status": "active",
        "access_enabled": 1,
    }


def _unlock(user_id):
    app = Flask(__name__)
    routes.register(app)
    client = app.test_client()
    _as(user_id)
    client.post(
        "/api/private-office/security/setup",
        json={"passcode": PASSCODE, "confirm_passcode": PASSCODE},
    )
    resp = client.post(
        "/api/private-office/security/unlock", json={"passcode": PASSCODE}
    )
    token = (resp.get_json() or {}).get("grant_token") or ""
    if token:
        _GRANTS[int(user_id)] = token
    return token


def _query_all(sql, params=()):
    conn = db.connect()
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        return [dict(row) for row in cur.fetchall()]
    finally:
        conn.close()


def setup_module(module):  # noqa: ARG001
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
        for uid in (OWNER, GUEST, STRANGER, NO_TIER):
            conn.execute(
                "INSERT INTO users (user_id, username, account_status, access_enabled) "
                "VALUES (?, ?, ?, 1)",
                (uid, f"member{uid}", "active"),
            )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS blocked_users "
            "(blocker_user_id INTEGER, blocked_user_id INTEGER)"
        )
        cur = conn.cursor()
        schema.ensure_private_schema(cur, force=True)
        convo.ensure_conversations_schema(cur, force=True)
        conn.commit()
    finally:
        conn.close()

    for uid in (OWNER, GUEST, STRANGER):
        svc.grant_entitlement(uid, "private_office.access", source="admin")
    _GRANTS.clear()
    for uid in (OWNER, GUEST, STRANGER):
        _unlock(uid)
    os.environ["PRIVATE_CONVERSATIONS_ENABLED"] = "1"
    _stub._test_user = None


@pytest.fixture(autouse=True)
def _flag_on(monkeypatch):
    monkeypatch.setenv("PRIVATE_CONVERSATIONS_ENABLED", "1")
    yield
    _stub._test_user = None


@pytest.fixture()
def client():
    return _app().test_client()


# ---------------------------------------------------------------------------
# Gate chain
# ---------------------------------------------------------------------------

def test_no_session_is_401(client):
    _stub._test_user = None
    assert client.get(BASE).status_code == 401


def test_no_private_tier_is_403_and_names_the_minimum(client):
    _as(NO_TIER)
    resp = client.get(BASE)
    body = resp.get_json() or {}
    assert resp.status_code == 403
    assert body.get("minimum_tier")


def test_absent_flag_is_404_for_an_entitled_member(client, monkeypatch):
    """Fail-closed. A deploy that forgot the env var has no surface at all."""
    monkeypatch.delenv("PRIVATE_CONVERSATIONS_ENABLED", raising=False)
    _as(OWNER)
    assert client.get(BASE).status_code == 404

    state = matrix.availability(convo.FEATURE_ID, tiers.TIER_PRIVATE)
    assert state["availability"] == matrix.AVAIL_FEATURE_DISABLED
    assert state["implementation"] == matrix.IMPL_IMPLEMENTED


def test_explicit_false_flag_is_404_too(client, monkeypatch):
    monkeypatch.setenv("PRIVATE_CONVERSATIONS_ENABLED", "false")
    _as(OWNER)
    assert client.get(BASE).status_code == 404


def test_locked_office_is_423(client):
    _as(OWNER)
    resp = client.get(BASE, headers={routes.GRANT_HEADER: ""})
    assert resp.status_code == 423


def test_gate_order_tier_is_answered_before_the_lock(client):
    """A member with no tier must not be told to unlock.

    423 here would confirm the feature exists and invite a passcode attempt
    against a surface this account can never reach.
    """
    _as(NO_TIER)
    resp = client.get(BASE, headers={routes.GRANT_HEADER: ""})
    assert resp.status_code == 403


def test_entitled_unlocked_and_flagged_on_is_200(client):
    _as(OWNER)
    resp = client.get(BASE)
    body = resp.get_json() or {}
    assert resp.status_code == 200
    assert isinstance(body.get("conversations"), list)


# ---------------------------------------------------------------------------
# Capability truth
# ---------------------------------------------------------------------------

def test_capabilities_route_never_claims_encryption(client):
    _as(OWNER)
    resp = client.get(f"{BASE}/capabilities")
    caps = (resp.get_json() or {}).get("capabilities") or {}
    assert resp.status_code == 200
    assert caps.get("end_to_end_encrypted") is False
    assert caps.get("message_ledger") == "comm_v2_messages"
    assert caps.get("attachment_authority") == "message_attachments"
    assert caps.get("rtc_provider") == "agora"


def test_capabilities_route_is_gated_like_everything_else(client):
    _stub._test_user = None
    assert client.get(f"{BASE}/capabilities").status_code == 401


# ---------------------------------------------------------------------------
# The full cycle, and what it is allowed to have written
# ---------------------------------------------------------------------------

def _create_direct(client, other_user_id=GUEST):
    _as(OWNER)
    return client.post(
        BASE,
        # `target_user_id` is the CANONICAL service's field name for the other
        # side of a direct thread. This pack passes the payload through rather
        # than translating a vocabulary of its own — a second field name is a
        # second schema, and the translation layer is where they drift.
        json={"office_scope": convo.SCOPE_DIRECT, "target_user_id": other_user_id},
    )


def test_create_classifies_a_canonical_conversation(client):
    resp = _create_direct(client)
    body = resp.get_json() or {}
    assert resp.status_code == 200, body
    conversation_id = int(body.get("conversation_id") or 0)
    assert conversation_id > 0

    canonical = _query_all(
        "SELECT id, conversation_type FROM comm_v2_conversations WHERE id = ?",
        (conversation_id,),
    )
    assert len(canonical) == 1
    assert canonical[0]["conversation_type"] == "direct"

    classified = _query_all(
        f"SELECT owner_user_id, office_scope FROM {convo.CLASSIFICATION_TABLE} "
        "WHERE conversation_id = ?",
        (conversation_id,),
    )
    assert len(classified) == 1
    assert int(classified[0]["owner_user_id"]) == OWNER
    assert classified[0]["office_scope"] == convo.SCOPE_DIRECT

    assert body.get("private_office", {}).get("end_to_end_encrypted") is False


def test_a_canonical_refusal_keeps_its_own_status_and_code(client):
    """The messaging authority's 400 must arrive as a 400.

    ``service._err`` returns ``{"status": <code string>, "http_status": <int>}``
    — ``status`` is the CODE, not the number. Reading it as a number raised
    ValueError inside the failure path, so every honest refusal from the
    messaging authority reached the client as a 503 with a stack trace, and the
    client retried a request that could never succeed. The real envelope is
    exercised here rather than a hand-written one, because a hand-written
    failure envelope is exactly what hid this.
    """
    _as(OWNER)
    resp = client.post(
        BASE, json={"office_scope": convo.SCOPE_DIRECT, "target_user_id": OWNER}
    )
    body = resp.get_json() or {}
    assert resp.status_code == 400, body
    assert body.get("code") == "invalid_recipient", body
    assert body.get("state") != "unavailable"


def test_organization_room_without_its_binding_is_refused(client):
    _as(OWNER)
    resp = client.post(BASE, json={"office_scope": convo.SCOPE_ORGANIZATION_ROOM})
    assert resp.status_code == 400
    assert (resp.get_json() or {}).get("ok") is False


def test_send_writes_to_the_canonical_ledger_and_nowhere_else(client):
    created = (_create_direct(client).get_json() or {})
    conversation_id = int(created["conversation_id"])

    before = {
        row["name"]
        for row in _query_all(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }

    _as(OWNER)

    resp = client.post(
        f"{BASE}/{conversation_id}/messages",
        json={"body": "office-only-line", "client_message_id": "idem-routes-1"},
    )
    assert resp.status_code == 200, resp.get_json()

    landed = _query_all(
        "SELECT body FROM comm_v2_messages WHERE conversation_id = ?",
        (conversation_id,),
    )
    assert any(row["body"] == "office-only-line" for row in landed)

    after = {
        row["name"]
        for row in _query_all(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    # Sending DOES create tables — the canonical presence system builds its own
    # on first use. That is the canonical presence authority doing its job, and
    # asserting "no new tables at all" would have been an assertion about
    # someone else's subsystem. What must hold is narrower and is the actual
    # invariant: sending a message creates nothing owned by Private Office.
    created = after - before
    assert not [name for name in created if name.startswith("private_office")], (
        f"sending created Private Office tables: {sorted(created)}"
    )
    for name in created:
        columns = {row["name"] for row in _query_all(f"PRAGMA table_info({name})")}
        assert "body" not in columns, f"{name} looks like a second message store"


def test_this_package_owns_two_tables_and_neither_can_hold_a_message(client):
    """The structural anti-duplication assertion, over the HTTP surface.

    Table names are not enough — a ``private_office_messages`` table could be
    added under a different name. So the columns are inspected too: nothing this
    package owns may have a place to put a message body.
    """
    _create_direct(client)
    owned = [
        row["name"]
        for row in _query_all(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name LIKE 'private_office_conversation%'"
        )
    ]
    assert sorted(owned) == sorted(
        [convo.CLASSIFICATION_TABLE, convo.LINKS_TABLE]
    ), owned

    forbidden = {"body", "message_type", "sender_user_id", "attachment_id"}
    for table in owned:
        columns = {
            row["name"] for row in _query_all(f"PRAGMA table_info({table})")
        }
        assert not (columns & forbidden), f"{table} has {columns & forbidden}"


def test_list_returns_the_thread_with_its_classification(client):
    created = (_create_direct(client).get_json() or {})
    conversation_id = int(created["conversation_id"])

    _as(OWNER)
    body = client.get(BASE).get_json() or {}
    ids = [int(item.get("conversation_id") or 0) for item in body["conversations"]]
    assert conversation_id in ids
    assert body["capabilities"]["end_to_end_encrypted"] is False


def test_detail_carries_the_canonical_control_payload(client):
    created = (_create_direct(client).get_json() or {})
    conversation_id = int(created["conversation_id"])

    _as(OWNER)
    resp = client.get(f"{BASE}/{conversation_id}")
    body = resp.get_json() or {}
    assert resp.status_code == 200, body
    # `control` is the canonical service's own payload, passed through rather
    # than re-shaped. Re-shaping it here would be this package owning a
    # conversation payload format, which is the first step to owning threads.
    assert isinstance(body.get("control"), dict)
    assert body["private_office"]["office_scope"] == convo.SCOPE_DIRECT


def test_a_thread_is_reachable_by_its_canonical_public_id(client):
    """``<ref>`` is a REF, not an integer.

    ``_conversation_access`` resolves a numeric ref against ``id`` and anything
    else against ``public_id``, so a public id is a first-class way to address a
    thread. Every test above happened to use a numeric id, which meant a route
    that coerced the ref with ``int(ref)`` passed the whole suite and would have
    500'd on the public-id form the moment a client used it. Both routes that
    take a ref are exercised here, in both addressing modes.
    """
    created = (_create_direct(client).get_json() or {})
    conversation_id = int(created["conversation_id"])
    rows = _query_all(
        "SELECT public_id FROM comm_v2_conversations WHERE id = ?", (conversation_id,)
    )
    public_id = str(rows[0]["public_id"] or "")
    assert public_id and not public_id.isdigit(), rows

    _as(OWNER)
    by_public = client.get(f"{BASE}/{public_id}")
    assert by_public.status_code == 200, by_public.get_json()
    resolved = by_public.get_json()["conversation"]
    assert int(resolved.get("id") or resolved.get("conversation_id") or 0) == conversation_id

    delegated = client.get(f"{BASE}/{public_id}/messages")
    assert delegated.status_code == 200, delegated.get_json()


# ---------------------------------------------------------------------------
# Membership is the canonical service's answer, not this pack's
# ---------------------------------------------------------------------------

def test_a_stranger_with_every_gate_satisfied_is_still_refused(client):
    created = (_create_direct(client).get_json() or {})
    conversation_id = int(created["conversation_id"])

    _as(STRANGER)
    resp = client.get(f"{BASE}/{conversation_id}")
    assert resp.status_code in (403, 404), resp.get_json()
    body = resp.get_json() or {}
    assert body.get("ok") is False


def test_a_stranger_cannot_send_into_someone_elses_office_thread(client):
    created = (_create_direct(client).get_json() or {})
    conversation_id = int(created["conversation_id"])

    _as(STRANGER)
    resp = client.post(
        f"{BASE}/{conversation_id}/messages", json={"body": "intrusion"}
    )
    assert resp.status_code in (403, 404)
    landed = _query_all(
        "SELECT body FROM comm_v2_messages WHERE conversation_id = ? AND body = ?",
        (conversation_id, "intrusion"),
    )
    assert landed == []


def test_an_unclassified_conversation_is_not_reachable_here(client):
    """Messenger threads do not appear on the Office surface.

    The classification is what makes a thread an Office thread. A plain
    conversation the same member owns must 404 here while remaining perfectly
    usable in Messenger.
    """
    # A pair no other test classifies. The canonical service DEDUPES direct
    # threads, so asking for OWNER-GUEST here would hand back the already
    # classified conversation and the test would pass for the wrong reason.
    result = comm_service.create_conversation(
        OWNER, {"conversation_type": "direct", "target_user_id": NO_TIER}
    )
    assert result.get("ok"), result
    plain_id = int(result["conversation_id"])

    _as(OWNER)
    resp = client.get(f"{BASE}/{plain_id}")
    assert resp.status_code == 404
    assert (resp.get_json() or {}).get("code") == "not_private_conversation"


# ---------------------------------------------------------------------------
# Sensitivity and links
# ---------------------------------------------------------------------------

def test_only_the_owner_can_change_sensitivity(client):
    created = (_create_direct(client).get_json() or {})
    conversation_id = int(created["conversation_id"])

    _as(GUEST)
    resp = client.post(
        f"{BASE}/{conversation_id}/sensitivity", json={"sensitivity": "RESTRICTED"}
    )
    assert resp.status_code == 403
    assert (resp.get_json() or {}).get("code") == "not_owner"

    _as(OWNER)
    resp = client.post(
        f"{BASE}/{conversation_id}/sensitivity", json={"sensitivity": "RESTRICTED"}
    )
    assert resp.status_code == 200, resp.get_json()
    assert resp.get_json()["private_office"]["sensitivity"] == "RESTRICTED"


def test_link_and_unlink_round_trip(client):
    created = (_create_direct(client).get_json() or {})
    conversation_id = int(created["conversation_id"])

    _as(OWNER)
    resp = client.post(
        f"{BASE}/{conversation_id}/links",
        json={"link_type": convo.LINK_DOCUMENT, "target_id": "42"},
    )
    assert resp.status_code == 200, resp.get_json()
    links = resp.get_json()["links"]
    assert any(link["target_id"] == "42" for link in links)

    resp = client.delete(
        f"{BASE}/{conversation_id}/links",
        json={"link_type": convo.LINK_DOCUMENT, "target_id": "42"},
    )
    assert resp.status_code == 200
    assert resp.get_json()["links"] == []


def test_a_link_target_that_is_content_is_refused_over_http(client):
    """Links are references. A sentence in the target column is leaked content."""
    created = (_create_direct(client).get_json() or {})
    conversation_id = int(created["conversation_id"])

    _as(OWNER)
    resp = client.post(
        f"{BASE}/{conversation_id}/links",
        json={
            "link_type": convo.LINK_DOCUMENT,
            "target_id": "the succession memo for Q3, see page 4",
        },
    )
    assert resp.status_code == 400
    assert (resp.get_json() or {}).get("ok") is False


# ---------------------------------------------------------------------------
# Failure is never emptiness
# ---------------------------------------------------------------------------

def test_a_failing_read_is_503_and_carries_no_conversations_key(client, monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("database is on fire")

    monkeypatch.setattr(convo, "list_for_member", _boom)

    _as(OWNER)
    resp = client.get(BASE)
    body = resp.get_json() or {}
    assert resp.status_code == 503
    assert body.get("state") == "unavailable"
    # The key must be ABSENT, not empty. An empty list is what a client renders
    # as "no conversations yet", which is the specific lie this shape prevents.
    assert "conversations" not in body


def test_a_failing_delegation_is_503_rather_than_a_silent_success(client, monkeypatch):
    created = (_create_direct(client).get_json() or {})
    conversation_id = int(created["conversation_id"])

    def _boom(*args, **kwargs):
        raise RuntimeError("messaging is down")

    monkeypatch.setattr(comm_service, "list_messages", _boom)

    _as(OWNER)
    resp = client.get(f"{BASE}/{conversation_id}/messages")
    body = resp.get_json() or {}
    assert resp.status_code == 503
    assert body.get("ok") is False
    assert "messages" not in body


# ---------------------------------------------------------------------------
# Cross-cutting: nothing on this surface claims encryption
# ---------------------------------------------------------------------------

def test_no_route_response_in_a_full_cycle_claims_encryption(client):
    created = _create_direct(client)
    conversation_id = int((created.get_json() or {})["conversation_id"])

    _as(OWNER)
    responses = [
        created,
        client.get(f"{BASE}/capabilities"),
        client.get(BASE),
        client.get(f"{BASE}/{conversation_id}"),
        client.post(f"{BASE}/{conversation_id}/messages", json={"body": "hello"}),
        client.get(f"{BASE}/{conversation_id}/messages"),
        client.post(f"{BASE}/{conversation_id}/read"),
    ]
    for resp in responses:
        text = json.dumps(resp.get_json() or {}).lower()
        # The negative phrasing is allowed and wanted — the capabilities notice
        # says in as many words that this is NOT end-to-end encrypted. What is
        # banned is an affirmative claim, in either the machine-readable flag or
        # the prose a client might surface as a badge.
        assert '"end_to_end_encrypted": true' not in text
        assert "is end-to-end encrypted" not in text
        assert "fully encrypted" not in text


def test_no_livekit_anywhere_on_this_surface():
    """RTC is Agora only. A LiveKit reference here is a second RTC path."""
    with open(convo_routes.__file__, "r", encoding="utf-8") as handle:
        source = handle.read().lower()
    assert "livekit" not in source


# ---------------------------------------------------------------------------
# The reverse direction: which threads reference this object
# ---------------------------------------------------------------------------

def _link_target(client, target_id="42", link_type=None):
    """Create an owner/guest thread and link it to one object."""
    created = (_create_direct(client).get_json() or {})
    conversation_id = int(created["conversation_id"])
    _as(OWNER)
    resp = client.post(
        f"{BASE}/{conversation_id}/links",
        json={
            "link_type": link_type or convo.LINK_DOCUMENT,
            "target_id": target_id,
        },
    )
    assert resp.status_code == 200, resp.get_json()
    return conversation_id


def test_reverse_lookup_finds_the_thread_that_references_the_object(client):
    conversation_id = _link_target(client, "42")

    _as(OWNER)
    resp = client.get(f"{BASE}/links/{convo.LINK_DOCUMENT}/42")
    body = resp.get_json() or {}
    assert resp.status_code == 200, body
    assert body["count"] == 1
    found = [int(row.get("id") or row.get("conversation_id") or 0)
             for row in body["conversations"]]
    assert found == [conversation_id]


def test_reverse_lookup_narrows_to_the_linked_thread_not_the_whole_office(client):
    """The member's *other* threads are not an answer to "who discussed this".

    The stranger case below proves the intersection is bounded by membership.
    It cannot prove the intersection is bounded by the *link*, because a
    stranger has no Office threads for a broken predicate to over-report. So
    this case gives the owner a second, unlinked thread: with the narrowing
    removed, the route hands back both and tells the member that a document
    they never mentioned is under discussion in a thread that never mentions
    it.

    Written after a mutation run: replacing the ``keep`` predicate with
    ``return True`` survived the suite as it stood, which meant the outer
    membership check was carrying an assertion the link check was supposed to
    own.
    """
    linked_id = _link_target(client, "42")
    # A different peer, because the canonical service returns the *existing*
    # direct thread for a pair rather than opening a second one — asking for
    # OWNER↔GUEST again would hand back the thread we just linked, and the test
    # would be comparing a conversation with itself.
    unlinked = (_create_direct(client, other_user_id=STRANGER).get_json() or {})
    unlinked_id = int(unlinked["conversation_id"])
    assert unlinked_id != linked_id

    _as(OWNER)
    resp = client.get(f"{BASE}/links/{convo.LINK_DOCUMENT}/42")
    body = resp.get_json() or {}
    assert resp.status_code == 200, body
    found = [int(row.get("id") or row.get("conversation_id") or 0)
             for row in body["conversations"]]
    assert found == [linked_id]
    assert unlinked_id not in found
    assert body["count"] == 1


def test_a_stranger_learns_nothing_about_a_document_linked_elsewhere(client):
    """The leak this route is shaped to prevent.

    ``conversations_for_target`` answers from the link table, which knows that a
    link exists and nothing about who may know it exists. Returning its rows
    directly would tell a stranger that document 42 is under discussion — and a
    count of private threads is itself the disclosure, before any title leaks.

    A stranger is entitled, unlocked, and past the flag. The only thing standing
    between them and the answer is that they are not a participant, which is
    exactly the property under test.
    """
    _link_target(client, "42")

    _as(STRANGER)
    resp = client.get(f"{BASE}/links/{convo.LINK_DOCUMENT}/42")
    body = resp.get_json() or {}
    assert resp.status_code == 200, body
    # An honest empty: the object exists and is linked, but not for them.
    assert body["count"] == 0
    assert body["conversations"] == []


def test_reverse_lookup_does_not_bleed_across_link_types(client):
    """Document 42 and record 42 are different objects that share a string."""
    _link_target(client, "42", link_type=convo.LINK_DOCUMENT)

    _as(OWNER)
    resp = client.get(f"{BASE}/links/{convo.LINK_RECORD}/42")
    body = resp.get_json() or {}
    assert resp.status_code == 200, body
    assert body["count"] == 0


def test_reverse_lookup_of_an_unlinked_object_is_an_empty_200(client):
    _create_direct(client)
    _as(OWNER)
    resp = client.get(f"{BASE}/links/{convo.LINK_DOCUMENT}/999")
    body = resp.get_json() or {}
    assert resp.status_code == 200, body
    assert body["count"] == 0
    assert body["conversations"] == []


def test_reverse_lookup_rejects_a_link_type_it_does_not_know(client):
    _as(OWNER)
    resp = client.get(f"{BASE}/links/NOT_A_LINK_TYPE/42")
    assert resp.status_code == 400
    assert (resp.get_json() or {}).get("ok") is False


def test_reverse_lookup_applies_the_target_shape_check(client):
    """A permissive URL converter must not become a permissive validator.

    ``<path:target_id>`` exists so identifiers containing slashes survive
    routing. It also means a sentence reaches the handler, and a sentence in a
    target column is leaked content — so the same ``safe_object_id`` check that
    guards the write guards the read.
    """
    _as(OWNER)
    resp = client.get(
        f"{BASE}/links/{convo.LINK_DOCUMENT}/the succession memo for Q3, see page 4"
    )
    assert resp.status_code == 400
    assert (resp.get_json() or {}).get("ok") is False


def test_reverse_lookup_is_gated_like_everything_else(client, monkeypatch):
    """Same gate chain, same order — this route is not a side door."""
    _stub._test_user = None
    assert client.get(f"{BASE}/links/{convo.LINK_DOCUMENT}/42").status_code == 401

    _as(NO_TIER)
    assert client.get(f"{BASE}/links/{convo.LINK_DOCUMENT}/42").status_code == 403

    monkeypatch.delenv("PRIVATE_CONVERSATIONS_ENABLED", raising=False)
    convo.reset_conversations_schema_cache()
    _as(OWNER)
    assert client.get(f"{BASE}/links/{convo.LINK_DOCUMENT}/42").status_code == 404


def test_a_failing_reverse_lookup_is_503_and_carries_no_conversations_key(
    client, monkeypatch
):
    """Failure is never emptiness — the same rule as the list route.

    "Not discussed anywhere" is a claim about data. If the read failed, the
    client must not be handed a shape it can render as that claim.
    """
    def _boom(*args, **kwargs):
        raise RuntimeError("database is on fire")

    monkeypatch.setattr(convo, "list_for_target", _boom)
    _as(OWNER)
    resp = client.get(f"{BASE}/links/{convo.LINK_DOCUMENT}/42")
    body = resp.get_json() or {}
    assert resp.status_code == 503
    assert body.get("state") == "unavailable"
    assert "conversations" not in body
    assert "count" not in body


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
