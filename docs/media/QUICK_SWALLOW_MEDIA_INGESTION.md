# Quick Swallow: media ingestion

How media gets from a phone or a browser into R2, why it is shaped this way, and
what is still slow.

This document exists because uploads were reported as slow across Posts, Reels,
Statuses and Messenger. The first thing an audit of those four routes turned up
is that they are **not** four separate pipelines. That framing matters, because
the obvious remedy — "build one shared resumable uploader" — was already done.
The real costs were somewhere else.

## What already existed

The native app has one upload engine, and every native surface reaches it:

| Surface | Entry point | Engine |
| --- | --- | --- |
| Posts | `HomePulseComposer` → `useComposerMediaQueue.uploadAll` | `MediaUploadManager` |
| Reels | same composer queue; `createReel` takes `media_ids` only | `MediaUploadManager` |
| Statuses | `CameraStudioScreen` | `MediaUploadManager` |
| Marketplace / Ads | their own screens | `MediaUploadManager` |
| Messenger | `uploadMessengerMedia` | `/api/messages/media/*` direct multipart |

`MediaUploadManager` creates a session against `/api/pulse/media/uploads`, and
the **server** chooses single-shot or multipart (threshold 16 MB, part size
8 MB). Parts go straight to R2 on presigned URLs. Nothing multi-gigabyte is
proxied through Flask. Progress, cancel, retry with backoff, and resume across
app restarts are all already there; `AsyncStorage` holds the session so a killed
app picks up from its completed-part list rather than from zero.

Messenger deliberately does *not* share the session model. It keys on the
attachment and asks the server for the resume point, letting the server read its
own storage instead of trusting a client-supplied part list. The two models are
different on purpose; what they share is `resumableUploadTransport`, which is
transport only.

On web, `static/js/pulse_upload_manager.js` sends desktop video of 8 MB or more
straight to Mux and proxies everything else through `/api/pulse/media/upload`.

So the answer to "which routes are on the slow legacy pipeline" is: none of the
native ones. The slowness was inside the shared engine, not outside it.

## What was actually costing time

### 1. A round trip in front of every part

`MediaUploadManager` signed one part per request, immediately before uploading
it. For a 350 MB status video at 8 MB parts that is 44 sequential round trips
that move no bytes at all — pure latency, paid serially, worst on the mobile
links where it hurts most. Messenger had already solved this; posts had not.

Signatures are now requested in batches. The batch size is **advertised by the
server** (`max_parts_per_request` on both the create and the resume responses)
rather than guessed, because `sign_parts` truncates anything past its cap and
returns 200. A client that guessed too high would upload a subset of the parts
and not discover it until `complete_upload` rejected a gapped part list — a
silent data-loss bug surfacing minutes later, far from its cause.

Batching widens the gap between minting a signature and using it, so a signature
can now age out mid-batch. That reads as 401/403/410, which `transientStatus`
deliberately does not retry. A part rejected that way is re-signed once and
re-sent; only a second rejection is a real failure.

A session persisted before this field existed, or an older server, omits the cap
and falls back to one part per request.

### 2. The whole file in memory before the first byte left

This is the one that makes a 90-minute video impossible, and it hid behind a
comment asserting the opposite.

The transport did `fetch(file://…).blob()` once and sent `blob.slice()` views of
it. The slice is genuinely zero-copy. Obtaining the blob is not:

- `RCTFileRequestHandler` reads the file with `NSDataReadingMappedIfSafe` —
  memory-mapped, cheap so far.
- `RCTNetworkTask.URLRequest:didReceiveData:` then does an unconditional
  `[_data appendData:data]` into a fresh `NSMutableData`, copying every mapped
  page into dirty heap. RN carries an explicit `@catch` there for
  `"Request's received data too long."`

Peak cost is therefore the full file size, in native memory, before a single
byte is uploaded. Survivable for a short clip; an immediate jetsam for a
multi-gigabyte one. The old design kept bytes out of the JS heap by putting the
entire file in the native heap.

Multipart now reads one part at a time through expo-file-system's `FileHandle`
(`offset` + `readBytes`). Peak memory is one part per part in flight — about
76 MB at four concurrent 8 MB parts — and it does not grow with duration.

The cost of this is that RN's `convertRequestBody` base64-encodes a `Uint8Array`
before handing it to native, roughly 1.33x the part size transiently plus some
CPU. Against an 8 MB part and an upload measured in seconds that is noise, and
the wire payload is unchanged because native decodes it back to `NSData` before
sending.

Single-part uploads still take the blob path — they are bounded by the server's
16 MB multipart threshold. URIs that cannot be opened as files (`ph://` asset
references, Android `content://`) fall back to it as well, because refusing the
upload outright would be worse than the memory cost.

