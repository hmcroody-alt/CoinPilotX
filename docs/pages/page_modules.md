# Page modules — a tab is a promise

`TYPE_TABS` in `services/pulsesoc_pages.py` is the **ceiling** for a page type, not a
promise that each tab has something behind it. An ARTIST page can show
`posts / music / videos / events / merch / about`; a brand-new artist page has none of
music, videos, events or merch. Rendering all six would hand the visitor four buttons
that lead to "Nothing here yet." — the definition of a dead end.

## How availability is decided

`module_availability()` classifies every tab in the type's ceiling:

- **Always backed** — `home`, `posts`, `about`. The page row itself is the data.
- **Link-backed** — `music`, `shop`, `merch`, `menu`. Backed only when the matching
  `link_type` exists in `pulse_page_links` (`TAB_LINK_SOURCE`).
- **Content-backed** — `videos`. Backed by the presence's own `pulse_posts` rows whose
  `post_type` is in `VIDEO_POST_TYPES`, so it needs no link.
- **Not yet wired** — `services`, `reviews`. No canonical source exists, so availability
  is `False` and the tab does not reach the public.

`events` is deliberately absent from every category. The `event` link type exists and
can be set, but `services/business_os/events` lists only for a caller holding a manager
role on the business — there is no public read. A tab that 403s for every visitor is
worse than no tab, so events stays hidden until a public listing exists.

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
