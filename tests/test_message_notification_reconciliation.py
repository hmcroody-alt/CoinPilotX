import json
import sqlite3
from unittest.mock import patch

import pytest
from pulse_communications_v2.models import ensure_schema
from pulse_communications_v2.notification_reconciliation import reconcile
from pulse_communications_v2 import service


@pytest.fixture
def database():
    conn = sqlite3.connect(':memory:')
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    ensure_schema(cur)
    cur.execute('CREATE TABLE notifications (id INTEGER, recipient_user_id INTEGER, user_id INTEGER, source_type TEXT, source_id TEXT, metadata_json TEXT, metadata TEXT)')
    cur.execute(
        "INSERT INTO comm_v2_conversations(id, public_id, conversation_type, privacy, created_at, updated_at) "
        "VALUES (10, 'notification-test', 'direct', 'private', '2026-01-01T00:00:00+00:00', '2026-01-01T00:00:00+00:00')"
    )
    cur.execute("INSERT INTO comm_v2_participants(conversation_id,user_id,last_read_message_id) VALUES (10,7,1)")
    cur.execute("INSERT INTO comm_v2_participants(conversation_id,user_id,last_read_message_id) VALUES (10,8,2)")
    for mid in (1, 2, 3):
        cur.execute("INSERT INTO comm_v2_messages(id,conversation_id,sender_user_id) VALUES (?,10,9)", (mid,))
    yield conn, cur
    conn.close()


def entry(mid=1, **overrides):
    return dict(key=f'os-{mid}', messageId=mid, conversationId=10, schemaVersion=1,
                notificationType='message', messageNamespace='comm_v2', recipientUserId=7, **overrides)


def test_read_and_unread_same_conversation(database):
    _, cur = database
    assert reconcile(cur, 7, [entry(2), entry(), entry()]) == ['os-1', 'os-1']


@pytest.mark.parametrize('field,value', [('recipientUserId', 8), ('notificationType', 'missed_call'), ('notificationType', 'live'), ('schemaVersion', 2), ('schemaVersion', True), ('messageNamespace', 'private'), ('messageId', True), ('messageId', -1), ('conversationId', 99)])
def test_unknown_or_other_account_preserved(database, field, value):
    item = entry(); item[field] = value
    assert reconcile(database[1], 7, [item]) == []


def test_legacy_requires_ownership_not_shared_membership(database):
    _, cur = database
    legacy = dict(key='legacy', type='message', messageId=1, conversationId=10, notificationId=25)
    cur.execute('INSERT INTO notifications VALUES (25,8,8,?,?,?,NULL)', ('comm_v2_message', '1', json.dumps({'conversation_id': 10})))
    assert reconcile(cur, 7, [legacy]) == []
    cur.execute('UPDATE notifications SET recipient_user_id=7,user_id=7')
    assert reconcile(cur, 7, [legacy]) == ['legacy']
    cur.execute("UPDATE notifications SET source_type='call'")
    assert reconcile(cur, 7, [legacy]) == []


@pytest.mark.parametrize('change', ["UPDATE comm_v2_messages SET deleted_at='deleted' WHERE id=2", "INSERT INTO comm_v2_message_deletions(message_id,conversation_id,user_id) VALUES(2,10,7)", "UPDATE comm_v2_conversations SET deleted_at='deleted' WHERE id=10", "INSERT INTO comm_v2_blocks(blocker_user_id,blocked_user_id,status) VALUES(7,9,'active')"])
def test_deleted_unsent_conversation_or_blocked(database, change):
    database[1].execute(change)
    assert reconcile(database[1], 7, [entry(2)]) == ['os-2']


def test_bounded_read_does_not_read_later_message(database):
    conn, cur = database
    with patch.object(service, '_disabled', return_value=None), patch.object(service, '_conversation_access', return_value=({'id': 10}, 'ok')), patch.object(service, '_read_receipts_allowed', return_value=True):
        result = service.mark_read(7, 10, existing_conn=(conn, cur), commit=False, through_message_id=2)
        assert result['last_read_message_id'] == 2
        row = dict(cur.execute('SELECT last_read_message_id,unread_count FROM comm_v2_participants WHERE user_id=7').fetchone())
        assert row == {'last_read_message_id': 2, 'unread_count': 1}
        # An old retry must never regress the already-confirmed watermark.
        service.mark_read(7, 10, existing_conn=(conn, cur), commit=False, through_message_id=1)
        assert cur.execute('SELECT last_read_message_id FROM comm_v2_participants WHERE user_id=7').fetchone()[0] == 2
        assert reconcile(cur, 7, [entry(2), entry(3)]) == ['os-2']


def test_malformed_and_missing_preserved(database):
    assert reconcile(database[1], 7, [None, {}, [], {'messageId': 'bad'}, entry(999)]) == []


def test_the_source_type_guard_stops_a_cross_id_space_dismissal(database):
    """Two message stores exist and their row ids overlap.

    comm_v2 writes `comm_v2_messages`. Six live web routes -- the ones that go
    through `pulse_send_conversation_message` in bot.py -- write `pulse_messages`
    instead, and nothing bridges the two tables, so `pulse_messages.id = 1` and
    `comm_v2_messages.id = 1` are unrelated messages that merely share a number.

    The only thing separating them at reconciliation time is `source_type`:
    `create_pulse_notification` stamps `entity_type` onto the row, comm_v2
    declares `comm_v2_message`, and the legacy delivery path declares
    `conversation`. Widen this filter to accept both and a web-sent, still-unread
    alert gets resolved against a comm_v2 row that happens to carry the same id --
    and if that unrelated comm_v2 message is read, iOS destroys the only copy of
    a message the user never saw.

    The consequence of keeping the filter narrow, which is worth stating plainly:
    message alerts from the web routes are never dismissed by this reconciler.
    They are preserved, which is the safe direction, but the fix does not cover
    them. Closing that gap needs its own namespace and its own read query against
    `pulse_messages` -- not a wider filter here.
    """
    _, cur = database
    # comm_v2 message 1 is read by user 7: the fixture sets last_read_message_id=1.
    assert reconcile(cur, 7, [entry(1)]) == ['os-1']
    # The same id, arriving from the web emitter, is a different message.
    web_alert = dict(key='web-1', type='chat_message', messageId=1, conversationId=10, notificationId=41)
    cur.execute('INSERT INTO notifications VALUES (41,7,7,?,?,?,NULL)',
                ('conversation', '1', json.dumps({'conversation_id': 10})))
    assert reconcile(cur, 7, [web_alert]) == []
    owner = cur.execute('SELECT recipient_user_id FROM notifications WHERE id=41').fetchone()[0]
    assert owner == 7
