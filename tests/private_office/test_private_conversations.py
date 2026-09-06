"""Private Conversations — the classification and delegation layer.

Run either way::

    python -m pytest tests/private_office/test_private_conversations.py
    python tests/private_office/test_private_conversations.py

What these tests defend
-----------------------
* **No second message ledger.** The strongest assertion in this file is
  structural and negative: after a full create-and-send cycle, the only tables
  this package created are its two, and every message row lives in
  ``comm_v2_messages``. A future change that quietly adds a
  ``private_office_messages`` table fails here rather than in production six
  weeks later, when two unread counts have already diverged.
* **Real delegation, not a mock of it.** ``pulse_communications_v2.service`` is
  driven against a real SQLite database through a stub ``bot``, so
  :func:`conversations.create` genuinely inserts a canonical conversation row.
  Monkeypatching ``create_conversation`` would have made these tests pass
  against a module that had stopped calling it.
* **Fail closed.** ``PRIVATE_CONVERSATIONS_ENABLED`` absent means every write
  and read refuses. The flag defaults OFF, the opposite of the feature matrix's
  usual absent-means-on convention, and that difference is worth a test.
* **Bindings are required where they are load-bearing.** An ORGANIZATION_ROOM
  with no organization node is refused at creation, because a room that claims
  a binding it does not have renders correctly and joins to nothing.
* **Links are references, never content.** A link target that looks like a
  value rather than an identifier — a sentence, a policy number with spaces —
  is refused by the same predicate the audit log uses.
* **Truthfulness.** ``capability_states`` reports ``end_to_end_encrypted:
  False``. There is no cryptographic E2EE on this path and no surface may claim
  one.
* **Errors are errors.** A failing canonical call raises rather than returning
  an empty list, so a screen can never render "no conversations yet" over a
  fetch that actually failed.
"""

import os
import sqlite3
import sys
import tempfile
import types

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest  # noqa: E402

from pulse_communications_v2 import service as comm_service  # noqa: E402
from services.private_office import audit  # noqa: E402
from services.private_office import conversations as convo  # noqa: E402
from services.private_office import model as po_model  # noqa: E402
from services.private_office import schema as po_schema  # noqa: E402

OWNER = 501
GUEST = 602
STRANGER = 703

_TMP_DB = os.path.join(tempfile.gettempdir(), "pulsesoc_private_conversations_test.db")


# ---------------------------------------------------------------------------
# Harness
#
# A stub `bot` with a real SQLite file behind it. The canonical service opens
# and closes its own connection per call, so an in-memory database would be
# discarded between the create and the assertion — the file is not incidental.
# ---------------------------------------------------------------------------

def _install_stub_bot(path: str):
    stub = types.ModuleType("bot")
    stub.sqlite3 = sqlite3

    def _db():
        conn = sqlite3.connect(path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    stub.db = _db
    sys.modules["bot"] = stub
    return stub


_stub = _install_stub_bot(_TMP_DB)


@pytest.fixture()
def cur(monkeypatch):
    """Fresh database per test, flag ON, canonical schema really created."""
    monkeypatch.setenv("PRIVATE_CONVERSATIONS_ENABLED", "1")
    if os.path.exists(_TMP_DB):
        os.remove(_TMP_DB)
    _install_stub_bot(_TMP_DB)

    # Autocommit. Two connections are genuinely in play — this one, and the one
    # the canonical service opens for itself — and a held-open write transaction
    # on this side locks the file under the delegated call. That is a property of
    # the harness, not of the code: in the app both sides share the request's
    # connection. Committing eagerly here keeps the delegation real rather than
    # mocking it away to dodge the lock.
    conn = sqlite3.connect(_TMP_DB, timeout=5, isolation_level=None)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    convo.reset_conversations_schema_cache()
    convo.ensure_conversations_schema(cursor, force=True)
    cursor.execute(po_schema.AUDIT_TABLE_DDL)
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS users "
        "(user_id INTEGER PRIMARY KEY, username TEXT, display_name TEXT, avatar_url TEXT)"
    )
    for uid in (OWNER, GUEST, STRANGER):
        cursor.execute(
            "INSERT INTO users (user_id, username, display_name, avatar_url) "
            "VALUES (?, ?, ?, '')",
            (uid, f"user{uid}", f"User {uid}"),
        )
    cursor.execute(
        "CREATE TABLE IF NOT EXISTS blocked_users "
        "(blocker_user_id INT, blocked_user_id INT)"
    )
    conn.commit()

    # The canonical service's own DDL already ran above via
    # ensure_conversations_schema; telling it so keeps `_ensure_columns` — which
    # wants far more of `bot` than a stub should have to provide — out of the
    # path. This is the only thing in this harness that is not the real code.
    monkeypatch.setattr(comm_service, "_SCHEMA_READY", True, raising=False)

    yield cursor

    conn.close()
    convo.reset_conversations_schema_cache()
    if os.path.exists(_TMP_DB):
        os.remove(_TMP_DB)


