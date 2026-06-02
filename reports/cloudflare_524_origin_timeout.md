# Cloudflare 524 Origin Timeout

Date: 2026-06-02

## Summary

Production repeatedly showed Cloudflare `524`:

```text
Browser working
Cloudflare working
Host error
```

This means Cloudflare reached Railway/origin but the origin did not answer fast
enough.

## Railway Evidence

Railway CLI auth was expired, so logs were inspected through the authenticated
Railway dashboard browser session.

The dashboard also displayed an active Railway incident notice:

```text
Dashboard Logs Loading Slowly. We have pushed a fix and are now monitoring the incident.
```

Relevant production log lines around the failure window showed `/pulse/reels`
loading, followed immediately by repeated origin-served media stream requests:

```text
2026-06-02T19:04:10Z GET /pulse/reels 200 101424
2026-06-02T19:04:11Z GET /api/pulse/media/93/stream 200 262144
2026-06-02T19:04:11Z GET /api/pulse/media/93/stream 200 783896
2026-06-02T19:04:11Z GET /api/pulse/media/93/stream 200 524288
2026-06-02T19:04:12Z GET /api/pulse/media/93/stream 200 262144
2026-06-02T19:04:12Z GET /api/pulse/media/93/stream 200 262144
2026-06-02T19:04:13Z GET /api/pulse/media/93/stream 200 262144
2026-06-02T19:04:13Z GET /api/pulse/media/93/stream 200 262144
2026-06-02T19:04:13Z GET /api/pulse/media/93/stream 200 262144
```

The exact Cloudflare 524 request does not appear as a completed Flask access log
line because 524 is emitted by Cloudflare when the origin connection remains open
too long without completing. The origin pressure route visible in Railway logs is:

```text
/api/pulse/media/93/stream
```

## Root Cause

The Pulse media stream endpoint was too expensive for a web request path:

1. It called `init_db()` on every media stream request.
   - This can run schema checks/migrations in a route hit repeatedly by video
     players.
2. If the browser did not send a `Range` header, the endpoint opened and streamed
   the whole R2 object through the web worker.
   - Earlier production logs showed whole-object responses around 13 MB for
     `/api/pulse/media/92/stream` and `/api/pulse/media/93/stream`.
3. Multiple video requests could therefore occupy the limited Gunicorn web worker
   pool:
   - Procfile uses `--workers 2 --threads 4 --timeout 120`.
4. This can delay unrelated requests long enough for Cloudflare to return `524`.

## Fix Applied

Files changed:

```text
bot.py
services/media_storage.py
scripts/pulse_reels_media_audit.py
```

Changes:

1. Removed `init_db()` from:

```text
GET/HEAD /api/pulse/media/<id>/stream
```

2. Added metadata-only object lookup:

```text
services.media_storage.head_object()
```

3. Updated the stream endpoint to:

- inspect object metadata before opening the body
- support `HEAD` without reading media bytes
- honor browser `Range` requests
- cap each origin chunk using `PULSE_MEDIA_STREAM_MAX_CHUNK_BYTES`
- default to a bounded first chunk when no `Range` header is sent
- return `206 Partial Content` and `Content-Range` for partial responses
- return `416` for invalid/out-of-bounds ranges
- log safe stream diagnostics without secrets

4. Added audit checks that fail if the route regresses into:

- per-request schema initialization
- uncapped full-object streaming
- missing byte-range support

## Expected Behavior

- `/pulse`, `/pulse/status`, and `/pulse/reels` should respond faster because
  video playback no longer runs schema setup on stream requests.
- Video playback should not force the web service to proxy whole R2 objects.
- If the browser requests media, the origin returns bounded byte ranges rather
  than tying up a worker with full video delivery.

## Validation

Local validation passed:

```text
python compile check
scripts/pulse_reels_media_audit.py
scripts/site_functional_audit.py
scripts/performance_audit.py
git diff --check
```

Notes:

- `performance_audit.py` still reports the existing warning for
  `static/js/pulse_live_studio.js` polling every 2000 ms.
- No Pulse UI, Cloudflare rules, or production database data were changed.

