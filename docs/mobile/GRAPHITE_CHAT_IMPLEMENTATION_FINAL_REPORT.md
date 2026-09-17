# Graphite Conversation Surface — Final Report

Mission: replace the Messenger conversation screen's navy-on-near-black palette with
the approved graphite direction. This is the report that mission's §10 asks for, in
the nine sections it names.

Commit: `f4a5f2f1413a4a7a60c9a6345aac57966d945a3c` — *messenger: ship the graphite
conversation surface*
Parent: `6f1026a0709032c7cbee046cc8e0ccb9b3502768`
Verdict: **PASS**, with the qualifications recorded in §9 — none of them is a
defect in the surface, and none of them was found by assuming.

---

## 1. Outcome

The conversation screen is graphite. A dark-gray header and composer anchor both
safe areas, a middle-gray canvas sits between them, incoming bubbles are gray and
outgoing bubbles are deep cobalt. Every colour of the conversation *surface* —
chrome, canvas, bubbles, insets, composer — now comes from one named token module
instead of ~30 scattered literals. What is still literal is feature accent rather
than surface, and §2.3 names all of it.

Three things were achieved that the brief asked for and that are easy to claim
without earning:

- **Every contrast number in this report was computed, not asserted.** The audit
  lives in `src/theme/__tests__/chatGraphiteContrast.test.ts` (342 lines) and runs
  in CI, so the palette cannot drift silently.
- **The two places where the approved baseline had to be left behind are asserted
  in both directions.** The test proves the specified value fails *and* the shipped
  value passes, so restoring the spec turns the suite red and prints why.
- **Nothing audio, call or livestream was touched**, and that is verified by the
  repository's own gate rather than by inspection (§5).

What changed visually, in one line each: the canvas lost its star field and its
glows; the bubbles became opaque; the header, composer and control chips became
tokenised graphite; the default wallpaper became a flat graphite gradient while the
ten inherited wallpapers were left exactly as they were.

---

## 2. Design implementation

### 2.1 Where the colour lives, and why it lives there

The palette ships as `mobile-native/src/theme/chatGraphite.ts` — a surface-scoped
`as const` token module — not as an addition to `colors` and not as a new
`ThemeMode`.

A new `ThemeMode` would have been the textbook answer and it would have rendered
nothing: `buildTheme` pins `const activeTheme: ThemeMode = "dark"`
(`src/theme/ThemeContext.tsx:212-214`), so a fourth mode type-checks, is
unreachable, and misleads the next reader into thinking the screen is
theme-switchable. Extending `colors` would have pushed conversation-specific values
into a module every screen reads. The repository already has ~26 surface-scoped
token modules — `messengerTheme`, `presenceTheme` and the rest — and this ships the
same way, which is the convention precisely *because* of the pinned mode.

### 2.2 Final tokens

| Token | Value | Role |
|---|---|---|
| `headerSurface` | `#292E36` | Header, composer chrome, both safe areas |
| `canvasTop` | `#3A4049` | Canvas, top of the vertical run |
| `canvasBottom` | `#343A42` | Canvas, bottom of the vertical run |
| `incomingSurface` | `#505761` | Incoming bubble fill (opaque) |
| `incomingBorder` | `rgba(214, 222, 232, 0.20)` | Incoming bubble edge |
| `outgoingSurface` | `#24549B` | Outgoing bubble fill (deep cobalt) |
| `outgoingBorder` | `#4D8FE9` | Outgoing bubble edge |
| `composerSurface` | `#20262E` | The inset input field |
| `insetSurface` | `rgba(20, 25, 32, 0.30)` | Quoted replies, media tiles, in-bubble insets |
| `controlSurface` | `#323842` | Header chips, back button, composer controls |
| `primaryText` | `#F7F8FA` | Message body, titles |
| `secondaryText` | `#C8D0DB` | Timestamps, delivery labels, metadata |
| `senderAccent` | `#7BDFFF` | Sender names, quoted-reply rule |
| `quietDivider` | `rgba(230, 236, 245, 0.16)` | Hairlines, lit top edges |
| `shadow` / `shadowOpacity` | `#080B0F` / `0.18` | Composer lift |

Measured contrast (WCAG 2.1 relative luminance; alpha values composited
source-over against what is actually behind them):

