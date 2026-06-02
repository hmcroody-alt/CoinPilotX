# Cloudflare Pulse Upload Fix

Date: 2026-06-02

## Root Cause

Production multipart uploads to `POST /api/pulse/media/upload` were blocked at the
Cloudflare edge before Flask or Railway received the request.

Original blocked request evidence:

- URL: `https://coinpilotx.app/api/pulse/media/upload`
- Method: `POST`
- Content type: `multipart/form-data`
- HTTP response: `403`
- Response server: `cloudflare`
- Response content type: `text/html`
- Cloudflare Ray ID: `a052e6014cedb5d9-ATL`
- Response page: `Attention Required! | Cloudflare`
- Railway route evidence: no `PULSE_MEDIA_UPLOAD_ROUTE_HIT` entry

Cloudflare Security Events showed the upload path as a managed-rule hotspot and
identified OWASP managed-rule anomaly enforcement:

- Managed rule: `949110: Inbound Anomaly Score Exceeded`
- Service: managed rules

After skipping managed rules only, a second edge layer returned a Cloudflare
managed challenge (`cf-mitigated: challenge`, Ray ID `a057ca39ab4a673b-ATL`).
This confirmed that Super Bot Fight Mode also needed a route-specific skip.

## Narrow Cloudflare Rule

Created and deployed this active Cloudflare custom rule:

- Name: `Allow authenticated Pulse media upload`
- Action: `Skip`
- Skip features:
  - All managed rules
  - All Super Bot Fight Mode rules
- Log matching requests: enabled

Expression:

```text
(http.host eq "coinpilotx.app" and http.request.uri.path eq "/api/pulse/media/upload" and http.request.method eq "POST" and any(http.request.headers["content-type"][*] contains "multipart/form-data"))
```

The skip is intentionally limited to one exact hostname, route, method, and
multipart content type. Custom firewall rules and rate limiting rules remain
enabled. Backend authentication and upload validation remain required.

## Endpoints Covered

Included:

- `POST /api/pulse/media/upload`

Not included:

- `POST /api/pulse/status`
- `POST /api/pulse/posts`
- `POST /api/pulse/reels/create`

Status, feed, and Reel publishing send media through the shared multipart upload
route first. Their create requests are JSON and do not need a WAF exception.

## Cache Purge

Cloudflare accepted a custom purge request for:

- `https://coinpilotx.app/static/js/pulse_upload_manager.js`
- `https://coinpilotx.app/pulse/status`
- `https://coinpilotx.app/pulse/reels`
- `https://coinpilotx.app/pulse`

## Frontend Handling

`static/js/pulse_upload_manager.js` now detects a non-JSON HTML `403` response
and shows:

> Upload was blocked by site security. Please try again or contact support.

The safe diagnostic includes only the HTTP status, content type, endpoint,
Cloudflare Ray ID when available, response headers, and a truncated raw response
body. It does not log secrets or upload contents.

The Pulse shell references a versioned upload-manager URL so the deployed fix is
not hidden behind the prior immutable static cache.

## Edge Retest

An unauthenticated multipart replay was repeated after the route-specific skip:

- HTTP response: `401`
- Content type: `application/json`
- Railway edge headers: present
- Cloudflare Ray ID: `a057cae95bbe4806-ATL`
- Safe response payload: `{"message":"Login required.","ok":false,"trace_id":"..."}`

This proves the multipart request now reaches the application and that backend
authentication remains intact. The Cloudflare HTML `403` is no longer returned
for the shared upload route.

## Local Validation

Passed:

- Python compile check
- JavaScript parse check
- `scripts/pulse_status_audit.py`
- `scripts/pulse_status_posting_audit.py`
- `scripts/pulse_media_surface_audit.py`
- `scripts/reels_upload_ui_audit.py`
- `scripts/pulse_reels_media_audit.py`
- `scripts/site_functional_audit.py`
- `scripts/performance_audit.py`
- `git diff --check`

The focused Status audit verifies text-only, image-only, text-plus-image, and
`.mov` upload contracts with readable JSON responses.

## Remaining Production Confirmation

Complete an authenticated browser pass after deployment:

1. Status image upload
2. Status `.mov` upload
3. Feed media post
4. Reel upload
5. Confirm Railway logs show `PULSE_MEDIA_UPLOAD_ROUTE_HIT`
6. Confirm R2/CDN URL is returned and rendered

## Remaining Risks

- The exact shared upload route now bypasses Cloudflare managed-rule and Super
  Bot Fight Mode inspection for multipart requests. Flask authentication, file
  validation, upload size limits, MIME checks, and rate controls remain the
  required enforcement layers.
- Keep Cloudflare match logging enabled and review upload-path event volume after
  deployment.
