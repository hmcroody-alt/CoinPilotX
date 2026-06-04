# Pulse Communications V2 Publish Readiness

Date: 2026-06-04

## Published State

Pulse Communications V2 is now the live Pulse Messenger surface by default.

- `/pulse/messages` serves Communications V2 when `PULSE_COMMUNICATIONS_V2_ENABLED` is enabled.
- `PULSE_COMMUNICATIONS_V2_ENABLED` now defaults to `true`.
- `/pulse/messages-v2` remains available as a direct V2 route.
- `/pulse/messages?legacy=1` and `/pulse/messages-legacy` remain available as rollback paths.
- Setting `PULSE_COMMUNICATIONS_V2_ENABLED=false` disables V2 API writes and returns `/pulse/messages` to the legacy messenger.

## Product Surface Added

- Desktop right details panel restored with members, live typing state, safety controls, public rooms, and fallback link.
- Details panel is collapsible on desktop.
- Mobile layout remains the stacked conversation/thread layout and hides the desktop details rail.
- Public room creation is visible from the main V2 UI.
- Public room discovery is loaded at startup.
- Safety controls for report-last-message and block-peer are visible in the details panel.

## Functional Coverage

Audits cover:

- Create DM and send DM.
- Create group and send group message.
- Create public/private rooms and send room message.
- Create community and channel.
- Attachment upload through the shared Pulse media pipeline.
- Message history and reload persistence.
- Permission enforcement.
- Typing indicators, read receipts, and reactions.
- Report/block/moderation flows.
- Desktop speed and layout.
- Mobile regression.
- Rollback with `PULSE_COMMUNICATIONS_V2_ENABLED=false`.

## Rollback

Immediate rollback:

```text
PULSE_COMMUNICATIONS_V2_ENABLED=false
```

That leaves the new schema in place but prevents V2 writes and restores the legacy messenger route behavior.

