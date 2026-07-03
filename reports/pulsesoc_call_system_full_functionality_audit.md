# PulseSoc Call System Full Functionality Audit

## What Was Hardened

- Incoming-call recipient acknowledgement now exists through `POST /api/calls/<call_id>/ring-seen`.
- The Messenger call overlay calls `ring-seen` once when an incoming call is actually shown to the callee.
- Admin delivery diagnostics now show whether the recipient was tracked online and whether the incoming overlay opened.
- Active-call polling now wakes on `visibilitychange`, `focus`, `pageshow`, and `online`, in addition to normal polling and realtime events.
- Stale ringing calls continue to be marked missed through status and active-call APIs.

## Call Chain Now Covered

```text
caller starts call
→ backend validates conversation membership
→ callee participant row is created
→ LiveKit token readiness is checked
→ incoming_call notification is created through the central notification system
→ push/call delivery jobs are created when eligible
→ realtime incoming_call events are published
→ callee browser polls/listens for active calls
→ incoming overlay opens
→ callee browser records ring-seen
→ admin diagnostics can show whether the phone/browser actually rang
```

## Admin Diagnostics Added

For each call, `/admin/calls/<call_id>/delivery` and `/api/admin/calls/<call_id>/delivery` now expose:

- `incoming_notification_created`
- `push_job_created`
- `call_job_created`
- `recipient_push_token_exists`
- `recipient_online`
- `recipient_overlay_opened`
- `realtime_event_emitted`
- `realtime_event_failed`
- `media_tracks_published = provider_event_required`

The final media-track truth still depends on LiveKit room events or live browser QA, not a fake local flag.

## Remaining Real-World Requirements

These cannot be honestly proven from code alone:

- Two authenticated test users on two devices.
- Browser or PWA microphone/camera permission allowed on both devices.
- Production deployment serving the latest pushed JS and routes.
- LiveKit credentials configured and able to create rooms.
- Recipient has an active browser/PWA session for in-app overlay, or valid push subscription/device token for locked-screen notification.
- Native CallKit-style locked-screen ringing requires native app integration; current web/PWA path can show push notifications where the platform allows it.

## QA Result

Static and local verification should confirm the code path is fully wired. Real production readiness still requires a two-device call:

1. User A opens Messenger conversation with User B.
2. User A taps audio call.
3. User B sees incoming overlay while app is open.
4. Admin call delivery shows `recipient_overlay_opened: true`.
5. User B accepts.
6. Both devices join the same LiveKit room and exchange audio.
7. Repeat for video.
8. Leave User B unanswered for 45 seconds and confirm missed-call status/notification.

## Known Blockers To Report If QA Fails

- `LIVEKIT_URL`, `LIVEKIT_API_KEY`, or `LIVEKIT_API_SECRET` missing or invalid.
- LiveKit room create/delete diagnostic fails.
- Recipient has no push subscription/device token.
- Recipient push preference, muted conversation, or block rule suppresses delivery.
- Browser blocks microphone/camera permission.
- Production cache serves old `static/pulsesoc_calls.js`.
- Native locked-screen ringing is required beyond web/PWA push; that needs native CallKit/APNs integration.
