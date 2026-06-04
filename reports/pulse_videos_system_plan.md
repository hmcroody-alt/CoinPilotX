# Pulse Videos System Plan

## Implemented Foundation

- `/pulse/videos` is the dedicated home for regular videos, current Live sessions, and replays.
- `pulse_videos` indexes playback metadata without duplicating original files.
- `pulse_video_views` records viewing activity and `pulse_video_categories` provides future categorization.
- Regular feed videos are indexed as `feed_video`.
- Reels retain their compatibility post for reactions/comments, but default to `reel_only` visibility and no longer appear in the regular feed.
- Statuses remain isolated from Feed, Reels, and Videos.

## Routing Contract

| Content | Feed | Videos | Reels | Status |
|---|---:|---:|---:|---:|
| Text | Yes | No | No | No |
| Photo | Yes | No | No | No |
| Regular video | Yes | Yes | No | No |
| Reel | No by default | No | Yes | No |
| Status | No | No | No | Yes |
| Live / replay | Discovery only | Yes | No | No |

## Next Safe Expansion

- Add direct upload resume support using Mux resumable uploads.
- Backfill old regular video posts in a bounded maintenance job.
- Add video category management and creator-side edit/archive controls.
