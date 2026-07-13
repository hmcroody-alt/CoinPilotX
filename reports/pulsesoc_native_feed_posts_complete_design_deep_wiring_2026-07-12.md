# PulseSoc Native Feed Posts — Design and Deep-Wiring Mission

Date: 2026-07-12

Active subsystem: Feed Posts

## Outcome

The existing native `HomeScreen`, `PostCard`, `PostDetailScreen`, feed API, media viewer, cache, event-sync layer, and navigation were refined in place. No second feed, post model, reaction client, comment client, media viewer, cache, realtime layer, or WebView was introduced.

This mission materially improves Feed Posts, but Feed Posts are **not ready to freeze**. Production poll/community/repost-specific payloads, native comment replies, owner edit/delete, direct report/block mutations, inline video playback, link cards, music cards, controlled realtime/offline reconciliation, and the complete requested device matrix remain incomplete or unproven.

## Production and native inspection

- Production feed UI: `bot.py` Home template and feed runtime around the production tabs and post card scripts.
- Production ranking/contracts: `services/pulse_feed_engine.py`; canonical reactions, post types, ranking, privacy and moderation remain server-owned.
- Production APIs: `/api/pulse/feed`, `/api/pulse/posts/:id`, react, save, repost, hide, comments, follow and mute routes.
- Native feed: `mobile-native/src/screens/HomeScreen.tsx`.
- Native card/media/comment surface: `mobile-native/src/components/PostCard.tsx`.
- Native detail/comments: `mobile-native/src/screens/PostDetailScreen.tsx`.
- Native API/cache/normalization: `mobile-native/src/api/feed.ts`.

## Production-to-native matrix

| Capability | Reused contract/component | Current result | Classification |
|---|---|---|---|
| Feed load/filter/pagination/refresh | `listFeed`, `FlatList`, `onEndReached`, `RefreshControl` | Existing server-ranked flow retained | Code-path verified |
| Filter restore | AsyncStorage + existing tabs | Selected filter now persists across relaunch | Code-path verified |
| Cache/offline fallback | `loadCachedFeed` | Existing per-filter cache retained | Code-path verified |
| Text/long text | `PostCard` | Multiline retained; long text now expands/collapses | Code-path verified |
| Images/gallery | shared `NativeMediaViewer` | One image remains cinematic; 2–4 images use stable grid; >4 count overlay | Code-path verified |
| Video/other media | shared media viewer | Opens canonical viewer; true inline feed playback remains missing | Incomplete |
| Reactions | canonical production reaction endpoint/set | Long-press picker, replace, remove, optimistic rollback and authoritative counts corrected | Code-path verified |
| Comments | canonical comments endpoint | Inline send/preview and detail composer retained; failed text preserved | Code-path verified |
| Replies | no native reply mutation inspected | Not invented | Blocked by native integration gap |
| Share | native OS Share + canonical deep link | Existing behavior retained | Code-path verified |
| Save/repost/follow | existing server mutations | Optimistic UI with rollback retained | Code-path verified |
| Report/block | existing Safety Hub navigation | Visible menu routes to server-authoritative safety surfaces | Code-path verified |
| Hide/mute | existing direct mutations | Removes affected content after success | Code-path verified |
| Realtime | shared invalidation registry | Refresh invalidations retained; full post event reconciliation unproven | Incomplete |
| Poll/community/music/link/repost cards | production supports several shapes | Canonical fields are not sufficiently normalized/rendered natively | Incomplete |
| Owner edit/delete/analytics | production routes exist | Not exposed by current native ownership model | Incomplete |

## Design and interaction changes

- Removed fabricated reaction-avatar dots and replaced them with real reaction-type summaries derived from server counts.
- Added a compact long-press reaction selector using production reaction keys.
- Corrected selected-reaction removal and reaction replacement in Home and Post Detail optimistic state.
- Added accessible selected/busy states to the primary reaction control.
- Added long-body Read more / Show less behavior.
- Added a responsive two-column image gallery with a remaining-item overlay.
- Removed the visible photo-comment button because its handler only announced unavailability.
- Kept the production action order: reaction, comment, repost, share, save.
- Preserved report, hide, block and mute in the post menu.
- Persisted the selected feed filter.

