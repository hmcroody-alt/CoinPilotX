# Pulse Communications V2 Cloudflare Security Plan

## Scope

Pulse Communications V2 remains disabled by default with `PULSE_COMMUNICATIONS_V2_ENABLED=false`. This plan documents Cloudflare protections to apply when V2 attachment uploads, message sends, live rooms, and notification surfaces are activated.

## Message Send Rate Limits

- Endpoint: `/api/pulse/communications/v2/conversations/*/messages`
- Suggested rule: authenticated users may send up to 60 messages per minute, with a stricter burst threshold of 12 messages per 10 seconds.
- Action: managed challenge or temporary block on abuse patterns, not global API blocking.
- Exclusions: owner/admin moderation tests and internal health checks.

## Attachment Upload Protections

- Endpoint: `/api/pulse/communications/v2/attachments/upload`
- Method: `POST`
- Content type: `multipart/form-data`
- Suggested rule: allow authenticated uploads while preserving WAF managed rules for unrelated routes.
- Skip scope, if needed: only the specific managed WAF rule that falsely blocks multipart uploads for this endpoint.
- Do not disable WAF globally, do not bypass all `/api` routes, and do not skip bot protection for unauthenticated requests.

## R2 Attachment Delivery

- Store originals in R2 using existing Pulse media storage.
- Serve images and files from `R2_PUBLIC_BASE_URL` when available.
- Prefer Mux playback for video messages once `mux_playback_id` exists.
- Keep R2 object keys private from UI except safe CDN URLs.

## Bot And Abuse Protection

- Keep bot protections active for unauthenticated traffic.
- Use path-specific exceptions only for authenticated multipart uploads.
- Add rate limits for:
  - room creation
  - group creation
  - direct-open attempts
  - message send
  - reaction toggles
  - attachment uploads
- Watch for spam patterns such as repeated public room joins, high failed upload rates, and repeated invite alerts.

## Live Rooms

- Endpoints:
  - `/api/pulse/communications/v2/conversations/*/live/mux/create`
  - `/api/pulse/communications/v2/live/mux/*`
  - `/api/pulse/communications/v2/live/mux/webhook`
- Webhook endpoint must validate Mux signatures before processing.
- Stream keys must never be exposed to viewers.
- Apply lower rate limits to create/disable actions than to read actions.

## WAF Rules That Should Not Block Authenticated Uploads

- Multipart form inspection false positives on the V2 attachment upload endpoint.
- Video MIME types including `video/mp4`, `video/quicktime`, and `video/webm`.
- Safe file types already validated by backend upload rules.

## Remaining Risks

- Large video uploads may still be constrained by Cloudflare, Railway, and app-level file size limits.
- Mux processing can be delayed; UI should prefer Mux playback when ready and fall back to durable R2 URLs only when Mux is unavailable.
- Twilio notification sending should remain disabled or dry-run until abuse controls and user notification preferences are complete.
