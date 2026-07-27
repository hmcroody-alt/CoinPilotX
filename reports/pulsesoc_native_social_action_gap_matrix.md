# PulseSoc Native — Social Action Gap Matrix

**Mission:** MISSION 1 — Complete Native Social Actions
**Scope audited:** `mobile-native/` (`App.tsx`, `src/`, `ios/`, `app.json`, `app.config.js`)
**Method:** three independent read-only audit slices, each required to cite `file:line` for every verdict. Disputed claims re-measured directly with grep before being recorded here.
**Date:** 2026-07-25

Verdict vocabulary is deliberately narrow, matching the mission's truthfulness constraint:

| Verdict | Meaning |
| --- | --- |
| **WIRED** | UI control exists, reaches a real backend endpoint, and handles failure. |
| **PARTIAL** | Reaches a real endpoint but is missing rollback, a catch, a call site on some surface, real pagination, or the inverse operation. |
| **MISSING** | No implementation. Types or endpoints may exist; nothing calls them. |
| **DEAD** | Implementation exists and is *unreachable* — no call site, or a call site whose result is discarded. |

A capability is not WIRED because a button renders.

---

## 0. Corrections established by re-measurement

Two claims from the first audit slice were **refuted** by the third slice and then confirmed refuted by direct measurement. Recording them because an audit that quietly overwrites its own earlier findings is not an audit.

**(a) `configurePulseShareCenter` is NOT test-only.** Slice 1 reported it called only from `src/sharing/__tests__/nativeShare.test.ts:65`, concluding copy-link was dead for feed posts. That slice grepped `src/` and never read `App.tsx`. Measured:

```
App.tsx:23    import { configurePulseShareCenter } from "./src/sharing/nativeShare";
App.tsx:121   configurePulseShareCenter(null);          // signed-out
App.tsx:124   configurePulseShareCenter((metadata) => { // signed-in
App.tsx:129   return () => configurePulseShareCenter(null);
```

The internal share center **is** registered in production, gated on `authState.status === "signedIn"`. Feed-post share therefore reaches `PulseShareScreen`, and copy link works. Corrected verdict below: share is WIRED, not PARTIAL.

**(b) The genuinely dead code is elsewhere.** Measured, zero call sites outside their own definition:

```
src/api/saved.ts:72        addSavedItem            → POST /api/pulse/saved   — DEAD
src/api/safety.ts:138      openSafetyWebFallback   → returns an object, opens nothing
src/screens/SafetyHubScreen.tsx:232, :333          → call it and discard the result
```

`SafetyHubScreen.tsx:232` ("Protected safety controls") and `:333` ("Open server mute controls") are visible, enabled buttons that produce no navigation, no toast, and no error. Those are the two worst placeholders found.

---

## 1. Master matrix — action × content type

Legend for surfaces: **H** HomeScreen, **PD** PostDetailScreen, **PR** ProfileScreen, **SE** SearchScreen, **SA** SavedScreen, **RE** ReelsScreen, **ST** StatusScreen, **MV** NativeMediaViewer, **RV** ReplayViewerScreen.

