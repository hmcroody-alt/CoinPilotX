# Chat Notifications Realtime Repair

Date: 2026-06-09

- Communications V2 has conversation unread counts, per-thread unread state, typing, read receipt, media attachment, voice note, and realtime polling endpoints.
- Chat notifications now map to the primary chat category and include conversation/message deep links.
- Push delivery is attempted immediately after notification creation through web push or Expo push tokens.
- Generic notification records are still created for history, but mobile deep links prefer `pulse://messages/<conversation_id>`.

Second-pass validation:

- `chat_realtime_audit.py`
- `chat_send_receive_audit.py`
- `pulse_message_unread_badge_audit.py`
- `pulse_comm_v2_message_density_audit.py`
- `pulse_comm_v2_realtime_readiness_audit.py`
- `message_realtime_notification_audit.py`

Production QA still requires a signed physical iOS/Android install with accepted notification permission.
