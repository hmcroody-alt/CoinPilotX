# PulseSoc Native Pulse Command Simulator QA

Status: partial fresh exact-parity screenshot evidence captured for this milestone.

Primary QA environment: Xcode Simulator.

Required evidence directory:

- `reports/screenshots/native-pulse-command-production-parity/`

## Current simulator coverage

- iPhone 17 Pro: captured for route open, inbox, and chat empty-state/composer geometry.
- Pro Max: required for next run.
- Compact iPhone: required for next run.

## Evidence targets

1. Messenger inbox
2. Chats tab
3. Calls tab
4. Groups tab
5. Rooms tab
6. Active-user rail
7. Conversation row
8. Direct conversation
9. Incoming/outgoing bubbles
10. Reply
11. Reaction
12. Context menu
13. Composer
14. Attachment
15. UNDX
16. Group detail
17. Room detail
18. Loading
19. Empty
20. Error
21. Offline/reconnect

## Current blocker/risk

Fresh visual evidence is still incomplete. Prior authenticated simulator QA exists for Pulse Command foundations, but exact production side-by-side evidence is not complete enough to freeze Pulse Command.

Captured this pass:

- `reports/screenshots/native-pulse-command-production-parity/pulse-command-open-attempt.png`
- `reports/screenshots/native-pulse-command-production-parity/pulse-command-inbox-iphone17pro.png`
- `reports/screenshots/native-pulse-command-production-parity/pulse-command-inbox-iphone17pro-refined.png`
- `reports/screenshots/native-pulse-command-production-parity/pulse-command-inbox-iphone17pro-refined-clean.png`
- `reports/screenshots/native-pulse-command-production-parity/pulse-command-chat-iphone17pro-refined.png`
- `reports/screenshots/native-pulse-command-production-parity/pulse-command-chat-iphone17pro-refined-copyfix.png`

Observed:

- Native `/pulse/messages` route opened on the iPhone 17 Pro simulator.
- Native `/pulse/messages/1` route opened and showed the chat shell, call actions, empty state, attachment actions, input, and send area.
- Local QA account had no conversations/messages, so populated row and bubble screenshots remain pending.
- Simulator automation in this Xcode runtime does not expose `simctl ui tap`; a dev warning toast could not be dismissed through `simctl`.
- Runtime emitted the existing `expo-av` deprecation warning. This remains a platform media migration follow-up, not a blocker for this visual-density slice.