| Action | Feed Post | Reel | Status | Photo / Video (MV) |
| --- | --- | --- | --- | --- |
| Like | **WIRED** `PostCard.tsx:316` → `HomeScreen.tsx:307` → `api/feed.ts:267` | **PARTIAL** `ReelsScreen.tsx:278` → `api/reels.ts:278`; no concurrency guard | **WIRED** `StatusScreen.tsx:155` → `api/status.ts:234`, seq-guarded | **PARTIAL** `NativeMediaViewer.tsx:186` images only; `onLike` omitted by `StatusScreen.tsx:430` |
| Emoji reactions | **WIRED** `PostCard.tsx:309-312`, `:926 REACTIONS` | **WIRED** `ReelsScreen.tsx:790-792`, long-press `ReelPlayerCard.tsx:270` | **PARTIAL** `StatusActionRail.tsx:128`; **removal impossible by design** `api/status.ts:37-39`, `StatusScreen.tsx:159` | **MISSING** |
| Comment (create) | **WIRED** `PostDetailScreen.tsx:134` → `api/feed.ts:334`; inline path PARTIAL (see §2) | **WIRED** `ReelsScreen.tsx:452` → `api/reels.ts:244` | **PARTIAL — send-only** `StatusScreen.tsx:206` → `api/status.ts:256`; **no read API at all** | **MISSING** |
| Comment (read/list) | **WIRED** via `api/feed.ts:213 getPostDetail` | **WIRED** `api/reels.ts:234` | **MISSING** — no endpoint, no thread UI | **MISSING** |
| Nested reply | **MISSING** — types exist (`api/feed.ts:49 parent_comment_id`, `:65 replies`, normalized `:398`); `addPostComment:334-338` never sends a parent id; no reply control at `PostDetailScreen.tsx:266` | **WIRED** `ReelsScreen.tsx:459`, `:830 insertReply`, `:736 CommentThread` | **MISSING** | **MISSING** |
| Comment edit | **MISSING** — `can_edit` at `api/feed.ts:56`, no API, no UI | **WIRED** `ReelsScreen.tsx:480` → `api/reels.ts:259` | **MISSING** | **MISSING** |
| Comment delete | **MISSING** — `can_delete` at `api/feed.ts:57`, no API, no UI | **WIRED** `ReelsScreen.tsx:497` → `api/reels.ts:266` | **MISSING** | **MISSING** |
| Comment reactions | **MISSING** | **WIRED** `api/reels.ts:252 reactToReelComment` | **MISSING** | **MISSING** |
| Comment pagination | **MISSING** — all comments in one shot; `PostDetailScreen.tsx:196-261` has no `onEndReached`; `api/feed.ts:346 listPostComments` takes no limit/offset and is never called | **PARTIAL** — client-side slice only (`ReelsScreen.tsx:687 visibleCount`, `:708`); `getReelComments` sends no limit/offset | **MISSING** | **MISSING** |
| Mentions | **MISSING** — only tool is `PostCard.tsx:562-565`, which appends the literal `"☺"` | **MISSING** — plain `TextInput` `ReelsScreen.tsx:717` | **MISSING** | **MISSING** |
| Repost | **PARTIAL** — hardcodes `reposted: true` (`HomeScreen.tsx:343`, `PostDetailScreen.tsx:123`); button stays live and re-POSTs. **Dead tap on ProfileScreen** — `:311` passes no `onRepost`, and `PostCard.tsx:347` calls `onRepost?.` | **PARTIAL** `ReelsScreen.tsx:314` → `api/reels.ts:292`; reachable only from `ReelMoreMenu:804`, not the action rail | **MISSING** — rail has only React/Reply/Share (`StatusActionRail.tsx:75-121`) | **MISSING** |
| Undo repost | **MISSING** — no endpoint | **MISSING** — `:804` label reads "Reposted" but still calls `onRepost` → duplicate repost | **MISSING** | **MISSING** |
| Save | **WIRED** `api/feed.ts:277` with rollback | **WIRED** `api/reels.ts:285`, `result.saved` consumed `ReelsScreen.tsx:306` | **MISSING** | **PARTIAL** — rendered only when `onSave` passed (`NativeMediaViewer.tsx:334`); StatusScreen omits it |
| Unsave | **PARTIAL** — same toggle endpoint; real unsave exists only for the library (`api/saved.ts:88 removeSavedItem`, sole caller `SavedScreen.tsx:146`) | **PARTIAL** — same | **MISSING** | **MISSING** — one-shot `onSave`, no saved state in `NativeMediaViewerItem:14-34` |
| Share | **WIRED** `PostCard.tsx:362` → `HomeScreen.tsx:354` → `nativeShare.ts:76` → share center | **WIRED** `ReelsScreen.tsx:326` → `api/reels.ts:299` | **PARTIAL** `StatusScreen.tsx:188` → `api/status.ts:263`; **try/finally with no catch** → unhandled rejection, sheet never opens | **WIRED** `NativeMediaViewer.tsx:192` |
| Copy link | **WIRED** (corrected) via share center → `PulseShareScreen.tsx:68-71 Clipboard.setStringAsync` | **WIRED** same path | **WIRED** same path, url from `api/status.ts:299` | **WIRED** same path |
| Edit content | **MISSING** — no `editPost`/`updatePost`; only `createPost:222`, `deletePost:298` | **MISSING** | **WIRED** `StatusScreen.tsx:393` → `api/status.ts:270` | n/a |
| Delete content | **WIRED** `PostCard.tsx:501-512` → `api/feed.ts:298`, owner-gated `HomeScreen.tsx:661 isContentOwner` | **WIRED** `ReelsScreen.tsx:374/:400` → `api/reels.ts:272` | **WIRED** `StatusScreen.tsx:394` → `api/status.ts:278` | **MISSING** |
| Report | **PARTIAL** — navigates to SafetyHub prefilled `reportType:"post"`; actual POST at `SafetyHubScreen.tsx:99`. Absent on PD and PR | **PARTIAL** `ReelsScreen.tsx:420` → `api/reels.ts:320`, but `.catch(()=>undefined)` at `:422` and **no UI** | **WIRED** `StatusScreen.tsx:397` with error surface `:401` | **MISSING** |
| Notification generation | **PLACEHOLDER** (see §4) | **PLACEHOLDER** — only delete emits `invalidateNativeSync("reels_delete")` `:384` | **PLACEHOLDER** — `StatusScreen.tsx:118` only *listens*, never invalidates, not even on delete | **MISSING** |

