# RICH LINK PREVIEW MISSION

**Native rich link previews inside PulseSoc Messenger.**

A PulseSoc link pasted into a conversation no longer renders as a blue URL. It
renders as the object it points at — a card with the author's picture, their
name, the caption, and a way in that lands on the native screen.

This report covers what shipped, what deliberately did not, and why the line
between the two is where it is.

---

## ROOT CAUSE

The messenger had no concept of *what a link was*. `messageLinks.ts` answered
"is this a link, and does this app claim it" — enough to make a URL tappable,
which was the previous mission's job. Nothing answered the next question:
"which object is it."

So a shared post arrived as `https://pulsesoc.com/pulse/post/2432`. The reader
saw a URL. The URL is the one part of a share that carries no information a
human can use — not the author, not the picture, not the caption. Everything
that would make the reader want to tap was on the other side of the tap.

The fix is not a preview service. It is a resolver that recognises the object,
and a fetch that asks the *destination screen's own loader* for the metadata.

### The rule that decided the whole scope

> A card may only exist where the preview can be fetched with the **same
> request a tap makes**.

This is recorded verbatim in `entityPreview.ts` because it is the thing that
must not drift. A preview endpoint built alongside the destination endpoint is
two code paths that must agree about authorization forever. They will not.
The first time one gets a new privacy rule and the other does not, the card
starts disclosing what the tap refuses.

So there is no preview endpoint. `useEntityPreview` calls `getPostDetail(id)`
for a post and `getPublicProfile(target)` for a profile — the same functions
`PostDetail` and `ProfileDetail` call when the reader taps through.

---

## NATIVE PREVIEWS

### What ships

| Entity | Card | By-id read reused | Route |
|---|---|---|---|
| **Post** | ✅ | `getPostDetail(id)` | `GET /api/pulse/posts/:id` |
| **Profile** | ✅ | `getPublicProfile(target)` | `GET /api/pulse/profile/:key` |
| Reel | ❌ | — | **no GET exists** |
| Marketplace product | ❌ | — | no read-one endpoint |
| Store | ❌ | — | no public by-id read |
| Conversation | ❌ | — | read mutates local cache |
| Invite | ❌ | — | no destination in the link table |

### Why the four refusals, individually

Each of these was checked against `bot.py` and the API layer directly, not
inferred from the presence of a screen.

- **Reel.** `/api/pulse/reels/<int:reel_id>` exists at `bot.py:90848` and is
  **PATCH and DELETE only**. There is no GET at all. The client's
  `getReelDetail` (`src/api/reels.ts:243`) looks like a fetch and is not — it
  reads the reel out of AsyncStorage. A card built on it would show whatever
  the viewer happened to have scrolled past, and nothing at all for a reel
  they had not seen. That is worse than a plain URL, because it is confidently
  wrong.

- **Marketplace product.** The only buyer-side read is
  `/api/pulse/marketplace/search`. There is no read-one. Carding a listing
  would mean searching for it by id and trusting the first result.

- **Store.** `loadSellerStoreSnapshot()` is own-store-only — it answers for
  the signed-in seller, not for an arbitrary store id. There is no public
  by-id read to reuse.

- **Conversation.** The only read is `getConversation`
  (`src/api/messenger.ts:580`), which ends in `await cacheMessages(...)`. It
  is a message-page fetch that **writes to the local cache**. Rendering a card
  would mean a side-effecting fetch fires while the reader scrolls past a
  link. And a conversation preview is the one preview where leaking a single
  line of content is a real disclosure.

Giving any of them a card would mean inventing a second, differently-authorized
way to read them — the exact thing this pair of modules exists to prevent. The
union grows when an entity gains a real by-id read, **not before**.

### `/open/*` is not a share URL

The brief listed `/open/post/:id`, `/open/reel/:id`, `/open/profile/:id` and
friends. Those paths are deliberately **not universal links**:

