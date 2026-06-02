# Production Video Delivery Root Cause

Date: 2026-06-02

Scope: Investigation only. No UI/code changes were made.

## Executive Finding

Videos are not failing because the player controls are missing. They fail because the production render path gives the browser the wrong playback source metadata on the main player and, on Feed, sometimes uses the raw CDN `.mov` URL instead of the first-party playable stream.

The same production media item is involved in both observed surfaces:

- Reel: `reel_id=5`
- Feed post: `post_id=939`
- Media: `media_id=94`
- Original media/CDN URL: `https://cdn.coinpilotx.app/pulse_media/2026/06/02/ScreenRecording_06-02-2026_13-45-47_1-b7c382f68b77fc03.mov`
- Stream URL: `https://coinpilotx.app/api/pulse/media/94/stream`

## Production Browser Evidence

### Reel Shown

Page: `https://coinpilotx.app/pulse/reels?tab=for_you`

Rendered card:

- `data-reel-id="5"`
- `data-media-id="94"`
- `data-media-url="/api/pulse/media/94/stream"`
- `data-media-cdn="https://cdn.coinpilotx.app/pulse_media/2026/06/02/ScreenRecording_06-02-2026_13-45-47_1-b7c382f68b77fc03.mov"`
- `data-media-mime="video/quicktime"`
- Card class became: `reel-card smart is-active is-broken`

Video element state:

- Background/blur video:
  - `src="/api/pulse/media/94/stream"`
  - `currentSrc="https://coinpilotx.app/api/pulse/media/94/stream"`
  - `readyState=4`
  - `duration=19.267007`
  - `videoWidth=884`
  - `videoHeight=1416`
  - `error=null`

- Main player video:
  - `<source src="/api/pulse/media/94/stream" type="video/quicktime">`
  - `currentSrc=""`
  - `readyState=0`
  - `duration=NaN`
  - `videoWidth=0`
  - `videoHeight=0`
  - `error=null`

Conclusion: the stream itself is reachable in the browser, but the main player advertises the stream as `video/quicktime` even though the stream endpoint serves MP4 playback bytes. The browser does not select the source, leaving the main player empty.

### Feed Video Shown

Page: `https://coinpilotx.app/pulse`

Rendered post:

- `data-post-id="939"`
- `data-post-type="video"`
- `data-media-count="1"`
- `data-media-id="94"`
- `data-media-url="https://cdn.coinpilotx.app/pulse_media/2026/06/02/ScreenRecording_06-02-2026_13-45-47_1-b7c382f68b77fc03.mov"`
- `data-media-mime="video/quicktime"`

Feed video element:

- `<source src="https://cdn.coinpilotx.app/...b7c382f68b77fc03.mov" type="video/quicktime">`
- `currentSrc=""`
- `readyState=0`
- `poster` also points at the `.mov`

Conclusion: Feed selects the raw CDN `.mov` URL, not `playback_url`, so it bypasses the first-party MP4 stream that Reels has available.

## HTTP / Stream Evidence

### Browser Asset Inventory

Browser asset capture for `https://coinpilotx.app/api/pulse/media/94/stream` succeeded:

- URL: `https://coinpilotx.app/api/pulse/media/94/stream`
- Content-Type observed by browser asset capture: `video/mp4`
- Downloaded bytes: `783,896`
- Local file signature: `ISO Media, MP4 Base Media v1 [ISO 14496-12:2003]`
- Header bytes begin with `ftypisom`, consistent with MP4.

Browser asset capture for the raw CDN `.mov` failed:

- URL: `https://cdn.coinpilotx.app/pulse_media/2026/06/02/ScreenRecording_06-02-2026_13-45-47_1-b7c382f68b77fc03.mov`
- Failure: `TypeError: Failed to fetch`

### Command-Line HTTP Probe

Unauthenticated command-line probes were blocked by Cloudflare challenge, which confirms the edge can intercept media requests:

- `HEAD https://coinpilotx.app/api/pulse/media/94/stream`
  - HTTP `403`
  - `server: cloudflare`
  - `content-type: text/html; charset=UTF-8`
  - `cf-mitigated: challenge`
  - `cf-ray: a059f5dd48ad31af-ATL`

- `GET https://coinpilotx.app/api/pulse/media/94/stream` with `Range: bytes=0-1023`
  - HTTP `403`
  - `server: cloudflare`
  - `content-type: text/html; charset=UTF-8`
  - `cf-mitigated: challenge`
  - `cf-ray: a059f5de5b3bb55b-ATL`

- `HEAD` and range `GET` against the raw CDN `.mov`
  - HTTP `403`
  - `server: cloudflare`
  - `content-type: text/html; charset=UTF-8`
  - `cf-mitigated: challenge`
  - CF-Ray examples:
    - `a059f5e00a9b53d2-ATL`
    - `a059f5e1be22fbce-ATL`

Because Railway CLI auth is currently invalid locally (`invalid_grant`), direct production DB and R2 HEAD inspection could not be completed from this workstation without re-authenticating Railway or providing production credentials. However, the browser stream success proves the application stream endpoint can retrieve and return playback bytes for `media_id=94`.