| Text | on `incoming #505761` | on `outgoing #24549B` | on canvas `#3A4049` | on header `#292E36` | on composer `#20262E` |
|---|---|---|---|---|---|
| `primaryText #F7F8FA` | **6.87:1** | **7.02:1** | 9.83:1 | 12.85:1 | 14.34:1 |
| `secondaryText #C8D0DB` | **4.69:1** | **4.79:1** | 6.72:1 | 8.78:1 | 9.80:1 |
| `senderAccent #7BDFFF` | **4.81:1** | — | — | — | — |

Every cell clears AA for the size it is used at; the three bold cells are the
worst cases, and they are the small text (timestamps, delivery state, sender
names) that AA 4.5:1 exists for. `insetSurface` over an incoming bubble
composites to `#3E444D`, where its label measures **9.24:1**.

**Incoming and outgoing are separated by hue, not by weight.** Their luminances
are 0.09385 and 0.09082 — **0.30 percentage points apart** (3.2% of the brighter
bubble's luminance, 3.3% of the dimmer one's). The
commit message's "within 0.4%" means percentage points, and that is the figure. The
two bubbles are told apart by colour, by which edge they hang off, and by which
corner is squared — never by which one is brighter. That is what keeps the thread
readable in grayscale, and it is why the sender bubble is a deep cobalt instead of
the brighter blue it would ordinarily get. Bubble-against-canvas separation is
1.43:1 (incoming) and 1.40:1 (outgoing); the outgoing border reads 2.28:1 against
its own fill.

### 2.3 Component ownership

`ChatScreen.tsx` reads the module 50 times across its style sheet. Distribution:
`secondaryText` ×10, `quietDivider` ×8, `primaryText` ×8,
`insetSurface` ×7, `senderAccent` ×3, `headerSurface` ×3, `controlSurface` ×3,
`composerSurface` ×2, and one each of the two bubble fills, the two bubble borders,
`shadow` and `shadowOpacity`.

Ownership by region:

- **Header** — surface, bottom hairline, back button (`controlSurface` + divider
  border), thread title, per-tone control chips.
- **Canvas** — owned by `ChatWallpaper`, not by `ChatScreen`. `canvasTop` and
  `canvasBottom` are consumed by `chatWallpaper.ts`, which is why they appear zero
  times in the screen.
- **Bubbles** — fill, border, body text, timestamp, delivery label, metadata row.
- **In-bubble structures** — quoted reply (`insetSurface` + a `senderAccent` left
  rule), media tiles, media-failed title and hint, link previews.
- **Composer** — `composerSurface` for the field, `headerSurface` for the dock,
  `controlSurface` for the buttons, `shadow`/`shadowOpacity` for the lift, and
  `secondaryText` for `placeholderTextColor` (`ChatScreen.tsx:1937`).

**What the sweep did not tokenise, stated plainly.** 56 colour literals remain in
`ChatScreen.tsx`, and they are feature accents rather than surface colour: the
voice-capture dock and its cancel/send/live-dot/waveform parts, voice playback and
rate controls, video and media badges, the media skeleton and failed states, the
ambient orbs and signal lines, the UNDX action card, the market-context chip, the
error and status banners, the moderated bubble, and the disabled send button. The
voice-capture dock is excluded on purpose — it sits against the protected-media
boundary (§5) and keeping its styles untouched is how that boundary was honoured.
The rest are simply outside this mission's scope; they are recorded as follow-up
work in §9 rather than left for someone to discover.

### 2.4 The wallpaper

`PULSESOC_GRAPHITE` in `chatWallpaper.ts` is the only spec in the file with an
empty `shapes` array, `stars: 0`, and a fully transparent scrim. That emptiness is
the design: the other ten specs each carry soft glows the eye has to dismiss before
it reaches a sentence.

**The id stays `pulsesoc_cosmic`.** It is a wire value — the server's allowed set
and `CONTROL_SETTING_DEFAULTS` in `pulse_communications_v2/service.py:894,947`, the
web build's `--control-wallpaper` stacks, and every stored `appearance.wallpaper`
row all name it. A new id would mean a new default on both sides of a
cross-language contract for a change that is entirely about colour. Only the spec
behind the id changed, and only its **label** moved — the picker now reads
**"PulseSoc Graphite"** in all 11 catalogs. The ten inherited wallpapers are
untouched, so anyone who picked one still gets exactly what they picked. (That
last sentence turns out to matter in production — see §7.2.)