**`ReplayViewerScreen.tsx` has zero social actions** — a back button only (`:100`).

---

## 2. Content-parity gaps by surface

The mission requires identical behavior across Feed, Reels, Statuses, Profile posts, Search results, Shared posts, Saved posts, and user timelines. Action visibility in this codebase is governed **purely by which callbacks a surface supplies**, plus the `detail` and `busy` props — there is no `readOnly`/`compact` prop. So an omitted callback silently removes a feature.

| Surface | PostCard rendered? | Gap |
| --- | --- | --- |
| `HomeScreen.tsx:630` | yes | full callback set — the reference surface |
| `PostDetailScreen.tsx:206` | yes, `detail` | no `onComment`, `onSubmitComment`, `onReport`, `onHide`, `onBlock`, `onMute`, `onFollow` |
| `ProfileScreen.tsx:311` | yes | **no `onRepost` → repost button renders and does nothing** |
| `SearchScreen.tsx:243` | **no** — own `SearchResultCard` | **zero social actions** |
| `SavedScreen.tsx:268` | **no** — own `SavedCard` | **zero social actions** — Open / Move / Remove only |
| `ContentPreviewRenderer.tsx:78-97` | yes | every callback is `noop` — intentional preview inertness, not a defect |

Additional inline-comment gap: `HomeScreen.tsx:366 handleInlineComment` is `try/finally` at `:369-398` with **no `catch`**. The count and preview it optimistically bumps never roll back. `ProfileScreen` and `PostDetailScreen` do not pass `onSubmitComment` at all.

---

## 3. Concurrency and failure handling

**Duplicate-request prevention is structurally insufficient in two of three content types.**

| Content | Guard | Defect |
| --- | --- | --- |
| Feed posts | `busyPostId` scalar (`HomeScreen.tsx:128`) → `busy` prop (`PostCard.tsx:307`) | a single scalar, not a pending set; HomeScreen only *checks* it for delete (`:482`) |
| Reels | `busyId` (`ReelsScreen.tsx:98`) | **only checked in `handleDeleteReei` (`:375`)**. React/save/repost set it and never read it → rapid taps fire concurrent requests, and the captured `previousCount` closure causes count drift |
| Statuses | `reactionSeqRef` per-status monotonic map (`StatusScreen.tsx:74`, bumped `:161`, stale discarded `:171`/`:174`) | **correct** — this is the pattern to promote |

Rollback: present throughout Reels (`:294`, `:308`, `:320`, `:416`, `:506`, `:520`) and Status (`:175`). Missing on the feed inline-comment path.

**Silent swallows — worse than an alert, because the user gets no signal at all:**

