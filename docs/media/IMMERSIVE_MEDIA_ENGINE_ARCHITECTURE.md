# Immersive Media Engine — Architecture

> Status: **Stage 0 complete (audit).** Implementation stages follow.
>
> Governing rule, from the brief: *ONE IMMERSIVE ENGINE. MANY MEDIA TYPES. NO
> DUPLICATE PLAYBACK SYSTEMS.* Mux is the video delivery path; Agora is the
> realtime foundation. **LiveKit is retired and must not be reintroduced.**

## Stage 0 — what already exists

This section is the audit the brief requires before any code is written. Its
purpose is to answer one question: *is there a foundation to unify, or does the
engine have to be built?* The answer is that there is a foundation, and most of
the hard parts of it are already solved.

Every claim below was verified directly against the tree, not inferred.

### The one video library

`expo-av` `~16.0.8`. It is the only video library in `package.json` — there is no
`expo-video`, no `react-native-video`. 21 files import from it. So there is no
library migration hiding inside this mission, and no risk of two decoders with
different lifecycle semantics.

### The playback arbiter already exists — and Stage 12 is mostly already built

`mobile-native/src/core/mediaPlaybackCoordinator.ts` (66 lines) is a module
singleton implementing exactly the "one active media at a time" rule Stage 12
asks for, via a priority ladder:

```
call 100 · recording 90 · live 70 · voice 60 · viewer 50
status 40 · reel 40 · feed 35 · music_preview 30 · radio 20
```

`claimMediaPlayback(owner)` refuses the claim outright when a higher-priority
owner holds the session, and awaits the previous owner's `pause()` before
handing over. It also subscribes to `AppState` and releases on background,
except for `call / recording / live / radio`, which are explicitly retained.

**This is the single most important audit finding.** Stage 12 does not need a new
mechanism; it needs the immersive engine to become a well-behaved *participant*
in the one that exists. Building a second arbiter would be the exact duplication
the brief forbids, and it would silently outrank the call priority ladder — which
is a realtime-audio hard-lock violation wearing a media-engine costume.

What the coordinator does **not** do, and what the engine must add on top:
it arbitrates *playback*, not *buffering*. Nothing today limits how many decoders
are warm or prefetches the next item. Stages 13 and 35 are genuinely new work.

### The full-screen viewer already exists

`mobile-native/src/components/NativeMediaViewer.tsx` (1,212 lines) is already the
shared full-screen surface. Opened by **7** call sites: `PostCard`,
`ConversationMediaGalleryHost`, `MarketplaceProductScreen`, `SavedScreen`,
`SellerStoreScreen`, `StatusScreen`, and itself.

It already solves a surprising amount of the brief:

| Brief stage | Already handled in `NativeMediaViewer` |
|---|---|
| §14 fast first frame, no black screen | `failed` and "no first frame yet" are deliberately *separate* flags. The docstring records the regression that forced the split: the watchdog set `failed`, which unmounted the `<Video>` and with it the poster it was displaying, so the viewer opened on a real frame and replaced it with a black card 15s later. |
| §30 no internal Mux identifiers / raw storage URLs | `url` (HLS playback) and `downloadUrl` (saveable file) are separate fields *by design*, with the reasoning written down: pointing Save-to-Photos at an `.m3u8` writes a text playlist to disk and Photos rejects it. |
| image + video + file in one surface | `kind: "image" \| "video" \| "file"`, plus zoom/pan, save, share, author press, double-tap-like. |
| cache correctness | `cacheIdentity` is deliberately *not* derived from `id`, because Messenger puts a **message** id there and feed puts a **media row** id there — the id spaces overlap and would serve one's bytes for the other. |
| audio hard-lock compliance | Calls `configureReelsAudioSession()` rather than `Audio.setAudioModeAsync` directly. |

What it does **not** have, and what the engine must add:

- **Vertical paging.** It pages *horizontally*, and only when `swipeToNavigate`
  is passed (off by default). Endless vertical continuation does not exist.
- **A session model.** It takes a fixed `items` array. There is no notion of
  "where did this come from" or "what comes after the array runs out".
- **Preload.** No prefetch of the next 1–2 items (§13).
- **Recycling.** No bounded window (§35).