### 2.5 Justified deviations

Three, all measured, all deliberate.

| Approved value | Measured on `#505761` | Shipped | Measured |
|---|---|---|---|
| `#BEC6D1` metadata grey | **4.24:1** — fails AA | `#C8D0DB` | **4.69:1** |
| `#61D8FF` (`colors.accentStrong`) | **4.44:1** — fails AA | `#7BDFFF` | **4.81:1** |
| `ContentTranslation` cyan wash | **4.04:1** label on graphite | `insetSurface` fill | **6.28:1** |

The first two are the same hues one step up. Both failures are asserted, not just
described, at `chatGraphiteContrast.test.ts:106` and `:109`:

```ts
expect(contrast(bubble, "#BEC6D1")).toBeLessThan(4.5); // the specified metadata grey: 4.24
expect(contrast(bubble, "#61D8FF")).toBeLessThan(4.5); // colors.accentStrong: 4.44
```

The third deviation is scoped rather than global: `ContentTranslation` is shared
with Reels, Marketplace and the feed, but `controlsMode="compact"` has exactly one
call site — the chat bubble — so the two changed rules are conversation-scoped even
though the component is not.

---

## 3. Files changed

20 files, **+952 / −267**.

| File | Δ | What |
|---|---|---|
| `src/theme/chatGraphite.ts` | +85 | New. The token module. |
| `src/theme/__tests__/chatGraphiteContrast.test.ts` | +342 | New. The palette audit. |
| `src/screens/__tests__/ChatScreenGraphiteTheme.test.tsx` | +277 | New. Screen-level assertions. |
| `src/screens/ChatScreen.tsx` | 99 | Literals → tokens across the style sheet. |
| `src/theme/__tests__/chatWallpaperContrast.test.ts` | 302 | Rewritten premise (§4). |
| `src/theme/chatWallpaper.ts` | 68 | Default spec becomes flat graphite. |
| `src/components/ContentTranslation.tsx` | 18 | Compact Translate pill fill. |
| `src/messaging/conversationWallpaper.ts` | 4 | Default-id comment and wiring. |
| `src/components/ChatWallpaper.tsx` | 2 | Doc: "Cosmic" → "Graphite" default. |
| `src/i18n/catalogs/{ar,de,en,es,fr,hi,ht,ja,ko,pt,zh}/extended.json` | 2 each | The picker label. |

No backend file, no navigation file, no shared theme file, and no audio, call or
livestream file appears in that list.

---

## 4. Behavior preserved

- **Every handler survives.** The literal→token substitution in `ChatScreen.tsx`
  is confined to style rules and one `placeholderTextColor`. No event handler, no
  effect, no request and no piece of state was touched.
- **The voice-capture dock and the per-tone header controls keep their own styles
  and all of their behaviour.** They were deliberately left outside the palette
  sweep (§5, protected-media gate).
- **Wallpaper precedence is unchanged and still honours a real choice over the
  default.** `conversationWallpaper.ts` resolves cache → server → default, and the
  default only ever fills a gap.
- **`chatWallpaperContrast` was not weakened when it was rewritten — it was
  strengthened.** The old file asked whether the wallpaper eroded in-bubble text.
  Opaque bubble fills make that question arithmetically vacuous: the wallpaper
  cannot reach the text at all. The rewrite asserts that stronger premise
  explicitly, *then* asserts what the premise enables — the bubble still reads as
  a shape over all eleven fields — and it imports the fills instead of pinning
  copies of them, so a retune cannot leave the test passing against stale values.
- **Cross-client parity is intact.** Because the wallpaper id did not move, a
  wallpaper chosen on the web build is still the same wallpaper on native.

---

## 5. Verification

Every command, with its exact result.

