# Artist Pages

Artist pages are the ARTIST (and CREATOR / PUBLIC_FIGURE) presentation of the
canonical Page entity — same backend, artist-specific tab set.

## Layout (native `PageScreen`)

- **Hero**: cover image, avatar, page name + Verified chip (only if actually
  verified), `@handle · Artist`, real follower and post counts.
- **Actions**: Follow (server-authoritative toggle via
  `POST /api/pages/:id/follow`), Share (native share of the deep link), Message,
  and Manage — Manage appears only when the viewer has a role on the page.
- **Tabs**: rendered from the server's `page.tabs` array, not hardcoded client-side.
  Typical artist set: posts / music / videos / events / merch / about.

## Real data only

- Metrics are counted from real rows (`page_follows`, `pulse_posts.page_id`).
  There is no seeding, no inflation, no placeholder numbers.
- **posts** — real page posts from the existing content system.
- **music** — read-only link into the existing Music screen; Page OS never writes
  to the music subsystem.
- **events** — links into the existing Events system.
- **merch** — links into the existing Marketplace (see page_marketplace.md).
- **about** — real bio/category/contact fields, or an honest "Nothing here yet."

A tab with no backing data renders an honest empty state; it never fabricates
content to look busy.

## Deep link

`pulsesoc://pulse/pages/<handle>` and `https://pulsesoc.com/pulse/pages/<handle>`.
