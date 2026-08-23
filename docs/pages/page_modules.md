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

There is no fourth category. A tab with no rule is a bug, and raises.

`events` is absent from every ceiling. The `event` link type is refused outright: it has
no owner resolver, so nothing can say whose event a ref names, and
`services/business_os/events` lists only for a caller holding a manager role on the
business — there is no public read. A tab that 403s for every visitor is worse than no
tab, so events returns only together with a visitor-safe listing.

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

`useLazyModule()` in `PageScreen.tsx` is that behaviour for all three modules. Its
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

Adding a module means adding a link type and its `TAB_LINK_SOURCE` entry — not a new
tab renderer that fabricates content.