| Command | Result |
|---|---|
| `./node_modules/.bin/jest src/theme/__tests__/chatGraphiteContrast.test.ts src/theme/__tests__/chatWallpaperContrast.test.ts src/screens/__tests__/ChatScreenGraphiteTheme.test.tsx` | **PASS** — 3 suites, **65 tests passed**, 4.685 s |
| `python scripts/realtime_audio_change_gate.py --base 6f1026a0 --head f4a5f2f1` | **PASS** — "No protected real-time audio path changed (20 file(s) inspected). Audio validation is not required for this change." exit 0 |
| `./node_modules/.bin/tsc --noEmit` | **PASS** — exit 0 |
| `scripts/protection/run_protection_suite.py` | **PASS** — 673 checks across 44 suites |
| `git merge-base --is-ancestor f4a5f2f1 origin/main` | **PASS** — exit 0, the commit is on `main` |

Two notes on how these were run, because both are traps in this repository:

- **`npx jest` and `npx tsc` are not safe here.** `npx tsc` resolves and installs
  `tsc@2.0.4` ("This is not the tsc command you are looking for", exit 1). Every
  command above used `./node_modules/.bin/…`.
- **The audio gate inspected 20 files, which is the whole commit.** A gate that
  reports a smaller number than the diff has not seen the change.

The 65 tests break down as: the palette audit (contrast for every text-on-surface
pair, the bubble-separation floor, the luminance-parity bound, and the two
asserted deviations), the wallpaper audit (the opacity premise, then bubble
separation over all eleven fields), and the screen-level suite (that
`ChatScreen`'s style sheet reads the tokens rather than literals).

---

## 6. Visual evidence

All under `reports/graphite-chat/`. Simulator: iPhone 17 Pro Max, iOS 26.5, dark
appearance.

| File | State captured |
|---|---|
| `00-launch.png` | Cold launch, app shell |
| `01-inbox.png` | Conversation list |
| `02-conversation.png` | Conversation open — header, canvas, both bubble kinds |
| `03-conv-4.png` | A second thread (id 4) — a different message mix |
| `03-conv-11.png` | A third thread (id 11) |
| `04-composer-multiline.png` | Composer grown to multiple lines |
| `05-keyboard-open.png` | Keyboard raised — composer dock against the safe area |
| `06-reply-composer.png` | Quoted-reply inset inside the composer |
| `07-native-build-conversation.png` | The same screen from the native Release build, not the dev bundle |

These files are **not committed**. They show a real signed-in account with real
names, avatars and message content, so they stay local deliberately; the paths are
recorded here so they can be produced on request.

---

## 7. Build and device evidence

Reported separately from the test evidence, and reported truthfully — including the
part that was initially claimed too strongly.

### 7.1 Simulator

The screen was verified in both a dev bundle and a Release build. The Release path
is the one that counts, and it has a trap: `expo export:embed` emits **plain JS**
(`var __BUNDLE_STA…`) while a Release `.app` needs **Hermes bytecode** (magic
`c61f bc03 c103 191f`). The bundle must be compiled with
`node_modules/react-native/sdks/hermesc/osx-bin/hermesc -emit-binary -O` before it
is installed, or the app launches on the previous bundle and every screenshot lies.
`07-native-build-conversation.png` is from a correctly compiled Release build.

A second trap, recorded because it cost real time: **`simctl install` does not
restart a running process.** A correct container with a stale screen is
indistinguishable from a failed install. `simctl terminate` followed by
`simctl launch` is what actually swaps the running JS.

### 7.2 Physical device — P3r7or (iPhone 16 Pro, `F45E640F-…`)

The chat build reached P3r7or in the mission's own session (`BUILD SUCCEEDED`,
container `25E8A919-28AE-4559-A581-F08A6A9CD897`, pid 24424). That artifact has
since been **superseded** by the Profile graphite build, which contains this
change; a `strings -a` lineage probe of the currently installed bundle confirms the
chat graphite survived (`#3A4049` and `#505761` both still present, as continuity
controls alongside the Profile-only markers).

**The qualification.** After deployment the device's conversation background did
not match the simulator's. That looked like a failed install. It was not. Reading
each device's `RCTAsyncLocalStorage_V1/manifest.json` off the device itself
(`devicectl device copy from --domain-type appDataContainer`) shows:

