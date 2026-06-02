# Pulse Production Publish Trace

## Scope

Evidence-only trace for the production Pulse publishing failure reported on
`https://coinpilotx.app/pulse/status`.

No application code, UI, layout, middleware, or infrastructure settings were
modified during this investigation.

## Executive Finding

The reproduced media-status failure occurs before the request reaches the
CoinPilotXAI application.

Cloudflare rejects the multipart upload request:

```text
POST https://coinpilotx.app/api/pulse/media/upload
HTTP/2 403
Content-Type: text/html; charset=UTF-8
Server: cloudflare
```

The HTML response is a Cloudflare block page with:

```text
Attention Required! | Cloudflare
Sorry, you have been blocked
You are unable to access coinpilotx.app
```

The frontend upload helper expects JSON. It attempts to parse this HTML page as
JSON and surfaces:

```text
Upload returned an unreadable response.
```

## Browser Reproduction

### Authenticated production baseline

The in-app browser was opened at:

```text
https://coinpilotx.app/pulse/status
```

The login flow completed successfully. Railway HTTP logs show:

```text
2026-06-02T01:40:45Z POST /login              302
2026-06-02T01:40:45Z GET  /pulse/status       200
2026-06-02T01:40:45Z GET  /api/pulse/status/rail?lane=for_you 200
```

This proves the browser session could load authenticated Pulse Status data.

### Status media attempt

The production Status composer was switched to Photo mode. The selected file
was:

```text
Ads.png
PNG image, 1024 x 1536
1.9 MB
```

After `Post Status`, the visible production UI retained the selected preview
and displayed:

```text
Upload returned an unreadable response.
```

Railway HTTP and application logs contained no matching
`POST /api/pulse/media/upload` route hit for the browser retry window. The
application route logger also did not emit `PULSE_MEDIA_UPLOAD_ROUTE_HIT`.

### Direct edge reproduction

The same endpoint and multipart field shape were replayed against production
with the same selected file:

```text
POST /api/pulse/media/upload
Content-Type: multipart/form-data

file=Ads.png
context_type=pulse_status
context_id=dedicated-status-page
```

Observed response:

```text
HTTP/2 403
Content-Type: text/html; charset=UTF-8
Server: cloudflare
CF-Ray: a052e6014cedb5d9-ATL
```

Safe response-body excerpt:

```html
<title>Attention Required! | Cloudflare</title>
<h1>Sorry, you have been blocked</h1>
<h2>You are unable to access coinpilotx.app</h2>
```

The Cloudflare page states that the submitted action triggered the security
service. The response is generated at the edge and is not application JSON.

## Exact Failing Request

| Field | Evidence |
| --- | --- |
| URL | `https://coinpilotx.app/api/pulse/media/upload` |
| Method | `POST` |
| Payload format | `multipart/form-data` |
| File field | `file` |
| Status context fields | `context_type=pulse_status`, `context_id=dedicated-status-page` |
| HTTP status | `403` |
| Response content type | `text/html; charset=UTF-8` |
| Response server | `cloudflare` |
| Response body | Cloudflare `Attention Required` block page |
| Railway route hit | No |
| Flask route logger hit | No |
| User account ID | Not exposed by the edge response; Flask auth was never reached |
| Direct reproduction timestamp | `2026-06-02T02:06:22Z` |

## Frontend Caller

The `/pulse/status` submit handler is in `bot.py`:

```text
bot.py:17695-17714
```

The upload call is:

```text
bot.py:17705-17707
```

It sends:

```js
PulseUploadManager.upload({
  url: "/api/pulse/media/upload",
  formData: fd,
  file: statusFile
})
```

The deployed upload helper is served from:

```text
/static/js/pulse_upload_manager.js
```

The production asset catches JSON parsing failure and converts it to:

```text
Upload returned an unreadable response.
```

## Backend Route

The intended Flask receiver exists at:

```text
bot.py:56958-57094
```

Route:

```text
POST /api/pulse/media/upload
```

The route performs authentication before upload processing and logs:

```text
PULSE_MEDIA_UPLOAD_ROUTE_HIT
PULSE_MEDIA_UPLOAD_START
PULSE_MEDIA_UPLOAD_COMPLETE
```

None of those markers appeared for the reproduced failure. The request did not
reach this route.

## Session, CSRF, And Middleware Results

| Check | Result | Evidence |
| --- | --- | --- |
| Browser session baseline | PASS | Authenticated `/pulse/status` and `/api/pulse/status/rail` returned `200` |
| Flask auth validation for failed upload | NOT EXECUTED | Cloudflare returned the response before Railway |
| Flask CSRF validation for failed upload | NOT EXECUTED | Cloudflare returned the response before Railway |
| Flask middleware rejection | NOT EXECUTED | No Railway HTTP upload entry and no application route-hit log |
| Service worker interception | NOT RESPONSIBLE | `static/sw.js` returns early for non-GET requests and does not intercept the upload POST |
| Edge security rejection | FAIL | Cloudflare returned `403 text/html` block page |

## Secondary Production Finding

Production serves `/static/js/pulse_upload_manager.js` with:

```text
Cache-Control: public, max-age=31536000, immutable
CF-Cache-Status: HIT
```

The production copy is older than the current workspace copy. The deployed
copy reports a generic unreadable-response message for non-JSON edge responses.
This does not cause the Cloudflare block, but it hides the real edge rejection
from the user.

## Request Classification

The observed failure is:

```text
a. Upload endpoint
```

It is not a status-create rejection, reel-create rejection, feed-create
rejection, media-processing exception, Flask auth failure, or Flask CSRF
failure. The upload never reaches those stages.

## Root Cause

Cloudflare edge security is blocking the Pulse multipart media upload request
before it reaches Railway. The returned HTML block page violates the frontend
JSON contract, producing the visible unreadable-response message.

## Follow-Up Evidence Still Needed

This trace closes the image-status failure path. Separate fresh captures are
still required for:

- `.mov` Status upload
- Reel upload
- Feed post with media

Those surfaces may share the same Cloudflare rejection, but that should be
verified rather than assumed.
