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

### The rule has one implementation: `_load_visible_page`

`_load_page(conn, ident)` loads a row. `_load_visible_page(conn, ident, viewer_user_id)`
loads it *and* enforces the lifecycle rule. **Every public read goes through the
second one.** The distinction is not stylistic: the rule was originally written inline
at each route, so it was several copies that had to stay in agreement, and they did
not. `list_page_posts` called `_load_page` and did not take a viewer at all, so an
unpublished presence's posts and videos were readable by anyone who knew the id — the
page 404'd and its content did not.

`toggle_follow` is in the family for a subtler reason. It could plausibly have refused
a hidden page with 403 ("isn't accepting followers"), but that answer *confirms the
page exists* to anyone guessing ids, and the follow endpoint becomes the existence
oracle the lifecycle rule exists to close. A stranger gets 404; a team member, who is
already allowed to know it exists, passes the visibility check and receives the honest
403.

PAUSED stays public. It means "not accepting new activity", not "hidden" — collapsing
the two is how a presence taking a break disappears from the people already following
it.

Pinned in `tests/pages/test_page_os.py::HiddenPresenceTests`, which drives a
`PUBLIC_READS` table rather than one route, so a *new* public read that forgets the
rule fails a test instead of becoming another copy nobody compared.

### The honest 403 has to be shown to somebody

That 403 only ever reaches a team member, and for its whole life `PageScreen` threw
it away: `onFollow` caught every rejection into an empty block and the button lifted
under the finger having changed nothing. The screen also never read `status` at all,
so the presence its own team had unpublished still rendered a Follow button the server
was always going to refuse. Two faults, one symptom, and the symptom reads as "this
app is broken" rather than "this presence is not published".

The client now mirrors the server's own predicate — `ACTIVE || PAUSED`, `isPublic` —
and withholds the control instead of offering a dead one, saying which of the two
hidden states it is in, because unpublishing and deactivating are different acts with
different steps back. Refusals that do arrive are repeated in the server's words.
Share stays: the team has reason to copy their own link before launch, and the note
underneath says who it will open for.

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

### Creation lands on setup, and the handle verdict is not assumed to be current

`check_handle` echoes the candidate it answered about, and the wizard now
matches that echo against what is in the box before reading the verdict. It
did not, and the check is debounced by 450ms — so from the keystroke that
changed the handle until the answer came back, "Available." stayed on screen
over an address nobody had checked and Next stayed enabled. The wizard carried
that through Details and Review, and the server refused the create with a 409
three screens after the point where it could have been said. Answers are also
sequence-guarded, because a slow check for an earlier handle arriving after a
fast one for the current handle would otherwise blank a verdict already given.

Every flow now lands on `PagesHub` with the new page focused. The generic entry
point used to open the new presence's public page, which for a minute-old
presence is the emptiest screen in the app: no avatar, no cover, no posts, and
every module unbacked. Management already knows what to do about that — the
`sections` array carries `ready` and a `setup` line naming the one thing each
unready section is missing — so which door an owner came through no longer
decides whether they are shown their next step or a blank page.

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

## The type is visible before the words are

`src/theme/presenceAccent.ts` gives each page type a hue: creative violet,
commerce cyan, community magenta, enterprise teal. This is the same sentence the
top of this document already makes — the type controls presentation — reaching
the viewer through colour rather than through a tab set.

It is a `Record<PageType, PresenceHue>` and not a grouping function, for the
reason directly above: a grouping function needs a fall-through, the fall-through
is silent, and the types nobody thought about are exactly the ones it swallows.
A seventeenth page type fails the build here until somebody says what colour it
is. `presenceAccent()` does have a fall-through — to brand teal — but only for a
type string this build has never heard of, which is a thing a newer server can
genuinely send and which a screen cannot refuse to render over.

Two things are deliberately outside it. The Presence tile on the profile and the
hub header stay fixed brand teal (`presenceTheme`), because they stand for all of
a member's presences at once and so must stand for none in particular. And the
`Verified` badge stays brand teal on every page: a trust marker that is a
different colour on every page is one people stop reading.

`presenceAccent.test.ts` asserts no hex. It holds the table exhaustive, holds the
alpha ramp inside the restraint bands `profileNeon` documents, and holds every
hue to 4.5:1 on the three surfaces it is drawn on. That last one is what produced
the `ink` token: the obvious primary action is a bright fill with a white caption,
and white on teal is about 1.6:1 — so filled actions caption in the dark
background colour, and the only gradient in the system is a cover wash that
carries no text.

### Where it is drawn, and the rule that decides

Four surfaces, and they are the four where the question "which presence is this?"
is actually live:

| Surface | What carries the accent |
| --- | --- |
| `PageScreen` | Cover wash, avatar ring, handle line, active tab, link cards, the Follow fill and its caption |
| `PresenceHubScreen` | A spine down the left edge of each card, plus the avatar ring and initial |
| `PagesHubScreen` | The selected presence's chip — fill, border and name |
| `PageCreateScreen` | The chosen page type's chip, filled in `base` and captioned in `ink` |