| | Simulator | P3r7or |
|---|---|---|
| Signed-in account | `user_id 15` — PulseSocMusic | `user_id 1` — roodycherie |
| `wallpaper.v1.pulsesoc_cosmic.<user>.<conv>` | every entry `pulsesoc_cosmic` | conv 5 → **`star_tunnel`**; conv 6, 12, 28, 30 → `pulsesoc_cosmic` |

The two devices are signed into **different accounts**, and the account on P3r7or
has a **real saved wallpaper choice** on conversation 5. The server's gap-filler
default is `pulsesoc_cosmic` (`pulse_communications_v2/service.py:894`), so a
server-confirmed `star_tunnel` can only come from a stored row — it is a choice,
not a default. §2.4's "anyone who picked one still gets exactly what they picked"
is the behaviour on screen. The header, bubbles, composer and control chips on that
same thread are graphite, because those are not wallpaper-dependent.

So: **the device did update.** What differs is per-viewer state, by design. Anyone
who wants graphite on a thread they have customised picks **"PulseSoc Graphite"** in
that conversation's control centre → Appearance → Wallpaper.

### 7.3 What was *not* proved on device

`devicectl` has no screenshot subcommand, so there is no device screenshot in §6.
The strongest available device claim is bundle identity — the installed bundle's
sha256 matches the simulator's byte for byte — plus the AsyncStorage read above.
That is stated as what it is, and not dressed up as a visual verification.

---

## 8. Git evidence

```
commit  f4a5f2f1413a4a7a60c9a6345aac57966d945a3c
date    2026-09-16 15:02:35 -0700
subject messenger: ship the graphite conversation surface
parent  6f1026a0709032c7cbee046cc8e0ccb9b3502768
        ("messenger: stop the delivery loop re-locking the page to write nothing")
stat    20 files changed, 952 insertions(+), 267 deletions(-)
```

`git merge-base --is-ancestor f4a5f2f1 origin/main` → exit 0. The commit is on
`main`, not parked on a branch.

The commit message carries the reasoning rather than a file list: why the palette is
not a `ThemeMode`, the two measured deviations with both numbers each, why the
bubbles are separated by hue instead of weight, why the wallpaper id could not move,
and what the rewritten wallpaper test now asserts.

A later commit on the Profile mission **promoted** the shared part of this palette
into `src/theme/graphite.ts` — the elevation ramp and the two text weights, which
both surfaces read. That promotion was proven value-preserving by extracting every
colour literal before and after: exactly **7 literals left** `chatGraphite.ts`
(`#20262E`, `#292E36`, `#343A42`, `#3A4049`, `#C8D0DB`, `#F7F8FA`,
`rgba(230,236,245,0.16)`) and **0 arrived**, each equal to the token that replaced
it. The conversation surface renders the same colours today as it did at
`f4a5f2f1`; it just no longer owns the ones it shares.

---

## 9. Remaining blockers

**None blocking.** Three items are open, and one correction is recorded.

1. **No device screenshot of the conversation surface.** `devicectl` cannot take
   one, and the substitutes available (bundle sha256 identity, a `strings -a`
   lineage probe with inverting markers and live controls, and the AsyncStorage
   read in §7.2) are evidence about the *build*, not about pixels. A device photo
   remains the one piece of verification only a human can supply.

2. **The graphite default cannot reach a conversation that already has a saved
   wallpaper.** This is the documented design — precedence puts a choice above the
   default, and the alternative would overwrite people's preferences on release —
   but it does mean "the chat is graphite now" is false for any thread someone
   customised. If the product decision changes, the place to change it is the
   precedence in `conversationWallpaper.ts`, not the token module, and it should
   change deliberately rather than as a side effect.

3. **56 feature-accent literals remain in `ChatScreen.tsx`** (§2.3). They are
   badges, banners, orbs and voice/video controls — not surface colour — so the
   graphite hierarchy is complete without them, but the screen is not yet fully
   tokenised. The voice-capture dock's share must stay literal until a mission
   that is actually about audio can touch it; the rest is ordinary follow-up.

4. **Correction to an earlier claim.** The first response to the device/simulator
   mismatch was to suspect the deployment. The evidence says otherwise, and it was
   only found by reading state off the device rather than re-reasoning about the
   build. Recorded here because the wrong diagnosis was the plausible one.

None of the open items touches the palette, the contrast audit, or the
protected-media boundary, all three of which are green (§5).
