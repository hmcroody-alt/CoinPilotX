# PulseSoc Native Home Interaction Parity

Date: 2026-07-11

## Preserved Workflows

- Pull to refresh reloads feed and status.
- Status Add/View routes into native Status surfaces.
- Composer Post/Reel/Live preserve server-authoritative publish, media upload, and Live handoff behavior.
- Composer Photo/Video/Music/Feeling/Location/Mention/Topic/Public remain visible and QA-addressable.
- Feed tabs call the existing feed API with matching tab keys.
- Feed cards preserve open detail, profile route, like/react, save, repost, share, comment, report, hide, block, mute, follow.
- Bottom navigation keeps Home/Reels/Create/Messages/Profile.

## Boundary Handling

- Marketplace/Poll/Question/More composer modes are shown as production-visible boundaries until the matching native backend contract is exposed.
- Music opens the existing PulseSoc media/music surface.
- Native comments route to Post Detail for semantic submit and threaded behavior.