The rule underneath is that **the accent means "which one", so on any surface
that offers a choice it appears on exactly one thing at a time.** The unselected
chips in both hubs and in the wizard stay neutral. Sixteen coloured chips in a
scrolling row is decoration, and worse, it takes the selection down with it —
if everything is coloured, colour has stopped answering the question. On
`PageScreen` there is no choice being offered, so the accent is free to run
through the whole page.

Two consequences worth stating, because both were mutation survivors before they
were rules:

- **The container carries it, not just the text.** A coloured word inside a chip
  highlighted in the app accent is two colours disagreeing about what is
  selected. Each surface's test asserts the fill or the border separately from
  the caption, because asserting only the caption let the fill silently revert.
- **It comes from `page_type`, per item.** Every one of these surfaces renders a
  list, so `presenceAccent(<one fixed type>)` passes any test that looks at a
  single card. Each suite renders an artist next to a restaurant and asserts the
  two differ.

`PageCreateScreen`'s flavour-category chips ("Music Artist", "Other Artist") are
deliberately left neutral: the word on those is a category, and the page type
behind it is not what the chip is naming. They keep the shared
`typeChipActive`/`typeChipTextActive` styles, which is why those styles still
exist — the page-type branch no longer layers over them, because both of their
values were being overwritten and their presence only suggested they still
decided something.

## The joins are tested, not just the calls

`tests/pages/test_page_os.py` tests one call at a time. The two defects this
work exists to close do not live in one call — real backend capability that
nothing reaches, and controls with nothing behind them, both survive a suite
where every piece is individually correct and the agreement between them is
what fails. `tests/pages/test_page_journeys.py` walks a presence from empty to
furnished, from both sides, and pins three joins:

- **The advertised tab is the readable tab.** `module_availability` decides
  which tabs a viewer is offered; a different function answers each tab. A tab
  a stranger is offered must not report itself unconnected when opened.
- **The setup line resolves.** `SETUP_RESOLUTIONS` carries out the one thing
  each unready section's `setup` names and asserts the section flips to
  `ready`. The table is asserted exhaustive against the sections the three
  representative page types actually produce, so advice that leads nowhere
  fails a test rather than shipping. `verification` is the one section an owner
  cannot resolve alone, and its line says so instead of offering a step.
- **The same page reads differently to the team and to a stranger, in exactly
  the ways it should.** A visitor is never handed a setup prompt, and never
  handed a tab that is the team's to fill.

### `RENDERABLE_TABS` is checked against the screen it claims to describe

The constant's comment says it *is* `PageScreen`'s branch set. That is a claim
about a file in another language, and it was true by attention rather than by
anything. It is now read out of `PageScreen.tsx` and compared both ways: a tab
in the constant with no branch behind it reaches a matching build as "This
section needs a newer version of the app" on the newest version of the app,
and a branch with no tab in the constant is working client code the server
will never ask for. The extraction asserts it found something before it
trusts what it found, so a rewrite of the screen fails loudly instead of
reporting perfect agreement about nothing.

### The type lists are sent where they can be, and checked where they cannot

`BUSINESS_PAGE_TYPES` had a third copy, in TypeScript, as two frozensets in
`PresenceHubScreen`. It had already drifted, and in the worst direction: types
neither set named fell through to "business", so an OTHER presence was offered a
Business OS door the server does not think it has. A page type is a string on
both sides of the wire, so nothing — not the compiler, not a test — could see
the two disagree. `public_view` now sends `business_os_capable` and the hub
reads it. Nothing private travels with it; `page_type` is already public and the
mapping is a constant, so the point is not secrecy but that one place decides.
A server that omits the field withholds the button, because a shorter card is a
smaller loss than a door onto nothing.

`PAGE_TYPES` cannot be sent — the create wizard needs the list before there is a
page to send it with — so it is checked instead, against the client's `as const`
array, in order: agreeing on membership while disagreeing on the first choice
offered is still a drift.

### A tab is advertised on the pointer, not on today's row count

`module_availability` asks whether the presence is *pointed at a source*, not
how many rows that source returns right now. So an artist between releases
keeps their Music section and it says "connected, nothing published" — rather
than the section vanishing from their page and reappearing on release day, a
presence changing shape under its own audience for something the owner did not
do. The alternative also costs a query into music_service, Marketplace and the
events domain on every public page load, to decide which headings to draw. The
tab is the promise that a section exists; the module read is where the honest
empty state lives, which is why `page_music` reports `linked` separately from
`tracks`.

Events are the one conjunction: the tab needs a linked business *and* an
environment that serves events, because with `BUSINESS_OS_EVENTS` off the whole
domain raises 503 and a linked business would otherwise raise a tab that cannot
load. Both halves are pinned separately — a flag that is global would otherwise
be enough on its own to give every venue in the product a dates tab with no
dates in it.

## Hard rules

- Real metrics only — no fabricated followers, reviews, or analytics.
- Tabs render only with real backing data; empty states are honest. See
  [page_modules.md](page_modules.md).
- Discovery and admin inspection reuse the canonical search and admin gates. See
  [page_search_and_admin.md](page_search_and_admin.md).
- Additive migrations only; verification is never auto-granted; ownership transfer
  requires the typed confirm phrase and is audited.
