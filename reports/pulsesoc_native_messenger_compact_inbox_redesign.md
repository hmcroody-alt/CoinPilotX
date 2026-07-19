# PulseSoc Native Messenger Compact Production Inbox Redesign

Date: 2026-07-18

## Root Causes

- Oversized Messenger layout: the native inbox previously spent first-screen height on a large `Pulse Command / Messenger V3` introduction card before the real inbox.
- Hero-card source: `mobile-native/src/screens/MessengerScreen.tsx` owned the extra lockup inside the `FlatList` header.
- Reserved vertical space: the removed block had its own orb, title, subtitle, plus action, border, background, margins, and decorative gap.
- Conversation visibility delay: first real conversations were pushed lower because Messenger repeated branding above search, filters, active contacts, and quick actions.
- Duplicate safe-area or inset behavior: Messenger hides the global tab header, so the screen needs one compact native header, not a second product hero.
- Global-overlay interference: `MessengerScreen` does not mount `ACTIVE PULSESOC CALL`, `Voice in progress`, phantom caller state, mini-player overlays, or a persistent music/call banner above the list.

## Implementation

### Files Inspected

| Reference area | Existing native component | Production API/source | Required modification | Reuse status | QA method |
| --- | --- | --- | --- | --- | --- |
| Messenger screen | `mobile-native/src/screens/MessengerScreen.tsx` | `listConversations`, `searchMessenger` | Remove hero, tighten hierarchy, keep `FlatList` | Reused and modified | Static audit + typecheck |
| Removed hero | Old Messenger header/lockup in `MessengerScreen` | Native screen header only | Delete component mount and style reliance | Removed | Forbidden-string audit |
| Search hooks | `PulseCommandSearch` | `/api/pulse/communications/v2/search`, people search fallback | Keep debounced search and stale-response cancellation | Reused | Static audit |
| Filter state | `PulseCommandSegmentRail` | Local conversation filter over canonical rows | Preserve All/Direct/Groups/Rooms/AI/Unread | Reused | Static audit |
| Conversation query | `listConversations` | Production Messenger backend | Keep cached data and refresh path | Reused | Static audit |
| Presence provider | `isActivePresence`, conversation presence field | Conversation/presence payload | Active rail uses real active conversations | Reused | Static audit |
| Realtime updates | `subscribeConversationUpdates` | Native conversation listener bridge | De-dupe by conversation ID | Reused | Static audit |
| Conversation row | `ConversationRow` | Canonical conversation ID/title/avatar/unread | Compact row with preview, timestamp, badges | Reused and modified | Static audit |
| Avatar component | `PulseCommandAvatar` | Canonical avatar URLs/presence | Keep existing avatar system | Reused | Typecheck |
| New Chat flow | `openNewChat` | `NewChat` stack route | Header, Add, and quick action route to production flow | Reused | Static audit |
| Group creation | `QuickAction` | Existing `Groups` native surface | Route to existing Groups surface; no fake group creator | Preserved boundary | Static audit |
| Room creation | `QuickAction` | Existing `Groups`/rooms native surface | Route to existing Groups surface; no fake room creator | Preserved boundary | Static audit |
| Control Center/settings | Gear button | `Chat` route with `openControlCenter` | Opens saved/first conversation control center | Reused | Static audit |
| Bottom navigation | `LogiNexusBottomNavigation` | App tab navigator | Messenger remains active tab; content bottom padding retained | Reused | Typecheck |
| Loading/error/empty | `ConversationSkeletonList`, `LogiNexusStatePanel` | Cached conversations + live load | Mutually exclusive loading/error/empty | Reused and modified | Static audit |
| Caching | `loadCachedConversations`, `cacheConversations` | AsyncStorage cache | Keep cached rows visible during refresh/error | Reused | Static audit |

### Files Changed

- `mobile-native/src/screens/MessengerScreen.tsx`
- `scripts/pulsesoc_native_messenger_compact_inbox_audit.py`
- `scripts/pulsesoc_native_pulse_command_exact_parity_audit.py`
- `reports/pulsesoc_native_messenger_compact_inbox_redesign.md`

### Components Reused

- `LogiNexusScreenShell`
- `LogiNexusSignalIndicator`
- `PulseCommandAvatar`
- `PulseCommandPanel`
- `PulseCommandSearch`
- `PulseCommandSegmentRail`
- `LogiNexusStatePanel`
- `LogiNexusBottomNavigation`

### Components Removed

- Large native inbox `Pulse Command / Messenger V3` hero/intro card.
- Hero-only decorative orb/large action pattern from the Messenger inbox.
- Reserved hero spacing and marketing-panel style intro block.

### APIs Reused

- `listConversations`
- `loadCachedConversations`
- `searchMessenger`
- `openDirectConversation`
- `subscribeConversationUpdates`
- `updateCachedConversationPreview`

### Realtime Events Reused

- Native conversation update subscription remains mounted through `subscribeConversationUpdates`.
- Incoming updates de-dupe by canonical conversation ID.
- Updated conversations reorder in place without creating duplicate rows.

### Routes Preserved

- Rows open `Chat` with canonical `conversationId`.
- Header plus, Add, and New Chat quick action open `NewChat`.
- Create Group and Start Room route to the existing native `Groups` surface, because no separate production-native creation screen exists yet.
- Safety opens `SafetyHub`.
- Settings gear opens `Chat` with `openControlCenter`.

