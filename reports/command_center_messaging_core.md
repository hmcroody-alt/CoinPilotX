# PulseSoc Command Center Real-Time Messaging Core

Date: 2026-06-19

## Architecture

Service 2C adds an append-only Command Center messaging event layer beside the existing Messages V2 database workflow.

- Main App remains authoritative for conversations, messages, permissions, receipts, typing, and unread counters.
- Successful local transactions optionally dispatch events to the Command Center worker.
- Dispatch runs in daemon threads only when `COMMAND_CENTER_ENABLED=true`.
- Worker failure does not change a successful message response.
- Existing realtime engine plus 12-second polling remains the delivery fallback.

## Event Model

New table: `command_center_message_events`

Fields:

- `id`
- `event_id`
- `conversation_id`
- `message_id`
- `sender_id`
- `recipient_id`
- `event_type`
- `payload_json`
- `status`
- `created_at`
- `processed_at`

Supported event types:

- `message_created`
- `message_delivered`
- `message_read`
- `message_edited`
- `message_deleted`
- `reaction_added`
- `reaction_removed`
- `typing_started`
- `typing_stopped`

Indexes cover conversation, recipient, event type, and creation time.

## Worker Endpoints

All require `Authorization: Bearer <COMMAND_CENTER_INTERNAL_TOKEN>`:

- `POST /internal/command-center/messages/event`
- `GET /internal/command-center/messages/unread/<user_id>`
- `GET /internal/command-center/messages/conversation/<conversation_id>/state`
- `POST /internal/command-center/messages/typing`

Conversation state returns event/typing metadata only. It does not return message bodies. An optional `user_id` query parameter enforces active participant membership.

## Main App Dispatch

`services/command_center_client.py` now supports:

- `enqueue_message_event()`
- `enqueue_message_delivered()`
- `enqueue_message_read()`
- `enqueue_typing_event()`
- `get_unread_counts()`
- `get_conversation_state()`

Disabled mode returns safe unavailable/default shapes and does not contact the worker.

## Delivery And Read Receipts

- Message save remains `delivery_status='sent'` in `comm_v2_messages`.
- Loading a conversation writes `delivered_at` for incoming messages.
- Loading/reading a conversation writes read receipt timestamps and clears the participant unread count.
- The latest incoming message is mirrored as consolidated delivered/read worker events.

## Typing Behavior

- Input is debounced before sending typing start.
- Typing stop is sent after five seconds of inactivity or input blur.
- Local typing rows expire after five seconds.
- Worker typing events also expire from conversation state after five seconds.
- Worker typing calls use a lightweight per-user/conversation rate guard.

## Unread Counters

- Existing `comm_v2_participants.unread_count` remains authoritative.
- Conversation rows render their own unread counts.
- Global chat badges use `data-chat-unread`.
- General alert badges remain separate and are not combined with message counts.

## Realtime And Polling

- Existing realtime engine events are consumed where available.
- Messages V2 polls every 12 seconds while visible.
- Hidden tabs reduce polling to 45 seconds.
- Polling requests are bounded and deduplicated by event id.
- No WebSocket rewrite was introduced.

## Security

- Existing conversation participant and block checks remain authoritative.
- Worker endpoints require internal bearer auth.
- Event types and positive identifiers are validated.
- Event payloads are depth/size bounded and token/secret/password/credential keys are removed.
- Conversation state does not expose message content.
- Internal tokens, database URLs, and filesystem paths are not returned or logged.

## Existing Behavior Preserved

No message storage, send route, conversation list, receipt, typing, unread, or realtime polling path moved out of the Main App. Command Center event dispatch is additive and disabled by default.

## Service 2D Boundary

Still not implemented:

- push notification migration/retries
- voice calls
- video calls
- AI summaries
- Redis pub/sub acceleration
- dedicated websocket fanout
- durable queue processing

## QA

Validation covers compilation, protected endpoint auth, invalid event rejection, valid event acceptance, unread/state shapes, typing events, disabled Main App behavior, worker skeleton regression, local worker HTTP smoke, Main App startup, and Messages route behavior.
