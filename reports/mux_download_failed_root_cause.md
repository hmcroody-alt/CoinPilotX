# Mux Download Failed Root Cause

Generated: 2026-06-02

## Finding

Mux credentials and asset creation are working, but Mux cannot download the source video URL.

Evidence supplied from production:

- Mux asset exists.
- Mux asset status: `errored`.
- Mux error: `download_failed`.
- Mux source URL: `https://cdn.coinpilotx.app/pulse_media/...`

## Public Fetch Evidence

Tested from this workspace on 2026-06-03 UTC:

```text
curl -I -L https://cdn.coinpilotx.app/pulse_media/
```

Result:

```text
HTTP/2 403
server: cloudflare
content-type: text/html; charset=UTF-8
content-length: 5423
cf-mitigated: challenge
```

Also tested with a Mux-like user agent:

```text
curl -I -L -A "Mux Video Ingest" https://cdn.coinpilotx.app/pulse_media/
```

Result:

```text
HTTP/2 403
server: cloudflare
content-type: text/html; charset=UTF-8
content-length: 5423
cf-mitigated: challenge
```

The first-party stream route is not a safe Mux source either when accessed publicly:

```text
curl -I -L https://coinpilotx.app/api/pulse/media/1/stream
```

Result:

```text
HTTP/2 403
server: cloudflare
content-type: text/html; charset=UTF-8
cf-mitigated: challenge
```

## Root Cause

Cloudflare is challenging media source URLs that Mux needs to download.

Mux receives a URL under:

```text
https://cdn.coinpilotx.app/pulse_media/...
```

Instead of receiving a media response such as:

```text
200 OK
content-type: video/mp4
```

Mux gets:

```text
403
content-type: text/html
cf-mitigated: challenge
```

So Mux asset creation succeeds, but Mux ingestion later fails with `download_failed`.

## Fix Applied

`services/media_service.py`

- Added `mux_source_url_for_key()` so Mux can use a dedicated public source base instead of the challenged user-facing CDN.
- Supported source base variables:
  - `MUX_SOURCE_BASE_URL`
  - `R2_MUX_SOURCE_BASE_URL`
  - `R2_DIRECT_PUBLIC_BASE_URL`
  - `R2_PUBLIC_DEV_URL`
  - `R2_R2DEV_BASE_URL`
- Added `inspect_mux_source_url()` to validate Mux source URLs before creating a Mux asset.
- If the source returns `403`, `text/html`, Cloudflare challenge, non-HTTPS, or another bad status, the upload now fails clearly before creating another errored Mux asset.
- Added safe logs:
  - `MUX_SOURCE_URL`
  - `MUX_SOURCE_FETCH_STATUS`
  - `MUX_ASSET_CREATE_RESPONSE`
  - `PULSE_MUX_SOURCE_UNREACHABLE`

`.env.example`

- Added media/Mux source variables for Railway setup.

## Required Railway Configuration

Keep:

```text
R2_PUBLIC_BASE_URL=https://cdn.coinpilotx.app
```

for user-facing CDN URLs.

Add one of these with a public, no-challenge R2 source base:

```text
MUX_SOURCE_BASE_URL=https://pub-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx.r2.dev
```

or:

```text
R2_MUX_SOURCE_BASE_URL=https://pub-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx.r2.dev
```

The value must serve objects publicly without cookies, auth, signed URLs, WAF challenge, or bot challenge.

## Expected Validation

Fresh upload should log:

```text
MUX_SOURCE_URL ... url=https://pub-...r2.dev/pulse_media/...
MUX_SOURCE_FETCH_STATUS ... status=200 content_type=video/mp4
PULSE_MUX_ASSET_CREATED ... mux_asset_id=... mux_playback_id=...
MUX_ASSET_CREATE_RESPONSE ... status=preparing status_code=201
```

Mux Dashboard should then show:

```text
Asset status: preparing
Asset status: ready
Playback ID generated
```

## Remaining Risk

If the direct R2 source URL is not configured in Railway, the app will continue using `https://cdn.coinpilotx.app` as a fallback and will now fail fast with a clear source-unreachable error instead of creating another errored Mux asset.

