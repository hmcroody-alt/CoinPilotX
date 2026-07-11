# PulseSoc LogiNexus Pulse Command Transformation

Status: first subsystem transformation milestone.

## Scope Completed

- Evolved the existing native Messenger inbox into `Pulse Command` without replacing the authoritative screen.
- Added shared `PulseCommand` primitives for command panels, contextual headers, search, segment rail, avatars, actions, and metrics.
- Kept existing server-authoritative conversation APIs, search API, cached conversations, unread counts, and native route dispatch.
- Added inbox-level live metrics for channels, unread conversations, and active calls using existing notification/calls contracts.
- Added a compact active signal rail based on server-provided conversation presence when available.
- Preserved native chat routing, profile routing, safety routing, group/room routing, and call entry routing.

## Conversation Surface

- Preserved `sendConversationMessage`, `sendTyping`, `syncConversation`, `markConversationSeen`, `uploadMessengerMedia`, voice recording, file/image attachment, and native media viewer handoff.
- Rebuilt the visible conversation shell around the shared LogiNexus screen shell and Pulse Command header.
- Added shared loading and empty state panels.
- Improved message bubbles, attachment panels, semantic send path, disabled send state, and keyboard/safe-area composer spacing.

## Groups, Rooms, And UNDX

- Updated Groups/Rooms to use Pulse Command command language, shared search, and shared state panels.
- Preserved group join/leave, group chat, report, room join, and group detail behavior.
- Replaced remaining visible Pulse AI labels in native screens with UNDX / Digital Intelligence Companion copy while preserving internal route/API compatibility.

## Not Claimed

- Physical-device microphone, camera, Bluetooth/audio routing, lock-screen ringing, push ringing, and background media upload remain release-device QA.
- Full long-thread, image-heavy, multi-device, reduced-motion, and large-text matrices are still pending.
- Calls list and full rooms detail are still foundation-level, not LogiNexus-complete.
