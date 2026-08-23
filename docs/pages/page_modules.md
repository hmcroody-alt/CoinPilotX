# Page modules — a tab is a promise

`TYPE_TABS` in `services/pulsesoc_pages.py` is the **ceiling** for a page type, not a
promise that each tab has something behind it. An ARTIST page can show
`posts / music / videos / merch / about`; a brand-new artist page has none of music,
videos or merch. Rendering all five would hand the visitor three buttons that lead to
"Nothing here yet." — the definition of a dead end.

## Two different kinds of empty

Keep these apart, because they get opposite treatment:

- **Unbacked** — the module is real and this page has not set it up yet. Music with no
  `music_artist` link; merch with no shop. Hidden from visitors, shown to the team as a
  setup prompt with the one screen that fills it.
- **Unrenderable** — nothing anywhere can draw this tab. `services` was this for months:
  no link source, no rows, no branch in `PageScreen`. It was hidden from visitors and so
  looked handled, but `_visible_tabs()` hands the team the *whole ceiling*, so every
  business owner had a Services tab that opened onto a blank screen.

The second kind is now impossible rather than merely absent. `RENDERABLE_TABS` names
every tab `PageScreen` has a branch for, and `module_availability()` raises on anything
outside it instead of recording it as unavailable. Adding a tab to a type's ceiling and
teaching a screen to draw it are now the same change. `TabCeilingTests` holds both
directions of that invariant.

## How availability is decided

`module_availability()` classifies every tab in the type's ceiling:

- **Always backed** — `home`, `posts`, `about`. The page row itself is the data.
- **Link-backed** — `music`, `shop`, `merch`, `menu`. Backed only when the matching
  `link_type` exists in `pulse_page_links` (`TAB_LINK_SOURCE`).
- **Content-backed** — `videos`. Backed by the presence's own `pulse_posts` rows whose
  `post_type` is in `VIDEO_POST_TYPES`, so it needs no link.
- **Link-and-flag-backed** — `events`. Needs the `business_os` link *and*
  `events_enabled()`, because with `BUSINESS_OS_EVENTS` off the whole events domain
  raises 503 and a linked business would otherwise raise a tab that cannot load.

There is no fifth category. A tab with no rule is a bug, and raises.

`services` is gone from BUSINESS, PROFESSIONAL_SERVICE and LOCAL_BUSINESS; those types
get `shop`. Marketplace already carries `service` and `booking` listing types, so the
catalogue exists — a separate services module would be a second commerce backend with
its own listings, payments and moderation to keep in sync.

The counts and the links `public_view` already fetched decide all of it; no extra
queries. The modules themselves stay lazy.

## Shop

The `store` link's `ref_id` is a marketplace `seller_user_id`, surfaced on `public_view`
as `shop_seller_id`. The shop/merch/menu tab reads
`/api/pulse/marketplace/search?seller_user_id=` — the presence's real listings from the
one canonical marketplace, never a second inventory table and never the global browse
screen.

## Who sees what

`_visible_tabs()` splits the audience:

- **Public viewers** get only backed tabs. An unbacked module is invisible, not empty.
- **Team members** (any role) get the full ceiling. For them an empty module is a
  setup prompt — the only place in the product that tells them the module exists.
  This is why an unrenderable tab in the ceiling is a live defect and not a
  harmless placeholder: visitors never saw `services`, and the owner always did.

`PageScreen` says an empty module differently to each audience. A visitor gets the fact
and nothing else — a control they cannot use is noise dressed up as help. The team gets
the same sentence plus the one screen that fills it: Connections for music and for
connecting a shop, the editor for an empty About, Manage for a first post or video. A
connected shop with no listings offers nothing, because listings are created in
Marketplace and a second door into it would be a second thing to keep in sync.

`public_view` returns both `tabs` (what to render) and `modules` (the availability
map). The client renders `tabs` as delivered and never widens the set: the server is
the side that knows what is backed.

## Loading

Modules load when their tab is opened, not with the page. The root payload stays
light, and a module failure is contained: a music catalogue outage leaves the rest of
the presence usable, shows "We couldn't load this section." and offers **Try Again**.

`useLazyModule()` in `PageScreen.tsx` is that behaviour for every lazy module. Its
fetch guard is a ref keyed on `presence id + retry counter`, never the module's own
state: an effect that depends on the state it sets cancels its own in-flight request on
the first re-render and hangs on the spinner forever.

## Music linkage

`page_music()` reads the `music_artist` link and asks the canonical catalogue
(`services/music_service`) for that artist's tracks. The presence stores a pointer,
never a copy of the discography.

- No link → `{"linked": false, "tracks": []}`. The catalogue is not queried at all,
  and the page name is **not** used as a guess — that would attribute a stranger's
  songs to this presence.
- Catalogue failure → `PageError(503)`, surfaced as "We couldn't load this section."
  A failure is never converted into an empty discography, which is a different and
  false statement.

## Events linkage

A presence connects to **the business that runs its dates**, not to each date. The
`event` link type is gone from `LINK_TYPES` entirely — it was declared and never
resolvable, and even working it would have needed re-doing every tour date. The
`business_os` link is the claim that can be checked (`business_os_business.owner_user_id`,
the owner only — pointing a presence at a business is an identity claim, not an
operational task) and it keeps answering as the calendar changes.

`page_events()` reads that link and calls `events_service.list_public_events()`. Two
things are new there and both are the point of the module:

- **A stranger read exists.** `list_public_events` takes no actor, filters to
  `status = 'published'` in SQL so an unpublished event is never *loaded*, drops events
  that have already ended, and returns `_event_visitor()` rows.
- **`_event_visitor` is a field allowlist**, not a blocklist. `event_id`, `title`,
  `description`, `venue`, `starts_at`, `ends_at`, `status`, `currency`, plus ticket
  tiers reduced to `ticket_type_id / name / price_cents / sold_out`. Organiser identity,
  the owning business id, attendees and every sales count are absent. A column added to
  `business_os_events` later is invisible to visitors until somebody decides it is
  public — the safe direction for that mistake to fail in. `_event_manage()` is the
  other half: the stored row, for a caller who holds a role on the business.

`starts_at` / `ends_at` are unvalidated free text server-side, so no SQL date comparison
can be trusted. "Has it ended" is decided in Python over a bounded 500-row window
(`PUBLIC_EVENT_SCAN_CAP`), `ends_at` beats `starts_at` so a festival is still on on its
second day, a zoneless timestamp reads as UTC, and anything unparseable counts as *not*
ended and sorts last. `PageScreen` shows an unparseable date back exactly as it was
typed, because "Late summer 2026" was meant to be read.

`page_events()` returns `enabled` and `linked` as **separate** flags, and the client
branches on both. They are different problems with different owners: `enabled: false` is
the environment, which nobody fixes from the app, so the team is told so and offered
nothing; `linked: false` is this presence, which the team can fix, so they get **Connect
a business**. A single `available: false` would send half the people who saw it to do
work that would not help. Neither flag is ever guessed — the client reads a missing one
as `false`, so an older server cannot be mistaken for a working events domain.

The `business_id` the link stores is deliberately **not** returned to the client. The
shop tab publishes `shop_seller_id` only because it must deep-link into Marketplace;
events need no such handle, so the internal key stops at the server and a caller who
never receives it cannot walk it into the Business OS management endpoints.

Event rows render as plain views, not pressables. This build has no event detail screen,
and a row that lifts under the finger and then does nothing is exactly the defect this
module exists to avoid.

Adding a module means adding a link type and its `TAB_LINK_SOURCE` entry — not a new
tab renderer that fabricates content.
