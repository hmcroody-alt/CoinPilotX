# PulseSoc Live Post and Replay Video Flow

Date: 2026-06-09

## Active Live Sessions

PulseSoc already has a first-class service for surfacing active live sessions as feed posts:

- `services/live_feed_service.py`
- `ensure_live_feed_post(...)`
- `mark_live_feed_ended(...)`

When a live session starts, the backend calls `ensure_live_feed_post(...)` and stores the resulting `feed_post_id` on the live session. The feed post uses:

- `post_type='live'`
- `live_session_id`
- `live_status='live'`
- `live_viewer_count`
- `playback_url`
- `preview_url`

This satisfies the product rule that live activity should appear as a normal feed item labeled live instead of a large standalone Home interruption.

## Ending Live Sessions

When the live host ends a stream, `/api/pulse/live/<live_id>/end` updates:

- `pulse_live_sessions.status='ended'`
- `publish_state='ended'`
- `stream_health='ended'`
- `recording_status`
- `recording_error`
- `ended_at`

It then calls `mark_live_feed_ended(...)`, updating the original live feed post to:

- `live_status='archived'` when replay exists
- `live_status='ended'` when no replay exists
- `replay_url`
- updated `body`

## Replay Video Storage

If a valid replay URL or Mux playback ID exists, the end-live path calls `pulse_video_index_upsert(...)` with:

- source type `replay`
- source id = live session id
- title
- creator/user id
- playback URL
- Mux playback id when available
- processing status `ready`

This is the idempotent path that makes live replays appear in Videos and creator video surfaces without creating fake replay entries when no recording exists.

## Current Repair

The disruptive middle Home Live Now block was removed from `/pulse`. The live feed-post and replay-video flow remains intact and is now protected by updated feed/realtime audits.

## Remaining Operational Notes

- Recording status values currently include replay-oriented states such as `replay_ready` and `replay_unavailable`; the reporting layer maps these to ready/unavailable behavior.
- Provider callbacks for durable live recordings should continue to call the same idempotent replay upsert path.
