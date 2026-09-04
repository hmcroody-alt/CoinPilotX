# Media Failure Handling

The governing rule: **a malformed file, a dead network or a refused permission must never
crash a screen, and must never be reported as success.**

## Failure vocabulary

`MediaFailureReason` in `mediaTelemetry.ts` is a closed union:

```
network · timeout · cancelled · not_found · forbidden · unsupported_type
too_large · corrupt · checksum_mismatch · no_disk_space
permission_denied · permission_limited · unavailable · unknown
```

Callers pick a code. They never pass `error.message` — see `MEDIA_SECURITY_MODEL.md` for
why. `mediaFailureReason(error)` derives the code from structural properties only:

| Observed | Code |
| --- | --- |
| `name === "AbortError"` | `cancelled` |
| `status` 404 / 410 | `not_found` |
| `status` 401 / 403 | `forbidden` |
| `status` 413 | `too_large` |
| `status >= 500` | `unavailable` |
| `code === "ENOSPC"` | `no_disk_space` |
| `code === "ETIMEDOUT"`, `name === "TimeoutError"` | `timeout` |
| `code` in `ECONNREFUSED` / `ENOTFOUND` / `ERR_NETWORK` | `network` |
| `name === "TypeError"` | `network` |

The last row is a deliberate trade-off. React Native's `fetch` reports an unreachable host
as a bare `TypeError` with the detail only in the message, and the message is the field
that carries the signed URL, so it stays unread. A genuine programming `TypeError` is
therefore misfiled as `network`. The cost is one wrong word in a message: both codes were
already retryable and neither changes control flow.

## Download failures

`mediaDownloader.ts`, constants: `MAX_CONCURRENT_DOWNLOADS = 3`, `MAX_ATTEMPTS = 3`,
`BASE_BACKOFF_MS = 600`, `RETRYABLE = {network, timeout, unavailable, unknown}`.

- **Retry is bounded and selective.** Three attempts with exponential backoff, and only for
  reasons that can plausibly succeed on a retry. A 403 is not retried — the entitlement is
  not going to change in 600ms, and retrying it just burns battery and rate limit.
- **HTTP >= 400 deletes the `.part` file** and throws with a status-derived reason. Nothing
  is left at the final path, so a subsequent lookup is an honest miss.
- **A zero-byte body is `corrupt`, not success.** `commitCachedMedia` refuses to index it.
- **Disk-full is refused before the first byte.** `ensureRoomFor` throws
  `MediaCacheFullError` and the transfer never starts — zero network requests.
- **Progress reports `fraction: null`** when `Content-Length` is unknown. A faked `0` renders
  as a bar that never moves, which reads as a hang.

### Idempotency

Concurrent requests for the same key — including requests whose signed URLs differ only in
signature — collapse into a single in-flight transfer via the `active` registry. Three
callers and one rotated-signature caller produce one network transfer and one `fileUri`;
`__mediaDownloaderState().active` returns to `0` afterwards. This is what prevents the
duplicate-media and duplicate-download symptoms in a fast-scrolling feed.

### Unhandled rejections

The `ActiveTask` placeholder promise is a permanently *pending* promise, never a rejected
one. A rejected placeholder is unhandled for the tick before it is replaced, and Node 22
terminates the process for that — which surfaced as whole Jest workers dying. The shared
promise also gets a `.catch(() => undefined)` at creation so a failure nobody has joined yet
is still not an unhandled rejection. On device the same bug would be a hard crash on a
failed download.

## Save failures

`saveMediaToGallery` returns a union; every branch is distinguishable:

| Situation | Result |
| --- | --- |
| Write completed | `{status: "saved", limited: false}` |
| iOS limited (add-only) access | `{status: "saved", limited: true}` |
| Declined, can ask again | `{status: "permission_denied", …}` |
| Declined, `canAskAgain: false` | `{status: "permission_denied"}`, message names Settings |
| Kind is not image or video | `{status: "unsupported"}`, message points at Share |
| Download failed, or `saveToLibraryAsync` threw | `{status: "failed", reason}` |

A document never reaches Photos and never reaches a permission prompt — the kind check runs
first and no download is attempted. In the viewer the button is hidden entirely for
unsupported kinds, because a button that can only answer "unsupported" is worse than no
button.

## Share failures

`shareMedia` degrades rather than failing:

1. Preferred: the real file through `Sharing.shareAsync`, so the recipient gets the picture.
2. If the platform reports no share sheet for files, or the file cannot be produced (offline,
   404), it falls back to `sharePulseObject` with the canonical link.

`{status: "shared", mode: "file" | "link"}` tells the caller which happened. Degrading to a
link is a real outcome; degrading to nothing is not.

## Surfacing to the user

`NativeMediaViewer` renders a polite live-region status line under the action row. It is
cleared whenever the visible index changes, so "Saved to your library." can never sit under
a photo that was not saved. The save button carries `accessibilityState={{disabled, busy}}`
and is disabled during the operation, so a double-tap cannot start two saves.
