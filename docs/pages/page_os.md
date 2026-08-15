# Page OS — Canonical Architecture

**Naming:** the product surface is called **Presence** (a user's Artist or Business
identity). The code, tables and routes are named `page`/`pages`. They are the same
system — there is no second implementation behind the product name, and adding one
would break the invariant this document exists to protect.

One canonical Page system serves all 16 page types. There are no per-type backends,
tables, or route families: an Artist page and a Restaurant page are the same entity
with different `page_type` and different server-decided tabs.

## Core invariant: PERSON ≠ PAGE ≠ STORE

- A **person** is a row in `users` and authenticates once.
- A **page** is a row in `pulse_pages`, owned by a user via `page_members` (role OWNER).
- A **store** is the existing marketplace seller surface; a page *links* to it, never
  replaces it.

One user owns any number of pages. There are no fake accounts, no separate logins,
no shadow identities.

## Page types (closed set of 16)

ARTIST, CREATOR, PUBLIC_FIGURE, BUSINESS, BRAND, STORE, RESTAURANT,
PROFESSIONAL_SERVICE, LOCAL_BUSINESS, NONPROFIT, ORGANIZATION, MEDIA, SPORTS_TEAM,
VENUE, EDUCATION, OTHER.

The type controls presentation (tab set, labels) — never a different backend.

## Lifecycle — no hard delete

`ACTIVE → PAUSED → UNPUBLISHED → DEACTIVATED`. Every transition is recorded in
`page_audit_log`. Nothing is deleted; history stays auditable. UNPUBLISHED and
DEACTIVATED pages 404 for non-members on public endpoints.

## Backend surface

- `services/pulsesoc_pages.py` — all business logic. Lazy `ensure_tables()` (additive,
  idempotent), `PageError(status_code)` for typed failures, `PERMISSIONS` matrix,
  `public_view`/`manage_view` separation.
- 16 routes in `bot.py` under `/api/pages/*` (see per-doc references).
- Content: page posts go through the **existing** `pulse_posts` pipeline with a
  `page_id` column — no new post tables, no feed-ranking changes. Attribution happens
  at serialization time in `pulse_feed_engine` (`_page_author`).
- Sentinel: observe-only `page` entity + `owns_page` edge, emission wrapped in
  try/except so Sentinel outages never break page writes. Sentinel never auto-seizes
  or auto-verifies.

## Native surface (mobile-native)

- `src/api/pages.ts` — typed client mirroring server shapes.
- `PageCreateScreen`, `PageScreen` (universal public shell), `PagesHubScreen`
  (management), FeedComposer identity switcher ("Posting as <Page>").
- Deep links: `pulse/pages/create`, `pulse/pages`, `pulse/pages/:handle`
  (static paths declared before the catch-all).

## Hard rules

- Real metrics only — no fabricated followers, reviews, or analytics.
- Tabs render only with real backing data; empty states are honest. See
  [page_modules.md](page_modules.md).
- Discovery and admin inspection reuse the canonical search and admin gates. See
  [page_search_and_admin.md](page_search_and_admin.md).
- Additive migrations only; verification is never auto-granted; ownership transfer
  requires the typed confirm phrase and is audited.
