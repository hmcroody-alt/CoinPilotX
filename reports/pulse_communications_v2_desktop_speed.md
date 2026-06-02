# Pulse Communications V2 Desktop Layout and Speed

## Scope

This pass improves only the Pulse Communications V2 desktop layout and request flow. Mobile CSS and behavior remain governed by the existing `max-width` rules, and the v2 backend architecture remains intact.

## Desktop Layout Changes

- Added desktop-only layout rules behind `@media (min-width: 941px)`.
- Set the desktop grid to:
  - left conversation list: `clamp(280px, 19vw, 340px)`
  - center thread: flexible main column
  - right details panel: `clamp(280px, 17vw, 320px)`
- Added a desktop details toggle that collapses the right panel to a narrow rail.
- Increased desktop chat-thread dominance at 1280px, 1440px, and 1920px widths.
- Reduced empty desktop spacing in conversation rows, message thread padding, and panel padding.
- Let the composer span the thread width cleanly with a larger input target.
- Increased desktop message bubble max width and tightened vertical spacing.

## Mobile Protection

Mobile remains under the existing `max-width: 720px` rules:

- single-column shell
- conversation list above thread
- `max-height: 42dvh` conversation list
- `min-height: 58dvh` thread
- mobile-only navigation button remains visible
- message max width remains `94%`

## Speed Changes

- Conversations load first.
- Selected conversation messages load second, after the conversation list paints.
- Default selected-thread fetch is capped at 40 messages.
- Older messages are fetched with `before_id` pagination.
- Active conversation metadata is cached client-side in `conversationCache`.
- Typing indicators are debounced and rate-limited client-side.
- Sending a message updates the local thread instead of immediately reloading the whole thread.
- Reactions update the local message payload instead of reloading the whole thread.
- No interval polling is used.
- Conversation previews and message payloads now use batched server-side lookups to avoid per-row decoration queries.

## Timing Logs

Server logs now emit `PULSE_COMM_V2_TIMING` for:

- `conversations_list`
- `selected_thread_messages`
- `send_message`
- `typing_indicator`
- `reaction`
- `read_receipt`

Client logs emit `Pulse Communications V2 timing` for the same user-facing request flow, plus attachment upload timing.

## QA Matrix

- Desktop 1280px: center chat column remains dominant, right panel constrained.
- Desktop 1440px: message bubbles and composer use the available width without dashboard-like dead space.
- Desktop 1920px: left and right rails cap at 340px and 320px while the center thread expands.
- Mobile: existing stacked layout remains unchanged.

## Validation Commands

```bash
.venv/bin/python -m py_compile pulse_communications_v2/*.py scripts/pulse_communications_v2_audit.py scripts/pulse_communications_v2_desktop_layout_audit.py scripts/pulse_communications_v2_mobile_regression_audit.py
node --check static/js/pulse_messages_v2.js
.venv/bin/python scripts/pulse_communications_v2_audit.py
.venv/bin/python scripts/pulse_communications_v2_desktop_layout_audit.py
.venv/bin/python scripts/pulse_communications_v2_mobile_regression_audit.py
.venv/bin/python scripts/site_functional_audit.py
.venv/bin/python scripts/pulse_performance_audit.py
git diff --check
```
