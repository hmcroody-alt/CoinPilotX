# Conversation Control Center V2 Wiring

## Summary

The Conversation Control Center V2 keeps the approved visual shell and removes the V1 pattern of visible placeholder controls. Controls now either call real Messenger APIs, persist real settings, update the current UI, or are hidden until a real dependency exists.

## Placeholders Found

- Visible call/video controls were present while the backend route returned a non-working phase placeholder.
- Control sections contained `Coming Soon`, `Requires Setup`, and `Unavailable` states for pinned messages, export, wallpaper, AI extras, group admin tools, storage links, destructive actions, and more.
- Direct conversation status could render `Unknown`.
- Conversation settings persisted, but several toggles did not affect behavior.

## Wired Now

- Search opens the real conversation search.
- Mute saves participant mute state and supports duration choices.
- Pin/unpin, archive, and mark unread use existing authenticated routes.
- Members fetch the real participant list.
- Shared media/files/largest files fetch real attachment rows.
- Shared links are extracted from real messages.
- Pinned messages read the existing message pin metadata.
- Message stats use real message, media, link, unread, and storage counts.
- Export Chat downloads a participant-safe JSON transcript.
- Notification lock-screen and preview settings now affect message push policy.
- Privacy read receipts and typing indicator settings now affect server behavior.
- Online/last-seen visibility updates the user presence privacy setting.
- Theme, wallpaper, bubble color, font size, density, reduced motion, high contrast, large text, and haptics apply to the live UI and persist.
- Clear cache clears local cache only.
- Report conversation creates moderation records.
- Block user uses the real block table for direct chats.
- Clear conversation, delete conversation, leave group, delete media, and reset settings use participant-safe server actions with confirmations.
- Create Note and Create Task write real conversation item records.

## Removed / Hidden Until Ready

- Messenger call/video controls are hidden because there is no real Messenger call provider behind `/voice/start` or `/video/start`.
- Extra AI actions beyond summary and smart replies are hidden because the current AI client only exposes those two chat-safe actions.
- Group admin controls beyond member viewing are hidden until role/admin mutation APIs exist.
- Disappearing messages, browser biometric privacy lock, hidden conversation recovery, scheduled send, and true contact fingerprint verification are hidden until the backend/native support exists.
- Voice reader, speech-to-text, and text-to-speech settings were hidden because there is no selected-message reader/dictation flow wired to the conversation yet.

## Backend Routes Added

- `GET /api/pulse/communications/v2/conversations/<conversation_ref>/control-center/media`
- `GET /api/pulse/communications/v2/conversations/<conversation_ref>/control-center/links`
- `GET /api/pulse/communications/v2/conversations/<conversation_ref>/control-center/pins`
- `GET /api/pulse/communications/v2/conversations/<conversation_ref>/control-center/export`
- `POST /api/pulse/communications/v2/conversations/<conversation_ref>/control-center/action`

## Database Changes

- Added `comm_v2_conversation_items` for conversation notes/tasks.

## Security Checks

- Every new route requires an authenticated user.
- Every new route validates the user is an active participant through `_conversation_access`.
- Report/block/delete/clear/reset actions only affect the current participant where appropriate.
- Export returns only messages the current participant can view.
- The UI does not claim true end-to-end encryption; it says protected channel/session.

## External Blockers

- Messenger audio/video calling needs a real call provider and call session model.
- True E2EE/contact fingerprint verification needs a cryptographic key architecture.
- Biometric privacy lock requires native shell support or a re-auth/PIN route.
- Scheduled send needs a scheduler/worker to deliver future messages safely.
- Full speech reader/dictation needs a selected-message UX plus browser/native capability handling.

## QA Notes

- Static audit updated for V2 wiring.
- Browser QA passed at 390x844 mobile and 1280x900 desktop.
- The inbox gear opened the Conversation Control Center from `/pulse/messages`.
- No visible Control Center `Coming Soon`, `Requires Setup`, `Unavailable`, `Unknown`, `Not implemented`, or internal architecture labels appeared in the panel.
- Mobile and desktop checks showed no horizontal page or panel overflow.
- Settings search filtered to Privacy correctly.
- Members detail panel loaded real participant data.
- High Contrast toggle updated the live Messenger shell class, saved through the settings path, and restored cleanly.
- No browser console errors were reported during the QA pass.
