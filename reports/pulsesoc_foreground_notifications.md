# PulseSoc Non-Intrusive Foreground Notifications

## What Changed

PulseSoc now treats eligible Intelligence push notifications differently when the app is already active in the foreground.

- Foreground PulseSoc clients receive a service-worker message instead of a persistent system banner.
- The page shows a GPU-accelerated in-app PulseSoc banner with sound and vibration cues.
- Background and locked-screen behavior still uses the normal service-worker `showNotification` path.
- Multiple foreground alerts are queued so only one banner appears at a time.
- Critical alerts require acknowledgement instead of auto-hiding.

## Foreground Behavior

When an `intelligence_pulse` arrives and a same-origin PulseSoc window is visible or focused:

1. The service worker builds the same normalized push payload used for lock-screen delivery.
2. The service worker posts `PULSESOC_FOREGROUND_NOTIFICATION` to visible app clients.
3. `static/notifications.js` queues the alert and renders one in-app banner.
4. Normal alerts auto-dismiss after 3 to 5 seconds.
5. Critical alerts stay visible until acknowledged.

## Background and Locked-Screen Behavior

When PulseSoc is backgrounded, closed, or the phone is locked, the service worker still calls `self.registration.showNotification(...)`.

This preserves:

- lock-screen notification delivery
- Notification Center delivery
- sound and vibration payloads
- deep links into `/pulse/alerts`
- existing message/comment/reaction push behavior

## Context Rules

- Reels, Live, Stories, video pages, fullscreen video, or active visible video receive a compact top banner.
- Messenger receives the same top-safe banner without touching the composer or typing area.
- Standard PulseSoc pages receive the richer in-app banner.
- No page reload or layout shift is introduced.

## Files Changed

- `static/service-worker.js`
- `static/sw.js`
- `static/notifications.js`
- `scripts/pulsesoc_foreground_notifications_audit.py`
- `reports/pulsesoc_foreground_notifications.md`

## QA Results

- Syntax checks passed for the active service worker, legacy service worker, and notification controller.
- The foreground notification audit passed.
- The local `/health` endpoint returned healthy.

Manual device QA still needed:

- locked iPhone notification display
- background app notification display
- Reels/live compact banner over active playback
- queue of five real push alerts
- critical security acknowledgement flow