## Functional Verification

- Search: PASS. `PulseCommandSearch` remains wired to production Messenger search with debounce and stale-response cancellation through `loadSequence`.
- Settings: PASS. Gear button is visible beside search and opens Conversation Control Center for the saved or first available conversation.
- Direct: PASS. Direct chip filters canonical `conversation_type === "direct"`.
- Groups: PASS. Groups chip filters canonical `conversation_type === "group"`.
- Rooms: PASS. Rooms chip filters canonical `conversation_type === "room"`.
- AI: PASS. AI chip supports `ai`, `intelligence`, and `undx`.
- Unread: PASS. Unread chip filters canonical unread counts and displays aggregate count.
- Active-contact routing: PASS. Existing active conversations open their canonical conversation IDs.
- New Chat: PASS. Header plus, Add rail item, and quick action route to `NewChat`.
- Create Group: PASS WITH BOUNDARY. The shortcut routes to the existing native Groups surface; no unsupported fake creator is exposed.
- Start Room: PASS WITH BOUNDARY. The shortcut routes to the existing native Groups/Rooms surface; no unsupported fake room creator is exposed.
- Conversation routing: PASS. Conversation rows navigate to `Chat` with canonical IDs.
- Presence: PASS. Presence derives from canonical conversation presence and `isActivePresence`.
- Unread counts: PASS. Row unread badges and tab badge path remain canonical.
- Realtime updates: PASS STATIC. `subscribeConversationUpdates` updates only the affected row and de-dupes by conversation ID.
- Pagination: PARTIAL. The recent conversation list is virtualized; the production conversation-list API currently exposes first-page list loading rather than a native inbox offset contract.

## Layout Verification

- Hero removed: YES.
- Reserved hero space removed: YES.
- Header compact: YES.
- Search-to-filter spacing compliant: YES.
- Quick actions compact: YES.
- Recent Conversations visible early: YES.
- Last row unobscured: YES STATIC. Bottom padding remains above fixed native tab navigation.
- Global call banner absent: YES.
- Invisible overlays absent: YES STATIC.

## Performance

- Cold load: NOT MEASURED ON DEVICE. Static safeguards are present: `FlatList`, compact skeleton, no large hero, and no foreground external fetch outside Messenger API calls.
- Cached load: STRUCTURALLY IMPROVED. Cached conversations load before refresh and remain visible while refresh runs.
- Time to first row: STRUCTURALLY IMPROVED. Removing the hero brings the first real rows substantially higher.
- Filter switching: STATIC PASS. Filtering is local over canonical cached/live rows and does not trigger duplicate fetches.
- Search latency: NOT MEASURED AGAINST PRODUCTION. The client debounces input and cancels stale responses with `loadSequence`.
- Scroll performance: STATIC PASS. `initialNumToRender={10}`, `maxToRenderPerBatch={8}`, `windowSize={7}`, and `removeClippedSubviews` are configured.
- Memory: STATIC PASS. No generated profiles, large media preloads, or extra polling loop were introduced.
- Rerender findings: STATIC PASS. Conversation update listener replaces only the affected conversation row by ID.

## Accessibility

- VoiceOver: PASS STATIC. Header, safety, new chat, search, settings, active rail, quick actions, rows, skeleton, and retry states expose labels/roles.
- Dynamic Type: PARTIAL STATIC. Native `Text` components are used; full large-text device pass still required.
- Increased Contrast: PASS STATIC. Cards use stronger dark glass, cyan borders, muted text, and unread badge contrast.
- Reduce Motion: PASS. No new animation loop was added to the inbox.
- Touch targets: PASS STATIC. Header buttons and gear are 44x44; rows and quick actions remain comfortably tappable.

## Device QA

- Compact: STATIC PASS, DEVICE QA PENDING.
- Standard: STATIC PASS, DEVICE QA PENDING.
- Pro: STATIC PASS, DEVICE QA PENDING.
- Pro Max: STATIC PASS, DEVICE QA PENDING.
- Physical iPhone: PENDING. The implementation is ready for physical-device QA, but this report does not claim physical-device proof.

## Repository

- Implementation commit: `7160bc58f5e8d8990b78c4ecfddfa11e2163769a`
- Branch: `main`
- Remote: `origin`
- Push: confirmed before this report update.

## Verification Commands

- `venv/bin/python scripts/pulsesoc_native_messenger_compact_inbox_audit.py`: PASS
- `venv/bin/python scripts/pulsesoc_native_pulse_command_exact_parity_audit.py`: PASS
- `npm run typecheck` from `mobile-native`: PASS

## Release Judgment

- Visual direction matched: YES.
- Production wired: YES STATIC.
- Ready for internal beta: YES for the compact native inbox.
- Safe to replace WebView Messenger inbox: NO. Physical-device QA, search/send proof, long-list measurement, and full group/room creation flow evidence remain required.
- Remaining blockers:
  - Physical iPhone QA.
  - Real production search latency measurement.
  - Long-list memory/scroll profiling.
  - End-to-end active-contact direct-conversation proof on device.
  - Dedicated native group/room creation screens if product requires one-tap create instead of routing to Groups.
- Next exact action:
  - Run native app on the connected iPhone, open Messenger, verify search, filters, active-contact route, New Chat, Groups/Rooms surface, one real conversation open, and bottom-nav clearance with screenshots.