def _reject(fn, *, status=None, code=None):
    with pytest.raises(convo.PrivateConversationRejected) as excinfo:
        fn()
    err = excinfo.value
    if status is not None:
        assert err.status == status, f"expected status {status}, got {err.status}"
    if code is not None:
        assert err.code == code, f"expected code {code}, got {err.code}"
    return err


def _direct(cursor, actor=OWNER, target=GUEST, **payload):
    body = {"target_user_id": target}
    body.update(payload)
    return convo.create(
        cursor, actor_user_id=actor, office_scope=convo.SCOPE_DIRECT, payload=body
    )


# ---------------------------------------------------------------------------
# Fail closed
# ---------------------------------------------------------------------------

def test_flag_absent_means_off(cur, monkeypatch):
    monkeypatch.delenv("PRIVATE_CONVERSATIONS_ENABLED", raising=False)
    assert convo.conversations_enabled() is False
    _reject(lambda: _direct(cur), status=404, code="feature_disabled")
    _reject(
        lambda: convo.list_for_member(cur, actor_user_id=OWNER),
        status=404,
        code="feature_disabled",
    )


def test_flag_falsey_string_means_off(cur, monkeypatch):
    monkeypatch.setenv("PRIVATE_CONVERSATIONS_ENABLED", "0")
    assert convo.conversations_enabled() is False
    _reject(lambda: _direct(cur), status=404, code="feature_disabled")


# ---------------------------------------------------------------------------
# The central structural claim: no second ledger
# ---------------------------------------------------------------------------

def test_create_writes_to_the_canonical_conversation_table(cur):
    created = _direct(cur)
    conversation_id = created["conversation_id"]
    assert conversation_id > 0

    cur.execute(
        "SELECT COUNT(1) FROM comm_v2_conversations WHERE id=?", (conversation_id,)
    )
    assert cur.fetchone()[0] == 1, "the conversation must exist in the canonical table"

    cur.execute(
        "SELECT conversation_type FROM comm_v2_conversations WHERE id=?",
        (conversation_id,),
    )
    assert cur.fetchone()[0] == "direct", (
        "a DIRECT office scope must map onto the canonical 'direct' type, not a new one"
    )


def test_package_creates_exactly_two_tables_and_none_of_them_hold_messages(cur):
    _direct(cur)
    cur.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name LIKE 'private_office_conversation%'"
    )
    names = sorted(row[0] for row in cur.fetchall())
    assert names == sorted([convo.CLASSIFICATION_TABLE, convo.LINKS_TABLE]), (
        "this package owns exactly two tables; a third is a second ledger forming"
    )

    for table in names:
        cur.execute(f"PRAGMA table_info({table})")
        columns = {row[1] for row in cur.fetchall()}
        assert not (columns & {"body", "message_type", "sender_user_id"}), (
            f"{table} has message-shaped columns — this package stores no messages"
        )