### 3. A request ceiling inheriting a storage limit

`interactive_security_guard` is the only size check in the whole media stack that
runs **before** the body is transferred. Every other limit reads
`file_storage.stream`, by which point the bytes have already crossed the wire and
been spooled by Werkzeug. A ceiling set too high therefore does not merely fail —
it fails slowly, which is the entire complaint.

`/api/pulse/media/upload` was falling back to `MEDIA_UPLOAD_MAX_VIDEO_MB` for its
request ceiling. That variable answers a different question (how large a *stored*
video may be) and production sets it to 700. Gunicorn runs with `--timeout 120`.
A 700 MB multipart POST needs **5.8 MB/s sustained for two full minutes** to
survive that timeout; no ordinary mobile link provides it. The request was
accepted, transferred for as long as the connection held, and then the worker was
killed.

Nothing legitimate was in that band — native goes direct to R2, and web hands
video to Mux well under 150 MB — so the ceiling only ever admitted requests that
could not finish. It is now `PULSE_MEDIA_MAX_REQUEST_MB` with a default of 150,
and `tests/test_media_request_ceiling.py` reads the ceiling expression out of
`bot.py` and checks it against the Procfile's actual worker timeout, so raising a
storage limit cannot quietly widen it again.

## Limits

| Thing | Where | Value |
| --- | --- | --- |
| Video duration | `stored_video_policy`, server-side at session creation | 90 minutes |
| Multipart threshold | `media_upload_sessions.MULTIPART_THRESHOLD` | 16 MB |
| Part size | `media_upload_sessions.PART_SIZE` | 8 MB (min 5 MB) |
| Parts per sign call | `media_upload_sessions.MAX_PARTS_PER_SIGN` | 12, advertised |
| Parts in flight | `resumableUploadTransport.PARALLEL_PARTS` | 4 |
| Signed URL TTL | `media_upload_sessions.SIGNED_URL_TTL_SECONDS` | ≤ 900 s |
| Session TTL | `media_upload_sessions.SESSION_TTL_SECONDS` | 3600 s |
| Proxied request body | `bot.py` `interactive_security_guard` | 30 MB generic, 150 MB pulse media |

Duration is checked server-side before a signed URL is issued, so an over-long
video is refused up front rather than after an hour of cellular upload. There is
no arbitrary byte cap on a valid 90-minute video, because the bytes never pass
through the backend — but "no cap" is not "load it all into RAM", which is what
the previous section is about.

## Known remaining issues

**`media_storage.save_public_file` writes to local disk, then uploads to R2.** A
double hop for everything that still proxies through Flask. Bounded by the
150 MB request ceiling, so it is a latency cost rather than a correctness one,
but it is real for the web upload path.

**`/api/pulse/communications/v2/attachments/upload` carries two disagreeing limit
tables.** `stage_attachment_upload` calls `_validate_attachment_upload` (image 25
/ video 250 / audio 25 / voice 15 / file 50 MB) and then `stage_upload`, whose
`pulse_comm_v2` branch reads image 100 / video 1024 / audio 100 / file 1024 MB
from the *same* environment variable names with different in-code defaults. The
stricter table always wins, so the 1 GB one is unreachable — and the
`before_request` guard for that path defaults to 1024 MB, meaning a 900 MB
attachment would transfer completely and then be rejected by a 50 MB check.

This is latent, not live: no shipped client posts to that route. The web
composer targets `/api/messages/media/*` (guarded by
`messenger_media_composer_wiring_audit.py`) and native targets its own direct
multipart path. It is left alone deliberately — the 1 GB defaults were a
deliberate prior decision, `pulse_comm_v2_large_file_composer_audit.py` pins
those literals, and changing either table without a client to test against would
be churn. It is written down here so the next person to touch that route knows
both tables exist.

**Web still proxies.** Non-video web uploads and sub-8 MB desktop video go
through Flask rather than direct to R2. That is the one surface that is not on
the resumable foundation.

## Rules for changing this

- The server picks the transport. The client never guesses from the size, or the
  threshold exists in two places.
- The resume point is the server's to report. Messenger reads its own storage;
  nothing the client claims about which parts landed is trusted.
- Anything that caps a batch must advertise the cap. Silent truncation that
  returns 200 turns into a failure far from its cause.
- Assert that the expensive path is *not reached*, not that the cheap one
  produced the right sizes. A byte-total or progress assertion passes on both the
  bounded and the unbounded implementation; only `expect(fetch).not.toHaveBeenCalled()`
  can tell them apart.
- Check the jest mock actually models the API you are testing. Both upload suites'
  `expo-file-system` mocks lacked `open()`, so the ranged-read path would have
  been silently exercising the fallback while reporting green.
