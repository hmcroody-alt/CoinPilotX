# Business Pages

Business pages cover BUSINESS, BRAND, STORE, RESTAURANT, PROFESSIONAL_SERVICE,
LOCAL_BUSINESS, NONPROFIT, ORGANIZATION, MEDIA, SPORTS_TEAM, VENUE, EDUCATION and
OTHER — all the same canonical Page entity with a business-oriented tab set.

## Layout (native `PageScreen` — same shell as artist pages)

- Hero with cover, avatar, name, verification chip (real only), handle + type
  label, real follower/post counts.
- Actions: Follow / Share / Message / Manage (role-gated).
- Tabs from the server: typically home / services / shop / about.

## Tabs

- **home** — the page's real post feed (existing `pulse_posts` pipeline).
- **services** — real service/category/contact data from the page record; honest
  empty state otherwise.
- **shop** — links into the existing Marketplace via the page's `store` link
  (see page_marketplace.md). No parallel commerce system.
- **about** — description, category, contact fields, links.

## Management (native `PagesHubScreen`)

The hub lists the user's pages and shows the selected page's `manage_view`.
Buttons are gated by the capability set the server returns:

- View public page (always)
- Advertising → existing BusinessOS Advertising (requires `manage_ads`)
- Marketplace → existing Marketplace manager (requires `manage_marketplace`)
- Payments → existing BusinessOS Payments (OWNER)
- Team list (requires `manage_members`)
- Measured analytics (real counts, labelled as measured)
- Owner zone: Activate / Pause / Unpublish / Deactivate (confirm dialog; nothing is
  deleted) and Request verification (only while unverified)

## Public vs private

`public_view` never leaks member lists, invite state, audit history, or manage
capabilities. `manage_view` requires a role on the page.