- `bot.py:56387` excludes `/open/` from the AASA claim.
- `services/search_visibility.py:115` marks it `NOINDEX_NOFOLLOW`, described
  in its own words as an "app hand-off interstitial".
- `services/app_promotion.py:453` generates it only as an in-page
  `APP_FIRST_HREF_PREFIX` href — an "open the app" button on a web page.

Nothing in the product ever puts an `/open/` URL into a message. The canonical
native paths are `/pulse/*`, and those are what the resolver matches. Adding
`/open/*` is a two-line change if the interstitial ever becomes shareable; it
is documented here as the next increment rather than shipped as dead code.

### What the card looks like

Dark glass over `chatGraphite.insetSurface`, 14pt corners, hairline border in
teal (`rgba(123, 223, 255, 0.28)`), media-first with a 1.6 aspect frame,
compact author row (26pt avatar, name, handle beneath only when both exist),
caption to three lines, and a footer carrying the brand eyebrow and the CTA.

It does not look like a foreign webpage embed because it is not one: there is
no favicon, no hostname, no "pulsesoc.com" chrome anywhere on it. It says
**PULSESOC POST** or **PULSESOC PROFILE** and offers **View Post →** or
**View Profile →**.

### One component, two kinds

`PulseEntityLinkCard` (renamed from `PulsePostLinkCard` via `git mv`, so the
history follows) draws both. Two components would have meant fixing the
non-reflowing shell, the unavailable state, the accessibility label and the
video-badge guard **twice** — and the second fix is the one that gets
forgotten.

Only the *words* differ by kind, and they are chosen in `cardCopy`, where every
translation key is a literal. A composed key such as
`messaging:${kind}Card.cta` would read more cleverly and would be invisible to
the i18n extractor — it would ship English to eleven locales.

**The copy is sourced from the resolved entity, never from the preview.** The
kind is known locally the moment the body is parsed; the preview is still in
flight. Sourcing the loading line from the preview would be the natural
mistake — the preview is where every other display value comes from — and it
would make every profile link in the app say "Loading post…" for the length of
a round trip.

---

## MESSAGE MODEL

**Nothing was persisted. That is the finding, not a shortcut.**

The brief asked for `entity_type`, `entity_public_id`, `canonical_url` and
`preview_version` columns, and a `PULSESOC_ENTITY` message type. Building them
would have contradicted two of the brief's own requirements:

1. **"Legacy raw URLs must upgrade to cards at render time without
   resending."** A stored `entity_type` is only written by the *send* path.
   Every post link already sitting in every conversation was sent before that
   column existed, so none of them would upgrade. A migration would have to
   parse bodies it had never parsed, and guess.

2. **"Server authoritative for live metadata."** A `preview_version` column is
   a snapshot. It freezes the author name and the caption at the version that
   wrote it — so a post edited after sharing shows the old caption forever,
   and a deleted post still shows its picture.

The card is therefore **derived at render time from the message body**. A
message is text; a card is a reading of that text; and a reading can be
improved later, where a stored snapshot is frozen.

This also makes the card unforgeable. There is no sender-supplied field
anywhere in it. `resolvePulseEntity` takes the URL out of the body, and the
preview comes back from the server for that id — so the author name on the
card is the author of the post the tap opens. "The card is the destination" is
true by construction rather than merely intended.

**Schema change: none. Migration: none. Send path: unchanged.** Every post
link in every existing conversation became a card the moment this shipped.

### One subject, or none

A body naming **two different** entities gets no card. The card is a claim that
*this message is that object*; with two candidates the claim is a guess, and
whichever came first would be promoted over the other for no reason the sender
chose. Those bodies keep their inline tappable links, which is the honest
rendering of "here are two things".

A body naming the **same** entity twice gets one card — repetition is not
ambiguity. The dedupe compares **kind as well as id**, because a post id and a
profile key occupy overlapping spaces: `resolveProfileTarget` produces
`profileKey: String(userId)`, so `post:2432` and `profile:2432` are different
objects with the same digits.