```
ReelsScreen.tsx:422   reportReel          .catch(() => undefined)
ReelsScreen.tsx:625   reportReelComment   .catch(() => undefined)
ReelsScreen.tsx:580   trackReelView       .catch(() => undefined)
ReelsScreen.tsx:785   onToggleReplies={() => undefined}   (empty handler)
StatusScreen.tsx:188  handleShare         try/finally, no catch
HomeScreen.tsx:366    handleInlineComment try/finally, no catch
```

Only Status surfaces a human-readable reaction error, via `api/status.ts:242 describeStatusReactionError`.

---

## 4. Notification generation — honest verdict

`invalidateNativeSync` (`core/eventSync.ts:98-115`) is a **purely local cache-invalidation bus**: it dedupes subsystems, unions the handlers registered through `registerSyncInvalidation`, awaits them, and returns. No `pulseApi`, no `fetch`.

But `eventSync.ts` as a whole is *not* local-only, and this is where the earlier "placeholder" label was too harsh:

```
core/eventSync.ts:79    DEFAULT_SYNC_ENDPOINT = "/api/pulse/sync/events"
core/eventSync.ts:195   fetchDeltaEvents → pulseApi(endpoint?after_id&after&limit=100)
core/eventSync.ts:136   startNativeEventSync — interval poll (min 15s, default 45s) + AppState active
core/eventSync.ts:297   shouldFallbackToFullRefresh — full resync on 404/405/501/503
```

Client-side notification *creation* does not exist and should not: `api/notifications.ts` exposes read/mark/delete/preferences only (`:57`, `:65`, `:70`, `:77`, `:85`, `:92`, `:98`, `:102`, `:109`, `:113`). Notification rows are created server-side as a side effect of the like/comment/repost endpoints. *(NOT VERIFIED — the `bot.py` handlers for those endpoints were not traced.)*

Push transport is **genuinely wired end-to-end**: `api/push.ts:140` POSTs `/api/push/subscribe` with `apns_token`/`fcm_token`/`device_id`; `:236` POSTs `/api/push/unsubscribe`; `App.tsx:86-93` registers on sign-in and re-syncs on AppState active; `app.json:24-28` declares `remote-notification`. Note `api/push.ts:73` — hard no-op unless `Device.isDevice`.

**The actual defect is narrow and fixable:** engagement actions do not invalidate the notification/activity subsystems. Existing `invalidateNativeSync` call sites:

```
AppNavigator.tsx:169  notification_received
HomeScreen.tsx:387 home_comment  :414 home_follow  :433 home_hide
                   :465 home_mute  :488 home_delete  :591 home_publish
PostDetailScreen.tsx:161  post_detail_delete
ReelsScreen.tsx:384       reels_delete
ProfileScreen.tsx:229     profile_delete
```

**No invalidation on like, react, save, repost, or share.** After a like, the badge and activity inbox refresh only when the 45-second poll happens to return a matching event.

Unverified risk: `app.config.js` derives `expoProjectId` from `EXPO_PUBLIC_EXPO_PROJECT_ID` defaulting to `""`; an empty project id breaks `getExpoPushTokenAsync`. Whether any build profile sets it is NOT VERIFIED.

---

## 5. Sharing subsystem

**Native share is real, and richer than "copy a URL"** — the mission's stated concern is already addressed for the primary path. `sharePulseObject` (`nativeShare.ts:76-81`) consults the module-level presenter first and only falls through to `openSystemShare`:

```ts
export async function sharePulseObject(metadata: PulseShareMetadata): Promise<ShareAction> {
  if (shareCenterPresenter?.(metadata)) return { action: Share.sharedAction };
  return openSystemShare(metadata);
}
```

`PulseShareScreen.tsx:128-147` offers six actions: Send in PulseSoc, Status/Story, Create Reel, Copy link, QR code, More apps. `shareComposerHandoff.ts` is live (`PulseShareScreen.tsx:96 saveShareComposerHandoff` → `HomePulseComposer.tsx:187 consumeShareComposerHandoff`, `:192 mergeShareIntoComposerBody`), with origin validation at `shareComposerHandoff.ts:70-81` (https, `pulsesoc.com` or subdomain, 15-minute max age).

**Two real gaps:**

