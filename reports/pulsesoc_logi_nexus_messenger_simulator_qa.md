# PulseSoc Pulse Command Simulator QA

Status: updated Xcode Simulator pass completed for this milestone.

## Required Local QA Configuration

Use the Xcode iPhone Simulator with the local API proxy and explicit local-only populated fixtures:

```bash
EXPO_PUBLIC_PULSE_API_BASE_URL=http://127.0.0.1:5107 \
EXPO_PUBLIC_PULSESOC_QA_AUTO_LOGIN=1 \
EXPO_PUBLIC_PULSESOC_QA_MESSENGER_FIXTURES=1 \
npm run --prefix mobile-native start:qa -- --host localhost
```

The fixture flag is ignored unless the API base is localhost or `127.0.0.1`.

## Simulator Checklist

- Pulse Command inbox opens with populated conversations.
- Chats tab shows pinned, muted, typing, unread, failed, verified, and long-name states.
- Calls tab renders active/recent call rows where available.
- Groups tab renders native group rows and detail/chat routes.
- Rooms tab renders native room rows and join/chat route.
- Direct conversation opens from fixture conversation.
- Long press opens message action sheet.
- Reply state appears in composer.
- Reaction updates the bubble.
- Failed message retry is visible.
- Message report/delete controls are visible.
- Media attachment opens `NativeMediaViewer`.
- UNDX conversation remains branded as Digital Intelligence Companion.

## Current Evidence

- `reports/screenshots/logi-nexus-messenger/pulse-command-inbox-fixtures-2.png`
  - Shows Pulse Command in the iPhone 17 Pro simulator with local-only QA fixtures enabled.
  - Verified 6 populated channels and unread count state.
  - Verified Chats / Calls / Groups / Rooms tab rail in the native Pulse Command surface.
- `reports/screenshots/logi-nexus-messenger/pulse-command-chat-fixture-cached.png`
  - Shows fixture conversation `9001`.
  - Verified cached/reconnect state, media attachment placeholder, voice placeholder, moderated message state, and composer tool row.

## Observed Runtime Warnings

- The app still shows the known app-wide `expo-av` deprecation warning in development builds.
- This is tracked as a future media dependency migration item and did not block Pulse Command rendering.

## Remaining Simulator QA

- Long-press action sheet still needs hands-on simulator interaction capture.
- Calls / Groups / Rooms tab destination clicks need a focused follow-up.
- Offline/reconnect needs a real local API disruption test.
- Large Text and Reduced Motion simulator settings still need a focused accessibility pass.