def test_canonical_type_vocabulary_is_not_extended(cur):
    assert comm_service.ALLOWED_CONVERSATION_TYPES == {
        "direct", "group", "room", "community_channel"
    }, "Private Conversations must not add a canonical conversation type"
    assert set(convo.SCOPE_TO_CONVERSATION_TYPE.values()) <= (
        comm_service.ALLOWED_CONVERSATION_TYPES
    )
    assert "community_channel" not in convo.SCOPE_TO_CONVERSATION_TYPE.values(), (
        "an office thread is never a community channel — that would place it in "
        "community discovery"
    )


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------

def test_classification_row_is_written_and_keyed_by_conversation(cur):
    created = _direct(cur)
    row = convo.classification(cur, created["conversation_id"])
    assert row["office_scope"] == convo.SCOPE_DIRECT
    assert row["sensitivity"] == convo.DEFAULT_SENSITIVITY
    assert int(row["owner_user_id"]) == OWNER


def test_classify_is_idempotent_on_the_same_conversation(cur):
    created = _direct(cur)
    conversation_id = created["conversation_id"]
    convo.classify(
        cur,
        conversation_id=conversation_id,
        owner_user_id=OWNER,
        actor_user_id=OWNER,
        office_scope=convo.SCOPE_GROUP,
    )
    cur.execute(
        f"SELECT COUNT(1) FROM {convo.CLASSIFICATION_TABLE} WHERE conversation_id=?",
        (conversation_id,),
    )
    assert cur.fetchone()[0] == 1, "reclassifying must update, never duplicate"
    assert convo.classification(cur, conversation_id)["office_scope"] == convo.SCOPE_GROUP


def test_unknown_scope_is_refused(cur):
    _reject(
        lambda: convo.create(
            cur, actor_user_id=OWNER, office_scope="SECRET_BUNKER", payload={}
        ),
        status=400,
        code="invalid_scope",
    )


def test_unknown_sensitivity_is_refused(cur):
    created = _direct(cur)
    _reject(
        lambda: convo.set_sensitivity(
            cur,
            conversation_id=created["conversation_id"],
            actor_user_id=OWNER,
            sensitivity="VERY_SECRET",
        ),
        status=400,
        code="invalid_sensitivity",
    )


def test_sensitivity_vocabulary_is_the_shared_one(cur):
    created = _direct(cur)
    row = convo.set_sensitivity(
        cur,
        conversation_id=created["conversation_id"],
        actor_user_id=OWNER,
        sensitivity=po_model.SENSITIVITY_RESTRICTED,
    )
    assert row["sensitivity"] == po_model.SENSITIVITY_RESTRICTED
    assert convo.DEFAULT_SENSITIVITY in po_model.SENSITIVITIES


def test_room_scope_without_binding_is_refused(cur):
    _reject(
        lambda: convo.create(
            cur,
            actor_user_id=OWNER,
            office_scope=convo.SCOPE_ORGANIZATION_ROOM,
            payload={"title": "Board"},
        ),
        status=400,
        code="missing_binding",
    )
    _reject(
        lambda: convo.create(
            cur,
            actor_user_id=OWNER,
            office_scope=convo.SCOPE_PROJECT_ROOM,
            payload={"title": "Acquisition"},
        ),
        status=400,
        code="missing_binding",
    )


def test_bound_room_is_created_private_and_not_discoverable(cur):
    created = convo.create(
        cur,
        actor_user_id=OWNER,
        office_scope=convo.SCOPE_ORGANIZATION_ROOM,
        payload={"title": "Board", "organization_node_id": 77},
    )
    cur.execute(
        "SELECT privacy, is_discoverable FROM comm_v2_conversations WHERE id=?",
        (created["conversation_id"],),
    )
    row = cur.fetchone()
    assert row["privacy"] == "private"
    assert int(row["is_discoverable"] or 0) == 0, (
        "an office room must never enter public room discovery"
    )
    assert convo.classification(cur, created["conversation_id"])[
        "organization_node_id"
    ] == 77


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------

