# PulseSoc Native — Post & Reel Delete Parity

**Date:** 2026-07-19
**Scope:** `mobile-native/` (Expo/React Native app), backed by existing production Flask routes in `bot.py`.
**Mission:** Restore a secure, owner-only Delete action for posts and Reels across the Native app's primary surfaces, reusing existing backend routes.

## 1. Summary

The Native app previously had no client-side way to delete a post or Reel, even though the production backend (`bot.py`) already exposes fully-implemented, ownership-checked delete routes used by the WebView. This work adds a destructive, owner-gated "Delete" action to the shared `PostCard` overflow menu and the Reels "More" sheet, wired into Home, Post Detail, Profile, and the Reels viewer, using the existing production routes with no backend changes.

## 2. Backend routes reused (no new routes created)

- `DELETE /api/pulse/posts/<id>` → `pulse_delete_post_common()` (`bot.py`). Ownership check: `post.user_id == current_user.user_id` OR group admin. Response: `{"ok": true, "message": "Post deleted.", "post_id": <id>}`.
- `DELETE /api/pulse/reels/<id>` (aliased `/api/reels/<id>`) → `api_pulse_reel_manage()`. Ownership check: server-computed `reel.can_manage` boolean (already accounts for group-admin overrides). Deletes both the `pulse_reels` row (`status='deleted'`) and its backing `pulse_posts` row (`status='deleted'`, `deleted_at`). Response: `{"ok": true, "message": "Reel deleted.", "reel_id": <id>, "trace_id": <id>}`.

Both routes already existed and are exercised by the WebView; this change only adds Native client wiring.

## 3. New/changed files

**New:**
- `mobile-native/src/api/contentOwnership.ts` — `resolveContentOwnerId()`, `resolveContentId()`, `isContentOwner()`. Canonical ownership resolution across every legacy/current field shape the backend has emitted (`user_id`, `author_id`, `owner_id`, `creator_id`, `uploader_id`, `posted_by`, `created_by`, plus nested `author`/`user`/`owner`/`creator` objects), and honors explicit server-computed flags (`can_manage`, `can_delete`, `is_owner`, `is_mine`, `is_author`) when present — this is what makes Reel ownership checks reuse the backend's own `can_manage` flag instead of re-deriving it client-side.
- `mobile-native/src/api/deleteErrors.ts` — `describeDeleteError(err, kind)`. Centralized, specific user-facing copy for 401/403/404/409/422/429/5xx and offline/network failures, shared by every delete call site.
- `mobile-native/src/api/__tests__/contentOwnership.test.ts`, `deleteErrors.test.ts`, `deleteApi.test.ts` — 25 new tests.