1. **Signed-out and pre-navigation fall-through.** `shareCenterPresenter` is `null` while signed out (`App.tsx:121`) and returns `false` if `navigationRef.isReady()` is false. Both cases silently degrade to the OS sheet, so copy link becomes unreachable.

2. **No media is ever attached.** `buildNativeSharePayload` (`nativeShare.ts:54-68`) returns `{ title, message, url }` and never reads `metadata.previewImageUrl` (declared `:21`, consumed only by the in-app `<Image>` at `PulseShareScreen.tsx:118`). Repo-wide, `src/` has **zero** hits for `expo-sharing`, `Sharing.shareAsync`, `MediaLibrary`, `saveToLibraryAsync`, `createAssetAsync`. The mission requires sharing "images, videos" — that is **MISSING**. Internal sends are text-only too (`PulseShareScreen.tsx:73-91` puts `payload.message` in the message body).

---

## 6. Deep links and Universal Links

**Client configuration is complete. The server is not serving the association files.**

```
src/navigation/linking.ts:7        prefixes: ["pulsesoc://", "https://pulsesoc.com"]
src/navigation/linking.ts:109      PostDetail   pulse/post/:postId
src/navigation/linking.ts:116      ReelDetail   pulse/reels/:reelId
src/navigation/linking.ts:122      StatusDetail pulse/status/:statusId
src/navigation/linking.ts:209      ProfileDetail pulse/profile/:profileKey
app.json:5                         scheme: "pulsesoc"
app.json:15-17                     ios.associatedDomains ["applinks:pulsesoc.com"]
app.json:33-48                     Android intentFilters autoVerify:true /pulse
ios/.../PulseSocNative.entitlements:5-10  associated-domains + aps-environment
ios/.../Info.plist:25-35           CFBundleURLSchemes pulsesoc, com.pulsesoc.app, …
ios/.../AppDelegate.swift:44-52    RCTLinkingManager continue userActivity
```

Shared URLs (`api/feed.ts:351 pulsePostUrl`, `api/status.ts:299 pulseStatusUrl`, asserted in `navigation/__tests__/canonicalObjectUrls.test.ts:15,17`) match those prefixes, so once the OS hands a URL to the app, routing works.

**Three blockers:**

1. No `apple-app-site-association` file exists in the repo; it is generated by `services/native_app_links.py:18`, which returns `None` unless `PULSESOC_APPLE_TEAM_ID` matches `^[A-Z0-9]{10}$`. `bot.py:107518` then serves **HTTP 503 `native_link_configuration_missing`**. Same for `assetlinks.json` (`bot.py:107534`, `native_app_links.py:46`, needs `PULSESOC_ANDROID_SHA256_CERT_FINGERPRINTS`). Without a 200-OK AASA, iOS never verifies the `applinks:` entitlement and taps open Safari.
2. `linking.ts:7` omits `www.pulsesoc.com`, and so does `app.json:15-17`. The sibling app declares both (`mobile/app.json:24-25`). A `www` link will not match.
3. `App.tsx:253` — `linking={authState.status === "signedIn" ? linking : undefined}`. **Deep links are inert while signed out**; there is no deferred-link replay after sign-in.

Verdict: the `pulsesoc://` custom scheme works today. Universal Links / App Links are **configured but non-functional**, pending two backend environment variables. Client MET, server NOT MET.

---

## 7. WebView — acceptance criterion already met

Grep of `WebView|react-native-webview|WebBrowser|openBrowserAsync|Linking\.openURL|InAppBrowser|expo-web-browser` across `mobile-native/src` → **no matches**. `package.json` → zero matches for `webview|web-browser`. The only hits anywhere are marketing prose at `store.config.json:88` and optional peer-dependency entries at `package-lock.json:5681-5694`.

`StatusViewerCard.tsx:213 styles.webButton` is a misleading *name* — it shares a link.

**"No WebView fallback exists for internal social actions" — MET, and it was met before this mission started.** The real problem was never WebView; it is missing and placeholder functionality.

Corollary worth noting: the app has **no outbound-URL primitive at all** beyond `Linking.getInitialURL`/`addEventListener` (`App.tsx:156-159`). So `ProfileScreen.tsx:361,366,370` "web links" and both `openSafetyWebFallback` buttons cannot open anything.

