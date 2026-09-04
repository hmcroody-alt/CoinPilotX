# Media Reliability Architecture

Scope: `mobile-native/` (Expo SDK 54 / RN 0.81.5). This document describes the shared
media *delivery* foundation — the half of the pipeline that runs after a canonical media
object exists and before a surface renders or acts on it.

## The pipeline

```
Upload ──► Canonical Media Object ──► Storage / CDN ──► Thumbnail / Preview
                                                              │
                                                              ▼
                                          Native Cache ──► Media Viewer ──► Shared Actions
```

Each box has exactly one owner in the app. The rule that makes this a foundation rather
than a suggestion is that a surface may not re-implement a box; it consumes it.

| Stage | Owner module | Notes |
| --- | --- | --- |
| Upload | `src/media/MediaUploadManager` (pre-existing) | Queue, progress, retry for outbound bytes. |
| Canonical Media Object | `src/media/mediaContract.ts` (pre-existing) | The shape every surface speaks. |
| Storage / CDN access | `src/media/mediaAccess.ts` (pre-existing) | Signing, base URLs, entitlement. |
| Thumbnail / Preview | `mediaContract` variants (pre-existing) | Derived URLs, not a second pipeline. |
| **Native cache** | **`src/media/mediaCache.ts`** | Bounded LRU, integrity, per-account paths. |
| **Fetch** | **`src/media/mediaDownloader.ts`** | Queued, resumable, retrying, idempotent. |
| Media viewer | `src/components/NativeMediaViewer.tsx` (pre-existing) | The only full-screen media surface. |
| **Shared actions** | **`src/media/mediaActions.ts`** | Save to gallery, share file, action order. |
| Telemetry | `src/media/mediaTelemetry.ts` | Recorder with a swappable sink. |
| Session hygiene | `src/media/mediaSessionCleanup.ts` | Purge on sign-out / account switch. |

The four bolded rows are what this mission added. Everything else already existed and was
deliberately left alone.

## Why the new modules sit where they do

`mediaCache` knows about bytes on disk and nothing about HTTP. `mediaDownloader` knows
about HTTP and delegates every disk decision to the cache. `mediaActions` knows about the
OS (Photos, the share sheet) and delegates every byte decision to the downloader. The
dependency arrows only ever point downward, so a change to the share sheet cannot alter
eviction policy, and a change to eviction cannot alter what a permission prompt says.

`NativeMediaViewer` is the join point. It is consumed by `SavedScreen`,
`MarketplaceProductScreen`, `StatusScreen`, `ChatScreen`, `SellerStoreScreen` and
`PostCard`, so wiring save-and-share into the viewer is what makes every one of those
surfaces inherit the foundation without touching six files.

## Single-ownership, enforced by test

`src/media/__tests__/mediaFoundationOwnership.test.ts` walks `src/` and fails the build if
ownership is violated:

- `createDownloadResumable` appears only in `mediaDownloader.ts`.
- `expo-media-library` and `saveToLibraryAsync` appear only in `mediaActions.ts`.
- `expo-sharing` appears only in `mediaActions.ts`.
- `pulsesoc-media` and `cacheFileUriFor` appear only in `mediaCache.ts`.
- No file under `screens/` or `components/` fetches bytes, saves to the gallery, or opens
  a file share sheet.
- The five media-foundation modules contain no `setAudioModeAsync`, `AVAudioSession` or
  `setCategory` — the realtime-audio boundary, asserted rather than promised.

This is the mechanism by which "no per-screen reimplementation" survives the next engineer
who is in a hurry.

## Result types instead of exceptions

`saveMediaToGallery` and `shareMedia` return discriminated unions
(`{status: "saved" | "permission_denied" | "unsupported" | "failed", …}`) rather than a
boolean or a thrown error. A boolean collapses "the user declined", "the file is a PDF"
and "the disk is full" into one indistinguishable `false`, and the natural UI response to
`false` is a generic toast — or worse, an optimistic "Saved". With a union the caller
cannot render success without matching `status === "saved"`, and the compiler enforces it.

## Telemetry

`mediaTelemetry.ts` is a recorder, not a network client, for the same reason
`discovery/analytics.ts` is: PulseSoc has no general-purpose client event ingest today, and
inventing an endpoint here would ship a client that 404s in production while passing every
test. The module owns *what a media event is*; `setMediaTelemetrySink` is the single change
needed the day an ingest exists.

## What this mission did not build

Server-side transcode, thumbnail generation, and upload dedupe are backend concerns and
were out of the chosen scope. Physical-device QA (Stages 44/45) and live network-transition
testing (Stage 29) cannot be executed from a sandbox and are recorded as open in
`MEDIA_DEVICE_QA.md`.
