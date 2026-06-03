# Mux Source URL QA Fix

Date: 2026-06-03

## Summary

Mux asset creation was failing with `download_failed` because Pulse was giving Mux the user-facing CDN URL on `cdn.coinpilotx.app`. That hostname is protected by Cloudflare challenge logic, so Mux received HTML/403 instead of video bytes.

The no-challenge R2 public development URL for the `pulse-media2` bucket is enabled and reachable:

```text
https://pub-61f1bfcbe96e493aa087250fc90dc8bc.r2.dev
```

Railway was updated for the CoinPilotX web service with:

```text
MUX_SOURCE_BASE_URL=https://pub-61f1bfcbe96e493aa087250fc90dc8bc.r2.dev
```

No secrets were exposed or changed.

## Cloudflare/R2 Findings

Bucket:

```text
pulse-media2
```

Public access:

```text
Enabled
```

Custom domain:

```text
cdn.coinpilotx.app
Status: Active
Access: Enabled
```

Public development URL:

```text
https://pub-61f1bfcbe96e493aa087250fc90dc8bc.r2.dev
```

## Failing CDN Source

Original Mux source example:

```text
https://cdn.coinpilotx.app/pulse_media/2026/06/03/ScreenRecording_05-30-2026_17-18-48_1-f8de9e3e92cc823b.mp4
```

Result:

```text
HTTP/2 403
Server: cloudflare
Content-Type: text/html; charset=UTF-8
cf-mitigated: challenge
```

Conclusion: this URL is not safe for Mux source ingestion.

## Working R2 Source

Equivalent R2 public source:

```text
https://pub-61f1bfcbe96e493aa087250fc90dc8bc.r2.dev/pulse_media/2026/06/03/ScreenRecording_05-30-2026_17-18-48_1-f8de9e3e92cc823b.mp4
```

HEAD result:

```text
HTTP/1.1 200 OK
Content-Type: video/mp4
Content-Length: 23331445
Accept-Ranges: bytes
Cache-Control: public, max-age=31536000, immutable
Server: cloudflare
```

Range result:

```text
HTTP/1.1 206 Partial Content
Content-Type: video/mp4
Content-Length: 256
Content-Range: bytes 0-255/23331445
Accept-Ranges: bytes
```

The first bytes identify a real MP4 container:

```text
ftypmp42
```

Conclusion: this URL is safe for Mux source ingestion.

## Railway Change

Service:

```text
CoinPilotX
coinpilotx.app
```

Variable added:

```text
MUX_SOURCE_BASE_URL
```

Value:

```text
https://pub-61f1bfcbe96e493aa087250fc90dc8bc.r2.dev
```

Deployment status after applying the variable:

```text
Deployment successful
Active deployment: Fix Mux source delivery for video uploads
```

## Code Path Confirmed

The deployed code supports the new source base through:

```text
services/media_service.py
```

Key behavior:

- `mux_source_url_for_key()` prefers `MUX_SOURCE_BASE_URL` or `R2_MUX_SOURCE_BASE_URL`.
- Mux source validation runs before asset creation.
- Logs include:
  - `MUX_SOURCE_URL`
  - `MUX_SOURCE_FETCH_STATUS`
  - `MUX_ASSET_CREATE_RESPONSE`

Expected log after fresh video upload:

```text
MUX_SOURCE_URL ... url=https://pub-61f1bfcbe96e493aa087250fc90dc8bc.r2.dev/pulse_media/...
MUX_SOURCE_FETCH_STATUS ... status=200 content_type=video/mp4
MUX_ASSET_CREATE_RESPONSE ... status=preparing status_code=201
```

## Fresh Upload Verification

Production Reels was reachable and authenticated in the QA browser.

Automated upload could not be completed from the browser automation layer because:

- the production session cookie is not exposed to local shell commands;
- the browser automation surface cannot attach a local file to the file picker;
- read-only page evaluation does not allow issuing the upload request directly.

No destructive actions were taken.

Manual fresh upload verification remains:

1. Upload a new short MP4 Reel from the production browser.
2. Confirm Railway logs show `MUX_SOURCE_URL` using the `pub-...r2.dev` base.
3. Confirm `MUX_SOURCE_FETCH_STATUS` is `200` with `video/mp4`.
4. Confirm `MUX_ASSET_CREATE_RESPONSE` is `created` or `preparing`.
5. Confirm Mux dashboard transitions the new asset to `Ready`.
6. Confirm Pulse stores `mux_asset_id`, `mux_playback_id`, and `mux_status`.
7. Confirm playback URL uses `https://stream.mux.com/{playback_id}.m3u8`.

## Result

The source URL root cause is fixed at the infrastructure/configuration layer. Mux should no longer receive the challenged `cdn.coinpilotx.app` URL for new video uploads once the fresh upload path is exercised.

The remaining proof step is a fresh production video upload performed through the real file picker.
