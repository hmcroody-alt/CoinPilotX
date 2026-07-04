# PulseSoc Messenger Full-Screen Media Viewer

## What Was Broken

Messenger image attachments rendered inside the chat thread, but tapping an image did not open a proper full-screen media experience. Older behavior could fall back to inline media or raw attachment links instead of keeping the user inside Messenger.

## Implementation

- Added `static/js/pulse_messenger_media_viewer.js` as a reusable full-screen image viewer.
- Added `static/css/pulse_messenger_media_viewer.css` for the dark glass, safe-area-aware viewer surface.
- Wired `templates/pulse_messages_v2.html` to load the viewer assets before the Messenger runtime.
- Updated `static/js/pulse_messages_v2.js` so image attachments render as `button` triggers, not raw image links.
- Added gallery normalization for both `attachments[]` payloads and legacy single `media_url` messages.
- Hardened `pulse_communications_v2/service.py` so `list_messages()` still returns message/media payloads if read-receipt or last-read writes are temporarily blocked by a database lock. Read-state updates are deferred instead of crashing the thread load.
- Delegated actions back to existing Messenger behavior:
  - Reply uses the existing reply composer state.
  - React uses the existing message reaction route.
  - Forward uses the existing message forward flow.
  - Report uses the existing protected message report route.

## Security Model

- The viewer does not create a public media route.
- Current Comm V2 attachments use existing authenticated media payloads and protected `/api/messages/media/<id>/download` where available.
- Message actions remain permission-protected by existing Comm V2 routes.
- Share fallback copies a conversation/message deep link, not the private media URL.
- Report action logs against the message ID through the existing moderation path.

## User Experience

- Tap/click an image in Messenger to open the full-screen viewer.
- Escape closes on desktop.
- Left/right arrows navigate media.
- On-screen previous/next buttons navigate the conversation gallery.
- Double tap/click zooms and quick-reacts.
- Pinch/scroll zoom is supported where the browser allows it.
- Swipe down closes on mobile when the image is not zoomed.
- Closing the viewer restores the same chat scroll position.

## Mobile QA

- Static checks verify mobile safe-area CSS and touch/zoom handlers.
- Direct service QA verified conversation `235` returns two messages with one image attachment and one voice attachment after the read-state fail-soft fix.
- In-app browser QA verified the viewer JS/CSS load and the full-screen overlay is present. The local browser did not render the real image message before the automation timeout because the thread loader was competing with local SQLite lock/notification preference errors; this was documented rather than marked as a full visual pass.
- Manual two-user mobile media QA is still required on the normal running app with a fresh image message.

## Desktop QA

- Static checks verify keyboard close/navigation, gallery controls, and no raw image navigation.
- Browser asset QA verified the Messenger page loads `pulse_messenger_media_viewer.js` and `pulse_messenger_media_viewer.css`.
- Direct backend QA verified the real image attachment payload is present for conversation `235`; click-to-open visual QA should be repeated after the local SQLite lock noise is cleared.

## Known Limitations

- Phase 1 focuses on images.
- Video, GIF, audio, and document-specific full-screen modes are prepared by the gallery structure but not fully redesigned in this pass.
- Native share support depends on browser/device support; fallback copies a safe conversation link.
- Local QA showed unrelated `/api/notification-preferences` and presence heartbeat 500s on the temporary `5074` server. They did not come from the media viewer files, but they made the browser automation noisy.
