# Pulse Notifications Backend

Status: implemented as a production-ready web/in-app foundation.

Implemented:
- Expanded `pulse_notifications` with actor, entity, deep link, read time, delivery status, and metadata fields.
- Added `pulse_notification_preferences`, `pulse_notification_devices`, and `pulse_notification_deliveries`.
- Added Pulse APIs for list, unread count, mark read, mark all read, delete, and preferences.
- Preserved existing legacy notification services and direct inserts for compatibility.

Primary routes:
- `GET /api/pulse/notifications`
- `GET /api/pulse/notifications/unread-count`
- `POST /api/pulse/notifications/<id>/read`
- `POST /api/pulse/notifications/read-all`
- `DELETE /api/pulse/notifications/<id>`
- `GET /api/pulse/notifications/preferences`
- `PATCH /api/pulse/notifications/preferences`

Notes:
- Existing notification producers can keep inserting into `pulse_notifications`.
- New producers should use `notification_service.create_pulse_notification(...)` for deep links and delivery logging.
