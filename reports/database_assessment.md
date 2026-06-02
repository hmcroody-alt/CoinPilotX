# Database Assessment

Generated: 2026-05-31

## Confirmed Local Schema

- `init_db()` completes.
- Local database has 418 tables.
- Local database has 397 indexes.
- Required Pulse table/column/index audit passed.
- Key tables exist:
  - `users`
  - `pulse_posts`
  - `chat_media_uploads`
  - `pulse_messages`
  - `pulse_reels`
  - `pulse_live_sessions`
  - `pulse_jobs`
  - `worker_heartbeats`

## Direct Integrity Probe

Local row counts:
- `users`: 786
- `pulse_posts`: 806
- `chat_media_uploads`: 980
- `pulse_messages`: 1421
- `pulse_reels`: 54
- `pulse_live_sessions`: 467
- `pulse_jobs`: 1621
- `worker_heartbeats`: 2

Local integrity checks:
- Media rows with raw `/Users` URL: 0
- Unavailable media rows: 0
- Failed media jobs: 0
- Messages without `sender_user_id`: 0
- Messages without receiver/conversation/thread: 0
- Live sessions without owner: 0
- Active posts without author: 41

## Risks

P1:
- Active `pulse_posts` with `user_id=0` should be formalized as system posts or backfilled to a system user.

P2:
- The platform schema is large and still mostly initialized through application code. Future production scale should move toward explicit migrations and migration verification.

P3:
- Add regular orphan scans for media, messages, live sessions, notifications, and marketplace records.

## Required Next Fix

Create a system actor model and enforce:
- user-authored content must have nonzero `user_id`.
- system-authored content must use a real system user or `actor_type='system'`.

