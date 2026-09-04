# The Canonical Media Object

Module: `mobile-native/src/media/mediaContract.ts` (pre-existing; documented here because
the delivery layer added by this mission is defined in terms of it).

## Why one shape

Nine surfaces — Messenger, Status, Reels, Feed, Marketplace, Profile media, Communities,
Business and Saved — receive media from routes that were written years apart. The backend
therefore emits `url`, `media_url`, `playback_url`, `cdn_url`, `download_url` and
`valid_url` depending on which route answered, and `type`, `media_type` and `mime_type`
depending on which era wrote it.

`CanonicalMediaRecord` is a permissive record covering every one of those spellings, and the
accessor functions are the *only* sanctioned way to read it. A surface that reaches for
`media.url` directly works until it meets a Mux-backed video, which has `playback_url` and
no `url`. That is the mechanism behind black players and broken thumbnails, and it is why
the accessors exist rather than a documented preference order in a comment.

## Accessors

| Function | Answers |
| --- | --- |
| `canonicalMediaId(media)` | `media_id ?? id ?? attachment_id ?? 0` — the identity. |
| `canonicalMediaState(media)` | Normalised lifecycle state across the four status fields. |
| `isCanonicalMediaReady(media)` | Ready, or state-less (legacy rows carry no state). |
| `isCanonicalMediaTerminal(media)` | Failed / rejected / deleted / expired — do not retry. |
| `isLikelyExpiringMediaUrl(url)` | Detects a signed URL by its query keys. |
| `hasRenderableMediaUrl(media)` | Is there any usable URL at all. |
| `hasRenderableImage(media)` | As above, restricted to still imagery. |
| `renderableMedia(list)` | Filters a list to items worth rendering. |
| `mediaRecordForCache(media)` | Strips volatile fields before persisting a record. |

Two states are deliberately distinct. **Not ready** means processing is still running and a
poll will eventually succeed; **terminal** means it never will. Collapsing them produces
either a spinner that runs forever or a retry loop against a deleted asset.

## How the delivery layer consumes it

The cache key is derived from the canonical id when one exists, and only falls back to a
URL digest when it does not:

```ts
mediaCacheKey({ mediaId: canonicalMediaId(media), url: mediaAccess.urlFor(media) })
```

`id:<n>` is stable across re-signing, across CDN migrations and across the six different
URL field names — the media object is the identity, and the URL is merely how the bytes are
reachable today. `isLikelyExpiringMediaUrl` is the reason the URL fallback strips the query
string: those signature keys are exactly the part that rotates.

`MediaActionTarget` in `mediaActions.ts` is the narrow projection the action layer needs —
`{url, mediaId, kind, surface, sourceUrl?}`. It is intentionally not `CanonicalMediaRecord`.
The share and save paths have no business reading `caption`, and a type that cannot express
a caption cannot leak one into a log or a filename.

## Rules

1. Never read a URL field directly from a media record; go through `mediaAccess`.
2. Never construct a cache key from a raw URL when a canonical id is available.
3. Never persist a `CanonicalMediaRecord` without `mediaRecordForCache` — signed URLs and
   expiry timestamps go stale on disk and produce 403s that look like corruption.
4. A new backend field belongs in `mediaContract.ts` and its accessor, not in the surface
   that first needed it.