def test_link_and_unlink_round_trip(cur):
    created = _direct(cur)
    conversation_id = created["conversation_id"]
    payload = convo.link(
        cur,
        conversation_id=conversation_id,
        actor_user_id=OWNER,
        link_type=convo.LINK_DOCUMENT,
        target_id="DOCUMENT:42",
    )
    assert payload["link_type"] == convo.LINK_DOCUMENT
    assert convo.list_links(cur, conversation_id)

    convo.unlink(
        cur,
        conversation_id=conversation_id,
        actor_user_id=OWNER,
        link_type=convo.LINK_DOCUMENT,
        target_id="DOCUMENT:42",
    )
    assert convo.list_links(cur, conversation_id) == []


def test_relinking_the_same_target_is_idempotent(cur):
    created = _direct(cur)
    conversation_id = created["conversation_id"]
    for _ in range(3):
        convo.link(
            cur,
            conversation_id=conversation_id,
            actor_user_id=OWNER,
            link_type=convo.LINK_RECORD,
            target_id="OBLIGATION:9",
        )
    assert len(convo.list_links(cur, conversation_id)) == 1, (
        "a retried link after a timeout must not become a duplicate or a 409"
    )


def test_link_target_that_is_content_rather_than_an_identifier_is_refused(cur):
    created = _direct(cur)
    for bad in ("the wire came from Chase on Tuesday", "4111 1111 1111 1111", "", "a" * 80):
        _reject(
            lambda bad=bad: convo.link(
                cur,
                conversation_id=created["conversation_id"],
                actor_user_id=OWNER,
                link_type=convo.LINK_FACT,
                target_id=bad,
            ),
            status=400,
            code="invalid_link_target",
        )


def test_unknown_link_type_is_refused(cur):
    created = _direct(cur)
    _reject(
        lambda: convo.link(
            cur,
            conversation_id=created["conversation_id"],
            actor_user_id=OWNER,
            link_type="BANK_ACCOUNT",
            target_id="1",
        ),
        status=400,
        code="invalid_link_type",
    )


def test_links_on_an_unclassified_conversation_are_refused(cur):
    result = comm_service.create_conversation(
        OWNER, {"conversation_type": "direct", "target_user_id": GUEST}
    )
    conversation_id = int(result["conversation_id"])
    _reject(
        lambda: convo.link(
            cur,
            conversation_id=conversation_id,
            actor_user_id=OWNER,
            link_type=convo.LINK_MEETING,
            target_id="MEETING:1",
        ),
        status=404,
        code="not_private_conversation",
    )


def test_reverse_lookup_finds_the_conversation(cur):
    created = _direct(cur)
    convo.link(
        cur,
        conversation_id=created["conversation_id"],
        actor_user_id=OWNER,
        link_type=convo.LINK_DOCUMENT,
        target_id="DOCUMENT:7",
    )
    found = convo.conversations_for_target(
        cur, link_type=convo.LINK_DOCUMENT, target_id="DOCUMENT:7"
    )
    assert found == [created["conversation_id"]]


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def test_list_returns_only_classified_threads(cur):
    office = _direct(cur)
    comm_service.create_conversation(
        OWNER, {"conversation_type": "group", "title": "Ordinary",
                "participant_ids": [GUEST]}
    )
    listed = convo.list_for_member(cur, actor_user_id=OWNER)
    ids = [int(item["id"]) for item in listed["items"]]
    assert ids == [office["conversation_id"]], (
        "an ordinary Messenger thread must not appear in the Private Office list"
    )
    assert listed["items"][0]["private_office"]["office_scope"] == convo.SCOPE_DIRECT


def test_list_can_filter_by_scope(cur):
    _direct(cur)
    convo.create(
        cur,
        actor_user_id=OWNER,
        office_scope=convo.SCOPE_PROJECT_ROOM,
        payload={"title": "Acquisition", "operations_project_id": 12},
    )
    only_rooms = convo.list_for_member(
        cur, actor_user_id=OWNER, office_scope=convo.SCOPE_PROJECT_ROOM
    )
    assert len(only_rooms["items"]) == 1
    assert only_rooms["items"][0]["private_office"]["office_scope"] == (
        convo.SCOPE_PROJECT_ROOM
    )


def test_a_stranger_sees_none_of_it(cur):
    _direct(cur)
    listed = convo.list_for_member(cur, actor_user_id=STRANGER)
    assert listed["items"] == []
    assert listed["count"] == 0


