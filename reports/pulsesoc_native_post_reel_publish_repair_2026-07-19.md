# PulseSoc Native Post + Reel Publish Repair — 2026-07-19

## Root-cause evidence

The physical iPhone failure was reproduced in production telemetry at 09:47 local time. The uploaded bytes succeeded, the canonical media record was readable, and media preview creation returned successfully. Publication then called the native-only `POST /api/pulse/reels/create-from-camera` route and received HTTP 500. The screenshot's generic upload failure therefore represented a publication-stage failure, not a byte-upload failure. The support trace value itself was not visible in the supplied screenshot; the request timestamp, native user agent, route, status, and surrounding media lifecycle correlated the incident safely without recording credentials or signed media URLs.

The native Camera Studio contract differed from production WebView. WebView uses `POST /api/pulse/posts` for Posts and `POST /api/pulse/reels/create` for Reels. Camera Studio used legacy direct-insert routes that bypassed the canonical feed engine and its canonical identifiers, moderation, indexing, notifications, and realtime publication behavior.

The same production log window also exposed two PostgreSQL compatibility defects: a SQLite `GLOB` expression in media-processing synchronization and a SQLite `PRAGMA` issued by preview persistence. The `GLOB` query repeatedly failed on PostgreSQL. Preview now issues the busy-timeout PRAGMA only for SQLite, and media synchronization normalizes a numeric context ID in Python before a portable update.

## Trace table

| Stage | Native service | Request | Backend | Observed result | Repair |
| --- | --- | --- | --- | --- | --- |
| Select/capture | `CameraStudioScreen` | local iOS asset | n/a | `.mov` selected | Preserved |
| Validate | `nativeMediaUpload` | local metadata | production limits | Accepted MOV under configured limit | Preserved |
| Upload bytes | `uploadNativeMedia` | multipart `file` | `POST /api/pulse/media/upload` | Succeeded | Reused unchanged |
| Processing | `pollNativeMediaProcessing` | media status | `GET /api/pulse/media/:id/status` | Media record returned; processing could continue | UI now distinguishes upload completion from processing |
| Preview draft | Camera API | media ID and draft metadata | `POST /api/pulse/camera/preview` | Succeeded | PostgreSQL PRAGMA guard added |
| Post publish | legacy Camera client | media ID | `/api/pulse/posts/create-from-camera` | Noncanonical path | Replaced with canonical Post route `/api/pulse/posts` |
| Reel publish | legacy Camera client | media ID | `/api/pulse/reels/create-from-camera` | HTTP 500 | Replaced with canonical Reel route `/api/pulse/reels/create` |
| Retry | Camera Studio | prior draft | upload + publish | Previously uploaded the file again | Reuses media ID and reconciles canonical feeds before retry |

## Production contract reuse

- Upload remains `POST /api/pulse/media/upload` with the existing multipart `file` field, session authentication, media service, storage, and processing pipeline.
- Regular Camera posts now use the canonical Post route and require a canonical post ID plus hydrated post response.
- Camera Reels now use the canonical Reel route and require both canonical Reel and Post IDs.
- Status, profile, and Messenger destinations retain their existing production services.
- No new upload system, database, storage service, processing pipeline, Post route, or Reel route was introduced.

## State and retry behavior

Camera Studio now identifies validation, upload, processing/reuse, publication, published, and failed stages. A failed publication preserves the selected asset, caption, privacy, and completed media record. A retry reuses that record. Before sending another create request, it looks for a publication with the same canonical media ID in My Posts or Reels, preventing the ordinary lost-response retry from duplicating the publication.

The capture policy text is now truthful: 1080p is a camera capture target, not proof that a gallery MOV was transcoded locally. The existing production backend remains authoritative for validation and processing.

## Verification status

- Production commit: `4d52d57b` (`Repair native post and reel publishing`) pushed to `origin/main`.
- Railway deployment: active and successful for CoinPilotX; all project services returned to Online.
- Production health: `GET https://pulsesoc.com/health` returned `{"ok":true,"service":"coinpilotx-web"}` after deployment.
- TypeScript typecheck: passed before commit and again in the clean integration worktree.
- Focused Post/Reel behavior audit: passed before commit and again after integration onto current `origin/main`.
- Canonical backend Reel final-step audit: passed locally.
- iPhone 16 Pro simulator build: passed for the native workspace/scheme; install and process launch passed. Authenticated publication remains pending.
- Signed physical-device build: passed on iPhone 16 Pro with Xcode 26.6, Debug configuration, Apple Development signing, and embedded JavaScript bundle.
- Physical install/launch: passed for `PulseSoc Native Dev` using bundle `com.pulsesoc.nativeapp.dev`.
- Side-by-side preservation: confirmed; `com.pulsesoc.nativeapp` and `com.pulsesoc.nativeapp.dev` remained installed separately.
- Physical Post publication: pending a controlled user action in the authenticated app.
- Physical Reel publication: pending a controlled user action in the authenticated app.
- WebView/native cross-client visibility: pending.

Physical-device acceptance remains open until a controlled Post and Reel are created after production deployment, opened by canonical ID on the iPhone, and observed in WebView. No physical or cross-client PASS claim is made by this report before that evidence exists.
