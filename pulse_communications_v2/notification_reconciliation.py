"""Read-only, account-scoped decisions. Unknown identities always survive."""
import json

MESSAGE_TYPES = frozenset(('message', 'new_message', 'chat_message', 'private_message',
                           'group_message', 'image_message', 'video_message', 'voice_message', 'file_message'))


def positive_id(value):
    if isinstance(value, bool):
        return 0
    try:
        number = int(str(value))
        return number if 0 < number <= 9007199254740991 else 0
    except (ValueError, TypeError):
        return 0


def reconcile(cur, user_id, entries):
    dismiss = []
    for entry in entries[:100]:
        if not isinstance(entry, dict):
            continue
        mid, cid = positive_id(entry.get('messageId')), positive_id(entry.get('conversationId'))
        if not mid or not cid:
            continue
        version = entry.get('schemaVersion')
        if version is not None:
            if version != 1 or isinstance(version, bool) or entry.get('notificationType') != 'message' or entry.get('messageNamespace') != 'comm_v2' or positive_id(entry.get('recipientUserId')) != user_id:
                continue
        else:
            # Old pushes lack account scope. Membership alone does NOT prove who
            # received one (both accounts may belong to the same conversation).
            nid = positive_id(entry.get('notificationId'))
            if not nid or entry.get('type') not in MESSAGE_TYPES:
                continue
            cur.execute('''SELECT source_type, source_id, metadata_json, metadata FROM notifications
                WHERE id=? AND COALESCE(recipient_user_id,user_id)=?''', (nid, user_id))
            row = cur.fetchone()
            if not row:
                continue
            row = dict(row)
            try:
                meta = json.loads(row.get('metadata_json') or row.get('metadata') or '{}')
            except (ValueError, TypeError):
                continue
            if row.get('source_type') != 'comm_v2_message' or positive_id(row.get('source_id')) != mid or positive_id(meta.get('conversation_id') or meta.get('conversationId')) != cid:
                continue
        cur.execute('''SELECT p.last_read_message_id, m.deleted_at, d.id AS removed,
                    c.deleted_at AS conversation_deleted, b.id AS blocked
                FROM comm_v2_messages m
                JOIN comm_v2_participants p ON p.conversation_id=m.conversation_id AND p.user_id=?
                JOIN comm_v2_conversations c ON c.id=m.conversation_id
                LEFT JOIN comm_v2_message_deletions d ON d.message_id=m.id AND d.user_id=p.user_id
                LEFT JOIN comm_v2_blocks b ON b.blocker_user_id=p.user_id AND b.blocked_user_id=m.sender_user_id AND b.status='active'
                WHERE m.id=? AND m.conversation_id=?''', (user_id, mid, cid))
        row = cur.fetchone()
        if not row:
            continue
        row = dict(row)
        if mid <= int(row.get('last_read_message_id') or 0) or row.get('deleted_at') or row.get('removed') or row.get('conversation_deleted') or row.get('blocked'):
            dismiss.append(entry.get('key'))
    return dismiss
