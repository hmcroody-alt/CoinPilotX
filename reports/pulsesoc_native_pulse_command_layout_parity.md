# PulseSoc Native Pulse Command Layout Parity

Status: first top-level layout pass completed.

## Production layout parity

- Current estimate: 84%.
- Top-level shell: 86%.
- Conversation list: 90%.
- Conversation screen: 82%.
- Calls: 78%.
- Groups: 82%.
- Rooms: 76%.

## What changed

- Kept one authoritative `MessengerScreen` and one authoritative `ChatScreen`.
- Preserved the production tab order: Chats, Calls, Groups, Rooms.
- Tightened the native conversation row toward production dimensions from `static/css/pulse_messages_v2.css`:
  - production row min-height: `72px`
  - native row target: `74`
  - production avatar: `48px`
  - native avatar: `48`
  - production title: `14px`
  - native title: `14`
  - production preview: `12px`
  - native preview: `12`
- Reduced Messenger header stack spacing and active-user rail spacing.
- Preserved shared bottom navigation and route state.

## Remaining layout gaps

- Conversation header avatar/presence/action sizing needs direct production comparison.
- Calls tab needs production call-history row parity.
- Groups and Rooms detail surfaces need exact production side-by-side review.
- Message list needs unread divider/date separator layout proof.
- Wide/desktop Messenger split-pane parity has not yet been remeasured in this pass.
