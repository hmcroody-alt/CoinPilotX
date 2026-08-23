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

## The measured block (Overview)

`manage_overview()` builds the one place numbers are reported, and it is the
only place that decides what counts as a real one. It is assembled from data
`manage_view` has already loaded, so it costs no extra queries.

- **A total is always reported, including zero.** A presence with no followers
  has none; suppressing the metric until it flatters would make it mean "at
  least one".
- **A delta exists only where the server counted a window**, and `delta` and
  `window` travel together. Team has a total and no delta, because nothing
  records when a member joined — an invented "+0 this month" is a claim
  nobody measured. Clients must test for the `delta` *key*, never truthiness:
  a delta of 0 is a measurement.
- **`status` and `verification` arrive as words**, mapped from the two enums by
  `_STATUS_WORDS`/`_VERIFICATION_WORDS`. The hub used to print the columns raw
  — "Status: ACTIVE · unverified" — which is a database row read aloud.
- **`pending`** is the labels of sections *this role may act on* that have
  nothing behind them yet, derived from the same `sections` array the tiles
  come from. It cannot name work that is not offered, and it never tells an
  ANALYST to go and do something the server would refuse.
- **`note`** says what is deliberately not measured. Reach and engagement have
  no source wired, so they are absent and named rather than filled in with a
  plausible number.
- **There is one heading over these numbers.** An "Insights" section used to
  sit beside Overview carrying the same followers/posts/team counts, rendered
  from a different object and free to disagree with it. The percentage from
  `page_completeness` likewise appears once, in Overview; the checklist card
  below it lists only the *unfinished items*, which is the next question rather
  than the same answer.
- `overview` and `completeness` are management-only and are asserted absent
  from `public_view`.

## Hard rules

- Real metrics only — no fabricated followers, reviews, or analytics.
- Tabs render only with real backing data; empty states are honest. See
  [page_modules.md](page_modules.md).
- Discovery and admin inspection reuse the canonical search and admin gates. See
  [page_search_and_admin.md](page_search_and_admin.md).
- Additive migrations only; verification is never auto-granted; ownership transfer
  requires the typed confirm phrase and is audited.
