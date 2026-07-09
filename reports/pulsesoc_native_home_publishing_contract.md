# PulseSoc Native Home Publishing Contract + Draft Recovery

Date: 2026-07-08

## Scope

This hardening pass focuses only on the native Home publishing workflow. It does not add a new product surface and does not change production WebView routes.

## Reused PulseSoc Systems

- Existing `/api/pulse/posts` post creation contract through `createPost(...)`.
- Existing shared native media upload hook and `/api/pulse/media/upload` pipeline.
- Existing server-side media validation, processing, moderation, authorization, and visibility rules.
- Existing Camera Studio, Live, Music, Status, Feed, and event-sync routing.
- Existing native event sync invalidation registry.

## What Changed

- Added durable Home Composer draft persistence through `AsyncStorage`.
- Restored text, mode, audience, topic, feeling, selected media metadata, and uploaded media ID metadata after reload/app resume.
- Added explicit recovered-draft UI with a safe clear action.
- Added server-authoritative retry for failed post creation payloads.
- Kept upload retry/cancel delegated to the existing shared native media upload pipeline.
- Blocked publish while a media upload is already active.
- Reset composer mode, audience, text, topic, feeling, media, retry state, and draft storage after successful publish.
- Added Home publish success invalidation for Activity and Notifications through the existing event sync registry.
- Kept Reel creation constrained to video media or the existing Camera Studio/Reel path.
- Kept Live creation on the existing Live Studio handoff.

## Post Publishing Contract

- Text-only posts publish through `POST /api/pulse/posts`.
- Image/video posts publish only after the shared upload hook returns a server media ID.
- Server response remains authoritative for post ID, normalized post payload, and feed refresh.
- Client-side validation only prevents empty publish and upload-in-flight submission.
- Server-side validation, moderation, visibility, permissions, and ranking are not duplicated in native code.

## Reel and Live Handoffs

- Reel mode requires video media or routes users into the existing native Camera Studio/Reel flow.
- Live mode opens the existing Live gateway instead of creating a native-only Live hosting path.
- Unsupported advanced publishing tools remain provider/backend fallbacks.

## Draft Recovery

- Draft recovery restores composer state visibly in Home.
- Local media file metadata is restored only as queue metadata; PulseSoc still requires the shared upload pipeline for server media IDs.
- Already uploaded server media results can be reused by ID after recovery.
- Drafts are cleared only after successful server publish or explicit Clear Draft.

## Upload Queue Persistence

- The composer persists queue metadata, upload stage, selected asset metadata, and uploaded media result metadata.
- Active upload transport is not faked after reload; users must retry through the existing media pipeline if the upload did not complete.
- Successful uploaded media IDs survive reload and can be included in the next server-authoritative publish request.

## Feed Invalidation After Publish

- Successful publish immediately prepends the returned post when available.
- Home then refreshes the selected feed through the existing feed API.
- Home publish success also invalidates Activity and Notifications through `invalidateNativeSync(...)`.

## Visible QA

Visible QA result:

- The built-in QA browser was opened visibly on the local QA web build.
- A local QA account authenticated through the native Login screen without committing or displaying credentials.
- The app navigated through native Dashboard -> Home, keeping the authenticated SPA session alive.
- Roody could see the Pulse Network hero, Status rail, Pulse Composer, Post/Reel/Live mode controls, Photo/Video/Music/Feeling/Location/Mention/Topic/Public controls, Publish Signal, feed tabs, and feed cards.
- Browser automation dropped during follow-up interaction, so empty-publish click, typed draft reload, and text-only publish were not truthfully marked as fully browser-verified in this pass.
- Static audit verified durable draft persistence, recovered-draft UI, retry state, upload queue metadata, publish reset, and feed invalidation wiring.

## Remaining Gaps

- Real photo/video picker permissions and camera/microphone behavior remain device QA.
- Browser-visible media upload depends on web picker support and available QA backend media contracts.
- Reel video publish with actual media remains release QA unless the QA browser can provide a valid file.
- Offline network loss/reconnect is structurally supported by persisted draft state but still needs device/provider QA.
- Visible end-to-end text publish must be rerun after the QA browser automation connection is stable enough to click/type through the composer without timing out.