---

## AUTHORIZATION

### Parity by construction

There is no preview endpoint. There is no second code path that could be made
more permissive than the first, **because there is no second code path**.

For a post, the card calls `getPostDetail(id)` → `GET /api/pulse/posts/:id`.
For a profile, `getPublicProfile(target)` → `GET /api/pulse/profile/:key`,
which gates at `bot.py:109274-109301` with 401 / 404 / 410 / 403 / 403 before
it assembles any payload.

### Parity cuts both ways, and that is the point

The profile route does not currently refuse a viewer the target has blocked.
Neither does the card. That is not the card leaking — it is the card being
exactly as permissive as the screen, which is the invariant worth having. The
one that matters is that **the card is never *more* permissive than the
screen**, and because both go through the same function, a tightening lands in
one place and both move together.

### What the reader is told

| Server says | Card says (post) | Card says (profile) |
|---|---|---|
| 403 / 401 | This content isn't available to you. | This profile isn't available to you. |
| 404 / 410 | This post is no longer available. | This profile is no longer available. |
| anything else | Content unavailable | Profile unavailable |

**Unavailable is a state, not an absence.** A post the viewer cannot see still
renders a card — a quiet one, with the teal border dropped to
`chatGraphite.quietDivider`, that says so. Falling back to a raw URL would be
worse in both directions: the sender's message would look broken, and the URL
itself would be the one thing still on screen.

**An unavailable card is not tappable.** `disabled` is set on the `Pressable`
and the CTA is dropped entirely. Sending someone to a screen that will show
them the same refusal, one navigation later, is a worse answer than the card
already gave them.

### Messenger never crashes on a failed preview

Every fetch is inside `try`. `stateForError` maps anything thrown to a state:

```
403, 401  → forbidden, cacheable
404, 410  → missing,   cacheable
otherwise → error,     NOT cacheable
```

**Cache has two shapes because failures do.** A 403 is an *answer* — the
server considered the request and refused it, so it is cached and not retried.
A dropped connection is *not an answer*, so it is never cached, and the next
render tries again. On a non-cacheable error the card falls back to the local
cache (`loadCachedProfile` / `loadCachedPostDetail`) so an offline reader still
sees the card they saw an hour ago — but **never** on a cacheable refusal,
because a 403 must not be answered out of a cache the viewer is no longer
entitled to.

---

## ROUTING

**No routing logic was duplicated.** `resolvePulseEntity` calls
`classifyLink` first and considers only its `internal` verdict. The path table
remains `navigation/linking.ts` and nothing else.

The consequence is the point: **a path the app stops claiming stops producing
cards on the same day it stops opening natively.** The card and the destination
cannot drift apart, because the card only exists when the destination does.

The patterns in `pulseEntity.ts` are not a second routing table. They are a
*narrowing* of one — every pattern there must already be claimed over here, and
if it is not, `classifyLink` rejects it first.

Tapping calls `onOpen(entity.url)` with **the URL the sender actually sent** —
not the path, not a URL the card rebuilt — which goes to `openMessageLink` and
into the existing native router. `/pulse/profile/roody` lands on
`ProfileDetail` (`linking.ts:74-79`). **Never a WebView first.**

### The bug the routing table caught

`/pulse/profile/edit` is shaped exactly like a profile and is not one —
`linking.ts:74` routes it to the **settings** screen before it ever looks for a
member. Without a guard, the card would have looked up a person called "edit",
been told there is no such person, and reported to the reader that somebody's
profile had been deleted.

`PROFILE_RESERVED` now holds it, case-insensitively. Two tests pin it; removing
the guard fails both.

---

## PERFORMANCE

**Chat never waits for a preview.** `useEntityPreview` returns
`{ status: "loading" }` synchronously and populates asynchronously.

