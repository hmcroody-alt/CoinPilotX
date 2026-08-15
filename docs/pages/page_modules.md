# Page modules — a tab is a promise

`TYPE_TABS` in `services/pulsesoc_pages.py` is the **ceiling** for a page type, not a
promise that each tab has something behind it. An ARTIST page can show
`posts / music / videos / events / merch / about`; a brand-new artist page has none of
music, videos, events or merch. Rendering all six would hand the visitor four buttons
that lead to "Nothing here yet." — the definition of a dead end.

## How availability is decided

`module_availability()` classifies every tab in the type's ceiling:

- **Always backed** — `home`, `posts`, `about`. The page row itself is the data.
- **Link-backed** — `music`, `shop`, `merch`, `menu`, `events`. Backed only when the
  matching `link_type` exists in `pulse_page_links` (`TAB_LINK_SOURCE`).
- **Not yet wired** — everything else (`services`, `videos`, `reviews`). No canonical
  source exists, so availability is `False` and the tab does not reach the public.

One query over the links table decides all of it. The modules themselves stay lazy.

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

In `PageScreen.tsx` the retry is a counter (`musicAttempt`), not the module's own
state. An effect that depends on the state it sets cancels its own in-flight request
on the first re-render and hangs on the spinner forever.

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
