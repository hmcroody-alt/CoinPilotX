# PulseSoc Conversation Control Center

## What Was Built

PulseSoc Messenger V3 now has a real Conversation Control Center opened from both the inbox search-bar gear and the gear button in the chat header. On a fresh inbox, the panel first shows the user's real conversations so they can choose which chat to manage without an ambiguous default. Desktop opens a right-side slide panel. Mobile opens a safe-area-aware bottom sheet. The panel uses real conversation data, member counts, unread counts, media counts, storage totals, participant role, mute/pin state, and persisted per-user conversation settings.

## Files Changed

- `pulse_communications_v2/models.py`
- `pulse_communications_v2/service.py`
- `pulse_communications_v2/routes.py`
- `templates/pulse_messages_v2.html`
- `static/js/pulse_messages_v2.js`
- `static/css/pulse_messages_v2.css`
- `scripts/conversation_control_center_audit.py`
- `reports/conversation_control_center.md`

## Routes Added

- `GET /api/pulse/communications/v2/conversations/<conversation_ref>/control-center`
- `PATCH /api/pulse/communications/v2/conversations/<conversation_ref>/control-center`

Both routes require an authenticated user and validate that the user is an active participant in the conversation before reading or saving settings.

## Settings Wired

- Notification mute duration, sound choice, lock-screen preference, preview preference, mentions, reactions, typing alerts, read receipt alerts.
- Appearance theme, bubble color, font size, density, animation level, particle reduction, high contrast.
- Privacy read receipts, typing indicator, online status, last seen, message preview, hidden conversation.
- Media auto-download preferences and upload quality.
- Productivity favorite/reminder preferences.
- Accessibility large text, reduced motion, high contrast, voice reader, speech-to-text, text-to-speech, haptics.

Quick actions reuse existing Messenger V3 behavior for search, mute, pin, archive, mark unread, report, and block.

## Coming Soon / Unavailable

The UI explicitly marks unsupported controls as Coming Soon, Requires Setup, or Unavailable. These include call/video, pinned message browser, export chat, wallpaper picker, disappearing messages, biometric privacy lock, most AI tools if AI is disabled, group administration tools, largest-file analysis, and destructive clear/delete flows.

## Security Checks

- Settings are stored per `conversation_id` and `user_id`.
- Users cannot read or modify settings for conversations they are not part of.
- Group-only controls are hidden for direct conversations.
- Admin-only group controls are gated by participant role.
- The UI does not claim true end-to-end encryption; it uses protected/session language.
- Block and report actions reuse existing moderation/block endpoints.
- Destructive or sensitive actions require confirmation where wired.
- No internal architecture names are exposed in the UI.

## Mobile QA Result

Passed in QA browser at 390x844 on `http://127.0.0.1:5077/pulse/messages`: the inbox search-bar gear opened the bottom sheet directly, the sheet presented real conversations, selecting `Roody Cherie` loaded the full Control Center, the sheet measured 390px in a 390px viewport, there was no horizontal overflow, and no console errors were reported. The existing in-thread gear remains available.

## Desktop QA Result

Passed in QA browser at 1280x900 on `http://127.0.0.1:5077/pulse/messages`: the inbox gear opened the chooser, selecting `Roody Cherie` loaded a 435px right slide panel with a 12px edge offset, and both the page and panel content had no horizontal overflow or console errors. Existing thread-level checks still cover Escape close, settings search, persisted notification settings, and group gating.

Group gating was also verified on `V2 Public Audit Room`: direct chat hid Group Settings, and the room conversation showed Group Settings with unavailable/coming-soon states for controls that are not production wired.

## Known Limitations

Call/video, export, advanced group roles, disappearing messages, and destructive conversation deletion remain safely unavailable because production-ready backends for those controls are not present in Messenger V3 yet.

## Next Steps

Connect advanced group administration, media-library search, export jobs, and biometric/privacy lock once those backends are implemented.
