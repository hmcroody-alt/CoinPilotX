# Graphite Profile — Implementation Final Report

**Verdict: PARTIAL.**

The design is implemented, tested and running on a device. Three of the mission's
enumerated *visual evidence* states could not be produced, for reasons that are
properties of this codebase and of `simctl` rather than of the work — each one is
named, root-caused and given substitute evidence in
[§13](#13-theme-comparison-evidence) and [§15](#15-remaining-blockers). Nothing in
this report is inferred from a passing type-check.

---

## 1. Starting point

| | |
|---|---|
| Starting branch | `main` at `origin/main` |
| Starting SHA | `0c7451c9a5704cb28c6885cb24f9ebf55e524ce5` — *"commerce: record what production actually answers after the deploy"* |
| Implementation commit | `a2f61e3c17625e172c6669bfb83ebebd2af74a30` — `feat(profile): implement graphite profile design system` |
| Diffstat | 14 files changed, 1934 insertions(+), 209 deletions(-) |
| Report commit | this file, added on the same branch directly after `a2f61e3c` |
| Worktree | `/Users/hmcherie/Desktop/cpx-profile-graphite`, created detached at `0c7451c9` |

The work was done in a dedicated worktree rather than the main checkout because the
main checkout is shared with other sessions and mutates underneath a long task; a
gate run against a moving tree proves nothing about the commit you push.

Worktree status at the implementation commit: clean. `reports/graphite-profile/`
(the visual evidence, [§12](#12-simulator--device-evidence)) is deliberately **left
untracked and uncommitted** — the screenshots contain a real signed-in account, real
member names and a photograph of a child. Paths are recorded here; the images are
not committed.

---

## 2. Design-system architecture reused

Nothing new was invented. Three existing mechanisms carried the whole change.

**Surface-scoped fixed token modules.** The app has ~26 of these
(`src/theme/*.ts`, each `as const` with an exported type). This is the convention
for "a defined palette for one surface", and it is the convention *because*
`buildTheme` pins the active appearance:

```ts
// src/theme/ThemeContext.tsx:212-214
// Dark is the only released appearance for now.
// Keep the other theme implementations intact for future activation.
const activeTheme: ThemeMode = "dark";
```

A new `ThemeMode` would type-check and render nowhere. A surface module renders.

**`createThemedStyles`** (`src/theme/themedStyles.ts:28`) — the Proxy-based lazily
resolved, memoised `StyleSheet` factory keyed on a module-level `paletteEpoch`. It
is the only theme-reactive path available to module-scope styles, and it is the
project's established memoisation pattern. Every style object added by this change
goes through it.

**In-place palette mutation.** `applyPaletteToLegacyColors(palette)`
(`ThemeContext.tsx:258-265`) mutates the shared `legacyColors` object *in place* and
then calls `bumpThemedStylesEpoch()`. That is why reading `colors.*` inside a
`createThemedStyles` factory is theme-reactive at all, and why a palette-*identity*
cache key would be wrong (the object never changes identity). The one-entry cache in
`profileSurface()` is therefore keyed on the six palette **fields** it reads, not on
the object.

---

## 3. Root cause of the inconsistent black surfaces

Four layers compounded. Each was doing something defensible on its own, which is
why the screen looked wrong and no single file looked wrong.

1. **`<GalacticAtmosphere variant="profile">` painted the canvas.** A near-black
   `["#02050A", "#040A14", "#06101C"]` gradient carrying 23 stars, two drifting
   nebulae, a planet, a galaxy smear, three dust motes and a closing scrim. **That
   layer is the cloudy, smoky appearance the design review rejected.** It could not
   be tuned into graphite: the haze is the nebulae and the scrim, not the gradient
   underneath them, so dimming the gradient would have left a dim haze.
2. **`ProfileHeader.root` was `colors.background`** — `#050910`, near-black, and a
   different near-black from the atmosphere's first stop.
3. **`profileNeon.panel` / `panelRaised` were translucent** navy glass composited
   over that near-black. A card's colour therefore depended on what happened to be
   behind it.
4. **`moduleIcon` had no fill at all** — only an `${accent}12` wash (7% alpha), so
   Profile OS tiles were holes in the canvas rather than objects on it.

A fifth finding, `styles.textTile`'s hardcoded `#0D2030`, turned out to be **dead
code** and was deleted. It also became the change's lineage marker
([§12](#12-simulator--device-evidence)).

The fix is layer 1: Profile stops subscribing to the atmosphere and paints
`<ProfileCanvas />` instead — one opaque two-stop vertical graphite run with no
children. **`GalacticAtmosphere` itself is untouched**, so Home, Reels, Messages and
every other variant keep their atmosphere. Layers 2–4 then became consistent by
being given real tokens.

Two stops, not one, because the approved treatment is a restrained vertical run (the
chat surface uses exactly the same pair from the same ramp) and because a single flat
colour across a ~1000pt scroll reads as a dead sheet rather than a surface.

---

## 4. Files changed

| File | Δ | What |
|---|---|---|
| `src/theme/graphite.ts` | **+99 (new)** | The shared ramp. Promoted from chat's local constants. |
| `src/theme/profileGraphite.ts` | **+211 (new)** | Profile's surface mapping over the ramp + theme fallthrough. |
| `src/components/ProfileCanvas.tsx` | **+61 (new)** | The root-cause fix: the opaque canvas. |
| `src/theme/chatGraphite.ts` | 31 | Rewired to consume `graphite.*`. **No value changed** — proven in [§5](#5-tokens-added-and-reused). |
| `src/components/ProfileHeader.tsx` | 308 | Hero, avatar, statistics panel, Profile OS tiles. |
| `src/screens/ProfileScreen.tsx` | 133 | Canvas swap, tabs, states, nav inset. |
| `src/theme/profileNeon.ts` | 25 | Translucent panels retired; decorative-only entries kept. |
| `src/theme/__tests__/profileGraphite.test.ts` | **+262 (new)** | 21 tests. |
| `src/components/__tests__/ProfileHeader.graphite.test.tsx` | **+494 (new)** | 26 tests. |
| `src/screens/__tests__/ProfileScreen.graphite.test.tsx` | **+468 (new)** | 23 tests. |
| `src/components/__tests__/ProfileHeader.test.tsx` | 30 | Existing 26 tests kept green against new fills. |
| `src/screens/__tests__/ProfileScreen.actions.test.tsx` | 7 | " |
| `src/screens/__tests__/ProfileScreen.media.test.tsx` | 7 | " |
| `src/screens/__tests__/ProfileScreen.perf.test.tsx` | 7 | " |

No backend file, no dependency, no `package.json`, no native module, no config.

---

## 5. Tokens added and reused

The mission's instruction was explicit: *inspect the approved chat implementation,
reuse its existing semantic background token as the authoritative Profile canvas
colour, do not approximate it separately, and if no centralised token exists,
promote one through the existing design system.*

No centralised token existed — the approved chat values were **literals local to
`chatGraphite.ts`**. So they were **promoted** into a new shared
`src/theme/graphite.ts`, and `chatGraphite.ts` now reads them back from there.

That promotion is value-preserving, and that is checkable rather than assertable.
Extracting every colour literal from `chatGraphite.ts` before (`f4a5f2f1`) and after
(`a2f61e3c`):

```
removed (now referenced as graphite.*):
  #20262E  #292E36  #343A42  #3A4049  #C8D0DB  #F7F8FA  rgba(230, 236, 245, 0.16)
added:
  (none)
```

Exactly seven literals left the file, zero arrived, and each one that left equals the
`graphite.*` token that replaced it. Chat's own bubble and link colours (`#080B0F`,
`#24549B`, `#323842`, `#4D8FE9`, `#505761`, `#7BDFFF`, and its two border rgba
values) stayed local, because they are chat's, not shared.

Two steps are **new** and exist only because Profile needs elevation chat does not
have: `raised` (`#454C56`) and `raisedStrong` (`#474E58`).

---

## 6. Exact final colour mapping

The shared ramp, ordered by measured HSL lightness — monotonic by construction:

| Token | Hex | HSL L | Δ vs `canvasTop` | Used for |
|---|---|---|---|---|
| `sunken` | `#20262E` | 15.29% | −10.40pp | Inset controls, floating nav surface |
| `chrome` | `#292E36` | 18.63% | −7.06pp | Header, footer, nav dock, both safe areas |
| `canvasBottom` | `#343A42` | 23.14% | −2.55pp | Canvas, bottom stop |
| `canvasTop` | `#3A4049` | 25.69% | — | Canvas, top stop |
| `raised` | `#454C56` | 30.39% | **+4.70pp** | Cards and panels on the canvas |
| `raisedStrong` | `#474E58` | 31.18% | +5.49pp | Tiles sitting directly on the canvas |
| `steelBorder` | `rgba(74, 85, 98, 0.65)` | — | — | Every restrained 1px edge |
| `quietDivider` | `rgba(230, 236, 245, 0.16)` | — | — | Hairlines and lit top edges |
| `primaryText` | `#F7F8FA` | 97.45% | — | Counts, names, headings |
| `secondaryText` | `#C8D0DB` | 82.16% | — | Labels, meta |

`raised` and `raisedStrong` are **alternatives at one elevation, not a stack**:
`raisedStrong` is 1.24:1 against the canvas but only **1.03:1 against `raised`**, so
nesting one in the other produces no visible step. The token doc says so.

### Deviations from the brief's fallback hexes, and why

The brief named its hexes as *"design targets, not permission to bypass the existing
theme architecture,"* to be used *"only where equivalent tokens don't already
exist."* Where an equivalent existed, the existing token won.

| Role | Brief target | Shipped | Why |
|---|---|---|---|
| Canvas | `#292D33` | `#3A4049` → `#343A42` | **The authoritative reuse.** This *is* the approved chat canvas, which the brief ranked above its own fallback. |
| Dark chrome | `#1D2127` | `#292E36` | Chat's already-approved header/dock step, same ramp. |
| Elevated | `#33383F` | `#454C56` | Sits on a lighter canvas, so it must be lighter to hold the same separation. |
| Floating nav | `#20262E` | `#20262E` | **Exact match.** |
| Steel border | `~#4A5562` | `rgba(74, 85, 98, 0.65)` | **Exact hex** (`#4A5562` = rgb 74,85,98) at the "restrained opacity" asked for. |
| Primary text | `~#F5F7FA` | `#F7F8FA` | Existing audited token, 2 units off. |
| Secondary text | `~#B8C0CC` | `#C8D0DB` | Existing audited token. `#B8C0CC` measures 4.73:1 on `raised` — it passes, but shipping it would give the product two secondary weights differing by an amount nobody can see. |

The canvas deviation is the load-bearing one, and it is checkable against the brief's
own numbers. The brief's own pair — canvas `#292D33` (18.04%) and elevated `#33383F`
(22.35%) — differ by **4.31pp** of HSL lightness. That fixes the meaning of the
statistics-panel requirement *"~4–6% lighter than canvas"* as percentage points, not
relative percent. Shipped: **+4.70pp**. Inside the range, measured on the metric the
brief's own numbers imply.

---

## 7. Components updated

**`ProfileCanvas`** (new). `<LinearGradient>` of two stops, `StyleSheet.absoluteFill`,
`pointerEvents="none"`, `accessibilityElementsHidden`. Bottom-most child, fully
opaque. Nothing is composited *over* the UI — which is the precise failure mode that
produced the rejected look. No `BlurView`, no animation, no shadow, no per-frame
work: two stops resolved once, memoised, static for the life of the screen.

**Cover photo.** Preserved untinted and unblurred. The full-bleed 0.04-alpha "grain"
tint is gone — over an uploaded photograph it was a translucent layer across the
whole image, and 0.04 alpha is still fog. The join to the body is **one localised
bottom-edge fade**:

```tsx
// src/components/ProfileHeader.tsx:354-358
<LinearGradient
  colors={["transparent", "transparent", surface.canvasTop]}
  locations={hasCover ? [0, 0.74, 1] : undefined}
```

With a real cover the ramp is confined to the bottom **26%**, where the avatar needs
it and the picture is already gone. Evenly spaced stops (the no-cover generated
field, where there is no photograph to protect) would start the ramp at half the
hero's height and crush everything below the subject.

**Avatar.** Photo, camera/edit control, online indicator and verification indicator
all preserved and unchanged in function. Three concentric decorative rings became
one: the breathing outer aura (`pulseWave`, an animated 320pt border) and the static
rotated `ringOrbit` were purely decorative and are gone; `ringGlow` kept its shape
and lost its `shadowOpacity: 0.9 / shadowRadius: 22`. Nothing functional was removed
to achieve that — the indicators are separate elements.

**Statistics panel.** `backgroundColor: surface.raised` (+4.70pp, 1.20:1 — a visible
step, well under the 3:1 that would make a card compete with its own contents), a
1px `surface.border` edge, and a **solid** `quietDivider` top-edge highlight. Column
separation was a `StyleSheet.hairlineWidth` divider and is now a **1px** one, so it
survives on every scale factor. `statValue` gained `color: surface.primaryText` —
8.16:1 on the panel. No shadow, no glow, no blur anywhere in the panel.

**Profile OS tiles.** `moduleIcon` gained `backgroundColor: surface.raisedStrong` —
the fill that turned the tiles from holes into objects. `moduleGlow`'s constant
shadow (`shadowOpacity: 0.55`, `shadowRadius: 10`, drawn always, not on press) is
deleted: the mission forbids constant glow, and a permanently lit shadow is also
per-frame compositing for no information. Icon, label, function, state, destination
and the existing orange/teal/purple/gold identity accents are unchanged. `moduleIcon`
is 58×58 inside a larger pressable, above the 44pt minimum.

**Subject correctness.** The grid represents the profile **being viewed**, not
automatically the signed-in user. Verified on device, not just in tests: another
member's profile shows visitor actions instead of Edit Profile, the visitor bio copy
*"This member has not added a bio yet."*, the heading **"ROODY's Profile OS"**, and a
3-tile grid against the owner's 18 — identical styling, different contents.

---

## 8. Navigation-overlap correction

The brief: compute the inset from actual nav height + safe-area inset + breathing
space, and never hardcode for one iPhone size. The mechanism already existed and
Profile was not using all of it.

```ts
// src/navigation/bottomNavMetrics.ts:90-94
export const BOTTOM_NAV_CONTENT_CLEARANCE =
  BOTTOM_NAV_DOCK_PADDING_TOP +          // 10
  BOTTOM_NAV_DOCK_PANEL_MIN_HEIGHT +     // 106
  BOTTOM_NAV_CREATE_OVERHANG +           // derived, currently 2
  BOTTOM_NAV_CONTENT_GAP;                // 12  → 130
```

`BOTTOM_NAV_CREATE_OVERHANG` is itself *derived* from the Create button's geometry
rather than written down, because — in the module's own words — *"a literal here is a
number nobody recomputes when the Create button changes."* And the clearance
deliberately **excludes** the safe-area inset *"rather than baking in one device's
home-indicator inset"*; the caller adds the live inset:

```ts
// src/navigation/BottomNavVisibility.tsx:247
const paddingBottom = Math.max(insets.bottom, 12)
  + (docked ? BOTTOM_NAV_CONTENT_CLEARANCE + playerClearance : BOTTOM_NAV_UNDOCKED_PADDING);
```

At a 34pt inset: **docked 164**, undocked 58. With the Radio mini-player loaded,
`BOTTOM_NAV_ACTIVE_PLAYER_CLEARANCE` (86) is added on top.

What actually changed on Profile: the **error and empty states** were using the
shell's default `bottomDock`, which reserves `Math.max(insets.bottom, 12)` — the safe
area **and nothing else**. `LogiNexusStatePanel` is `flex: 1`, so on a docked Profile
its lower edge, its border and the "Try again" button's breathing room all ran
underneath the dock. They now pass `bottomDock={false}` plus `dock.contentPadding`,
which is the identical derived number the loaded screen's list uses. One source for
the clearance, for every state of the screen.

Hide-on-scroll-down / show-on-scroll-up, visibility near the top, visibility on short
pages, availability under overlays, the Reels-specific motion rule, and every action
and selected state are untouched — `dock.handlers` is passed straight through.

Measured on device rather than asserted: at the end of the scroll with the dock
visible, the last content ends at ~y712 against a dock top edge of ~y740.
(`05-nav-overlap-fixed-bottom-of-scroll.png`.)

---

## 9. Accessibility verification

Contrast, computed from the shipped hexes (WCAG 2.1 relative luminance; borders and
fills composited source-over before measuring):

| Text | on `canvasTop` | on `canvasBottom` | on `chrome` | on `raised` | on `raisedStrong` |
|---|---|---|---|---|---|
| `primaryText` `#F7F8FA` | 9.83:1 | 10.80:1 | 12.85:1 | 8.16:1 | 7.91:1 |
| `secondaryText` `#C8D0DB` | 6.72:1 | 7.38:1 | 8.78:1 | 5.58:1 | 5.40:1 |

Every pairing clears **AA for normal text (4.5:1)**; the weakest, secondary on
`raisedStrong`, is 5.40:1. Primary text clears AAA (7:1) on every surface.

Non-text separation: `raised` vs canvas 1.20:1, `raisedStrong` vs canvas 1.24:1,
`chrome` vs `canvasBottom` 1.19:1. The steel border composites to 1.316:1 against the
canvas when drawn on a `raised` card — stronger than the fill manages alone, which is
the point: the fill states the shape, the border finishes it.

*(One correction landed with this report: `graphite.ts` documented primary text as
"12.6:1 on the canvas" and secondary as "7.0:1". Recomputation gives 9.83:1 and
6.72:1 — the old figures were `chrome`'s, misattributed. The docstring now carries
the measured values for all three surfaces. No colour changed.)*

**No state is communicated by colour alone.** Tile identity accents ride on top of an
icon and a text label; selected tabs carry `accessibilityState.selected`; the
statistics panel's column separation is a 1px divider, i.e. geometry.

**Colour-blind distinguishability** follows from the ramp being achromatic — every
graphite step differs in lightness, which no colour-vision deficiency removes. The
functional accents (teal, orange, purple, gold, blue) are retained but never
load-bearing.

**Dynamic Type.** Verified at `content_size accessibility-extra-large`. Tile labels
truncate and overlap the next row at that setting. **This is pre-existing, and that
was proven rather than argued**: `moduleIcon`'s and `moduleLabel`'s geometry is
byte-identical across the diff (only `backgroundColor` and `color` were added), and
then the *pre-change* bundle was reinstalled and re-shot at the same setting —
identical truncation. Baseline `07-BASELINE-pre-change-dynamic-type-xl.png` against
`06-dynamic-type-accessibility-xl.png`. Not a regression, and not fixed here: fixing
it is a layout change to tiles across the app and is out of this mission's scope.

**VoiceOver.** Labels, order, roles and selected states are unchanged — no
`accessibilityLabel`, `accessibilityRole` or `accessibilityState` was added, removed
or reworded by this diff. `ProfileCanvas` is explicitly hidden from the tree
(`accessibilityElementsHidden`, `importantForAccessibility="no-hide-descendants"`),
so the new layer adds nothing for a screen reader to step through.

**Reduce Motion / Reduce Transparency / Increased Contrast.** Honoured, but with an
honest caveat that matters. These are **app-owned settings in this codebase, not OS
settings**: they originate in `src/settings/schema.ts:63-65, 172-174, 356-358`, are
toggled in `src/screens/settings/AccessibilitySettingsScreen.tsx:96-97`, and are
consumed at `ThemeContext.tsx:217, 220, 246`. `xcrun simctl ui <dev>
increase_contrast enabled` therefore **cannot reach the app**, and `simctl ui` has no
reduce-motion or reduce-transparency option at all (it supports only `appearance`,
`increase_contrast`, `content_size`). See [§15](#15-remaining-blockers).

What *is* verifiable: the surfaces this change owns have nothing for those settings
to act on. The canvas is an opaque static gradient — no `BlurView`, no translucency,
no animation, no shadow. The change *removed* the two continuous animations on the
surface (`pulseWave`'s breathing aura) and every constant shadow. That is the
strongest form of honouring Reduce Motion and Reduce Transparency: there is nothing
left to reduce.

---

## 10. Performance verification

Against the mission's explicit prohibitions:

- **No full-screen `BlurView`** — none added; the rejected atmosphere's scrim was
  *removed*.
- **No per-frame gradient recalculation** — `ProfileCanvas` is `memo`'d, takes only a
  `testID`, and resolves two stops once.
- **No large animated shadows** — `ringGlow`'s `shadowRadius: 22 / shadowOpacity: 0.9`
  and `moduleGlow`'s always-on `shadowRadius: 10 / shadowOpacity: 0.55` were both
  deleted.
- **No repeated theme-object creation during render** — `profileSurface(palette)` is
  one-entry cached, keyed on the six palette fields it reads (not on object identity,
  which never changes: [§2](#2-design-system-architecture-reused)).
- **No unmemoised tile styles** — every style object goes through
  `createThemedStyles`.

Net layer count went **down**: 23 stars, two nebulae, a planet, a galaxy smear, three
dust motes, a scrim, two decorative rings and two constant shadows were removed and
replaced by one static two-stop gradient. Image caching, list virtualisation and lazy
loading are untouched — no list, image or data-fetch code is in the diff.

Press feedback reuses the existing animation and haptic systems at
`PROFILE_PRESS_DURATION_MS = 140`, inside the mission's 120–180ms window. No
continuous animation. No new dependency.

---

## 11. Tests

### Added — 70 new tests across 3 suites

`src/theme/__tests__/profileGraphite.test.ts` (**21**) — the ramp's monotonic
ordering, the `raised`/`raisedStrong` non-stacking constraint, contrast floors,
non-graphite palette fallthrough, the cache's key fields, and `GRAPHITE_THEME_CANVAS`.

`src/components/__tests__/ProfileHeader.graphite.test.tsx` (**26**) — canvas fills,
statistics-panel fill/border/divider/count contrast, tile fills and identity accents,
the localised cover fade with and without a cover, absence of glow and shadow, tile
pressed/selected/disabled/loading states, avatar indicators surviving the ring
simplification.

`src/screens/__tests__/ProfileScreen.graphite.test.tsx` (**23**) — `ProfileCanvas`
replacing `GalacticAtmosphere`, docked vs undocked `paddingBottom` (**164 / 58** at a
34pt inset), the error and empty states' corrected clearance, Posts/Media/About tabs,
signed-in vs viewed-user subject resolution, with/without bio, verified/unverified,
active/inactive, premium/non-premium, loading/empty/offline/error.

`src/components/__tests__/ProfileHeader.test.tsx` — the pre-existing **26** kept green
against the new fills.

Two harness notes worth recording, both of which had silently produced vacuous
assertions before being fixed: RNTL 12.9.0 **excludes hidden elements by default**,
so the canvas needs `{ includeHiddenElements: true }`; and `fireEvent(el, "pressIn")`
looks for an `onPressIn` prop that `Pressable` never exposes and **silently no-ops** —
driving a real pressed state requires `responderGrant`/`responderRelease` with
`persist: () => {}` and `currentTarget: { measure: () => {} }` on the synthetic event,
plus fake timers to clear the 130ms `DEFAULT_MIN_PRESS_DURATION`.

### Results — every command, verbatim

| Command | Result |
|---|---|
| `npx tsc --noEmit -p mobile-native/tsconfig.json` | **exit 0**, no output |
| `npx jest` (full suite) | **450 suites, 7799 tests passed, 0 failed** |
| `npx jest` (the 4 profile suites) | **4 suites, 96 tests passed, 2.163 s** |
| `npm run i18n:validate` | **OK, 11 locales, catalog 1.0.0** |
| `python3 scripts/protection/run_protection_suite.py` | **"PulseSoc protection suite passed: 673 checks across 44 suites"**, exit 0 |
| `scripts/realtime_audio_change_gate.py --base 0c7451c9 --head a2f61e3c` | **"No protected real-time audio path changed (14 file(s) inspected)."** exit 0 |
| `scripts/protection/native_theme_parity_gate.py` | 8 palettes × 23 colours, 8 metrics, 6 durations, the `"dark"` pin — exit 0 |
| `scripts/protection/native_background_parity_gate.py` | 250 values across 9 tables — exit 0 |
| `scripts/protection/native_layout_parity_gate.py` | 8 geometry values — exit 0 |
| `npx expo-doctor` | **15/17** |

The protection suite was run **on the commit**, in the isolated worktree — not on the
live shared tree, where the result would have been meaningless.

Pre-existing, unrelated, and not introduced here:
- `i18n:validate` emits 4 advisory plural warnings (ar/es/fr/pt). Advisory, not a gate.
- `expo-doctor`'s 2 failures are RN Directory metadata ("Untested on New
  Architecture: react-native-callkeep, react-native-voip-push-notification"; "No
  metadata available: pulse-now-playing, pulse-video-mixer") and patch drift
  (expo 54.0.36/~54.0.37, expo-constants 18.0.13/~18.0.14, expo-file-system
  19.0.23/~19.0.24, expo-local-authentication 17.0.8/~17.0.9,
  jest-expo 54.0.17/~54.0.18). Both are present on `origin/main`.

---

## 12. Simulator / device evidence

Reported separately from the test results, and truthfully.

**Build.** The diff is JS-only — no native module, no `package.json`, no Podfile — so
the graphite build was produced by re-bundling rather than a full `xcodebuild`
(DerivedData was 7.8G against 9.8Gi free; a full Release build was the riskier path).

```
npx expo export:embed --platform ios --dev false --entry-file index.ts \
  --bundle-output /tmp/pg-bundle/main.jsbundle --assets-dest /tmp/pg-bundle/assets
node_modules/react-native/sdks/hermesc/osx-bin/hermesc -emit-binary -O \
  -out /tmp/pg-bundle/main.hbc /tmp/pg-bundle/main.jsbundle
```

Two traps were hit and are worth recording. `npx react-native bundle` does not work
in this repo ("react-native depends on @react-native-community/cli for cli commands")
— the Expo SDK 54 path above is the working one, and the entry is `index.ts`. And
`expo export:embed` emits **plain JavaScript** (`var __BUNDLE_STA…`) while a Release
`.app` carries **Hermes bytecode** (magic `c61f bc03 c103 191f`); swapping the plain
JS in would have been a silent lineage mismatch. Hence the `hermesc` step:
11,938,573 B of JS → 14,199,105 B of bytecode with the correct magic (only warnings
were pre-existing undeclared `ReadableStream`/`WritableStream`).

Then: swap `main.jsbundle`, rsync assets, `codesign --force --sign -
--timestamp=none` the app bundle only (the 25 `Frameworks` entries keep their
existing ad-hoc signatures), `simctl install` **over the top** — which preserves
sign-in state where `uninstall` would have signed the account out irrecoverably.

**Lineage was proven three times, not asserted** — for the built bundle, for the
swapped `.app`, and for the *installed container* copy. Each probe reported old and
new **counts** side by side, with live control strings, because a
`strings … | grep -c` probe silently reports `0` on a missing file and would have
"passed" on nothing:

```
marker         old  new
#0D2030 (deleted dead code)     1  ->  0    inverted
#474E58 (new raisedStrong)      0  ->  1    inverted
Pulse DNA      (control)        1  ->  1    live both sides
Reduce Motion  (control)        1  ->  1    live both sides
```

**Captured on iPhone 17 Pro Max simulator** (`reports/graphite-profile/`, uncommitted):

| File | State |
|---|---|
| `00-launch.png` | Home — atmosphere intact, confirming zero blast radius |
| `01-profile-hero.png` | Hero + identity |
| `02-profile-os-grid.png` | Profile OS grid, owner's 18 tiles |
| `03-tabs-offline-error.png` | Posts/Media/About + offline/error |
| `04-hero-cover-fade-dock-visible.png` | Cover→body fade, dock visible near top |
| `05-nav-overlap-fixed-bottom-of-scroll.png` | End of scroll: content ends ~y712, dock top ~y740 |
| `06-dynamic-type-accessibility-xl.png` | `content_size accessibility-extra-large` |
| `07-BASELINE-pre-change-dynamic-type-xl.png` | Same setting, **pre-change bundle** |
| `08-BASELINE-pre-change-profile.png` | Pre-change Profile — the rejected look |
| `09-no-cover-accent-field.png` | No cover: generated field |
| `10-AFTER-graphite-profile-with-cover.png` | The approved graphite result |
| `11-system-appearance-light.png` | `simctl ui appearance light` — see §17 |
| `12-increase-contrast-enabled.png` | `simctl ui increase_contrast enabled` — **proves nothing**, see §17 |
| `13-another-users-profile.png` | Visitor actions, visitor bio, "ROODY's Profile OS" |
| `14-visitor-tile-grid-and-tabs.png` | Visitor's 3-tile grid vs owner's 18 |
| `15-iphone-16-pro-SIGNED-OUT-blocked.png` | Smaller viewport — **blocked**, see §17 |

Another member's profile was reached with `xcrun simctl openurl <dev>
"pulsesoc://pulse/profile/<handle>"` (route `ProfileDetail: { path:
"pulse/profile/:profileKey" }`, `src/navigation/linking.ts:272-274`) rather than by
tapping — after a mis-registered drag gesture navigated into an unintended screen and
a subsequent stray tap opened the Sign out confirmation, which was cancelled
immediately with the session intact. Deep links after that point.

Scrolling requires `mouse_move` → `left_mouse_down` → several `mouse_move` →
`left_mouse_up`; a `left_click_drag` registers as a tap on this simulator.

---

## 13. Theme comparison evidence

Every Profile colour resolves through `profileSurface(palette)`, which branches once:

```ts
// graphite / dark              non-graphite palettes
canvasTop:  graphite.canvasTop   palette.background
chrome:     graphite.chrome      palette.surfaceRaised
raised:     graphite.raised      palette.surface
border:     graphite.steelBorder palette.border
coverScrim: `${graphite.canvasBottom}73`  `${palette.background}73`
isGraphite: true                 false
```

So **Black paints its true black and White its plain white**, flat — those palettes
resolve both canvas stops to the same value, and a gradient between two equal colours
is a solid fill. No Profile-specific hardcoded colour survives to break another theme
(the one that existed, `#0D2030`, was dead and is deleted). Nothing is globally
replaced: `colors.ts` and `ThemeContext.tsx` are not in the diff, and the theme
parity gate confirms all 8 palettes × 23 colours unchanged.

"Switching updates Profile immediately" is verified through the mechanism that
actually governs it — `applyPaletteToLegacyColors` mutating `legacyColors` in place
followed by `bumpThemedStylesEpoch()` — not by re-rendering with a different prop.

**But device evidence for Black, White and Light Futuristic is impossible on this
build**, because `ThemeContext.tsx:214` pins `activeTheme = "dark"`. Those palettes
are unreachable at runtime. The unit tests, which reach them via the `__testing`
export (`ThemeContext.tsx:382+`: `ThemeContext, LIGHT_FUTURISTIC, DARK, BLACK, WHITE,
HIGH_CONTRAST_DARK`), are the only honest verification available, and they are green.
Claiming a device screenshot of a White-theme Profile would require unpinning a
released appearance, which is outside this mission.

---

## 14. Protected-media audit

The mission's absolute lock: do not touch audio, livestream, video calls or audio
calls — code, config, tests, dependencies, session ownership, permissions, routing,
native capabilities, background modes, or deployment variables.

- `scripts/realtime_audio_change_gate.py --base 0c7451c9 --head a2f61e3c` →
  **"No protected real-time audio path changed (14 file(s) inspected)."** exit 0.
- The full protection suite (**673 checks across 44 suites**, covering livestream,
  reels, chat, uploads, camera, payments, auth and navigation) passed on the commit.
- Zero `Audio.setAudioModeAsync`, `AVAudioSession`, LiveKit/Agora, microphone, track,
  publication or background-mode reference in the diff. No `expo-av` call site added
  (the legacy allowlist is capped at six files; this change adds none).
- No dependency, no `package.json`, no `app.json`, no `eas.json`, no Podfile, no
  entitlement, no environment variable.
- The re-signing step touched only the app bundle's own signature. The 25 prebuilt
  `Frameworks` — which include the realtime media frameworks — kept their existing
  ad-hoc signatures untouched. (Re-signing them with
  `CODE_SIGNING_ALLOWED=NO` semantics is what makes the app crash at launch under
  dyld; it was deliberately not done.)

---

## 15. Remaining blockers

Five, in descending order of how much they cost the verdict. All are environmental;
none is an unfinished piece of the design.

1. **Black / White / Light Futuristic have no device evidence.**
   `ThemeContext.tsx:214` pins `activeTheme = "dark"`, so they are unreachable at
   runtime. Evidence is the `__testing`-based unit tests only. *To close:* unpin the
   appearance selector — a separate, releasable decision.
2. **`simctl ui increase_contrast` does not reach the app.** `highContrast`,
   `reduceMotion` and `reduceTransparency` are app-owned settings
   (`src/settings/schema.ts`), not read from iOS. `12-increase-contrast-enabled.png`
   is therefore an identical screenshot that **proves nothing**, and it is labelled
   as such. *To close:* toggle them in the app's own Accessibility settings and
   re-shoot. Note the Profile surfaces have no transparency or motion for them to act
   on ([§9](#9-accessibility-verification)).
3. **Reduce Motion / Reduce Transparency cannot be toggled by `simctl` at all** — it
   supports only `appearance`, `increase_contrast`, `content_size`. Same closure path
   as (2).
4. **The smaller-viewport capture is blocked.** `PulseSoc iPhone 16 Pro
   (C980AEE0-…)` boots and installs fine but is **signed out**, and signing in is out
   of bounds. The 393×852 coverage comes from RNTL metrics instead
   (`paddingBottom === 164` docked / `58` undocked), which is the number that
   governs the overlap. The file is named
   `15-iphone-16-pro-SIGNED-OUT-blocked.png` rather than presented as a capture.
5. **Dynamic Type truncation at accessibility-XL is pre-existing and remains.** Proven
   pre-existing by re-shooting the pre-change bundle at the same setting
   ([§9](#9-accessibility-verification)). Fixing it is a tile-layout change across the
   app, not a colour change, and is out of scope here.

Two smaller observations, deliberately left alone:

- `ContentCover.tsx:144` paints its image-decode placeholder with
  `theme.colors.surfaceRaised` (`#111f2a`), so a navy flash can appear over a
  graphite tile for one decode. It is a *correct* theme token in a component shared
  with the feed; changing it would recolour the feed, which the scope rules forbid.
- An orphaned `simctl recordVideo` process was left behind by an interrupted capture
  and was terminated; no artifact from it is referenced here.

---

## 16. Rollback

Three levels, cheapest first.

**Device only** (restores the pre-change app in place, keeps the session):

```bash
cp /tmp/pg-bundle/main.jsbundle.OLD-BACKUP "<app>/main.jsbundle"   # 14,228,672 B, Hermes
codesign --force --sign - --timestamp=none "<app>"
xcrun simctl install <device-udid> "<app>"
```

This artifact is not hypothetical — it was actually exercised this session (installed,
screenshotted as `07-`/`08-`, then reverted) to prove the Dynamic Type baseline.

**Source, whole change:**

```bash
git revert --no-edit a2f61e3c17625e172c6669bfb83ebebd2af74a30
```

Clean: the three new source files and the three new test files disappear, and
`chatGraphite.ts` returns to its own literals. **Chat is unaffected either way** — the
promotion changed no value ([§5](#5-tokens-added-and-reused)), so reverting it changes
none back.

**Source, canvas only** (keep the tokens and tests, restore the old backdrop): swap
`<ProfileCanvas />` for `<GalacticAtmosphere variant="profile">` at
`ProfileScreen.tsx:617` and `:753`. `GalacticAtmosphere` was never modified, so it is
a two-line change. This is the granular escape hatch if the graphite canvas is the
only thing rejected.

---

## 17. Verdict

**PARTIAL.**

Everything the mission asked to be *built* is built, on a device, with lineage proven
by an inverting marker against live controls: the layered graphite hierarchy over a
promoted shared token, the opaque canvas that removes rather than masks the rejected
haze, the localised cover fade, the simplified rings with every functional indicator
intact, the statistics panel at +4.70pp with a solid highlight, filled Profile OS
tiles with no constant glow and correct subject resolution, and the navigation
clearance corrected for the states that were still using the shell's default.
7799 tests, 673 protection checks and four parity gates are green on the commit, and
the audio gate reports no protected path touched.

It is not PASS because three enumerated visual states have no honest device evidence —
three themes that the codebase pins out of reach, two accessibility settings the
simulator cannot toggle, and a smaller device that is signed out — and a fourth
(Dynamic Type) shows a real defect that I proved was pre-existing rather than fixed.
Each is named above with the exact reason and what would close it. Calling this PASS
would require presenting a screenshot that proves nothing as though it proved
something.