**Changed:**
- `mobile-native/src/api/feed.ts` — added `deletePost(postId)`.
- `mobile-native/src/api/reels.ts` — added `deleteReel(reelId)`.
- `mobile-native/src/components/PostCard.tsx` — new `onDelete?: (post) => void` prop; overflow menu renders a destructive "Delete" item (only when the caller supplies `onDelete`) that shows a native `Alert.alert` confirmation ("Delete post? … This cannot be undone.") before invoking the callback. Added `menuActionDangerText` style (red).
- `mobile-native/src/screens/HomeScreen.tsx` — added `useAuth` + `isContentOwner`/`describeDeleteError` imports, `handleDelete()` (server-confirmed removal: call API → filter `posts` → `invalidateNativeSync` → clear `activePostId` if needed; error path sets the existing error banner). `onDelete` is only passed to `PostCard` when `isContentOwner(item, currentUserId)` is true.
- `mobile-native/src/screens/PostDetailScreen.tsx` — added `useAuth` + delete wiring; `handleDelete()` calls the API, invalidates sync, and navigates back on success (closing comment composer implicitly, since the whole screen unmounts); on failure, surfaces the mapped error and re-enables the UI. Gated by `isContentOwner(post, currentUserId)`.
- `mobile-native/src/screens/ProfileScreen.tsx` — added local `busyPostId` state and `handleDeletePost()` (mirrors `HomeScreen`'s pattern); gated by the screen's existing `owner` flag (true only when viewing your own profile with no `profileTarget`).
- `mobile-native/src/screens/ReelsScreen.tsx` — added `deleteReel`/`invalidateNativeSync`/`describeDeleteError`/`Alert` imports; `ReelMoreMenu` gained an `onDelete` prop rendering a "Delete Reel" `MenuAction`, gated by the same `reel.can_manage` flag already used for "Promote Reel". `confirmDeleteReel()` shows an `Alert.alert` confirmation; `handleDeleteReel()` calls the API, closes any open modal tied to that reel (`commentReel`/`reactionReel`/`musicReel`/`moreReel`), removes the reel from the `reels` list (which unmounts its `ReelPlayerCard`, automatically stopping video/audio playback and releasing the player via its existing `useEffect` cleanup), and invalidates sync. On failure, shows an `Alert.alert` with the mapped error message and leaves the list untouched.

## 4. Behavior implemented (mapped to mission requirements)

- **Visible only to the owner:** the Delete menu item/action is conditionally rendered only when ownership is confirmed (`isContentOwner` for posts, `can_manage` for Reels) — non-owners never see it.
- **Confirmation required:** every delete path shows a native `Alert.alert` destructive-style confirmation before calling the API. No first-tap deletes anywhere.
- **Duplicate-tap protection:** each call site checks/sets a busy lock (`busyPostId`, `busyId`, `deleting`) before issuing the request, and `PostCard`'s menu buttons are `disabled={busy}`.
- **Server-confirmed removal, no optimistic delete:** list/state mutation only happens inside the `try` block after the API call resolves successfully — on failure the list is left untouched and a specific error message is shown (matches the existing `handleHide`/`handleMute` idiom).
- **Cache reconciliation:** every successful delete calls `invalidateNativeSync(["activity", "notifications"], ...)` with a `pulse_post_deleted`/`pulse_reel_deleted` event, matching the existing propagation mechanism used by hide/mute/comment.
- **Reel player cleanup:** handled implicitly and correctly — removing the deleted reel from the `reels` array unmounts its `ReelPlayerCard`, whose existing `useEffect` cleanup already calls `releaseMediaPlayback`/`pauseAsync`/`stopAsync` and unloads any attached `Audio.Sound`. `handleDeleteReel` additionally closes the comment/reaction/music/more modals if they were open for that specific reel, and clears `moreReel`.
- **Post cleanup:** `PostDetailScreen.handleDelete` navigates back on success, which unmounts the comment composer and the whole detail screen; `PostCard`'s own inline comment composer/reaction tray close automatically since the post disappears from whichever list rendered it.
- **Error handling:** `describeDeleteError()` maps 401 (session expired → re-auth), 403 (not the owner), 404 (already removed), 409 (changed elsewhere, refresh), 422 (server validation message passthrough), 429 (rate limited), 5xx (retry later), and offline/network errors to specific copy; unmapped statuses fall back to the server's `message`.
- **Accessibility:** the Delete `Pressable` in `PostCard` reuses the existing `accessibilityRole="button"` / `accessibilityLabel` pattern already used for Hide/Report/Block/Mute (`Delete post <id>`).
- **No full rerenders/refetches:** deletion is a targeted `Array.filter` on existing state, exactly like the pre-existing hide/mute/not-interested implementations — no `load("refresh")` call is triggered on success.

## 5. Test coverage added

`mobile-native/src/api/__tests__/`:
- `contentOwnership.test.ts` (16 cases) — direct field resolution, legacy aliases, nested author/user/owner/creator objects, missing/invalid/zero/negative ids, explicit permission-flag precedence over id comparison, missing-current-user handling.
- `deleteErrors.test.ts` (9 cases) — every mapped HTTP status (401/403/404/409/422/429/5xx), network/offline detection, generic fallback for both `Error` and non-`Error` thrown values.
- `deleteApi.test.ts` (4 cases) — `deletePost`/`deleteReel` call the correct existing route + `DELETE` method and propagate errors, verified against a mocked `pulseApi` client.

**Full suite result:** `npx jest` → 7 suites, 62 tests, all passing (25 new, 37 pre-existing, no regressions).
**Type check:** `npx tsc --noEmit` → clean, no errors.
**Whitespace:** `git diff --check` on every changed/added file in this mission → clean.

No UI-render/snapshot tests were added for the modified screens (`HomeScreen`, `ProfileScreen`, `PostDetailScreen`, `ReelsScreen`, `PostCard`) because none of the pre-existing screens in this codebase have render-level test coverage today (only `LoginScreen.test.tsx` does, using heavy manual mocking of navigation/session/biometrics) — adding that scaffolding for four large screens was judged out of proportion to this change and is called out below as a gap rather than silently skipped.

## 6. Verification performed

- `npx tsc --noEmit` — clean.
- `npx jest` — 62/62 passing.
- `git diff --check` on the exact set of files touched by this mission — clean, no whitespace conflict markers.
- Manual code-path review of every new function against the actual backend response shapes (`{ok, message, post_id}` / `{ok, message, reel_id, trace_id}`), not the mission's illustrative (and inaccurate) `DeleteContentResponse` sketch.

## 7. Not performed / explicit gaps (flagged, not silently skipped)

- **Physical iPhone QA with two real logged-in accounts performing real, irreversible deletes** (mission Section 21's 31-step script) was **not executed autonomously**. Permanently deleting data and confirming irreversible destructive UI actions are both outside what I can perform without your explicit, per-action confirmation, and this mission's QA script requires exactly that against production accounts. If you want this run, I can either (a) walk through it live with you approving each delete, or (b) you run the script yourself using the flows now in place.
- **Search and Saved-content surfaces** (`SearchScreen.tsx`, `SavedScreen.tsx`) do not render the shared `PostCard`/`ReelPlayerCard` components — they use their own lighter-weight list-item cards and route "open" elsewhere. Delete was not added there in this pass; they were audited and confirmed out of scope for this change rather than missed.
- **Notification deep-links** (`NotificationCenterScreen.tsx`) route into `PostDetailScreen`/Reels for the actual content view, so Delete is reachable there transitively, but the notification list item itself has no inline Delete action.
- **`GroupsScreen.tsx`** renders group posts with its own local `GroupPostCard` component (not the shared `PostCard`), which already gates its own admin/moderation actions behind `group.can_manage`; it was left untouched since it's a materially different component and ownership model (group admin vs. content author).
- **Profile Reels tab:** `ProfileScreen.tsx` has no Reels tab (`TabKey = "posts" | "media" | "about"`), so Reel deletion from a profile isn't reachable there — only through the main Reels viewer, which is fully wired.

## 8. Commits

- `mobile-native/src/api/{feed,reels,contentOwnership,deleteErrors}.ts` and their tests: typed delete API + shared ownership/error helpers.
- `mobile-native/src/components/PostCard.tsx`: owner-gated destructive Delete menu item.
- `mobile-native/src/screens/{HomeScreen,PostDetailScreen,ProfileScreen,ReelsScreen}.tsx`: wiring, confirmation, cache invalidation, player/modal cleanup.

(See git log for the actual commit messages used.)