## Database / Row Evidence Available From Rendered Production Payload

Production-rendered data exposes:

- Reel row identity:
  - `pulse_reels.id = 5`
  - author/user shown as owner account in the UI
  - active card references `media_id=94`

- Feed row identity:
  - `pulse_posts.id = 939`
  - `post_type=video`
  - post title/body: `TESTING` / `TEST...`
  - media list references `media_id=94`

- Media row identity:
  - `chat_media_uploads.id = 94`
  - `media_type=video`
  - `mime_type=video/quicktime`
  - original/CDN `.mov` URL above
  - stream URL `/api/pulse/media/94/stream`
  - browser-observed stream content type `video/mp4`

## Code Path Findings

### Feed Uses The Wrong Source

In `bot.py`, the Pulse feed `mediaHtml(items)` currently chooses:

```js
const src = mediaUrl(m.valid_url || m.media_url);
```

It ignores `m.playback_url` even though `services/pulse_feed_engine.py` includes it in media payloads:

```py
"playback_url": resolved.get("playback_url") or payload.get("playback_url") or valid_url
```

Expected Feed behavior:

```js
const src = mediaUrl(m.playback_url || m.mux_hls_url || m.valid_url || m.cdn_url || m.media_url);
```

### Reels Uses The Right URL But The Wrong MIME Type

In `bot.py`, Reels chooses stream-first source:

```js
media.playback_url || media.mux_hls_url || media.valid_url || media.cdn_url || media.media_url
```

But the shared renderer receives `mime_type` from the original upload:

```js
mime_type: mime
```

For media `94`, this is `video/quicktime`, while `/api/pulse/media/94/stream` returns `video/mp4`. The main player is rendered as:

```html
<source src="/api/pulse/media/94/stream" type="video/quicktime">
```

That is the exact mismatch causing the main player to remain unloaded.

### Poster/Thumbnail Also Point At Video URLs

Rendered Feed and Reels payloads also set poster/thumb/backdrop to `.mov` URLs. This causes extra raw CDN media fetch attempts and should be avoided for video unless a real image poster exists.

## Root Cause Classification

- Missing object: Not proven. Browser successfully fetched MP4 bytes through `/api/pulse/media/94/stream`.
- Broken URL: Partially. Feed renders the wrong URL for video playback by using CDN `.mov` instead of `playback_url`.
- Bad MIME: Yes. Reels main player uses `type="video/quicktime"` for an MP4 stream URL.
- Failed transcode: Unlikely for media `94`; stream endpoint returns MP4 bytes and MP4 header.
- Failed playback generation: Not primary. Playback stream exists, but renderers do not consume it correctly.
- Failed Range request: Unconfirmed from direct unauthenticated curl because Cloudflare challenges command-line requests. The browser can fetch stream bytes. The Flask route code supports `Accept-Ranges` and `Content-Range`.
- Cloudflare/CDN issue: Present for raw CDN `.mov` and unauthenticated command-line probes, but the app should avoid raw CDN `.mov` for playback and use the first-party stream/playback URL.

## Exact Fix Recommended

Do not change layout/UI.

1. Feed renderer:
   - Change feed `mediaHtml()` source selection to prefer `playback_url` for video:
     - `playback_url`
     - `mux_hls_url`
     - `valid_url`
     - `cdn_url`
     - `media_url`

2. Shared media renderer:
   - When `media.playback_url` is a first-party stream URL and the media is video, force source MIME to `video/mp4` unless a Mux HLS URL is being used.
   - Alternatively omit the `<source type>` attribute for stream URLs so the browser sniffs the MP4 response.

3. Video poster/thumb normalization:
   - Do not use `.mov`, `.mp4`, `.webm`, or `/api/pulse/media/<id>/stream` as `poster_url`, `thumbnail_url`, or CSS backdrop.
   - Use a real image poster if present; otherwise leave poster/backdrop empty.

4. Optional backend hardening:
   - In `services/media_service.resolve_media()`, if `playback_url` is present and `playback_mime_type` is present, expose `playback_mime_type` in the media payload.
   - In `services/pulse_feed_engine._canonical_media_payload()`, include `playback_mime_type`.

## Validation Plan For Fix

After applying the fix:

1. Open `https://coinpilotx.app/pulse/reels?tab=for_you`.
2. Confirm active Reel `media_id=94` main player has:
   - `currentSrc=https://coinpilotx.app/api/pulse/media/94/stream`
   - `readyState >= 2`
   - `duration > 0`
   - no `is-broken` class
3. Open `https://coinpilotx.app/pulse`.
4. Confirm Feed post `939` uses:
   - `/api/pulse/media/94/stream`
   - not the raw CDN `.mov`
5. Confirm no browser requests to the `.mov` URL are needed for video playback.
6. Confirm no Cloudflare challenge HTML is returned to media elements.
7. Confirm Range playback still works through the first-party stream endpoint.

