# PulseSoc Native Pulse Command WebView Visual Port

Date: 2026-07-17

## Outcome

The current PulseSoc WebView Messenger V3 visual language is now represented in the native React Native inbox and conversation surfaces without embedding a WebView or creating a second messaging system.

## Production code reused

- Canonical conversation and message API routes from `mobile-native/src/api/messenger.ts`.
- Existing-account session and server-owned user identity.
- Conversation normalization, cached history, queued sends, reconnect behavior, read state, typing state, reactions, replies, deletion, reporting, media upload, and call routing.
- WebView design tokens and geometry inspected from `templates/pulse_messages_v2.html`, `static/css/pulse_messages_v2.css`, and `static/js/pulse_messages_v2.js`.

## Native implementation

- A single Pulse Command / Messenger V3 lockup with an animated, reduced-motion-aware signal orb.
- Search, filters, active-presence rail, quick actions, conversation signal badges, unread badges, avatar images, and stronger pinned-state depth.
- Compact native chat identity header with presence, audio call, video call, and conversation controls.
- Ambient thread depth, differentiated incoming/outgoing bubbles, native media cards, interactive native voice playback, persistent composer, emoji, microphone, attachment, and send actions.
- Messenger and Chat now own their headers and safe-area spacing so the app shell does not duplicate the WebView hierarchy.

Browser DOM, CSS selectors, and HTML audio controls cannot execute directly inside React Native. Those presentation-only pieces were translated to native `View`, `Text`, `Image`, `Animated`, and `expo-av` primitives. The backend contracts and canonical data model were not duplicated or replaced.

## Xcode iPhone Simulator evidence

- Device: PulseSoc iPhone 16 Pro simulator
- Inbox: `reports/screenshots/native-pulse-command-webview-port-2026-07-17/inbox.png`
- Conversation: `reports/screenshots/native-pulse-command-webview-port-2026-07-17/thread.png`
- QA data: controlled localhost account and localhost-only Messenger fixtures
- Production user data: not used

## Verification

- `npm run --prefix mobile-native typecheck`: passed
- `scripts/pulsesoc_pulse_command_code_reuse_audit.py`: passed
- `scripts/pulsesoc_native_messenger_audit.py`: passed
- `scripts/pulsesoc_native_messenger_device_qa_audit.py`: passed
- `scripts/pulsesoc_native_pulse_command_exact_parity_audit.py`: passed
- Scoped `git diff --check`: passed

## Remaining review boundary

This pass ports the supplied WebView inbox and direct-conversation visual system. Group detail, room detail, the full Conversation Control Center, and every media/permission state remain separate focused surfaces and were not cosmetically rewritten beyond the shared primitives in this pass.
