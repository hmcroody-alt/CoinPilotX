# PulseSoc LogiNexus Messenger Simulator QA

Status: completed for first Pulse Command transformation milestone.

## QA Environment

- Primary target: Xcode iPhone Simulator.
- Expected local API/proxy: existing authenticated simulator QA stack.
- Hardware-only checks remain unclaimed.

## Simulator Walkthrough Targets

- Open Pulse Command inbox.
- Verify command header, live metrics, search, segment rail, active signal rail, and conversation list.
- Open a conversation.
- Verify contextual header, voice/video call entry, message history, loading/empty state, composer, attachment actions, and semantic send button.
- Open Groups and Rooms from Pulse Command segment rail.
- Open UNDX and verify Digital Intelligence Companion copy.
- Confirm no red screen or route loop.

## Current Evidence

- Inbox screenshot: `reports/screenshots/logi-nexus-messenger/inbox-after.png`
- UNDX screenshot: `reports/screenshots/logi-nexus-messenger/undx.png`
- Xcode iPhone 17 Pro Simulator launched the native app with local authenticated auto-login using the working local API base `http://127.0.0.1:5107`.
- Verified Pulse Command inbox renders with command header, metrics, search, tabs, active signal rail, and bottom navigation.
- Verified UNDX renders with Digital Intelligence Companion copy and the existing assistant endpoint preserved.

## Caveats

- The development build still shows the existing `expo-av` deprecation warning in Metro logs; this is tracked as a future media dependency task.
- Full direct conversation send QA needs a seeded conversation in the local account.
- Physical-device-only camera, microphone, push, background call, and audio-route checks are not claimed.