### The audio hard lock is the binding constraint

`config/realtime-audio-protected-paths.json` → rule `expo_av_global_audio_mode`:

```json
"markers": ["Audio.setAudioModeAsync("],
"allowed_paths": [ 6 files ],
"frozen_at_baseline": true,
"max_allowed_paths": 6
```

The six are `pulseRadio.ts`, `reelsAudioSession.ts`, `voiceMessagePlayback.ts`,
`callSignalMedia.ts`, `MusicScreen.tsx`, `ChatScreen.tsx`. **A seventh call site
fails CI.**

So the immersive engine may never call `Audio.setAudioModeAsync` itself. It must
route through `reelsAudioSession.ts`, which is the member of that allowlist built
for media playback — and which `NativeMediaViewer` and `PostCard` already use.
This is not a workaround; it is the boundary working as designed.

### One finding that changes an existing module's premise

`mobile-native/src/discovery/reelTransfer.ts` (114 lines) is a one-shot module
slot that carries a tapped `PulseReel` to the Reels player, so the player opens
on the reel you actually tapped. Its opening docstring states the reason:

> "There is no endpoint that returns a single reel. `/api/pulse/reels/<id>`
> exists for PATCH and DELETE only"

**That is no longer true as of `d8fafe932`** (Mission I), which added `GET` to
that route. The docstring is now stale.

This matters for §25 (return to the exact origin) and for the engine's seeding
path generally: the transfer slot exists because a by-id read was impossible, and
a by-id read is now possible. The slot is still the better path for the in-app
case — carrying an object you already hold beats re-fetching it, and it removes a
network round trip from the tap — so this is **not** a call to delete it. It is a
call to correct the docstring and to note that the *fallback* when the slot misses
can now be a real fetch instead of "silently give up and show someone else's
reel", which is the failure the module itself documents.

Recorded here rather than fixed inline, because it is Mission I's sentence to
correct and it sits in a module this mission has not otherwise touched yet.

### State: no shared source of truth

There is **no Zustand in `mobile-native`**, despite `CLAUDE.md` claiming it — a
stale claim, consistent with other known drift in that file. State is three
patterns: module-level pub/sub singletons (`savedStore.ts`,
`mediaPlaybackCoordinator.ts`), per-screen local React state, and a JSON file
cache per feed/profile/query.

`HomeScreen`, `ProfileScreen` and `ReelsScreen` each hold their own independent
`posts` / `reels` array. This is the structural reason §25 (return to the exact
origin) and §37 (handoff without restarting at 0) are hard: the origin's list is
private to the origin's component.

### Feeds and pagination

All three home tabs — `for_you`, `following`, `friends` — are **one screen**
(`HomeScreen.tsx`, 3,430 lines) multiplexed on a `selectedFeed` state, sharing one
`FlatList` and one media pipeline. They are not three implementations.

| Surface | API | Model |
|---|---|---|
| Home (all 3 tabs) | `listFeed` | `offset` + `has_more` |
| Profile | `listPublicProfilePosts` | `offset` + `has_more` |
| Reels | `listReels` | `lane` + `offset` |
| Search | `searchPulse` | none — flat array |
| Group | embedded in `getGroupDetail` | none |

**Stage 28 (session-level seen-ID dedupe) is genuinely new.** There is no
`seen_ids` / `exclude_ids` parameter anywhere in `src/api/`. Offset pagination
over a *ranked* feed double-serves items whenever ranking shifts between pages,
so endless continuation will surface duplicates without it.

### Distinct playback implementations today

Five, all on `expo-av`, all already participating in the coordinator:

1. `NativeMediaViewer` — full-screen modal (priority `viewer`)
2. `ReelPlayerCard` (902 lines) — vertical `FlatList` item, `onViewableItemsChanged` decides active (priority `reel`)
3. `StatusViewerCard` (517 lines) — paged rail, 6s auto-advance for images (priority `status`)
4. `DiscoveryPreviewMedia` (271 lines) — muted inline loop (priority `feed`)
5. `PostCard` (1,853 lines) — inline feed video (priority `feed`)

They differ in **chrome and lifecycle**, not in playback mechanism. That is the
finding that makes unification tractable.

## The decision

