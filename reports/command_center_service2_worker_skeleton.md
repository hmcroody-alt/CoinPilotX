# PulseSoc Command Center Worker Skeleton

Date: 2026-06-19

## Purpose

Service 2A creates the foundation for a dedicated PulseSoc Command Center Worker. The worker is designed to later own heavy background and realtime responsibilities while the current Flask main app remains the user-facing web/API service.

## Current Scope

This phase only adds:

- Worker Flask entrypoint.
- Worker configuration loader.
- Health endpoint.
- Internal bearer-token protection.
- Database connectivity check.
- Optional Redis connectivity check.
- Heartbeat logging.
- Protected test event receipt endpoint.
- Local audit script.
- Railway deployment plan.

No messaging, notification, presence, AI, media, security, or realtime behavior has been moved.

## Worker Files

- `services/command_center_worker/__init__.py`
- `services/command_center_worker/app.py`
- `services/command_center_worker/config.py`
- `services/command_center_worker/health.py`
- `services/command_center_worker/security.py`
- `services/command_center_worker/heartbeat.py`

## Endpoints

- `GET /internal/command-center/health`
  - Returns service identity, enabled state, database status, Redis status, heartbeat interval, auth configured yes/no, timestamp, and version.
  - Does not expose tokens, database URLs, Redis URLs, filesystem paths, or secrets.

- `POST /internal/command-center/events/test`
  - Requires `Authorization: Bearer <COMMAND_CENTER_INTERNAL_TOKEN>`.
  - Accepts a test event payload.
  - Returns `{ "accepted": true, "event_id": "...", "status": "received" }`.
  - Stores only a bounded in-memory event receipt for local verification.

## Environment Variables

- `PULSESOC_SERVICE_NAME=command-center-worker`
- `PULSESOC_SERVICE_ROLE=worker`
- `COMMAND_CENTER_WORKER_ENABLED=true`
- `COMMAND_CENTER_INTERNAL_TOKEN=<strong internal secret>`
- `DATABASE_URL=<Railway Postgres URL>`
- `REDIS_URL=<optional now, required later>`
- `COMMAND_CENTER_HEARTBEAT_SECONDS=30`
- `RELEASE_VERSION=<optional release identifier>`

## What Remains In Main App

- Web routes and mobile/web APIs.
- Authentication, sessions, CSRF, account state, and permissions.
- User profiles.
- Feed routes and rendering.
- Post, reel, video, status, and live UI routes.
- Admin pages.
- Current messaging, notification, email, upload, media, AI, and security side effects.
- Disabled-by-default Command Center dispatch wrapper from Service 1.

## What Will Move Later

- Realtime message delivery and websocket fanout.
- Presence and online/offline state processing.
- Typing indicators and unread counter fanout.
- Push notification retries and provider receipt handling.
- Email/SMS notification dispatch queues.
- Security alert aggregation and escalation.
- AI chat summaries and moderation/scam analysis.
- Media processing dispatch and attachment metadata jobs.
- Voice/video call signaling.

## Railway Deployment Plan

Do not create the Railway service until explicitly approved.

Recommended service name:

`PulseSoc Command Center Worker`

Start command:

`gunicorn services.command_center_worker.app:app --bind 0.0.0.0:$PORT`

Required variables:

- `PULSESOC_SERVICE_NAME=command-center-worker`
- `PULSESOC_SERVICE_ROLE=worker`
- `COMMAND_CENTER_WORKER_ENABLED=true`
- `COMMAND_CENTER_INTERNAL_TOKEN=<generate strong secret>`
- `DATABASE_URL=<same Railway Postgres URL>`
- `REDIS_URL=<optional now, required later>`
- `COMMAND_CENTER_HEARTBEAT_SECONDS=30`

After the service is deployed and verified, the main app can later be configured with:

- `COMMAND_CENTER_ENABLED=true`
- `COMMAND_CENTER_INTERNAL_URL=<private worker URL>`
- `COMMAND_CENTER_INTERNAL_TOKEN=<same strong secret>`

Do not enable these main-app dispatch variables until a specific migration phase verifies the endpoint contract and fallback behavior.

## Security Notes

- Protected worker endpoints require a bearer token.
- If the token is missing, protected operations refuse requests.
- Health output only reports whether internal auth is configured; it never returns the token value.
- Database and Redis URLs are never returned.
- Redis is optional in this skeleton and reports `redis_ok: null` when not configured.
- Test events are stored only in bounded memory and are not a production queue.
- No user-facing workflow depends on this worker yet.

## Local Commands

Compile:

`python -m py_compile services/command_center_worker/app.py services/command_center_worker/config.py services/command_center_worker/health.py services/command_center_worker/security.py services/command_center_worker/heartbeat.py scripts/command_center_service2_worker_audit.py`

Audit:

`python scripts/command_center_service2_worker_audit.py`

Run locally:

`COMMAND_CENTER_INTERNAL_TOKEN=local-dev-token PORT=8081 python -m services.command_center_worker.app`