## Verification

- `npm run --prefix mobile-native typecheck`: passed.
- `python3 scripts/pulsesoc_native_feed_posts_complete_audit.py`: passed.
- `npm ci --prefix mobile-native --no-audit --no-fund --progress=false`: passed.
- Expo Doctor: 17/17 passed.
- Release iOS Simulator build with embedded updated Feed bundle: passed; bundle contains the new reaction selector.
- Simulator Home launch/deep link: passed. The authenticated session retained Status fixtures and the captured Home viewport did not expose an actual Feed card, so card interaction is not classified simulator-verified.
- Connected iPhone 16 Pro Release build using development identity: passed.
- Connected iPhone install and launch as `com.pulsesoc.nativeapp.dev`: passed.
- Physical scrolling, reaction picker, media, comment keyboard and share interaction were not observable through the command-line launch result and remain physical-device-only.

Older broad audits remain stale and were not counted as mission passes: they require removed placeholder copy (`Open in PulseSoc`), exact historical formatting of Safety Hub navigation, deprecated Composer Poll/Location/Mention strings, and an old roadmap heading. The full-wiring inventory also flags current Home drawer and Composer gaps outside Feed Posts. These failures were not suppressed or “fixed” by restoring dead controls.

## Honest completion percentages

| Area | Completion |
|---|---:|
| Overall Feed capability | 68% |
| Feed UI design | 82% |
| Visual quality | 80% |
| Interaction | 72% |
| Deep wiring | 66% |
| Filters | 78% |
| Post card | 86% |
| Text | 88% |
| Images | 82% |
| Video | 48% |
| Music | 30% |
| Links | 30% |
| Reposts | 52% |
| Communities | 38% |
| Polls | 20% |
| Reactions | 88% |
| Comments | 70% |
| Replies | 15% |
| Sharing | 72% |
| Saving | 82% |
| Follow/unfollow | 78% |
| Edit/delete | 10% |
| Report/mute/block | 65% |
| Analytics | 5% |
| Realtime | 38% |
| Offline/reconnect | 48% |
| Loading/empty/error | 70% |
| Accessibility | 76% |
| Responsive behavior | 78% |
| Performance | 74% |
| Xcode Simulator QA | 20% |
| iPhone 16 Pro QA | 15% |
| Device-size coverage | 12% |
| Backend/business reuse | 94% |
| Frontend utility reuse | 92% |
| Existing native component reuse | 96% |

## Evidence

`reports/screenshots/native-feed-posts-complete-design-deep-wiring-2026-07-12/`

The user-provided concept is stored as a design reference only. It is not claimed as runtime evidence.

- `design-reference-not-runtime-evidence.png`: user concept only.
- `pro-feed-home-shell.png`: fresh Release simulator Home/filter-shell capture; not a Feed-card interaction claim.

Physical-device evidence: build/install/launch command results only; post-change screenshots are not available through the installed device CLI.

## Freeze and replacement readiness

- Simulator parity freeze: **NO**.
- Physical-device freeze: **NO**.
- WebView Feed replacement readiness: **NO**.
- WebView/native parallel compatibility: **YES**; no production backend or WebView files were modified.
- Production Feed controls removed: **NO**. One unsupported native-only photo-comment affordance was removed because it was a dead control, not a working production capability.
- Major Feed sections moved: **NO**.
- All visible native Feed controls wired: **YES for the controls retained in this pass**; missing production capabilities are documented rather than rendered as inert controls.

## Next exact Feed mission

Remain on Feed Posts. Extend canonical normalization and rendering for poll, community, repost, link and music payloads; add native owner authorization plus edit/delete; implement comment replies; then run controlled-backend reaction/comment/save/follow/moderation/realtime/offline tests and the full compact/standard/Pro/Pro Max/physical-device matrix.