---

## 8. Accessibility coverage

Totals across `mobile-native/src`: `accessibilityLabel` 347 / 61 files, `accessibilityRole` 327 / 62 files, `accessibilityState` 71 / 38 files, **`accessibilityHint` 10 / 7 files**.

Coverage tracks exactly the surfaces previously audited. Best: `PostCard.tsx` (26 label / 24 role / 5 state), `ChatScreen.tsx` (34/21), `StatusActionRail.tsx` (7/5/3 — the only social hint in the app), `PulseShareScreen.tsx` (3/3, all six actions labeled at `:203`), `ReelPlayerCard.tsx` (5/6/1), `NativeMediaViewer.tsx` (5/4).

**Zero accessibility props, despite rendering social action controls:**

| File | Pressables | Unlabeled social controls |
| --- | --- | --- |
| `screens/LiveScreen.tsx` | 33 | react "Fire" `:628`, Share `:631`, mute `:606`, host profile `:613`, join `:622`, leave `:625` |
| `screens/MarketplaceScreen.tsx` | 27 | Save `:256`/`:332`, **Report `:259`/`:335`** — the app's only content-specific report control |
| `screens/EventsScreen.tsx` | 23 | Share `:162-164`, watch `:159`/`:235`, host profile `:150`/`:230` |
| `screens/SavedScreen.tsx` | 19 | the **only** surface reaching `removeSavedItem`/`moveSavedItem`/collection CRUD |
| `components/FeedComposer.tsx` | 13 | — |
| `components/reels/ReelPhotoSurface.tsx`, `ReelLiveViewerSurface.tsx` | — | zero |
| `screens/ProfileScreen.tsx` | 12 | one occurrence total, and it is `accessibilityLiveRegion` at `:298`, not a label. Share `:318-320` unlabeled |

---

## 9. Work queue implied by this matrix

Ordered by leverage. Items marked *(promote)* mean the implementation already exists in Reels and should move to shared code rather than be rewritten — a guarantee stated twice eventually disagrees with itself.

1. *(promote)* `src/social/commentTree.ts` from `ReelsScreen.tsx:821 findComment`, `:830 insertReply`, `:836 updateCommentTree`, `:840 removeCommentFromTree`, `:844 toggleSetValue`, `api/reels.ts:383 normalizeComments`/`countCommentTree` — all have zero Reels coupling.
2. *(promote)* shared recursive `CommentThread` from `ReelsScreen.tsx:736` with its per-node Reply/Like/Edit/Delete/Report rail (`:777-783`) and "View N replies" toggle (`:784`).
3. *(promote)* the Status concurrency pattern — `reactionSeqRef` (`StatusScreen.tsx:155-186`) + `describeStatusReactionError` (`api/status.ts:242`) — into a shared lock module, then adopt it in Reels' `handleReact` and replace the scalar `busyPostId`/`busyId` with a pending set.
4. Posts: nested replies, comment edit/delete, real server-side pagination, mentions.
5. Statuses: comment **read** API + thread UI, repost, save/unsave.
6. Undo repost for every content type; stop hardcoding `reposted: true`.
7. Fix the missing catches (`HomeScreen.tsx:366`, `StatusScreen.tsx:188`) and the silent swallows (`ReelsScreen.tsx:422`, `:580`, `:625`).
8. `ProfileScreen.tsx:311` — pass `onRepost` or hide the button; a dead tap is worse than an absent control.
9. Social actions on `SearchResultCard`, `SavedCard`, `ReplayViewerScreen`, and `NativeMediaViewer`.
10. Invalidate `notifications`/`activity` on like, react, save, repost, share.
11. Attach media to share payloads; handle the signed-out share fall-through.
12. Delete or implement `addSavedItem` and `openSafetyWebFallback`; fix `SafetyHubScreen.tsx:232`/`:333`.
13. Accessibility props on the seven zero-coverage surfaces.
14. `www.pulsesoc.com` prefix; deferred deep-link replay after sign-in.

Items 1–3 are prerequisites for 4–6, which is why they lead.
