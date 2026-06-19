# PulseSoc Service 1 Main App Readiness Report

Date: 2026-06-19

## Scope

This change prepares the current PulseSoc Flask/web application to remain the main user-facing service while future Command Center worker services are introduced. No background job ownership has moved yet.

## Current Files And Ownership

- `bot.py`: Web routes, mobile/web APIs, authentication, sessions, admin pages, feed routes, messaging routes, notification routes, upload routes, live routes, status/reel/video UI routes, and several current side effects.
- `services/notification_service.py`: Push notification policy and provider handoff used by current message and notification flows.
- `services/notification_orchestrator.py`: Notification provider health and orchestration helpers.
- `services/push_service.py`: Push token and push delivery helpers.
- `services/chat_realtime_service.py`: Chat realtime metadata and conversation health helpers.
- `services/pulse_feed_engine.py`: Feed creation, ranking helpers, and local background job records.
- `services/media_service.py`: Media upload validation, persistence, and metadata helpers.
- `services/email_service.py`: Transactional email provider handoff.
- `services/alert_engine.py` and `services/alert_service.py`: Alert rules, alert evaluation, and alert delivery helpers.
- `services/live_*`: Live stream routing, presence, discovery, archive, and health helpers.

## Current Routes

- Messaging UI and APIs: `/messages`, `/pulse/messages`, `/api/messages/*`, `/api/pulse/messages/*`.
- Notifications: `/pulse/notifications`, `/api/pulse/notifications/*`, provider-facing push helpers.
- Feed and creator surfaces: `/pulse`, `/pulse/create`, `/api/pulse/posts/*`, `/api/pulse/feed/*`.
- Reels and videos: `/pulse/reels`, `/pulse/videos`, related upload and playback APIs.
- Statuses: `/pulse/status`, `/api/pulse/status/*`.
- Live: `/pulse/live`, `/pulse/live/studio/<id>`, `/api/pulse/live/*`.
- Admin: `/admin/system`, `/admin/security`, `/admin/*health*`.
- New Service 1 health endpoint: `/api/service/health`.

## Current Background-Like Tasks

- Realtime message side effects and chat event emission.
- Push notification creation and provider dispatch.
- Email sends through Brevo/SMTP helpers.
- Feed background job row creation in `pulse_feed_engine`.
- Scam/security/login event recording and admin alerting.
- Media upload processing and metadata persistence.
- Live presence, live ranking, and stream health checks.
- AI summaries, AI assist, moderation, scam scanning, and creator intelligence helpers.

## Should Remain In Main App

- User-facing web routes and mobile/web APIs.
- Authentication, sessions, CSRF, permissions, and account state checks.
- User profiles, feed read APIs, post/reel/video/status page rendering.
- Admin pages and read-only diagnostics.
- Upload entry points and current synchronous validation.
- Safe dispatch envelope creation for future worker jobs.

## Should Later Move To Command Center Worker

- Realtime message delivery retries and websocket fanout.
- Notification retry queues and provider receipt processing.
- AI summaries, AI assist, moderation summaries, and scam scanning.
- Attachment transcoding, media processing, and long-running metadata extraction.
- Presence tracking and online/offline fanout.
- Queue processing, scheduled jobs, and delayed retries.
- Security alert aggregation and high-volume anomaly analysis.

## Service 1 Readiness Added

- `services/command_center_client.py` provides a disabled-by-default internal dispatch wrapper.
- `/api/service/health` reports service identity, database status, Command Center readiness, timestamp, and commit/version without exposing secrets.
- `/admin/system` shows Main App status, Command Center enabled/disabled state, URL/token configured yes/no, and last dispatch test status without exposing endpoint or token values.
- `scripts/command_center_service1_audit.py` validates compile/import/health/disabled-dispatch/optional-env/no-secret behavior.

## Remaining Risks

- Current side effects still execute inside the main Flask app until the worker is built and specific call sites are migrated.
- Production queue and retry semantics still need dedicated worker design before heavy tasks are moved.
- Real Command Center endpoint contracts are intentionally stubbed and must be versioned before enabling dispatch in production.