**The shell is the same size as the card.** The loading state occupies the
*finished* layout rather than collapsing to nothing: a skeleton in the media
frame, the avatar fallback, the "Loading post…" line. A card that grows when it
resolves shoves the conversation under the reader's thumb mid-scroll, which is
worse than a moment of grey.

**No preview storms on pagination.** Two layers of dedupe, because there are
two different races:

- `resolved` — a module-level map keyed `kind:id`. The second render of the
  same entity reads the answer instead of refetching it.
- `inFlight` — a map of in-progress promises. Ten messages linking the same
  post, all mounting in the same frame, share **one** request. Without this,
  `resolved` would not have been populated yet and all ten would fire.

**The key carries the kind.** `entityKey` is `` `${ref.kind}:${ref.id}` ``.
Dropping the kind would let a profile whose numeric key matches a post id serve
the post's picture, which is the single worst outcome this whole feature can
produce.

---

## TEST RESULTS

**73 tests across the three Mission F suites, all green.**
Full gate: `npm run verify` → **484 suites, 8335 tests, 0 failures.**

### The 20-case matrix

| # | Case | Result |
|---|---|---|
| 1 | Canonical post URL → id + in-app path | ✅ |
| 2 | Query string and fragment kept on the opened URL, ignored for matching | ✅ |
| 3 | `www.` and a trailing slash are the same post | ✅ |
| 4 | Lookalike host `pulsesoc.com.evil.example` refused | ✅ |
| 5 | Non-site subdomain `staging.pulsesoc.com` refused | ✅ |
| 6 | Deeper path `/pulse/post/2432/edit` refused | ✅ |
| 7 | `post/0`, non-numeric id, `javascript:`, nonsense — all refused | ✅ |
| 8 | Invite link `/r/XK92QP` stays external, gets no card | ✅ |
| 9 | Canonical profile URL → key + in-app path | ✅ |
| 10 | Numeric profile key stays a **string**, not a post id | ✅ |
| 11 | Percent-encoded key decoded once, so the lookup means what the URL means | ✅ |
| 12 | `/pulse/profile/edit` and `/EDIT` refused — a screen, not a person | ✅ |
| 13 | Injected path key `..%2F..%2Fadmin`, spaces, empty key refused | ✅ |
| 14 | Bare link → card, and body reads as link-only | ✅ |
| 15 | Link in prose → card, and the prose is kept | ✅ |
| 16 | Same post linked twice → one card | ✅ |
| 17 | Two different posts → **no** card | ✅ |
| 18 | A post **and** a profile → no card (kind is part of identity) | ✅ |
| 19 | Non-entity link alongside a post → the post still cards | ✅ |
| 20 | Post and profile with the **same id** resolve to different previews | ✅ |

Plus, on the component and the engine: both kinds' eyebrow and CTA wording,
both loading lines, both forbidden lines, both missing lines, the dropped CTA,
the non-firing press when unavailable, three accessibility labels, the
sender's-exact-URL hand-back, same-call authorization parity, field mapping
from the profile payload, four error→state mappings, and the never-reach-cache
rule on a refusal.

### Mutation testing — 8 mutations, 8 caught

Green on first run means nothing until the tests are shown to be able to fail.

| # | File | Mutation | Caught by |
|---|---|---|---|
| 1 | `entityPreview.ts` | `entityKey` drops the kind | post/profile same-id test |
| 2 | `entityPreview.ts` | empty-payload guard removed | empty→missing test |
| 3 | `entityPreview.ts` | prefer full-size avatar over thumbnail | field-mapping test |
| 4 | `entityPreview.ts` | offline cache fallback removed | non-cacheable-error test |
| 5 | `PulseEntityLinkCard.tsx` | `cardCopy` collapsed to the post branch | 6 profile-wording tests |
| 6 | `PulseEntityLinkCard.tsx` | `cardCopy` collapsed to the profile branch | 6 post-wording tests |
| 7 | `PulseEntityLinkCard.tsx` | copy sourced from `preview?.kind` | both loading-line tests |
| 8 | `pulseEntity.ts` | `PROFILE_RESERVED` guard removed | 2 reserved-segment tests |

