# PulseSoc Mobile Feed Phase Report

## Scope

Phase 3 builds the first production PulseSoc social surface in the Expo mobile app. The work is limited to feed and status creation. Reels, Messaging, Marketplace, and Notification Center remain untouched for future phases.

## APIs Used

- `GET /api/pulse/feed?tab=for_you&offset=<n>&limit=<n>`: timeline feed pagination.
- `GET /api/pulse/feed?profile=<username>&offset=<n>&limit=<n>`: profile post bootstrap.
- `GET /api/pulse/posts/:id`: post detail for deep links and comment screens.
- `POST /api/pulse/posts`: create text, image, video, shared, and status posts using existing backend payloads.
- `PATCH /api/pulse/posts/:id`: edit the signed-in user's post body.
- `DELETE /api/pulse/posts/:id`: delete the signed-in user's post.
- `POST /api/pulse/posts/:id/react`: like/unlike through the existing reaction endpoint.
- `GET /api/pulse/posts/:id/comments`: load post comments.
- `POST /api/pulse/posts/:id/comments`: create comments and replies when `parent_comment_id` is supported.
- `POST /api/pulse/posts/:id/repost`: repost existing PulseSoc content.
- `POST /api/pulse/media/upload`: upload image and video media for post creation.
- `POST /api/track`: mobile analytics event sink.

## Media Architecture

The composer uses Expo Image Picker for camera, photo library, and video library selection. Selected media is uploaded through `services/feed/mediaUpload.ts` with `XMLHttpRequest` so upload progress can be surfaced in the UI. The upload request uses the existing secure session cookie and posts multipart form data to `/api/pulse/media/upload` with `context_type=pulse_post`.

Uploaded media ids are passed to `POST /api/pulse/posts`. Images render with React Native `Image`; videos render lazily in-feed with Expo AV and native controls. Upload failure states preserve the draft and allow retry.

## Pagination Architecture

`HomeFeedScreen` uses `FlatList` virtualization, pull to refresh, and offset-based infinite scroll. Backend responses are normalized through `services/feed/feedApi.ts`, using `posts`, `next_offset`, and `has_more` when available. New or refreshed pages are merged by post id to prevent duplicates.

Profile screens reuse the feed profile filter for recent posts while keeping the UI ready for a future dedicated public profile JSON endpoint.

## Performance Strategy

- `FlatList` virtualization for timeline and comment lists.
- Stable post ids for list keys and page merging.
- Lazy media rendering inside post cards.
- Pull-to-refresh and footer loading states instead of full-screen reloads after initial load.
- Local optimistic count updates for likes, comments, and reposts.
- Feed analytics hooks for load, view, create, like, comment, and repost events.

## Deep Link Architecture

The mobile linking config supports:

- `pulse://post/:id`
- `pulse://profile/:username`

Post links open `PostDetailScreen` and hydrate from `GET /api/pulse/posts/:id`. Profile links open `ProfileDetailScreen` and hydrate recent posts from the profile feed filter.

## Validation Results

- `npm run typecheck`: passed.
- `npm run audit:foundation`: passed.
- `npm run audit:authentication`: passed.
- `npm run audit:feed`: passed.
- Expo launch check: passed.

## Known Issues

- The existing backend does not expose a dedicated public profile JSON endpoint with follower and following counts. The mobile profile screen shows available author data and recent posts, and marks follower metrics as pending that endpoint.
- Full live verification for signup-owned post creation, media upload, comments, and delete/edit requires a production test account and media-capable device or simulator.
