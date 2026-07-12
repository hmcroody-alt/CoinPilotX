# PulseSoc Native Home Interaction Parity

Date: 2026-07-11

## Preserved Workflows

- Pull to refresh reloads feed and status.
- Status Add/View routes into native Status surfaces.
- Composer Post/Reel/Live preserve server-authoritative publish, media upload, and Live handoff behavior.
- Composer Photo/Video/Music/Feeling/Location/Mention/Topic/Public remain visible and QA-addressable.
- Feed tabs call the existing feed API with matching tab keys.
- Feed cards preserve open detail, profile route, like/react, save, repost, share, report, hide, block, mute, follow, and now inline comment submit.
- Feed card `Comment` focuses the inline composer when available; `View all comments` still opens Post Detail for the full threaded surface.
- Inline comment submit reuses `/api/pulse/posts/:id/comments`, updates the preview/count from the server response, and invalidates Activity/Notifications.
- Feed card overflow exposes production safety/action controls without keeping every destructive action permanently visible on the card.
- Bottom navigation keeps Home/Reels/Create/Messages/Profile.

## Boundary Handling

- Marketplace/Poll/Question/More composer modes are shown as production-visible boundaries until the matching native backend contract is exposed.
- Music opens the existing PulseSoc media/music surface.
- Inline feed-card comment media/voice/attachment actions show explicit native unavailable states until matching production contracts are enabled on native cards.
- Post Detail remains the threaded comment destination and uses the same comment API wrapper as Home.
