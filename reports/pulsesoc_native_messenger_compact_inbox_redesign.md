# PulseSoc Native Messenger Compact Inbox Redesign

Date: 2026-07-18

## Root Causes

- Oversized Messenger layout: the native inbox mounted a `Pulse Command / Messenger V3` lockup above the search/filter stack, delaying the first visible conversation rows.
- Hero-card source: `mobile-native/src/screens/MessengerScreen.tsx` owned the extra header/hero block inside the `FlatList` header.
- Reserved vertical space: the old block carried its own orb, copy, plus action, margins, border treatment, and vertical gap.
- Conversation visibility delay: the list spent vertical budget on repeated branding before showing actionable inbox content.
- Duplicate safe-area or inset behavior: Messenger hides the global tab header, so the inbox needs its own compact header but not a second large product card.
- Global-overlay interference: no `ACTIVE PULSESOC CALL` or `Voice in progress` block is mounted by this inbox screen.

## Implementation

- Files inspected:
  - `mobile-native/src/screens/MessengerScreen.tsx`
  - `mobile-native/src/api/messenger.ts`
  - `mobile-native/src/components/PulseCommand.tsx`
  - `mobile-native/src/components/Screen.tsx`
  - `mobile-native/src/navigation/AppNavigator.tsx`
  - `mobile-native/src/navigation/GlobalNavigation.tsx`
- Files changed:
  - `mobile-native/src/screens/MessengerScreen.tsx`
  - `scripts/pulsesoc_native_pulse_command_exact_parity_audit.py`
  - `scripts/pulsesoc_native_messenger_compact_inbox_audit.py`
  - `reports/pulsesoc_native_messenger_compact_inbox_redesign.md`
- Components reused:
  - `LogiNexusScreenShell`
  - `LogiNexusSignalIndicator`
  - `PulseCommandAvatar`
  - `PulseCommandPanel`
  - `PulseCommandSearch`
  - `PulseCommandSegmentRail`
- Components removed:
  - The large native inbox `Pulse Command / Messenger V3` lockup.
  - Its unused `PulseCommandOrb` and `PulseCommandAction` imports.
- APIs reused:
  - `listConversations`
  - `loadCachedConversations`
  - `searchMessenger`
  - `openDirectConversation`
  - `subscribeConversationUpdates`
- Routes preserved:
  - Conversation rows navigate to `Chat` with canonical conversation IDs.
  - New Chat opens `NewChat`.
  - Group and room actions continue routing to the existing `Groups` surface.
  - Safety opens `SafetyHub`.
  - Settings gear opens the existing Conversation Control Center path for the active/saved conversation.

## Functional Verification

- Search: PASS. Existing production search API remains wired; stale request responses are ignored by `loadSequence`.
- Settings gear: PASS. The gear remains visible beside search and opens Conversation Control Center for the saved or first conversation.
- Direct: PASS. Filter state remains backed by `conversationMatchesFilter`.
- Groups: PASS. Group filter remains backed by conversation type.
- Rooms: PASS. Room filter remains backed by conversation type.
- AI: PASS. AI filter still accepts `ai`, `intelligence`, and `undx`.
- Unread: PASS. Unread filter still checks canonical unread count.
- Active-contact routing: PASS. Existing active conversations open their canonical conversation ID.
- New Chat: PASS. Header action, Add avatar, and quick action all route to `NewChat`.
- Create Group: PASS. Existing quick action route preserved.
- Start Room: PASS. Existing room entry route preserved.
- Conversation routing: PASS. Rows navigate to `Chat` with canonical IDs.
- Presence: PASS. Presence still derives from `isActivePresence`.
- Unread counts: PASS. Unread badge remains row-local and bottom-nav badge logic is untouched.
- Realtime updates: PASS static. `subscribeConversationUpdates` remains mounted and de-duplicates by conversation ID.
- Pagination: NOT CLAIMED. This inbox still uses the existing first-page conversation load contract; long-list pagination remains future work.

## Layout Verification

- Hero removed: YES.
- Reserved hero space removed: YES.
- Header compact: YES. New header is a short row with `Messages`, `Pulse Command`, safety, and compose actions.
- Search-to-filter spacing compliant: YES. Header stack gap is `6`.
- Quick actions compact: YES. Quick-action cards are 56px minimum height with 6px gaps.
- Recent Conversations visible early: YES. The old large card is gone and initial loading uses skeleton rows.
- Last row unobscured: PASS static. Content bottom padding remains above the fixed bottom nav.
- Global call banner absent: YES.
- Invisible overlays absent: YES static. No extra hero, call, or voice popup block is mounted by `MessengerScreen`.

## Performance posture

- Cold load: not benchmarked in this turn.
- Cached load: improved structurally by showing header/skeleton/cached rows instead of a blocking full-screen loading panel.
- Time to first row: improved structurally by removing the hero and reducing gaps.
- Filter switching: unchanged production local filter, no extra fetch.
- Search latency: safer through stale-response cancellation; actual provider latency not benchmarked.
- Scroll performance: improved with `initialNumToRender`, `maxToRenderPerBatch`, `windowSize`, and `removeClippedSubviews`.
- Memory: no new heavy media or background polling introduced.
- Rerender findings: realtime updates still replace only the affected conversation in state.

## Accessibility

- VoiceOver: static labels are present for safety, new chat, search, settings, active rail, and rows.
- Dynamic Type: existing text components remain native text; full large-text simulator QA not run in this turn.
- Increased Contrast: colors were strengthened for muted text, badges, and card borders.
- Reduce Motion: no new animation loop added.
- Touch targets: header actions and gear are 44x44; rows remain at least 64px high.

## Device QA

- Compact: static density follows compact spacing rules; simulator not run in this turn.
- Standard: static density follows standard spacing rules; simulator not run in this turn.
- Pro: static density follows Pro spacing rules; simulator not run in this turn.
- Pro Max: static density follows Pro Max spacing rules; simulator not run in this turn.
- Physical iPhone: not verified in this turn.

## Repository

- Prior commits:
  - `fcaaac30` Tighten PulseSoc Messenger spacing
  - `bd21556d` Refine PulseSoc Messenger color accents
- This report accompanies the completion pass that adds compact header, skeleton loading, search-result rendering, stale request protection, and audit coverage.

## Release Judgment

- Visual direction matched: YES.
- Production wired: YES static.
- Ready for internal beta: YES for the compact inbox layout.
- Safe to replace WebView Messenger inbox: NO. Full physical-device QA, long-list performance measurement, and pagination evidence remain required before replacement.
- Remaining blockers:
  - Physical-device login/search/send QA.
  - Real long-list scroll performance measurement.
  - Group creation and room creation deep-flow screenshots.
  - End-to-end realtime message/update proof on device.
- Next exact action:
  - Run the native app on the connected iPhone, capture the compact inbox, open search, open a conversation, send a message, and capture no-overlap bottom-nav evidence.
