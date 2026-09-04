# Media Cache Policy

Module: `mobile-native/src/media/mediaCache.ts`
Tests: `mobile-native/src/media/__tests__/mediaCache.test.ts`

## Location and shape

```
<FileSystem.cacheDirectory>/pulsesoc-media/u<scope>/<digest><ext>
```

`cacheDirectory` is chosen over `documentDirectory` deliberately: cached media is
regenerable from the CDN, so the OS is entitled to reclaim it under storage pressure. The
index is a per-scope AsyncStorage record under `pulsesoc.native.mediacache.index.<scope>`.

## Keys

`mediaCacheKey({ mediaId, url })` returns:

- `id:<n>` when a canonical media id exists — the media object is the identity.
- `u:<fnv1a digest>` otherwise, computed over the lowercased URL **with the query string
  and fragment stripped**.

Stripping the query is the point. CDN URLs are signed and the signature rotates, so keying
on the full URL means every re-sign is a cache miss and the same photo is downloaded again
each time a feed refreshes. The path is the stable identity.

An empty input returns `""`, and an empty key is never cached — there is nothing to key on,
and inventing a key would let two unrelated files collide.

## Bounds

Defaults, overridable through `configureMediaCache`:

| Bound | Default | Meaning |
| --- | --- | --- |
| `maxBytes` | 256 MB | Total cached bytes for the active scope. |
| `maxAgeMs` | 14 days | Age ceiling; enforced even when under quota. |
| `minFreeDiskBytes` | 128 MB | Headroom the device must retain after a download. |

`ensureRoomFor(bytes)` runs *before* a transfer starts and throws `MediaCacheFullError` if
the device cannot afford it. Refusing up front is why a full disk costs zero network bytes
rather than a download that dies at 98%.

Eviction (`evictMediaCache`) removes expired entries first, then least-recently-used by
`lastAccessAt`. `lastAccessAt` is refreshed on every hit, so an item the user keeps opening
outlives one they downloaded once.

## Integrity

A cache that can return a half-written file is worse than no cache — it hands a truncated
buffer to a decoder, which is where black frames and native crashes come from. So
`lookupCachedMedia` verifies on every read that the file still exists and that its size
matches what the index recorded. A mismatch is not repaired: the entry and the file are
dropped and the lookup reports a miss, which the downloader then services normally.

`commitCachedMedia` refuses to index a zero-byte file and deletes it. A 200 response with
an empty body is a failure that looks like a success, and indexing it would cache the
failure permanently.

## Writes are atomic

The downloader writes to `<destination>.part`. Only `commitCachedMedia` moves that file to
its final path, and only after the size check passes. Nothing is ever visible at the
canonical path in a partial state, so an app kill mid-download costs a retry, not a
corrupt entry.

The move is a `moveAsync`, not a copy — a copy would momentarily double the footprint of
the largest file in the cache, which is exactly the moment the device is least able to
afford it.

## Clearing

- `clearMediaCache(scope)` clears one account. Directory first, then index — in that order,
  because the reverse leaves orphaned bytes with nothing pointing at them.
- `clearAllMediaCaches()` removes the whole `pulsesoc-media` root and every
  `pulsesoc.native.mediacache.index.*` key. This is the sign-out path; see
  `MEDIA_SECURITY_MODEL.md`.
