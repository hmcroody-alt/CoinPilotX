# Pulse Web Push Foundation

Status: implemented.

Implemented:
- Push public key endpoint remains available.
- Browser subscription is saved through `/api/push/subscribe`.
- Subscriptions are mirrored into `pulse_notification_devices`.
- Unsubscribe remains available.
- Service workers handle background push and notification click deep links.
- Push permission is only requested when the user clicks the enable push action.

Deep link behavior:
- Default push click route is `/pulse/notifications`.
- Notification payload data can override the URL.

Production requirements:
- `VAPID_PUBLIC_KEY`
- `VAPID_PRIVATE_KEY`
- Optional `VAPID_SUBJECT`, recommended as `mailto:support@pulsesoc.com`
- `pywebpush` installed in the runtime
