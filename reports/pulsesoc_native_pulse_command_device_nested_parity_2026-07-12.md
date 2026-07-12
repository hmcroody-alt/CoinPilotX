# PulseSoc Native Pulse Command Device and Nested Parity QA

Date: 2026-07-12  
Status: substantial completion, not frozen.

## Production and reuse

Reinspected live `https://pulsesoc.com/pulse/messages` before changes. Production still exposes All, Direct, Groups, Rooms, AI, Unread; search; three quick actions; thread header/calls; composer; details/safety. Native reused `MessengerScreen`, `ChatScreen`, API/cache/search wrappers, message models, domain action rules, upload functions, media viewer, navigation, and localhost-only fixtures. No WebView, API, or database contract changed.

## Simulator blocker diagnosis

Root cause: compact, standard, and Pro Max devices were first-booting concurrently, causing CoreSimulator migration and install services to serialize or stall. Repair: `xcrun simctl shutdown all`, then boot, `bootstatus -b`, install/launch, and capture one device at a time. No device erase, runtime deletion, or DerivedData deletion was needed. All four iOS 26.5 device classes recovered.

## Implementation

- Added localhost/dev-only deterministic QA start-route and nested-state controls.
- Added persistent per-conversation drafts using existing AsyncStorage.
- Replaced exposed inline media tools with a production-shaped attachment sheet reusing existing photo, camera, document, and voice handlers.
- Expanded localhost-only nested fixtures for short, long, multiline, link, emoji, system, image, video, document, voice, failed, read, reply, reaction, deleted/moderated states.
- Preserved optimistic send/retry/reaction/delete rollback paths and cached reconnect behavior.

## Evidence

Directory: `reports/screenshots/native-pulse-command-device-nested-parity-2026-07-12/`

- `compact-inbox-populated.png` — Simulator verified, mock data.
- `standard-inbox-populated.png` — Simulator verified, mock data.
- `pro-inbox-populated.png` — prior valid Pro evidence retained because inbox rendering did not change.
- `promax-inbox-populated.png` — Simulator verified, mock data.
- `pro-context-menu.png` — Simulator verified context menu/reactions/reply/report/safety ownership rules.
- `pro-attachment-sheet.png` — Simulator verified sheet geometry and existing media actions.
- `promax-chat-reply-keyboard.png` — blocked for keyboard geometry by the simulator's first-use keyboard tutorial; it still proves cached reconnect copy and nested attachment rendering.

## Classification

Simulator verified: four device widths for inbox hierarchy; populated nested bubbles; context-menu modal; attachment sheet; cached reconnect banner; long content; safe areas and bottom navigation.  
Mock-state verified: fixture conversations/messages, failed/read/reply/reaction/media/system states.  
Code-path verified: draft persistence, search debounce, cache fallback, retry, reaction rollback, delete/report, group/room/AI routing, upload handlers.  
Blocked: unobscured keyboard/composer capture; automatic reconnect reconciliation; fresh group/room/AI nested screenshots; selected-filter auto-scroll evidence.  
Physical-device-only: real camera/microphone, Bluetooth/speaker, push/lock-screen/background calls, cellular transitions, large uploads, file-provider edges.

## Honest parity

- Production layout 91%; visual 86%; feature 91%; interaction 87%.
- Inbox 93%; list 92%; conversation screen 84%; bubbles 86%; reply/reaction/context menu 84%; composer 82%; attachments 78%.
- Calls 68%; Groups 74%; Rooms 68%; AI/UNDX 74%; Safety 70%; offline/reconnect 62%.
- Responsive behavior 90%; loading/empty/error 76%.
- Xcode Simulator QA 72%; device-size coverage 100%.
- Backend/business reuse 96%; frontend utility reuse 92%; existing native component reuse 94%.

Pulse Command is not frozen and cannot replace production Messenger yet. Next mission remains Pulse Command: keyboard tutorial clearance, deterministic reconnect reconciliation, selected-filter visibility, and group/room/AI nested evidence.