Mutations 5 and 6 are the pair that matters. **A one-sided test on a two-sided
branch is the gap a passing suite hides best** — pinning only the profile
wording would let `cardCopy` collapse into the profile branch and stay green
while every shared post in every conversation started calling itself a profile.

### A test that was passing against nothing

The component suite failed 11 of 14 on first run with the i18n engine returning
`"A11y Open"` — a humanised version of the key's own last segment. That is
close enough to a real string that an assertion on `toBeTruthy()` would have
passed **against no translations at all**. Fixed with
`beforeAll(() => activateLocale("en"))`, and the reason is recorded in the test
file so it is not removed as boilerplate.

### Gates

- `npm run verify` — typecheck + i18n validate + jest: **green**
- Realtime audio gate — *"No protected real-time audio path changed (0 file(s)
  inspected)."*
- `config/realtime-audio-protected-paths.json` — 0 matches against any touched
  path
- No stale `PulsePostLinkCard` reference anywhere in the repo

### Still owed: the physical device pass

The brief's own test — share one post to Apple Messages and the same post to
PulseSoc Messenger on a physical iPhone, and compare — is pending the device
install that follows this commit. It is the one test that cannot be faked in
jest, because what it measures is whether ours looks *better*.

---

## FILES CHANGED

```
 .../messages/{PulsePostLinkCard.tsx => PulseEntityLinkCard.tsx} |  83 ++++++--
 .../messages/__tests__/PulseEntityLinkCard.test.tsx             | 168 +++++++++
 mobile-native/src/i18n/catalogs/{11 locales}/extended.json      |  10 ++ each
 mobile-native/src/links/__tests__/entityPreview.test.ts         | 114 ++++++++
 mobile-native/src/links/__tests__/pulseEntity.test.ts           |  55 ++++
 mobile-native/src/links/entityPreview.ts                        | 126 ++++++---
 mobile-native/src/links/pulseEntity.ts                          |  69 ++++-
 mobile-native/src/screens/ChatScreen.tsx                        |   4 +-
 18 files changed, 686 insertions(+), 43 deletions(-)
```

Eleven locales each gained exactly ten lines — `messaging.profileCard` with
eight literal keys — and **zero deletions**, which is the proof that the JSON
round-trip did not silently reflow anything else.

No backend file changed. No schema changed. No new route.

---

## COMMITS

- `e12be1ee4` — *feat(messenger): the long-press menu carries out every action it lists* (Mission E, prior)
- `12e42d77e` — *test(messenger): pin both sides of Info's group-vs-direct wording* (Mission E, prior)
- **this commit** — *feat(messenger): a profile link is the same card, read the same way*

---

## FINAL JUDGMENT

**Shipped, with the scope narrower than the brief and the reason recorded in
the code.**

Two entities card: post and profile. Four do not, and each refusal is backed by
a specific missing endpoint rather than by "we ran out of time". The rule is
stated once, in the module that enforces it: *a card may only exist where the
preview can be fetched with the same request a tap makes.*

The alternative — building a preview endpoint per entity — would have shipped
six cards this week and an authorization divergence within the year. A preview
that can disclose what a tap cannot is not a smaller bug than a missing card;
it is a different and worse one. A resolver kind with no renderer behind it is
also dead code that reads as finished work, so the kinds arrive with their
cards or they do not arrive.

What was built is durable in a way a stored preview would not have been:
nothing is persisted, so every link already in every conversation upgraded on
install; and nothing is sender-supplied, so the card cannot be made to say
something the destination does not.

The one honest gap is the device comparison against Apple Messages, which needs
the hardware and follows this commit.
