# PulseSoc Native Home Feed Interaction + Media Handoff QA

Date: 2026-07-09

## Scope

This pass stayed focused on Home feed QA/hardening only. It did not add new Home product features, did not start final UI/UX polish, did not focus on Android, and did not touch production WebView routes.

## Runtime

- Built-in QA browser: used visibly.
- Native web QA URL: `http://127.0.0.1:8094`.
- Local API proxy: `http://127.0.0.1:5108`.
- Disposable local backend: `http://127.0.0.1:5107` with temporary SQLite data.
- QA account: runtime-only local account. No password was committed or written to reports.

## Seeded QA Fixtures

The local backend used disposable fixtures only:

- `Home Feed Interaction QA`: text post for like/comment/save/share/follow/report/hide/block/mute routing.
- `Home Media Handoff QA`: image post with one `chat_media_uploads` row attached through `media_ids_json`.
- `Home Broken Media QA`: broken image URL row attached through the same backend media payload path.

All fixtures were consumed through the existing `GET /api/pulse/feed` contract. No native-only feed data was injected into the app.

## Visible QA Result

Result: passed with scoped hardening.

Roody could visibly watch the native Home feed render with seeded posts, action controls, image media, and broken-media coverage.

Visible checks completed:

- Home feed loaded authenticated through local QA backend/proxy.
- Like changed the first post to `fire 1`.
- Save changed the first post to `Saved`.
- Repost changed the first post to `Reposted` and created a server-side repost row.
- Comment opened `/pulse/post/1?title=Comments`.
- Card-level Post Detail opened `/pulse/post/1?title=Post`.
- Author/Profile and Follow controls opened `/pulse/profile/homefeedqa?title=Home%20Feed%20QA`.
- Share was clicked and did not crash or navigate away from Home in the web QA runtime.
- Report opened `/pulse/safety/reports?title=Report`.
- Block opened `/pulse/safety/blocks?title=Blocked%20Users`.
- Mute opened `/pulse/safety/mutes?title=Muted%20Users`.
- Hide removed only the selected local card and left the remaining feed intact.
- Refresh reloaded server-authoritative feed state and restored the locally hidden card.
- Image media opened `NativeMediaViewer` and closed cleanly.
- Broken media opened `NativeMediaViewer` and closed without crashing the feed.
- Add Status opened `/pulse/status?openCreator=true`.
- Reels navigation opened `/pulse/reels`.

## Backend Contract Evidence

Disposable local backend evidence:

- `pulse_reactions` contained one `fire` reaction for post `1`.
- `pulse_post_saves` contained one save row for post `1`.
- `pulse_saved_items` contained one saved post item for post `1`.
- `pulse_live_events` contained `reaction_added` and `post_reposted`.
- `GET /api/pulse/feed` returned:
  - `Home Media Handoff QA` with one image media payload.
  - `Home Broken Media QA` with one media payload for the broken-media safe path.

## Scoped Fixes

- Added stable QA selectors and accessibility labels to Home feed action controls and media thumbnails.
- Added stable QA selectors to `NativeMediaViewer` close/previous/next/share controls.
- Removed the outer feed card button role after visible QA exposed web nested-button warnings. The card still routes by press, while child actions remain proper buttons.

## Known Gaps

- Reply is not a direct Home-card action; reply remains inside Post Detail/comment flow.
- Follow from Home currently routes to Profile; it does not mutate follow state in this native Home card.
- Share is browser-verified as safe/no-crash. Native provider share sheets remain device-release QA.
- Add Status is route-verified, but its current web accessibility role is less explicit than the feed action buttons.
- Physical media playback/capture and large-video behavior remain device-release QA items.

## Conclusion

Home can remain foundation-complete. This pass proves the Home feed interaction surface is wired to native destinations and server-authoritative action APIs, with media handoff into `NativeMediaViewer` and safe broken-media behavior.
