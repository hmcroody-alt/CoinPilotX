# PulseSoc Native Home Foundation Progress

## Scope

Mission: PulseSoc Native Home Experience Foundation - Phase 1.

Dashboard work is paused for this phase. The native Home foundation now prioritizes feature/workflow parity with the current production PulseSoc Home surface while preserving server-authoritative backend logic and WebView compatibility.

## Completed

### Pulse Network Hero

- Added a native Pulse Network hero to `HomeScreen`.
- Reused existing feed/status data already returned through native APIs.
- Added native actions for:
  - Pulse Radio
  - Live
  - Safety scan
  - Refresh
- Added loading/cache awareness through feed and status load state.
- Kept metrics lightweight and derived from server-returned feed/status payloads.

### Status rail

- Added native Home status rail.
- Reused existing `/api/pulse/status/rail` wrapper through `listStatuses`.
- Reused cached metadata through `loadCachedStatuses`.
- Added:
  - Add Status entry
  - Empty state
  - Seen/unseen labels
  - Status detail routing
  - Status creator routing
  - Cached/offline indicator

### Pulse Composer

- Added a Home-specific inline `HomePulseComposer`.
- Reused:
  - `createPost`
  - `useNativeMediaUpload`
  - `MediaUploadPreview`
  - server-side post validation
  - existing media upload pipeline
- Added composer parity controls:
  - Post
  - Reel
  - Live
  - Photo
  - Video
  - Music
  - Feeling
  - Location
  - Mention
  - Topic
  - Audience/Public selector
  - 3000 character counter
  - Publish Signal button
- Kept unsupported advanced flows as safe handoffs/fallback notes.

### Feed category tabs

- Added native feed tabs:
  - For You
  - Following
  - Friends
  - Communities
  - Trending
  - Crypto
  - Scam Alerts
  - Arena Highlights
  - Roast Clips
  - Questions
  - My Posts
- Tab selection is session-stateful.
- Refresh, pagination, cache fallback, and empty/error states now respect the selected tab.
- Existing `/api/pulse/feed` query contract is reused with `feed` and `tab`.

### Feed Foundation

- Preserved existing feed loading, pagination, pull refresh, offline cache, and post detail navigation.
- Added Home-level event invalidation hooks for activity, notifications, and marketplace refresh paths.
- Preserved the production `author_public_player_id` feed field during native normalization so Home author taps can reach native profiles.
- Added the production profile resolver's display-name fallback for legacy feed records without stable public identifiers.
- Extended feed card controls with native-visible actions:
  - Comment
  - Follow
  - Report
  - Hide
  - Block
  - Mute
- Server-authoritative actions remain on existing backend routes. Client-only destructive behavior was not introduced.

### Navigation

- Wired Home actions to existing native or safe fallback routes:
  - Pulse Radio to dashboard media route
  - Live to native Live tab
  - Safety Scan to Safety Hub
  - Add Status to native Status creator
  - Status item to Status detail
  - Photo/video/reel to Camera Studio
  - Profile, comments, media, share, save, report, block, mute to existing native surfaces
- Verified native route destinations in the built-in QA browser for Live, Safety Hub, Status Creator, Pulse Radio module shell, Post Detail, Profile Detail, and NativeMediaViewer.

## Reused Backend/API/Business Logic

- `/api/pulse/feed`
- `/api/pulse/status/rail`
- `/api/pulse/posts`
- `/api/pulse/media/upload`
- Existing media processing/status polling
- Existing native event sync invalidation registry
- Existing native dashboard routing/fallback boundary
- Existing profile, status, live, safety, growth, and camera navigation

## Fallback-Only Areas

- Native Live hosting remains on safe Studio/web provider fallback.
- Advanced native Music picker is routed through existing dashboard/media surfaces.
- Location and mention native pickers are visible as controls but still require dedicated backend/native contracts for full native selection.
- Reel publishing through composer requires attached video or Camera Studio handoff.
- Delete/pin actions are not client-implemented without explicit server-authorized native contracts.

## Known Blockers

- Physical-device media/camera behavior remains release QA, not a development blocker.
- Some feed tab payload richness depends on production feed engine support for the requested tab.
- Native composer draft persistence beyond session-state is not complete.
- Browser QA did not complete a successful post/reel media publish. Empty publish validation and upload/publish controls were verified, while provider/device media selection remains separate QA.
- Several specialized feed categories depend on production feed-engine support; the native client sends the requested `feed` and `tab` values and safely renders empty/error results.

## Current Estimates

- Home foundation: 82%
- Hero: 86%
- Status: 84%
- Composer: 80%
- Feed tabs: 90%
- Feed cards: 84%
- Feed interactions: 78%
- Publishing: 68%
- Navigation: 92%
- Visible QA coverage: 86%

## Next Highest-Value Home Task

Native Home publishing contract and draft recovery hardening.

Reason: Phase 1 now exposes and visibly verifies the Home structure, navigation, feed controls, and composer states. The remaining highest-risk Home gap is successful text/media publishing across Post and Reel modes, durable draft recovery, and explicit contracts for location/mention selection.
