# Performance Assessment

Generated: 2026-05-31

## Confirmed Local Performance

`scripts/performance_audit.py` passed with warnings:
- `/dashboard`: 183 ms, 51 DB operations, warning.
- `/pulse/premium/undx`: 18 ms locally but about 1.88 MB response size, warning.
- `static/js/pulse_live_studio.js`: 2000 ms poll interval, warning.

Representative local Pulse surfaces:
- `/pulse`: 12 ms, 125679 bytes.
- `/pulse/reels`: 11 ms, 75898 bytes.
- `/pulse/messages`: 10 ms, 32510 bytes.
- `/pulse/premium`: 12 ms, 31270 bytes.

## Confirmed Production Smoke Timing

Measured from this environment:
- `/`: about 4269 ms.
- `/pulse` login redirect: about 3360 ms.
- `/pulse/reels` login redirect: about 2930 ms.
- `/pulse/messages` login redirect: about 3594 ms.
- `/pulse/live` login redirect: about 3360 ms.
- `/api/pulse/search?q=pulse`: about 1449 ms to 401.

## Risks

P1:
- Production latency appears materially higher than local. Need APM and Railway cold-start/DB timing.

P2:
- UNDX payload remains too large.

P2:
- Live polling at 2 seconds can become expensive with audience growth.

P3:
- Static asset cache and CDN behavior should be measured through Cloudflare, not only Flask headers.

## Required Next Fixes

1. Add production APM timings by route: app time, DB time, render time, response size.
2. Split UNDX payload into server-side lazy views or separate endpoints.
3. Replace aggressive polling with SSE/WebSocket where appropriate, keeping REST fallback.
4. Add production CDN cache report for static assets and media objects.

