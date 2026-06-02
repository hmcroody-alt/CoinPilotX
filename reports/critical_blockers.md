# Critical Blockers

Generated: 2026-05-31

## Evidence Sources

- Production public smoke check against `https://coinpilotx.app`.
- Local full platform audit: passed.
- Local performance audit: passed with 3 warnings.
- Local media engine audit: passed foundation checks with worker/ffmpeg warnings.
- Direct SQLite integrity probes against local `coinpilotx.db`.
- Code inspection of media, live, worker, environment, and routing paths.

## P0 - Platform Breaking

### P0-1: Production live reliability cannot be declared verified

Status: blocked.

Evidence:
- Local live audits pass for route contracts, signaling endpoints, audio/video state, replay lifecycle, and viewer fallback.
- Browser/device production QA was not authenticated in this pass.
- `static/js/pulse_live_studio.js` uses public STUN servers only: `stun:stun.l.google.com:19302` and `stun:stun.cloudflare.com:3478`.
- No TURN credential/config path was confirmed in the live client.

Risk:
- Viewers behind restrictive NAT/firewalls may fail to receive audio/video even though signaling routes work.

Required fix:
- Add production TURN support with explicit environment variables, short-lived credentials if possible, and live diagnostics that show ICE candidate type, selected transport, audio bytes, and video bytes.

### P0-2: Production media durability must be verified in Railway/R2

Status: blocked.

Evidence:
- Local media storage provider is `local`.
- Full platform audit confirms production gate requires R2 env vars when running in a production-like environment.
- Production credentials and bucket contents cannot be verified from this local environment.

Risk:
- Local media success does not prove R2 object persistence, CDN availability, content-type correctness, or replay durability.

Required fix:
- Run `scripts/r2_upload_smoke_test.py`, `scripts/phase2_media_cdn_audit.py`, and media playback QA in Railway with production R2 variables loaded.

## P1 - Major Degradation

### P1-1: Media worker heartbeat is stale in local database

Status: confirmed.

Evidence:
- `scripts/media_engine_audit.py` reported latest media heartbeat age around 731,990 seconds.

Risk:
- If this mirrors production, media processing, moderation, repair, thumbnails, and replay work may stall.

Required fix:
- Verify Railway worker service is running and connected to the same production database as the web service.

### P1-2: ffmpeg is missing locally

Status: confirmed locally.

Evidence:
- `scripts/media_engine_audit.py` reported `ffmpeg available: False`.
- `nixpacks.toml` includes ffmpeg, so this is expected to be solved in Railway but not proven here.

Risk:
- Local video/transcoding QA cannot prove production transcoding.

Required fix:
- Confirm Railway build has ffmpeg and run a production video upload/transcode/playback smoke test.

### P1-3: Local database contains active Pulse posts with `user_id=0`

Status: confirmed locally.

Evidence:
- Direct local query found 41 active `pulse_posts` rows where `user_id` is null or 0.
- Samples include audit/admin/system-seeded style posts.

Risk:
- Authorless records weaken relationship integrity and can confuse profile/feed attribution if such rows exist in production.

Required fix:
- Add an explicit system user or system actor model, backfill system-generated posts, and enforce nonzero user IDs for user-authored posts.

## P2 - Optimization

### P2-1: UNDX page payload is too large

Status: confirmed locally.

Evidence:
- `scripts/performance_audit.py` warns `/pulse/premium/undx` response size is about 1.88 MB.

Risk:
- Slow mobile rendering and large memory pressure.

Required fix:
- Continue splitting UNDX into truly lazy-loaded route/view payloads.

### P2-2: Live Studio polls every 2000 ms

Status: confirmed locally.

Evidence:
- `scripts/performance_audit.py` warns `static/js/pulse_live_studio.js` contains a 2000 ms poll interval.

Risk:
- More server load under many viewers.

Required fix:
- Prefer SSE/WebSocket updates with adaptive polling fallback.

## P3 - Future Scaling

### P3-1: Public production routes have multi-second response times in smoke check

Status: observed.

Evidence:
- Public production smoke check measured `/` at about 4269 ms and protected redirects around 2930-3594 ms from this environment.

Risk:
- Growth-stage latency if reproduced near target users.

Required fix:
- Run production APM, CDN cache analysis, cold-start analysis, and regional latency testing.

