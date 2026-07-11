# PulseSoc Pulse Command Offline / Reconnect

Status: partial, existing architecture preserved.

## Current Coverage

- Conversation list cache continues through `loadCachedConversations`.
- Conversation messages cache continues through `loadCachedMessages`.
- Chat screen keeps existing polling sync and foreground recovery.
- Failed outbound message state is visible and retryable.
- Local-only QA fixtures can simulate failed delivery and moderated/unavailable messages without production exposure.

## Remaining Proof

- Simulator network disruption test.
- Offline outbound queue persistence beyond current failed-message retry surface.
- Duplicate prevention after reconnect with multiple pending local messages.
- Ordering proof after delayed sync events.
- Cursor sync proof across Activity and Notifications for Messenger events.

## Risk

Medium. The current UI no longer silently hides failures, but release confidence still depends on stress testing reconnect behavior with populated threads.
