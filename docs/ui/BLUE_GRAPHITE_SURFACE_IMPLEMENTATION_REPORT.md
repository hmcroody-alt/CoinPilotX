# Blue-Graphite Surface — Home Card and Bottom Dock

Implementation commit: `98de38a3`
Base: `origin/main` @ `e429a6cf`
Scope: the Pulse Network / Curious hero card, and the shared floating bottom navigation.

**Status: code complete and green in CI-equivalent checks. NOT yet visually verified on
a simulator or on the physical iPhone. This report does not claim PASS.** See
[Outstanding](#outstanding) for exactly what is missing.

---

## Root cause: where the near-black actually came from

Neither surface's colour was where a reader would look for it. This is the finding that
matters most, because it is why earlier attempts to "lighten the card" would have had no
visible effect at all.

### The hero card

The card is `PulseNetworkHero` in `mobile-native/src/screens/HomeScreen.tsx`. Three
candidate surfaces all turn out to be innocent:

| Candidate | Value | Why it is not the cause |
| --- | --- | --- |
| `styles.hero` | `rgba(5, 15, 29, 0.03)` | 3% alpha — effectively clear |
| `LogiNexusPanel` `tone="default"` | `glassStrong` | overridden by the `style` array the hero passes |
| `styles.heroAtmosphere` | none | a clipping wrapper only |

The actual near-black was the **base gradient inside `GalacticAtmosphere`** — a hardcoded
`["#02050A", "#040A14", "#06101C"]` ramp (relative luminance 0.0014 → 0.0049, i.e. all but
black) painted by a component whose name suggests decoration rather than surface colour.
The card is opaque because that layer is opaque.

### The dock

`LogiNexusBottomNavigation` in `mobile-native/src/navigation/GlobalNavigation.tsx`.
Here the cause was conventional — `styles.bottomPanel.backgroundColor` was
`rgba(7, 14, 32, 0.95)`.

### Why this was a single defect and not two

A near-black plate on an indigo-violet page reads as harsh regardless of the content on
it. Both surfaces sit directly on `PulseBackground`, so both produced the same mismatch
against the same field, from two unrelated declarations.

---

## The `GalacticAtmosphere` problem, and why it is a prop

`GalacticAtmosphere` has a second live caller: `ReelsScreen`, which renders it
**full-screen behind video**. There, dark *is* the design and the media is what sits on
top. Changing the component's base ramp would therefore have lightened the Reels canvas
as a side effect — a regression in a protected media surface, produced by a change whose
brief never mentioned Reels.

So the material is opt-in, via a new `surface?: "space" | "blueGraphite"` prop that
defaults to `"space"`. Existing callers are unchanged *by construction* rather than by
audit. Only `PulseNetworkHero` passes `surface="blueGraphite"`.

---

## Tokens

All seven live in `mobile-native/src/theme/blueGraphite.ts`. No component holds a raw
blue-graphite hex — `blueGraphite.test.ts` walks `src/` and fails if one appears
anywhere else.

| Token | Value | Role |
| --- | --- | --- |
| `surfaceBlueGraphiteCore` | `#363D46` | card centre — the base graphite |
| `surfaceBlueGraphiteEdge` | `#243D5D` | card blue edge — **deviates, see below** |
| `surfaceBlueGraphiteDeep` | `#263854` | card deep navy perimeter |
| `surfaceBlueGraphiteNavCore` | `#303843` | dock centre |
| `surfaceBlueGraphiteNavEdge` | `#243B5A` | dock blue edge |
| `surfaceBlueGraphiteNavDeep` | `#1F314B` | dock deep navy perimeter |
| `surfaceBlueGraphiteBorder` | `rgba(118, 150, 196, 0.26)` | hairline for *new* surfaces only |

`surfaceBlueGraphiteBorder` is currently **unused by both migrated surfaces** — the card
keeps its existing teal edge and the dock keeps its existing blue one, per the
invariants. It exists for future blue-graphite surfaces that have no accent border of
their own.

### The one deviation from the approved list

The approved card blue edge was `#29466A`. It ships as `#243D5D`.

`#29466A` is **lighter** than the core it sits beside (luminance 0.0589 vs the core's
0.0456). The core is already the binding contrast constraint: `colors.muted` (`#9aa8b7`)
on `#363D46` measures **4.53:1**, barely over the 4.5:1 floor that the hero's 12pt metric
labels require. Those labels span the full card width, so they land on this stop — where
`#29466A` measures **3.97:1**. That is a WCAG AA failure on shipped text.

`#243D5D` is the same hue and saturation, lowered until the ramp stops brightening
(luminance 0.0450, a hair under the core). That makes the core the lightest pixel on the
card and therefore the only value contrast has to be argued against. Muted measures
**4.56:1** there. Still blue-dominant (B > G > R, asserted).

The approved visual direction is unchanged: navy still enters at the right edges. It now
deepens *into* the perimeter rather than lifting out of it — which is what "deep navy
enters subtly at the edges" describes anyway.

**The nav pair ships verbatim.** `surfaceBlueGraphiteNavEdge` (`#243B5A`, luminance
0.0424) is also lighter than its own core (`#303843`, 0.0386), but muted measures
**4.68:1** there, so it passes as given and was not touched. The asymmetry is
deliberate — the card was calibrated because it failed, not because of a rule.

---

## Gradient geometry

Both surfaces share one axis: `start {x: 0, y: 0.06}` → `end {x: 1, y: 0.94}` —
mostly horizontal, slightly tilted down. Stops at `[0, 0.52, 0.84, 1]`.

Projecting the unit square's corners onto that axis:

| Corner | t | Colour there |
| --- | --- | --- |
| upper-left | 0.00 | core graphite |
| lower-left | 0.47 | core graphite |
| centre | 0.50 | core graphite — **no spotlight** |
| upper-right | 0.53 | core, just turning — "slightly bluer" |
| lower-right | 1.00 | deep navy |

This is the approved direction stated as numbers, and it is asserted rather than
described (`blueGraphite.test.ts`, "the axis puts the blue where the brief puts it").

The dock reuses the card's stop positions rather than values tuned for a wide pill,
because `expo-linear-gradient` normalises the axis to the unit square — a stop at 0.52 is
52% across whatever it paints on. Separate numbers would have made the two surfaces
differ for no rendered reason.

### Not a cloudy overlay

Every `base` stop is fully **opaque** (asserted). The only alpha in the file is the
optional `edge` perimeter layer, and it is the *deep* token at low alpha — a darker,
in-family navy, so it deepens edges without lightening, fogging or hazing. Compositing
its strongest stop over the core lands on `#313B4A`: half a step. Asserted to never
raise luminance, and to stay under 1.3:1 against the bare core.

---

## How the dock's material is attached

The dock gets a `LinearGradient` **sibling layer**, not a fill. Two constraints force
this:

1. A gradient cannot be a `backgroundColor`.
2. The obvious fix — `overflow: "hidden"` on the panel so a plain absolute-fill child
   respects the 38pt radius — **would clip the Create circle**, which deliberately
   overhangs the panel by `BOTTOM_NAV_CREATE_MARGIN_TOP`.

So the layer carries its own `borderRadius: 37` (the panel's 38 less its 1pt border; RN
lays absolute children out against the padding box) and clips itself. It is first in
document order, so it paints under every tab with no `zIndex`, and `pointerEvents="none"`
keeps it out of the hit path.

`bottomPanelBlueGraphite` still sets an opaque `backgroundColor` underneath. The gradient
is the material; the fill guarantees the panel is never translucent, not even for the
frame before the gradient paints.

**All geometry stayed in `bottomPanel`** — radius, padding, `minHeight`, border, shadow.
`bottomNavMetrics.ts` mirrors two of those numbers and every scrollable surface in the
app reserves clearance against them, so moving one would leave a band of dead space
app-wide. Tests assert the panel measures identically with the material on and off.

---

## Theme scope

Gate: `theme.mode === "dark" && !theme.highContrast`.

`buildTheme` pins `const activeTheme: ThemeMode = "dark"` — dark **is** the released
blue/futuristic appearance. Two consequences worth stating plainly:

- In production the gate is effectively always-on. That is correct, not a bug.
- Because the mode is pinned, asking `ThemeProvider` for black or white yields dark. So
  the "other themes are unchanged" tests build a theme and override `mode` directly.
  Written any other way they would silently assert against dark and prove nothing.

High contrast is excluded because it **substitutes** the palette (`HIGH_CONTRAST_DARK`);
a surface tuned against the normal ramp has no standing to override it. Note this is a
distinct field from `reduceTransparency`, which is the OR of the appearance preference
and high contrast. Reduce Transparency alone says "stop layering" and leaves the palette
alone — so under Reduce Transparency the surface **keeps its blue-graphite colour**,
which is the explicit requirement that switching off translucency must not return a
surface to black. `fallback` is the core graphite, never black.

Off the gate, Black / White / Light Futuristic / high contrast keep
`rgba(7, 14, 32, 0.95)` byte-for-byte, asserted against a pinned literal.

---

## Accessibility

Computed from the shipped tokens. Small text needs 4.5:1, large (18pt, or 14pt bold)
needs 3:1.

### Card stops

| Foreground | core `#363D46` | edge `#243D5D` | deep `#263854` | Bar | Result |
| --- | --- | --- | --- | --- | --- |
| `text` `#f4f7fb` (9pt tile labels) | 10.22 | 10.28 | 11.00 | 4.5 | pass |
| `muted` `#9aa8b7` (12pt metrics) | **4.53** | 4.56 | 4.88 | 4.5 | pass — binding |
| `accent` `#32e6b3` (13pt pill) | 6.85 | 6.90 | 7.38 | 4.5 | pass |
| `danger` `#ff5f7e` (23pt) | 3.76 | 3.78 | 4.05 | 3.0 | pass |
| `intelligence` `#9f7cff` (14pt/900) | 3.56 | 3.58 | 3.83 | 3.0 | pass |
| `safety` `#3ff0a0` (14pt/900) | 7.41 | 7.46 | 7.99 | 3.0 | pass |
| `warning` `#f3c461` | 6.73 | 6.78 | 7.25 | 3.0 | pass |
| `accentStrong` `#61d8ff` | 6.68 | 6.72 | 7.19 | 3.0 | pass |

### Dock stops

| Foreground | navCore | navEdge | navDeep | Bar | Result |
| --- | --- | --- | --- | --- | --- |
| `muted` (inactive label, 12pt) | 4.89 | 4.68 | 5.41 | 4.5 | pass |
| `accent` (active label, 12pt) | 7.39 | 7.09 | 8.19 | 4.5 | pass |
| `text` | 11.03 | 10.57 | 12.22 | 4.5 | pass |

Contrast is measured against the **worst stop**, not an average — text does not get to
choose which part of a gradient it lands on.

### Non-colour distinctions

- **Active vs inactive dock tabs** separate by luminance (0.605 vs 0.383, Δ0.222), not
  by hue alone, so the dock survives a red/green deficiency and grayscale. Asserted.
- **VoiceOver / Dynamic Type / Bold Text / Reduce Motion** — untouched. `accessibilityRole`,
  `accessibilityLabel` and `accessibilityState` are asserted present and correct on all
  five tabs with the material rendered.

### Not yet verified

Rendered contrast of **text over the composited `edge` perimeter layer** is argued from
arithmetic (the layer only ever darkens, and darkening a passing background cannot fail
a light foreground) rather than sampled from a screenshot. Icon contrast is likewise
computed from token values, not measured off a device.

---

## Performance

No new work per frame. The material is two static `LinearGradient` views with constant
props — no `Animated` interpolation drives colour, no JS runs per frame, no blur view, no
image asset, no network request. The dock gains exactly one non-interactive view; the
card gains none (its existing two gradients changed their props).

Not measured on-device. Scroll and navigation animation timings are unchanged by
inspection, not by instrumentation.

---

## Tests

New — 51 tests across 3 files:

| File | Covers |
| --- | --- |
| `src/theme/__tests__/blueGraphite.test.ts` | token values pinned; hierarchy as luminance; full contrast matrix; axis corner projections; opacity; no re-declared hex anywhere in `src/` |
| `src/navigation/__tests__/bottomNavBlueGraphite.test.tsx` | dock ramp/axis painted; opaque fill; self-clipping; out of hit path; paints under tabs by document order; geometry identical on/off; safe area; routing, Create button, active state, labels |
| `src/components/__tests__/galacticAtmosphereBlueGraphite.test.tsx` | card ramp/axis; default `"space"` path preserved; other themes unchanged |

### Negative control

Reverting the three source files and re-running the two component suites produces
**8 failures / 21 passes**. The 8 are the material assertions; the 21 that still pass are
the geometry, hit-target and routing invariants — which are *supposed* to hold either
way, since their job is to prove nothing moved. The suites are therefore not vacuous.

### Gates run

| Gate | Result |
| --- | --- |
| `npm run verify` (typecheck + i18n + jest) | **PASS** — 462 suites, 7958 tests |
| `npx tsc --noEmit` | clean |
| `scripts/realtime_audio_change_gate.py --base origin/main --head HEAD` | *"No protected real-time audio path changed (7 files inspected)"* |
| Pre-existing surface locks | `backgroundSurfaces.test.ts`, `chatGraphiteContrast`, `profileGraphite`, `businessPaletteLock`, `ThemeContext` — all pass |

### Protected systems

Neither touched file appears in `categories[].paths` of
`config/realtime-audio-protected-paths.json`, so edits here are not gated — verified
programmatically, not assumed. Pulse Radio is visually inside the dock but no playback,
lifecycle, session or microphone code was modified. The dock test stubs
`core/pulseRadio` to its shape only, deliberately without behaviour, so it cannot drift
into standing in for the audio suite.

---

## Stage 4 — the lower composer region: no change made

The brief authorised harmonising an exposed near-black backing surface in the
Create a Signal region *only if* it is a distinct surface responsible for the mismatch.
Inspecting the region:

| Surface | Value | Disposition |
| --- | --- | --- |
| `GlobalNavigation.styles.bottomPanel` | was `rgba(7, 14, 32, 0.95)` | **this was it** — now blue-graphite |
| `GlobalNavigation.styles.miniPlayer` | `rgba(10, 24, 52, 0.94)` | already navy-blue, not near-black — left alone |
| `SpatialCreateConsole.styles.card` | `rgba(7, 14, 32, 0.95)` | this *is* the Create a Signal container, which the brief explicitly protects |
| `SpatialCreateConsole.styles.backdrop` | `colors.background` @ 0.86 | a functional dimming scrim — must stay |

So the dock's own fill was the whole cause, and no additional surface needs
harmonising. **This conclusion is from source, not from a screenshot** — if device
verification shows a remaining mismatch in that region, `SpatialCreateConsole.card` is
the first place to look, and it will need an explicit decision because the brief protects
it.

---

## Files changed

| File | Change |
| --- | --- |
| `mobile-native/src/theme/blueGraphite.ts` | new — 7 tokens, 2 surface specs |
| `mobile-native/src/components/GalacticAtmosphere.tsx` | `surface` prop; base/edge ramps read from the spec |
| `mobile-native/src/navigation/GlobalNavigation.tsx` | gated material layer + opaque fill |
| `mobile-native/src/screens/HomeScreen.tsx` | hero passes `surface="blueGraphite"` |
| 3 test files | new |

No other file was modified. No unrelated change is included in `98de38a3`.

---

## A note on how this work was recovered

The implementation was begun in `/Users/hmcherie/Desktop/cpx-bluegraphite`, uncommitted,
on detached `9cbaf759`. That commit is **not an ancestor of `main`**, and local `main`
itself turned out to be a stale parallel lineage — 10 commits that all have patch-id twins
on `origin/main`, which was 57 commits ahead.

This mattered concretely. The gate depends on `Theme.highContrast`, a field added by
`8716c1f7` which is on `origin/main` but absent from local `main`. Transplanted onto local
`main` the work produced 6 TypeScript errors that looked like defects in the
implementation; the implementation was correct and the base was wrong.

Work was therefore re-based onto `origin/main` @ `e429a6cf`, where the three modified
files are byte-identical to the original base, so the patch applied cleanly. The original
worktree was left untouched. **Anything picking this up should confirm it is on
`origin/main`, not local `main`.**

---

## Outstanding

Required by the brief, not done. The brief states PASS may not be declared on code or
simulator evidence alone, and it is not declared here.

1. **iOS simulator build + screenshots** (iPhone 17 Pro Max) — not run.
2. **Signed physical-device build + screenshots** — not run. The brief names an
   iPhone 16 Pro.
3. **The 12-screenshot visual matrix** — top of Home, the card, status area,
   Create a Signal, the dock, the dock over varied content, the four themes,
   Increase Contrast, Reduce Transparency.
4. **Calibration against the approved mockup** — the shipped values are the approved
   production tokens plus one measured contrast correction. Whether they match the mockup
   *rendered on glass* is unconfirmed.
5. **Push** — `98de38a3` is committed in a local worktree only. Not pushed.

Reject criteria to check against on-device: gray and disconnected from the blue page;
cloudy; predominantly bright blue; lost component separation; back to near-black; text or
icons unreadable; layout moved; other themes regressed.

### Residual risks

- **The nav edge is lighter than the nav core.** It passes AA so it shipped verbatim, but
  it means the dock's ramp brightens slightly toward its blue edge while the card's does
  not. Whether that reads as intentional on glass is a visual question.
- **`surfaceBlueGraphiteBorder` is unused.** A token with no consumer can drift out of
  the family unnoticed; the contrast tests do not cover it.
- **Reduce Transparency is argued, not observed.** The code path is asserted in tests and
  the reasoning is in `ThemeContext`, but no screenshot confirms the rendered result.
- **Platform-wide consistency is now uneven by construction.** Two surfaces are
  blue-graphite; the global header (`rgba(3, 9, 18, 0.96)`), `PostCard`'s action sheet and
  the `SpatialCreateConsole` family are still near-black. On Home this is invisible
  because the dock and card dominate, but any screen showing the header beside the dock
  will show two different materials.

---

## Addendum — simulator verification, and the defect it found

The sections above were written before the app had been run. It has now been built and
run on the iPhone 17 Pro Max simulator, and the verification found a real defect that the
whole green test suite could not see.

### The defect: `BLUE_GRAPHITE_NAV.edge` was never rendered

`blueGraphite.ts` defined an `edge` layer for both surfaces and `blueGraphite.test.ts`
asserted it was well-formed. The card rendered it, because `GalacticAtmosphere` paints
both layers. **The dock never did** — `GlobalNavigation` rendered `base` alone.

Nothing caught this. The token was exercised by unit tests, so it did not read as dead
code; the dock's tests asserted the base ramp and passed. It was only visible in pixels.

The consequence is directional. `base` carries the shared axis, which is mostly
horizontal, so it can only place navy at the ends of *that* axis. `edge` deliberately
carries no axis, so `expo-linear-gradient` defaults it to top-to-bottom. Without it the
dock deepened across its width and its top and bottom edges sat flat at core graphite,
while the card — which had both layers all along — deepened on all four. The two surfaces
were not the same material.

### The fix, and the A/B that proves it

A second `LinearGradient` now renders `BLUE_GRAPHITE_NAV.edge` directly above the base,
`pointerEvents="none"`, clipped to the same 37pt radius, under every tab.

Both builds were made from the same tree — the only difference being the presence of that
layer — installed in turn, and sampled at the same pixel column through the dock
(x=420, screenshot 1320×2868):

| y (dock top → bottom) | before | after | delta |
|---|---|---|---|
| 2465 | `#303843` | `#2E3846` | −2 R, +3 B |
| 2500 | `#303843` | `#2F3845` | −1 R, +2 B |
| 2610 – 2670 (middle) | `#303843` | `#303843` | **0** |
| 2720 | `#303843` | `#2F3845` | −1 R, +2 B |
| 2762 | `#303843` | `#2D3847` | −3 R, +4 B |

Before, the dock is flat `#303843` — exactly `surfaceBlueGraphiteNavCore`, with no
vertical variation anywhere. After, the middle is byte-identical and both edges deepen and
turn bluer, strongest at the extremes. That is a symmetric perimeter deepening that leaves
the centre alone, which is what the layer is for.

The bottom-most sample confirms the alpha arithmetic: compositing
`rgba(38, 56, 84, 0.26)` over `#303843` predicts `#2D3847`, which is what rendered.

### Other surfaces, measured rather than eyeballed

Sampled against the tokens (nearest-token Euclidean distance):

- card centre `#353F49` — d=4 from `surfaceBlueGraphiteCore`
- card lower-right `#25405F` — d=4 from `surfaceBlueGraphiteEdge`
- dock centre `#303843` — **d=0** from `surfaceBlueGraphiteNavCore`
- dock right `#233957` — d=4 from `surfaceBlueGraphiteNavEdge`

The card is lighter than the dock, so the dock anchors, as required. The page background,
status circles, header and Reels chrome are unchanged. The expanded Create a Signal
composer sits on its own blue translucent container with no exposed near-black backing, so
Stage 4 needed no edit — that is now a visual observation, not just a source reading.

### Test-suite gap, closed

Seven tests were added to `bottomNavBlueGraphite.test.tsx` covering the *rendered* layer:
its ramp, its absent axis, that every stop is translucent, its paint order relative to the
base and the tabs, its clipping radius, its exclusion from the hit path, and its absence
under high contrast and the three non-blue themes.

Removing the layer again fails six of the seven. The seventh asserts absence off-gate, so
it correctly stays green — noted here so a future reader does not mistake it for a
vacuous test.

### What this does *not* establish

Simulator only. The brief requires a signed physical-device build on an iPhone 16 Pro and
a 12-screenshot device matrix, and neither has been run. **This is still not a PASS.**