**Extend `NativeMediaViewer` into the immersive engine. Do not write a new player.**

The brief's Stage 0 instruction is "do not create another player if the existing
foundation can be unified." It can be. A sixth player would mean a sixth
participant in the priority ladder, a second audio-session consumer, and a second
place where the HLS-vs-download distinction has to be re-learned — all three of
which are already solved once.

The engine is therefore a **session layer above the existing viewer**, not a
replacement for it:

```
ImmersiveMediaSession        (new — §1: origin, queue, cursor, seen-set, continuation)
        │
        ├── seeding            (new — from the tapped item; reuses reelTransfer's slot idea)
        ├── continuation       (new — §28 dedupe, needs a server-side seen-ID contract)
        └── window/preload     (new — §13 preload next 1–2, §35 bounded recycling)
        │
NativeMediaViewer            (extend — gains vertical paging alongside its horizontal axis)
        │
mediaPlaybackCoordinator     (unchanged — engine becomes a participant, never a rival)
        │
reelsAudioSession            (unchanged — the only legal audio-session door)
```

Explicitly out of scope, per the hard locks: Agora realtime, livestream engine
and audio, audio/video calls, PushKit, CallKit, Pulse Radio, unrelated
audio-session behaviour, composer/posting logic, marketplace.

§38 holds throughout: **a Post remains a Post and a Reel remains a Reel.** The
engine unifies *presentation*, not identity.

## What is built

Five modules under `mobile-native/src/immersive/`, four of them pure. Each was
mutation-tested against its own suite before the next was started, and the counts
below are the surviving-mutant scores, not coverage percentages.

| Module | What it owns | Mutants |
| --- | --- | --- |
| `immersiveSession.ts` | origin, queue, cursor, `seen` set, continuation flags (§1, §25, §28, §38) | — |
| `immersiveContinuation.ts` | the only network in the engine: asks the *origin's own* endpoint for more | — |
| `immersiveWindow.ts` | what is mounted and what is preloaded (§13, §35) | 17/19 |
| `useImmersiveSession.ts` | the controller: three named races, plus the playback claim (§12) | 21/23 |
| `immersiveGesture.ts` | which axis owns a drag, and what it means (§6) | 22/22 |

The remaining escaped mutants are equivalents, recorded rather than papered over:
two in the window (an unclamped bound that `entryKey` already rejects, and an
empty-queue early return made redundant by the clamp arithmetic), and two in the
controller (a `canContinue` check that the outer effect's guard already blocks,
and a setState-after-unmount that React 18 no longer reports).

### Three decisions worth stating outside the code

**The axes are swapped relative to the existing viewer.** `NativeMediaViewer`
spends the horizontal axis on the collection and the vertical on dismiss.
Immersive media is the other way round, and the carousel is *contained*: at the
last frame a further horizontal swipe resolves to nothing rather than falling
through to the next post. A fall-through would make the boundary between "inside
this post" and "on to the next" invisible — the same gesture would sometimes
advance a frame and sometimes eject the user into somebody else's media,
depending on a count they cannot see.

**The bottom of the feed does not dismiss; the top does.** "Nothing below"
describes the instant a continuation is in flight, not the feed, so dismissing on
it would throw the user out because the network was slow. The top is different:
the first entry *is* the thing they tapped, so pulling down off it is a return to
the origin (§25), not a discard.

**Identity preservation is a termination condition.** `appendImmersivePage`
returns the *same object* for a page that adds no entries and moves neither the
cursor nor the continuation flag. This is not a micro-optimisation — a React
caller re-runs its "should I fetch more?" effect whenever session identity
changes, so a session that is a new object after every all-duplicate page asks
for another one immediately, forever. That presents as a feed which is
permanently loading rather than as an error. It was found by the controller hook
hanging, not by reasoning.

### Not yet built

The vertical screen that mounts the arbiter over `NativeMediaViewer` (§6/§12
wiring), the feed player handoff (§37), and the §43/§44 device matrix.

§28's dedupe is **client-side only, and that is the correctness guarantee** — no
`seen_ids`/`exclude_ids` parameter exists anywhere in `src/api/`. A server-side
contract would be a bandwidth optimisation on top, not a fix.