def test_a_failing_canonical_call_raises_rather_than_returning_empty(cur, monkeypatch):
    monkeypatch.setattr(
        comm_service,
        "list_conversations",
        lambda *a, **k: {"ok": False, "status": 503, "code": "db_down",
                         "message": "Messaging is unavailable."},
    )
    _reject(
        lambda: convo.list_for_member(cur, actor_user_id=OWNER),
        status=503,
        code="db_down",
    )


def test_a_failing_create_raises_and_leaves_no_dangling_classification(cur, monkeypatch):
    monkeypatch.setattr(
        comm_service,
        "create_conversation",
        lambda *a, **k: {"ok": False, "status": 403, "code": "blocked",
                         "message": "This direct message is unavailable."},
    )
    _reject(lambda: _direct(cur), status=403, code="blocked")
    cur.execute(f"SELECT COUNT(1) FROM {convo.CLASSIFICATION_TABLE}")
    assert cur.fetchone()[0] == 0, (
        "a failed canonical create must not leave a classification pointing at nothing"
    )


# ---------------------------------------------------------------------------
# Membership is the canonical service's answer, not a second one
# ---------------------------------------------------------------------------

def test_member_view_resolves_through_the_canonical_access_check(cur):
    created = _direct(cur)
    view = convo.require_member_view(
        cur, actor_user_id=OWNER, conversation_ref=created["conversation_id"]
    )
    assert view["conversation_id"] == created["conversation_id"]
    assert view["private_office"]["is_private_office"] is True


def test_member_view_on_an_unclassified_conversation_is_refused(cur):
    result = comm_service.create_conversation(
        OWNER, {"conversation_type": "direct", "target_user_id": GUEST}
    )
    _reject(
        lambda: convo.require_member_view(
            cur, actor_user_id=OWNER, conversation_ref=int(result["conversation_id"])
        ),
        status=404,
        code="not_private_conversation",
    )


# ---------------------------------------------------------------------------
# Truthfulness
# ---------------------------------------------------------------------------

def test_capability_states_never_claim_encryption(cur):
    states = convo.capability_states()
    assert states["end_to_end_encrypted"] is False
    assert states["disappearing_messages"] is False
    assert states["rtc_provider"] == "agora"
    assert states["message_ledger"] == "comm_v2_messages"
    assert states["attachment_authority"] == "message_attachments"


def test_every_classification_payload_repeats_the_encryption_truth(cur):
    created = _direct(cur)
    payload = created["private_office"]
    assert payload["end_to_end_encrypted"] is False, (
        "the truth must ride on the payload, not only in a doc a screen author "
        "never read"
    )


def test_no_livekit_reference_anywhere_in_this_package():
    source = open(convo.__file__, encoding="utf-8").read().lower()
    assert "livekit" not in source.replace("no livekit is referenced", "")


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def test_classification_and_links_are_audited_with_ids_not_content(cur):
    created = _direct(cur)
    convo.link(
        cur,
        conversation_id=created["conversation_id"],
        actor_user_id=OWNER,
        link_type=convo.LINK_DOCUMENT,
        target_id="DOCUMENT:5",
    )
    cur.execute(
        f"SELECT action, object_id FROM {po_schema.AUDIT_TABLE} ORDER BY id"
    )
    rows = [dict(r) for r in cur.fetchall()]
    actions = {r["action"] for r in rows}
    assert audit.ACTION_CONVERSATION_CLASSIFY in actions
    assert audit.ACTION_CONVERSATION_LINK in actions
    for row in rows:
        assert " " not in str(row["object_id"]), (
            "an audit object_id with a space is a value wearing an id's clothes"
        )


def test_no_message_action_exists_in_the_conversation_vocabulary():
    conversation_actions = [a for a in audit.ACTIONS if a.startswith("PRIVATE_CONVERSATION_")]
    assert conversation_actions, "the vocabulary must be registered"
    assert not any("MESSAGE" in a for a in conversation_actions), (
        "message history belongs to the canonical system; a second copy here "
        "would be a second, diverging record of the same thread"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
