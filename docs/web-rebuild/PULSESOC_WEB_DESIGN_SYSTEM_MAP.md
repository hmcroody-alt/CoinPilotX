# PulseSoc Web Rebuild — Design System Map

> Source of truth: the native app at `mobile-native/` (Expo 54 / RN 0.81.5 / React 19 / TS 5.9).
> This document extracts real token values from native so the website can be rebuilt to feel
> like PulseSoc, not a separate product.
>
> Status: IN PROGRESS — sections filled in order.
> All file references are relative to `/Users/hmcherie/Desktop/CoinPilotX/`.

## 1 — How theming is actually wired

### Mechanism: React Context + a deliberate mutable-global bridge

There is **no Zustand store for theme**. It is `React.createContext` plus two migration
bridges that exist because ~50–160 files predate the context.

| Layer | File | What it is |
|---|---|---|
| Frozen default palette | `mobile-native/src/theme/colors.ts:1-25` | A **mutable** exported object. 162 files import it directly. |
| Runtime theme | `mobile-native/src/theme/ThemeContext.tsx:290-334` | `ThemeContext` + `ThemeProvider` + `useTheme()`. 29 files use `useTheme()`. |
| Legacy bridge #1 | `ThemeContext.tsx:258-265` `applyPaletteToLegacyColors` | **Mutates** the shared `colors` object in place so legacy readers see the active palette. |
| Legacy bridge #2 | `themedStyles.ts:28-51` `createThemedStyles` | A `Proxy` around `StyleSheet.create` that rebuilds lazily when a palette epoch bumps. 125 files use it. |

`useTheme()` falls back to a synthesised dark theme when rendered outside a provider
(`ThemeContext.tsx:341-360`), so components never crash untethered.

### Light/dark: implemented but **switched off**

Five palettes exist — `DARK` (`ThemeContext.tsx:31`), `BLACK` AMOLED (`:39-47`),
`LIGHT_FUTURISTIC` (`:55-79`), `WHITE` (`:86-96`), plus `HIGH_CONTRAST_DARK` (`:99-109`)
and `HIGH_CONTRAST_LIGHT` (`:111-121`) partial overrides.

**But `buildTheme` hard-pins the mode:**

```ts
// ThemeContext.tsx:212-214
// Dark is the only released appearance for now.
// Keep the other theme implementations intact for future activation.
const activeTheme: ThemeMode = "dark";
```

So `system`/`light`/`white`/`black` are unreachable through `ThemeProvider` today. Only the
test escape hatch `__testing.ThemeContext` (`:382-402`) can render them. **Web implication:
ship dark as the default and only released surface, but build the light token set now —
the native light values are already designed and accessible-checked.**

Accessibility inputs that DO take effect: `highContrast`, `reduceTransparency` (collapses
`glass` → `surface`, `glassStrong` → `background`, `:220-223`), `fontScale`,
`compactDensity`, `boldText`, `reduceMotion` (`theme.duration(ms)` returns `0`),
`hapticFeedback`.

### The ~14 per-feature themes: accumulated drift, with two exceptions

Real importer counts (grep across `mobile-native/src`, excluding self):

| File | LOC | Importers | Purpose |
|---|---|---|---|
| `logiNexus.ts` | 190 | **90** | The de-facto real design system: typography scale, spacing, radius, motion, depth. **Not per-feature — treat as core.** |
| `logiNexusMotion.ts` | 63 | 58 | Shared motion helpers built on logiNexus.motion |
| `storeMotion.ts` | 407 | 51 | Store/marketplace choreography |
| `storeLight.ts` | 177 | 38 | Store surface tokens |
| `adsLight.ts` | 175 | 27 | Ads manager surface |
| `messagesLight.ts` | 187 | 14 | Messaging surface |
| `eventsLight.ts` | 153 | 13 | Events surface |
| `ordersLight.ts` | 190 | 11 | Orders surface |
| `paymentsLight.ts` | 312 | 10 | Payments/checkout surface |
| `marketplaceLight.ts` | 110 | 9 | Marketplace surface |
| `presenceTheme.ts` | 44 | 9 | Presence entry-point fixed identity |
| `presenceAccent.ts` | 187 | 8 | Page-type → hue table (**intentional**, see below) |
| `insightsLight.ts` | 95 | 7 | Insights/analytics |
| `hubLight.ts` | 88 | 5 | Business hub |
| `adsMotion.ts` / `insightsMotion.ts` / `marketplaceMotion.ts` | 197/234/277 | 5/5/5 | Per-feature motion |
| `premiumTheme.ts` | 86 | 4 | Premium/subscription |
| `moneyTheme.ts` | 165 | 3 | Wallet/money |
| `businessLiveMotion.ts` | 219 | 3 | Business live profile |
| `marketplaceCheckoutDark.ts` | 102 | 2 | Checkout dark variant |
| `profileNeon.ts` | 58 | 2 | Profile blue treatment (**intentional**) |
| `progressTheme.ts` | 71 | 2 | Progress indicators |
| `paymentsMotion.ts` | 260 | 2 | Payments motion |
| `pulseBackground.ts` | 327 | 2 | The backdrop identity (**core**) |

**Honest verdict: it is fragmented, but not uniformly.** Three distinct things are mixed
in one directory:

1. **Genuine core** — `colors.ts`, `logiNexus.ts`, `pulseBackground.ts`, `ThemeContext.tsx`.
   `logiNexus` is what 90 files actually consume for type/space/radius/motion. Its
   name says nothing about that, which is itself the problem.
2. **Two documented, defensible scoped palettes** — `profileNeon.ts` and
   `presenceAccent.ts`. Both carry explicit reasoning for why they are *not* in
   `colors.ts` (`profileNeon.ts:1-19`: "mutating `colors` would repaint the feed,
   messenger and every other screen"; `presenceAccent.ts:26-34`: the hub tile must not
   be painted in one presence's hue). These are intentional architecture.
3. **The `*Light.ts` family — drift.** `adsLight`, `storeLight`, `messagesLight`,
   `ordersLight`, `paymentsLight`, `marketplaceLight`, `insightsLight`, `hubLight`,
   `eventsLight`. Each is a feature team's private re-derivation of surface/border/
   text tokens. Nothing layers them onto the core theme; they are **independent
   constant modules** that neither read `useTheme()` nor respond to a theme change.
   Naming them "Light" while the app ships dark-only is further evidence they were
   added ad hoc.

### Answer for the web team

**There is not one source of truth — there are roughly four.** But you can share a token
set, because the fragmentation is *additive*, not contradictory: the `*Light` files mostly
re-express the same handful of semantic roles (surface, raised, border, text, muted,
accent) at slightly different values. The web should:

- Adopt `colors.ts` + `logiNexus.ts` + `pulseBackground.ts` as the **canonical** token set.
- Port `profileNeon` and `presenceAccent` as **named scoped themes** (they are real brand).
- **Do not port the nine `*Light.ts` files individually.** Collapse them into per-surface
  CSS custom-property overrides on the canonical set (`[data-surface="store"] { --pulse-surface: … }`).
  Reproducing the fragmentation on web would institutionalise a native accident.

## 2 — Core tokens

### 2.1 Colour — the canonical dark palette
`mobile-native/src/theme/colors.ts:1-25`. Every value is exact.

| Token | Value | Semantic role |
|---|---|---|
| `background` | `#050910` | App background (deep near-black navy) |
| `surface` | `#0b141c` | Card / sheet fill |
| `surfaceRaised` | `#111f2a` | Elevated card, input fill |
| `text` | `#f4f7fb` | Primary text |
| `muted` | `#9aa8b7` | Secondary text, metadata |
| `accent` | `#32e6b3` | **Brand teal.** Primary action, active state |
| `accentStrong` | `#61d8ff` | Secondary accent (cyan), links, gradient tail |
| `warning` | `#f3c461` | Warning / pending |
| `danger` | `#ff5f7e` | Error, destructive, LIVE badge |
| `border` | `#203746` | Hairline, divider, input border |
| `intelligence` | `#9f7cff` | UNDX / AI surfaces (violet) |
| `creator` | `#42e7d4` | Creator / reels / radio |
| `economy` | `#f6c85d` | Money, payouts, marketplace value |
| `safety` | `#3ff0a0` | Verified, safe, success |
| `crypto` | `#62e0ff` | Crypto subsystem |
| `disabled` | `#51606c` | Disabled control |
| `focus` | `#8df7ff` | Focus ring |
| `glass` | `rgba(11, 24, 34, 0.82)` | Translucent panel |
| `glassStrong` | `rgba(15, 36, 50, 0.94)` | Modal / sheet backdrop panel |
| `signalDim` | `rgba(50, 230, 179, 0.12)` | Accent wash (teal @12%) |
| `signalSoft` | `rgba(97, 216, 255, 0.12)` | Cyan wash |
| `dangerSoft` | `rgba(255, 95, 126, 0.14)` | Danger wash |
| `warningSoft` | `rgba(243, 196, 97, 0.14)` | Warning wash |

**Light palette (built, not shipped)** — `ThemeContext.tsx:55-79`:
`background #f6f8fb`, `surface #ffffff`, `surfaceRaised #eef2f7`, `text #0b141c`,
`muted #5b6b7c`, `accent #00966f`, `accentStrong #0071a6`, `warning #a06a00`,
`danger #c02341`, `border #d3dde7`, `intelligence #6a3fd6`, `creator #0f8f83`,
`economy #9a7413`, `safety #0a8a52`, `crypto #0a7ba3`, `disabled #a3b0bc`,
`focus #0071a6`, `glass rgba(255,255,255,0.86)`, `glassStrong rgba(246,248,251,0.96)`,
washes at 10% alpha. These accents were explicitly darkened to clear 4.5:1 on light.

**AMOLED "Black" variant** (`ThemeContext.tsx:39-47`): `background #000000`,
`surface #070a0d`, `surfaceRaised #10161c`, `border #1b2b36`,
`glass rgba(5,8,10,0.92)`, `glassStrong rgba(3,5,7,0.97)`.

**High contrast dark** (`:99-109`): `background #000000`, `surface #0a0a0a`,
`surfaceRaised #161616`, `text #ffffff`, `muted #d6dde4`, `border #7b8b99`,
`accent #4dffc8`, glass opaque. **High contrast light** (`:111-121`):
`text #000000`, `muted #2b3843`, `border #4a5a68`, `accent #00614a`.

### 2.2 The gradient identity — `PulseBackground`
This is the single strongest brand signal and the web must reproduce it.
`mobile-native/src/theme/pulseBackground.ts`.

**Approved palette (`:30-39`) — nothing outside this object may introduce a colour:**

| Name | Hex |
|---|---|
| `base` | `#101A4A` |
| `navy` | `#13235C` |
| `indigo` | `#1E2B78` |
| `darkViolet` | `#31206E` |
| `accentPurple` | `#7C4DFF` |
| `softLavender` | `#A67CFF` |
| `pulseCyan` | `#42E8D0` (only 2 of 14 nodes — "any more and it stops being an accent") |

**Dark field gradient — six stops, exact (`:183-193`):**
```
#101A4A 0%, #13235C 22%, #1E2B78 46%, #31206E 62%, #13235C 84%, #101A4A 100%
```
Six stops rather than three "because a three-stop ramp over this range bands visibly
on OLED" (`:178-181`).

**Dark bottom glow (`:194-197`):**
```
rgba(124,77,255,0) 0%, rgba(124,77,255,0.08) 62%, rgba(16,26,74,0.5) 100%
```
Height = 45% of the field (`:85`), layer opacity base `0.7` (`:87`).

**Light field gradient (`:213-216`):**
```
#F6F7FC 0%, #F1F2FA 22%, #ECEBF8 46%, #EFEDFB 62%, #F4F5FC 84%, #F8F8FD 100%
```
Light bottom glow: `rgba(124,77,255,0) 0%, rgba(124,77,255,0.05) 62%, rgba(255,255,255,0) 100%`.
Light node inks: accent `#5B3FC4`, lavender `#7C4DFF`, pulse `#1F9E8C`; line `#5B3FC4`;
`opacityScale: 0.55`.

**The node/line mesh over the gradient** — 14 nodes, 4 breathing; 7 hairlines.
Positions are a **fixed deterministic table** (`:117-155`), never random: "a random field
cannot be reviewed, cannot be regression-tested, and re-rolls itself on every remount".

Hard ceilings (`:46-57`) — these are limits, not defaults:
- node opacity ≤ `0.20`
- line opacity ≤ `0.12`
- halo = 30% of its own node's opacity (can never outshine it)
- bottom glow effective alpha ≤ `0.10`

Geometry (`:75-90`): halo scale `3.2×`, line thickness `1px`, node drift `10px`,
line travel `16px`, resting progress `0.4` when motion is suppressed.

Cycles (`:65-72`), full there-and-back: drift `30000ms`, pulse `8000ms`, travel `36000ms`.
"Below about fifteen seconds ambient drift starts reading as activity."

Variants (`:245-253`): `default` (all tiers, 1×), `quiet` (core tier only, 0.7 opacity,
1.15× cycle, 0.7 glow — for dense surfaces), `elevated` (0.9× cycle, 1.35× glow — for
hero surfaces), `static` (no animation, 0.85 opacity).
Intensity scale (`:256-259`): `subtle: 0.7`, `standard: 1`.

### 2.3 Typography
**There are NO custom fonts.** `expo-font` is a dependency but there is **no `useFonts`
call and no font file anywhere in the repo** — `mobile-native/src/assets/` contains only
`brand/pulsesoc-mark.png` and four `.wav` call sounds. The app renders in the **platform
system font** (SF Pro on iOS, Roboto on Android). Web equivalent: the system UI stack.

The scale lives in `logiNexus.typography` (`src/theme/logiNexus.ts:89-115`).
Exact size / line-height / weight:

| Token | Size | Line-height | Weight |
|---|---|---|---|
| `display` | 34 | 39 | 900 |
| `title` | 24 | 30 | 900 |
| `sectionTitle` | 18 | 24 | 900 |
| `body` | 15 | 22 | 600 |
| `metadata` | 12 | 17 | 800 |
| `label` | 12 | 16 | 900 |
| `button` | 14 | 18 | 900 |
| `metric` | 26 | 31 | 900 |

Home-feed sub-scale (`:98-114`): `brand` 27/32/900, `heroLabel` 12/16/900,
`heroMetric` 34/39/900, `heroSupporting` 14/20/700, `sectionLabel` 13/17/900,
`cardAuthor` 16/20/900, `cardBody` 16/23/600, `cardMetadata` 12/17/800,
`cardMetric` 13/17/900, `buttonPrimary` 15/19/900, `buttonSecondary` 13/17/900,
`badge` 11/15/900, `tab` 14/18/900, `emptyTitle` 18/23/900, `emptyBody` 14/21/700.

**Characteristic: the type is very heavy.** Almost everything is 800–900; body copy is
600, not 400. This is a deliberate identity choice and the web must match it or it will
read as a different product. Line-height ratio is tight, ~1.15 for headings and ~1.45 for body.

Accessibility weight bump (`ThemeContext.tsx:242-243`): `boldText` moves body
`400 → 600` and title `800 → 900`. Global font scaling is clamped to
`min(2, max(1.2, 1.6 / scale))` (`:280`).

### 2.4 Spacing scale
`logiNexus.spacing` (`src/theme/logiNexus.ts:116-126`) — a clean 4px base:

`xs 4` · `sm 8` · `md 12` · `lg 16` · `xl 20` · `xxl 24` · `xxxl 32` · `huge 40` · `giant 48`

Row metrics from the theme (`ThemeContext.tsx:235-240`):
`rowMinHeight` 56 (compact 46) × fontScale, `rowPaddingVertical` 12 (compact 8),
`rowPaddingHorizontal` 16, `sectionGap` 26 (compact 18).
Minimum tap target: **44pt** (`profileNeon.ts:55`, Apple's floor).

### 2.5 Border radius
`logiNexus.radius` (`src/theme/logiNexus.ts:127-136`):
`small 8` · `medium 12` · `large 16` · `card 18` · `panel 20` · `capsule 999` · `circular 999`
Theme default `radius: 14` (`ThemeContext.tsx:240`).
`profileNeon.radius` (`profileNeon.ts:53`): `panel 20` · `card 16` · `action 14`.

Measured reality in `src/components` + `src/screens` (`grep borderRadius`):
`8` (309 uses) ≫ `999` (137) > `14` (68) > `10` (63) > `12` (54) > `16` (49) > `4` (39) >
`18` (28) > `20` (19) > `24` (18). So **8px is the real default radius and 999 the pill**,
even though the token set nominates 12/16/18.

### 2.6 Depth / shadow / glow
There is **no shadow token object.** `logiNexus.depth` (`src/theme/logiNexus.ts:161-176`)
is a set of **opacity** values, not shadow presets:
`none 0` · `subtle 0.18` · `commandStrip 0.22` · `orbit 0.24` · `selectedTab 0.26` ·
`panel 0.28` · `feedCard 0.30` · `hero 0.34` · `composer 0.36` · `floating 0.38` ·
`floatingNav 0.42` · `modal 0.48` · `livePulse 0.50` · `feedRail 0.20`.

Shadows are written inline per call site. Measured `shadowRadius` distribution:
`12` (9) · `14` (7) · `8`/`18`/`10` (4 each) · `7`/`16` (3) · `24` (2). `elevation` is
barely used (three occurrences, one of them `9000` for an overlay z-order hack).

**The glow idiom is `shadowColor: <accent>` rather than black** — e.g.
`shadowColor: colors.accent`, `shadowColor: colors.accentStrong`, and
`shadowColor: tone` on orbs/rings. On web this maps to a coloured `box-shadow`
or `filter: drop-shadow()`, not an elevation ramp.

### 2.7 Glass / blur
**There is no blur library installed.** No `expo-blur`, no `BlurView` anywhere.
`logiNexus.ts:60-65` states this explicitly and says the panels compensate by sitting
"slightly more opaque than a true blur would need". Glass is therefore **alpha fill only**:
`colors.glass`, `colors.glassStrong`, `profileNeon.panel` `rgba(9,20,38,0.72)` /
`panelRaised` `rgba(14,30,54,0.88)`, `logiNexus.colors.businessLive.panel`
`rgba(14,24,35,0.72)` / `panelStrong` `rgba(14,24,35,0.92)`.

**Web opportunity:** `backdrop-filter: blur()` is cheap and well-supported on web. The web
build can deliver the *intended* glass that native couldn't. Keep the same alpha values as
the fallback for `@supports not (backdrop-filter: blur(1px))`.

### 2.8 Scoped brand palettes worth porting

**`profileNeon`** (`src/theme/profileNeon.ts:23-56`) — the profile blue:
`electric #3d8bff` · `cyan #61d8ff` · `deep #0b2f6b` · `violet` = `colors.intelligence`.
Fills `rgba(61,139,255,0.10)` / `0.16`; borders `0.34` / `0.52`; glow `0.45`;
glowCyan `rgba(97,216,255,0.38)`; hairline `rgba(120,170,255,0.16)`.
Gradients: primary action `#4f9bff → #2563eb`; horizon
`rgba(61,139,255,0) → rgba(61,139,255,0.22) → rgba(97,216,255,0.40)`.

**The alpha doctrine** is stated as a rule (`profileNeon.ts:15-18`, restated at
`presenceAccent.ts:36-44`) and the web must honour it:
> borders ~0.30–0.45, fills ~0.10–0.18, and only the primary action and the avatar ring
> are allowed to be genuinely bright.

**`presenceAccent`** (`src/theme/presenceAccent.ts:50-59`) — four hues by page type:
`enterprise #32e6b3` (business/pro-service/local/org/other) ·
`creative #9f7cff` (artist/creator/public figure/media) ·
`commerce #61d8ff` (brand/store/restaurant) ·
`community #ff6ad5` (nonprofit/sports team/venue/education).
Each hue is **generated**, not hand-written (`:111-123`): `fill` = base@0.12,
`fillStrong` = base@0.18, `border` = base@0.34, `glow` = base@0.42,
`wash` = base@0.18 → base@0. **`ink` = `colors.background` (`#050910`)** — dark ink on a
bright fill, because "white on teal is about 1.6:1, and unreadable is not a matter of
taste" (`:94-98`). **There is deliberately no bright-to-deep gradient for filled buttons.**

**`logiNexus.colors.home`** (`logiNexus.ts:12-45`) — the feed tokens:
`backgroundDeepSpace #030712`, `backgroundNetworkVoid #07101d`,
`surfaceGlass rgba(11,22,51,0.03)`, `surfaceGlassStrong rgba(18,26,61,0.03)`,
`surfaceSignal rgba(50,230,179,0.105)`,
**`borderSubtle rgba(100,160,255,0.28)`** — the feed hairline; there is a long comment
(`:18-32`) explaining it was changed from pale cyan to muted indigo so it belongs to the
`PulseBackground` field. Also `borderActive rgba(45,226,194,0.55)`,
`borderIntelligence rgba(139,92,246,0.5)`, `borderCreator rgba(45,226,194,0.48)`,
`borderSafety rgba(63,240,160,0.5)`, `accentRadio #42e7d4`.

**`logiNexus.colors.businessLive`** (`logiNexus.ts:58-87`): `background #03070C`,
`accent #2EE6A8`, `secondary #3FD4FF`, `warning #F5B544`, `textPrimary #EEF6FB`,
`textMuted #8FA5B8`, `textDim #5A7186`, `hairline rgba(64,224,178,0.14)`,
`gridLine rgba(63,212,255,0.18)`, `overlayScrim rgba(3,7,12,0.82)`.

## 3 — Token-system integrity

Measured across `mobile-native/src/screens` + `mobile-native/src/components`
(138 component `.tsx`, 157 screen `.tsx`, excluding `__tests__`):

| Metric | Count |
|---|---|
| 6-digit hex literals | **276** |
| 3-digit hex literals | 36 |
| Distinct 6-digit hex values | **160** |
| `rgba(...)` literals | **641** |
| Files importing `theme/colors` directly | 162 |
| Files using `createThemedStyles` | 125 |
| Files using `useTheme()` | **29** |

Most common hardcoded hexes:

| Value | Uses | Note |
|---|---|---|
| `#ffffff` | 29 | Pure white — no token for it; should be `--pulse-on-accent` |
| `#08110f` | 21 | A near-black green-tinted surface, not in any palette |
| `#02050b` | 10 | Deeper than `background #050910` |
| `#a77cff` | 5 | ~`intelligence #9f7cff` drifted |
| `#03120f`, `#030812` | 4 each | More off-palette near-blacks |
| `#ffbf55`, `#ff9f9f`, `#ff6b8d`, `#ff5fa8`, `#ff4b74`, `#f3d58a`, `#e5484d` | 3 each | Seven distinct warm/danger hues where `warning`/`danger` exist |
| `#68f3de`, `#3bdfff`, `#36f0cf`, `#32e6b3` | 3 each | Three near-misses of the brand teal plus the real one |

Worst offenders by file: `components/ConversationControlCenter.tsx` (29),
`screens/CallScreen.tsx` (21), `components/covers/ContentCover.tsx` (18),
`screens/ChatScreen.tsx` (16), `screens/MessengerScreen.tsx` (12),
`screens/ProfileEditScreen.tsx` (10), `components/ReelPlayerCard.tsx` (8),
`components/PostCard.tsx` (6).

### What this means for the web team

**The token system is real but leaky, and it leaks hardest on exactly the
highest-identity surfaces** — calls, chat, reels, post cards, content covers. 160
distinct hex values against a ~23-token palette means roughly **7 undocumented colours
for every documented one**, and the 641 rgba literals are almost all one-off alpha
washes rather than the `signalDim`/`signalSoft` tokens.

Practical guidance:

1. **Trust `colors.ts`, `logiNexus.ts`, `pulseBackground.ts`, `profileNeon.ts` and
   `presenceAccent.ts` as canonical.** They are documented, tested (`presenceAccent.test.ts`
   holds the alpha bands and contrast ratios), and internally consistent.
2. **Do NOT port the literals.** The near-misses (`#36f0cf`, `#68f3de`, `#3bdfff`
   vs `#32e6b3`/`#61d8ff`; `#a77cff` vs `#9f7cff`) are drift, not intent. Snapping
   them to the nearest token is a visual improvement, not a regression.
3. **The one gap the palette genuinely has is a media/overlay ramp.** The repeated
   `#08110f` / `#02050b` / `#030812` / `#03120f` family are all "darker than
   `background`, for video and cover scrims". The web token set below adds explicit
   `--pulse-scrim-*` tokens so this need stops being met by literals.
4. **`#ffffff` at 29 uses is an `on-accent` / `on-media` need,** not a surface. Name it.

## 4 — Component inventory

138 `.tsx` files under `mobile-native/src/components` (excluding `__tests__`); 25 at the
top level, the rest in 20 feature folders.

### 4.1 The actual shared primitive library

There are only **two** files that function as a cross-app primitive kit, and they are
both named after `logiNexus`:

**`mobile-native/src/components/LogiNexus.tsx` (204 lines)** — 7 exported primitives, all
driven by a single `tone` prop:

| Primitive | Line | Variants | States |
|---|---|---|---|
| `LogiNexusPanel` | `:13` | 8 tones | — (adds a 2px top signal bar in the tone colour, `:182-190`) |
| `LogiNexusCard` | `:23` | 8 tones | — (`glass` fill, hairline border at tone`66` = 40% alpha) |
| `LogiNexusBadge` | `:32` | 8 tones | — (capsule, border = tone, fill = tone`1f` ≈ 12%, uppercase, `letterSpacing: 1`) |
| `LogiNexusMetric` | `:43` | 8 tones | — (big value + caption, `minHeight: 66`) |
| `LogiNexusButton` | `:53` | `solid` \| `outline` × 8 tones | `disabled` → `opacity 0.56`; `pressed` → `opacity 0.76`; `minHeight: 44` |
| `LogiNexusEmptyState` | `:93` | 8 tones | — (Card + Badge "quiet sector" + title + body) |
| `LogiNexusSignalIndicator` | `:103` | 8 tones | `active` \| inactive (→ `colors.disabled`) |

The `tone` union (`src/theme/logiNexus.ts:179`) is
`default | intelligence | creator | economy | safety | crypto | danger | warning`,
resolved by `toneColor()` (`:181-190`) to the corresponding palette colour, defaulting
to `colors.accent`.

**Note the solid-button ink rule** (`LogiNexus.tsx:88`): solid buttons put
`colors.background` (`#050910`) on the tone fill, never white. Same reasoning as
`presenceAccent.ink`.

**`mobile-native/src/components/Screen.tsx` (250 lines)** — layout + state shells:

| Primitive | Line | Notes |
|---|---|---|
| `Screen` | `:17` | Title/subtitle + ScrollView; title 28/800, subtitle 15/21 |
| `LogiNexusScreenShell` | `:38` | Non-scrolling shell; **transparent, never filled** (`:150-162` explains why: a fill would cover the root `PulseBackground`) |
| `LogiNexusScrollContainer` | `:47` | Scroll shell with bottom-nav clearance + auto-hide-nav scroll handlers |
| `LogiNexusSection` | `:75` | Titled section, `gap: spacing.md` |
| `LogiNexusStatePanel` | `:87` | **The shared state component — see §5** |
| `LogiNexusResponsiveColumns` | `:117` | 1/2/3 columns by width; **already has a breakpoint model, see §10** |

`Panel.tsx` is a 19-line thin wrapper.

### 4.2 Where the rest of the primitives actually live — per feature, duplicated

The kit above has **no input, no tab bar, no chip, no avatar, no toast, no bottom sheet,
no skeleton, no divider and no progress primitive.** Those exist, but each feature folder
grew its own. This is the component-level mirror of the `*Light.ts` theme drift.

| Web primitive needed | Native implementations (pick one to port, don't port all) |
|---|---|
| **Button** | `LogiNexus.tsx:53` (canonical) · `auth/signup/PulsePrimaryButton.tsx` · `marketplace/GlowButton.tsx` |
| **Text input / secure field** | `auth/SecureTextField.tsx` · `auth/ManualLoginForm.tsx` · `listingWizard/controls.tsx` |
| **Switch / toggle** | `ads/PauseSwitch.tsx` · `ads/ModeToggle.tsx` · `marketplace/ModeToggle.tsx` · `messages/AwayModeSwitchTile.tsx` |
| **Card** | `LogiNexus.tsx:23` (canonical) · `ads/CampaignCard.tsx` · `orders/OrderCard.tsx` · `hub/SectionCard.tsx` · `insights/TipCard.tsx` · `marketplace/OfferCard.tsx` · `payments/BalanceCard.tsx` · `store/StoreKpiCard.tsx` · `payments/PayoutMethodCard.tsx` |
| **Avatar** | `messages/InboxAvatar.tsx` · `events/AvatarStack.tsx` (stacked/overlapping) · `activity/TypeCircle.tsx` |
| **Badge / pill** | `LogiNexus.tsx:32` (canonical) · `ads/AdsStatusPill.tsx` · `orders/OrdersStatusPill.tsx` · `orders/SourceBadge.tsx` · `crypto/IntelligenceBadge.tsx` · `PulseIdBadge.tsx` |
| **Chip** | `messages/ContextChip.tsx` · `messages/FilterChips.tsx` · `marketplace/CategoryChipRail.tsx` · `ads/WalletChip.tsx` |
| **Tab bar** | `ads/AdsTabBar.tsx` · `store/StoreTabBar.tsx` |
| **List row** | `messages/ConversationRow.tsx` · `activity/NotificationRow.tsx` · `store/StoreListingRow.tsx` · `payments/LedgerRow.tsx` · `insights/RankedListingRow.tsx` · `insights/SourceBreakdownRow.tsx` · `events/EventRow.tsx` |
| **Modal** | No shared primitive. RN `<Modal>` used raw in **23 files**. |
| **Bottom sheet** | `store/StoreBulkSheet.tsx` · `MasterNavigationDrawer.tsx` · `ConversationControlCenter.tsx` · `StatusActionRail.tsx` · `PulseCommand.tsx` — all bespoke |
| **Toast / banner** | `InAppNotificationBanner.tsx` (the closest to a toast) · `messages/ExpiryBanner.tsx` · `payments/RefundActionBanner.tsx` · `store/StoreAttentionBanner.tsx` · `events/LiveNowBanner.tsx` |
| **Skeleton** | No shared skeleton component. `ActivityIndicator` used in **104 files**; per-feature `*States.tsx` files carry the loading treatment |
| **Progress** | `ads/BudgetPacingBar.tsx` · `events/CapacityBar.tsx` · `insights/HealthRing.tsx` · `auth/signup/SignupProgress.tsx` · `auth/signup/PasswordStrengthMeter.tsx` · `orders/OrderTimeline.tsx` |
| **Divider** | No component. `borderTopWidth: StyleSheet.hairlineWidth` + `colors.border` inline |
| **Charts / sparklines** | `insights/DualLineChart.tsx` · `crypto/AssetSparkline.tsx` · `store/StoreSparkline.tsx` · `ads/SpendBarChart.tsx` (all `react-native-svg`) |
| **Status LED / dot** | `store/StoreStatusLed.tsx` · `messages/PresenceDot.tsx` · `LogiNexus.tsx:103` |

### 4.3 Brand / atmosphere components (high value on web)

| Component | Purpose |
|---|---|
| `PulseBackground.tsx` | Draws the gradient + node mesh from `theme/pulseBackground.ts`. **Mounted once at the root** — shells are transparent so it shows through (`Screen.tsx:150-162`) |
| `GalacticAtmosphere.tsx` | Atmosphere layer, driven by `theme.galacticBackground` |
| `StaticUFOField.tsx`, `WelcomeUfoOverlay.tsx` | Decorative craft field / welcome overlay |
| `home/LivingPulseSocWordmark.tsx` | The animated wordmark — **the logotype treatment for web** |
| `auth/PulseSocBrandHeader.tsx`, `auth/signup/SignupBrandHeader.tsx`, `auth/LoginBackground.tsx` | Brand headers |
| `PulseCommand.tsx` | Command palette / global action surface |
| `MasterNavigationDrawer.tsx` | Global nav drawer |

### 4.4 Recommendation for the web kit

Build **one** primitive layer with the `tone` API from `LogiNexus.tsx` (it is the only
abstraction that is genuinely shared and it is good), and fill the 9 gaps it has —
input, chip, tab, avatar, modal, sheet, toast, skeleton, divider — **once**, rather than
19 times. The feature `*Card`/`*Row`/`*States` files should become compositions of that
layer, not siblings of it.

## 5 — State presentation patterns

### 5.1 The shared state component

`LogiNexusStatePanel` (`mobile-native/src/components/Screen.tsx:87-115`) is the one
app-wide state surface. It takes a **closed union** of eight kinds
(`Screen.tsx:15`):

```
"loading" | "empty" | "offline" | "error" | "success" | "permission" | "unsupported" | "maintenance"
```

Each kind maps to a tone and a glyph, with no fall-through ambiguity:

| State | Tone (`Screen.tsx:131-137`) | Colour | Glyph (`:139-147`) |
|---|---|---|---|
| `error` | `danger` | `#ff5f7e` | `!` |
| `offline` | `warning` | `#f3c461` | `⌁` |
| `maintenance` | `warning` | `#f3c461` | `◌` |
| `unsupported` | `warning` | `#f3c461` | `↗` |
| `success` | `safety` | `#3ff0a0` | `✓` |
| `permission` | `intelligence` | `#9f7cff` | `◇` |
| `loading` | `default` | `#32e6b3` | spinner (`ActivityIndicator`) |
| `empty` | `default` | `#32e6b3` | `·` |

Visual spec (`Screen.tsx:205-239`): `glassStrong` fill, `radius.panel` (20),
border at tone + `80` (50% alpha), a 48×48 circular glyph ring, `minHeight: 240`,
`padding: spacing.xxl` (24), title `sectionTitle` centred, body `body`/`muted`
capped at **`maxWidth: 520`** and centred.

`LogiNexusEmptyState` (`LogiNexus.tsx:93-101`) is a lighter, in-flow empty variant.

### 5.2 Per-feature state modules

Six features ship their own state file rather than using the shared panel:
`ads/AdsStates.tsx`, `dropshipping/DropshippingStates.tsx`, `messages/MessagesStates.tsx`,
`orders/OrdersStates.tsx`, `payments/PaymentsStates.tsx`, `store/StoreStates.tsx`.

`messages/MessagesStates.tsx` is the best model to port. It exports, in one file so
"every state reads as the same surface" (`:1-7`):

- `MessagesSkeleton({ rows = 6 })` — **six ghost rows that echo the real row layout**
  (avatar circle + 45% bar + 80% bar + 38% chip), `accessibilityLabel="Loading conversations"`.
  This is the skeleton idiom to copy: shape-matched placeholders, not a spinner.
- `MessagesEmpty` — generic empty.
- `MessagesFilterEmpty({ filter })` — **per-filter empty copy from a
  `Record<InboxFilter, {title, body}>`** (`:52-66`), so "no unread" and "no disputes"
  are different sentences, not one generic line.
- `MessagesError({ message, onRetry })` — inline error **with a retry action**.
- An offline banner that "never claims freshness — it says the list may be stale and
  offers a refresh".

### 5.3 THE RULE: error and empty must never co-render

This is enforced in this codebase, and it is enforced **structurally, not by
convention**. Three independent implementations, all with the reasoning written down:

**(a) A closed union with one `switch`** —
`mobile-native/src/components/dropshipping/DropshippingStates.tsx:5-18`:

> `DropshippingStateView` returns `null` for `READY` and renders a block for every
> other state. A screen calls it once, and when it returns a block the screen renders
> that *instead of* its content, not above it. That is the mechanical reason an error
> and an empty state cannot co-render here: they are branches of one `switch` over a
> closed union, so there is no arrangement of booleans that produces both.

**(b) A closed state list instead of booleans** —
`mobile-native/src/api/dropshipping.ts:1810-1817`. `DROPSHIPPING_STATES` is a 20+
member union (`LOADING`, `EMPTY`, `READY`, `STALE`, `SESSION_EXPIRED`,
`SUPPLIER_DISCONNECTED`, …) precisely so that failure modes cannot be collapsed:

> They are one closed set rather than a pile of booleans because several of them look
> alike and must not be allowed to co-render: an `ERROR` that also draws the `EMPTY`
> copy tells a merchant their supplier has no products when in fact the request
> failed.

**(c) Empty gated on a successful read** —
`mobile-native/src/screens/PrivateConversationsScreen.tsx:208-218`:

> Empty is a claim about data and is gated on a successful read. The refusal panels
> below are the mutually exclusive alternative — `ready` is null in every one of those
> branches, so the two can never co-render.

And `mobile-native/src/screens/dropshipping/SuppliersScreen.tsx:145`:
"The list failing is an error about the list. It is never EMPTY."

### 5.4 How the web must honour it

**Do not port this as a lint rule or a code-review note. Port it as a type.**

1. **Model every data surface as a discriminated union, not a booleans bag.**
   `{ status: 'loading' } | { status: 'error', error } | { status: 'ready', data }`.
   `empty` is then not a status at all — it is `status === 'ready' && data.length === 0`.
   That makes "empty while errored" **unrepresentable**, which is the whole point.
2. **`empty` requires EVERY contributing source to be `ready`.** A page that merges
   three fetches may only say "nothing here" when all three succeeded. If any one
   failed, the page is in `error`.
3. **The shared web `<StateView>` renders exactly one branch** and replaces the
   content region — it must never be composed *above* a list.
4. **Error always carries a retry affordance;** empty never does (there is nothing
   to retry). This is the user-visible tell that the two are distinct.
5. **Offline is its own state**, warning-toned, and it must say the data may be stale
   rather than claiming it is current. Never render offline as error or as empty.
6. **Empty copy should be context-specific** — mirror the `Record<Filter, copy>`
   pattern so a filtered empty explains the filter, not the feature.
7. **Skeletons must be shape-matched** to the content they replace (avatar circle +
   text bars at realistic widths), which also prevents layout shift on web.

A suggested CI guard for web: fail any component where an `empty` render path is not
transitively guarded by a successful-fetch discriminant.

## 6 — Motion

### 6.1 Library: RN `Animated` only

**There is no Reanimated and no Moti.** `mobile-native/package.json` has neither.
Everything is React Native's built-in `Animated` (920 references across `src`),
with `useNativeDriver: true` in 40 files. Gradients are `expo-linear-gradient`;
vector work is `react-native-svg`; gestures are `react-native-gesture-handler`;
haptics are `expo-haptics`.

**Web consequence: everything here is transform/opacity-based and therefore maps
cleanly to CSS transitions, CSS keyframes, or the Web Animations API. No physics
library is required.** (`useNativeDriver: true` means only `transform` and `opacity`
are animated — exactly the two GPU-composited properties on web.)

### 6.2 Canonical durations
`logiNexus.motion` (`mobile-native/src/theme/logiNexus.ts:137-160`):

| Token | ms | Use |
|---|---|---|
| `instant` | 80 | Immediate feedback |
| `quick` | 150 | Toggle, chip, small state change |
| `standard` | 240 | Default transition |
| `reveal` | 360 | Panel/sheet reveal |
| `entrance` | 600 | Per-element entrance fade/slide |
| `stagger` | 90 | Gap between neighbours (element N starts at N × 90ms) |
| `ambient` | 1400 | Base ambient loop |
| `ringDraw` | 1100 | Ring/arc draw |
| `scanSweep` | 2600 | Scan stripe |
| `borderShimmer` | 5200 | Rotating card border |
| `tickerCycle` | 26000 | Ticker |

The long ones are long deliberately (`:151-155`): "anything faster reads as activity
rather than atmosphere, and all of them are suppressed outright under reduce-motion."

### 6.3 Easing
`logiNexusMotion.easing` (`mobile-native/src/theme/logiNexusMotion.ts:7-11`) — three curves:

| Token | RN | CSS equivalent |
|---|---|---|
| `standard` | `Easing.inOut(Easing.quad)` | `cubic-bezier(0.45, 0, 0.55, 1)` |
| `exit` | `Easing.out(Easing.quad)` | `cubic-bezier(0.5, 1, 0.89, 1)` |
| `enter` | `Easing.in(Easing.quad)` | `cubic-bezier(0.11, 0, 0.5, 0)` |

Measured usage across `src`: `Easing.inOut(Easing.sin)` (14 — ambient breathing),
`Easing.inOut(Easing.quad)` (12), `Easing.out(Easing.quad)` (10),
`Easing.out(Easing.cubic)` (5), `Easing.in(Easing.quad)` (5), `Easing.linear` (4).
`Easing.inOut(Easing.sin)` for loops → CSS `cubic-bezier(0.37, 0, 0.63, 1)` or
`animation-timing-function: ease-in-out` on an `alternate` keyframe.

Springs appear for pops only, e.g. `marketplaceMotion` `tension: 160`.

### 6.4 The per-feature motion pattern

Seven per-feature motion modules (`storeMotion` 407 LOC / 51 importers,
`marketplaceMotion` 277, `paymentsMotion` 260, `insightsMotion` 234,
`businessLiveMotion` 219, `adsMotion` 197, `logiNexusMotion` 63 / 58 importers).

They all follow the **same three-part shape**, which is genuinely good architecture and
should be carried to web:

1. **A named ambient period table.** `storeMotion.ts:47-64` — `headerSheen 6000`,
   `bellWiggle 5000`, `statusPing 2000`, `ledBlink 1300`, `bannerTilt 4000`,
   `bannerShimmer 7000`, `ctaGleam 9000`, `trendBob 2600`. Named "so the whole screen's
   tempo can be read in one place — and so it is obvious that nothing here is fast".
   `marketplaceMotion` equivalents: `offerPing 2000`, `offerShimmer 7000`,
   `buttonGlow 2600`, `buttonGleam 9000`, `rocketBob 2600`.
2. **A one-shot duration table.** `storeMotion.ts:67-79` — `sparkline 900` (left-to-right
   draw), `valueSlide 320`, `badgePop 420`, `tabSlide 220`, **`press 110`**.
   `marketplaceMotion`: `heartPop 380`, `addedConfirm 1400`, `modeSwap 180`.
3. **Hooks that take `reducedMotion` as a parameter and still return the same props.**
   `useStorePress` (`storeMotion.ts:386-407`): under reduce-motion the handlers are
   returned but do nothing, "so callers wire the same props either way". This means
   reduce-motion can never cause a layout or API difference — only a visual one.

Entrance choreography: `STORE_ENTRANCE_MS = 550`, `STORE_STAGGER_MS = 80`,
`ENTRANCE_TRAVEL = 12px` upward (`storeMotion.ts:35-39`). The core token set uses
`entrance 600` / `stagger 90` (`logiNexus.ts:149-150`) — these two disagree slightly;
**pick 600/90 for web** and normalise.

Press feedback: scale to **`0.98`** for cards and buttons ("should feel like they take
the touch"), **`1.05`** for listing thumbnails ("swell"), over 110ms.

### 6.5 Reduce-motion — it is a first-class input, not an afterthought

Two mechanisms, both must be reproduced:

- `theme.duration(ms)` (`ThemeContext.tsx:249`) **returns `0`** when reduce-motion is on.
- `useLogiNexusReducedMotion()` (`logiNexusMotion.ts:14-34`) subscribes to
  `AccessibilityInfo.reduceMotionChanged` live.
- Ambient loops additionally gate on foreground (`useAppForegrounded`,
  `storeMotion.ts:86`) so nothing animates off-screen.
- `PulseBackground` has a `restingProgress: 0.4` (`pulseBackground.ts:89`) — under
  reduce-motion the field is drawn at 40% through its cycle rather than frozen at 0,
  so the composition still looks designed.

**Web:** `@media (prefers-reduced-motion: reduce)` must zero every duration, and the
`static` background variant (`pulseBackground.ts:252`) is the reduce-motion backdrop.
Use `document.visibilityState` / `IntersectionObserver` to pause ambient loops off-screen —
the direct equivalent of `useAppForegrounded`.

### 6.6 Haptics
`expo-haptics`, by frequency: `Haptics.selectionAsync` (21) for tab/chip/selection
changes, `Haptics.impactAsync` with `ImpactFeedbackStyle` (19/18) for presses and
confirmations, `Haptics.notificationAsync` with `NotificationFeedbackType` (17/15) for
success/warning/error outcomes. Gated by `theme.hapticFeedback` (`ThemeContext.tsx:247`).

**Web has no equivalent worth shipping.** The Vibration API is unsupported on iOS Safari
and intrusive elsewhere. Every haptic must be **replaced by a visual or auditory
confirmation** on web — see §10.

### 6.7 Native → web motion mapping

| Native | Web |
|---|---|
| `Animated.timing(opacity/transform)` + `useNativeDriver` | CSS `transition` on `opacity`/`transform` |
| `Animated.loop(sequence)` ambient | CSS `@keyframes` + `animation-direction: alternate` |
| `Animated.stagger` / index × 90ms | `animation-delay: calc(var(--i) * 90ms)` |
| `Animated.spring({tension:160})` | `linear()` easing or a short `cubic-bezier` overshoot |
| Sparkline draw 900ms | SVG `stroke-dasharray`/`stroke-dashoffset` transition |
| Shimmer / gleam / sheen | Animated `background-position` on a `linear-gradient` |
| `useStorePress` scale 0.98 | `:active { transform: scale(0.98) }`, 110ms |
| Thumbnail swell 1.05 | `:hover { transform: scale(1.05) }` — **web gets hover, native has none; use it** |
| Screen transitions (React Navigation) | View Transitions API where available, else fade/slide 240ms |
| Haptics | **No equivalent — replace with visual confirmation** |
| `AccessibilityInfo.isReduceMotionEnabled` | `matchMedia('(prefers-reduced-motion: reduce)')` |
| `useAppForegrounded` | `visibilitychange` + `IntersectionObserver` |

## 7 — High-identity surfaces

These four carry the brand. Values below are exact from the native source.

### 7.1 Post card — `mobile-native/src/components/PostCard.tsx` (1,841 lines)

**The feed is not a stack of cards. It is a divided list.** (`:1313-1319`)
```
card: { borderBottomColor: logiNexus.colors.home.borderSubtle,  // rgba(100,160,255,0.28)
        borderBottomWidth: 1, paddingTop: 14, paddingBottom: 14 }
cardInset: { paddingHorizontal: 16 }
```
There is **no card background fill and no card border radius** — the post sits directly
on the `PulseBackground` field, separated from its neighbours by one indigo hairline.
Media bleeds (`mediaBleed: { marginTop: 12 }`) outside the 16px inset.

| Part | Spec (file:line) |
|---|---|
| Avatar | 48×48, `borderRadius: 24`, **`borderWidth: 2` in `colors.accent` `#32e6b3`** (`:1271-1277`) |
| Avatar fallback | same box, `borderWidth: 1` in `colors.border` (`:1278-1285`) |
| Author row | `flexDirection: row`, `gap: 9`, `minWidth: 0` (`:1263-1269`) |
| Author name | `typography.home.cardAuthor` = 16/20/900 (`:1239-1243`) |
| Body | `typography.home.cardBody` overridden to **15/22/600** (`:1294-1299`) |
| Action row | `borderTopWidth: 1` in `borderSubtle`, `marginTop: 10`, `paddingTop: 4` (`:1222-1229`) |
| Action button | `flex: 1` (equal thirds), `minHeight: 38`, `radius.medium` (12), `gap: 6` (`:1200-1209`) |
| Action pressed | `backgroundColor: rgba(255,255,255,0.05)` (`:1210-1212`) |
| Action icon | 15/900, `colors.muted`; **active → `colors.danger`** (`:1213-1221`) |
| Action label | 11/800, `textTransform: capitalize`; active → `colors.accent` (`:1231-1238`) |
| Badge row | wrap, `gap: 7`, `marginTop: 8` (`:1288-1293`) |
| "Automated" pill | `borderRadius: 999`, border `#f4b740`, fill `rgba(244,183,64,0.12)`, text `#f4c96b` at **8px/900** with `letterSpacing 0.5` (`:1249-1261`) |

**Web:** a `<article>` with `border-bottom: 1px solid var(--pulse-feed-hairline)`, no
radius, no fill. This is the single most important composition to get right — filling
the card would hide the gradient and instantly read as a different product.

### 7.2 Reel card — `mobile-native/src/components/ReelPlayerCard.tsx` (677 lines)

Plus three surface variants in `src/components/reels/`: `ReelCarouselSurface.tsx`,
`ReelLiveViewerSurface.tsx`, `ReelPhotoSurface.tsx` — i.e. the reel *player* is one
component and the *presentation* is swapped. Full-bleed media with an overlaid action
rail; scrims are drawn from the off-palette dark family catalogued in §3
(`#02050b` / `#030812` family) — the web token set names these `--pulse-scrim-*`.

**Web:** full-viewport-height snap container
(`scroll-snap-type: y mandatory`) on phone; on desktop this becomes a centred 9:16
stage with the rail beside it, not overlaid — see §10.

### 7.3 Profile header — `mobile-native/src/components/ProfileHeader.tsx` (667 lines)

The most elaborate surface in the app. Composition, outermost first:

1. **Cover** — `StyleSheet.absoluteFillObject` image (`:585`); brand covers use
   `aspectRatio: 1600 / 640` (= 2.5:1).
2. **Atmosphere over the cover** — two nebulae (`nebula` 300×300 `borderRadius: 220`
   offset `right:-90 top:-70`; `nebulaTwo` 220×220 `left:-70 top:40`), a `pulseWave`
   320×320 ring at `borderWidth: 1.5`, a `grain` wash of `rgba(5,9,16,0.12)`, angled
   `trail` hairlines (1px wide, rotated `14deg` / `-11deg`), and a **`horizon`**:
   a 960×960 circle with `borderRadius: 480` positioned at `top: 196` so only its top
   arc enters the hero — the comment says it "reads as a planet limb".
   A `horizonGlow` 132px tall sits at the bottom.
3. **Body overlaps the cover** — `body: { marginTop: -96, paddingHorizontal: 18 }`.
4. **The avatar ring stack** (`:601-607`) — four concentric layers in a 128×128 box:
   - `ringOuter` 144×144, `borderWidth: 1`
   - `ringOrbit` 138×138, `borderWidth: 1`, `rotate: -38deg` — **one lit segment via
     `borderTopColor` on an otherwise dim ring, "the cheapest way to imply rotation
     without animating anything"**
   - `ringGlow` 132×132, `borderWidth: 2`, `shadowOpacity: 0.9`, `shadowRadius: 22`
   - `avatar` 112×112, `borderRadius: 56`, `borderWidth: 3`
   Ring pulse animates scale `1 → 1.12` (`:247`); avatar shrinks `1 → 0.78` and lifts
   `0 → 14px` on scroll 0→200 (`:259-260`).
   Overlays: `verifiedSeal` 28×28 bottom-right, `presenceDot` 18×18 `borderWidth: 3`
   bottom-left.
5. **Identity** — name 28/900 `letterSpacing 0.2`; handle 14 muted; badges are
   `borderRadius: 999` pills at 11/900 capitalized.
6. **Stats panel** — `profileNeon.panel` fill, `profileNeon.border`,
   `profileNeon.radius.panel` (20), plus a **`statsRail`: a 2px lit top edge fading
   left-to-right**. Stat cells `minHeight: 74`, value 21/900, label 10/800
   `letterSpacing: 0.7` uppercase, divided by hairline `statDivider` inset
   `marginVertical: 14`.
7. **Actions** — `minHeight: 48`, `minWidth: 92`, `profileNeon.radius.action` (14);
   primary = `borderStrong`, secondary = `panel` + `border`,
   selected = `fillMedium` + `cyan`.
8. **Module grid** — 4-up (`width: "25%"`), icon tiles 58×58 `borderRadius: 20`,
   label 11/800, status 9/900. `moduleGlow` uses `shadowOpacity: 0.55`,
   `shadowRadius: 10`, **`elevation: 0` on purpose** — the Android Material shadow
   "would render as a grey smear under the tile rather than a gold halo".

**Owner override:** a profile's `theme.accent_color` wins over `profileNeon`
(`profileNeon.ts:11-13`). The web must support the same per-profile accent.

**Web:** this whole atmosphere layer is *easier* on web — nebulae and horizon are
`radial-gradient` divs, the trails are 1px rotated elements, grain is an overlay.
The lit-arc ring is `border-top-color` on a rotated circle, exactly as native.
Disabled/pressed opacities: `0.55` / `0.7`.

### 7.4 Message bubbles — `mobile-native/src/screens/ChatScreen.tsx` (3,106 lines)

`MessageBubble` at `:2040`; styles at `:2585-2640`. Exact:

```
bubble:      { borderRadius: 17, maxWidth: "84%", minWidth: 88,
               paddingHorizontal: 12, paddingVertical: 10, gap: 6 }
mineBubble:  { backgroundColor: rgba(37,83,158,0.82),
               borderColor:     rgba(93,174,255,0.58),
               borderWidth: 1, borderBottomRightRadius: 6 }
theirBubble: { backgroundColor: rgba(12,24,43,0.88),
               borderColor:     rgba(105,218,240,0.28),
               borderWidth: 1, borderBottomLeftRadius: 6 }
moderatedBubble: { borderColor: rgba(255,204,102,0.35) }
body:        { color: colors.text, fontSize: 15, lineHeight: 21 }
meta:        { color: colors.muted, fontSize: 9 }
forwarded:   { fontSize: 11, fontWeight: 800, textTransform: uppercase }
```

Key identity notes:
- **Both bubbles are translucent, not opaque** (0.82 / 0.88) — the `PulseBackground`
  shows through. Do not flatten these to solid on web.
- **Both have a 1px luminous border**, blue for mine, cyan for theirs. This is the
  PulseSoc chat signature and no other messenger does it.
- **The tail is a radius asymmetry, not a drawn tail**: 17px everywhere except
  6px on the bottom corner facing the speaker.
- Mine right-aligned, theirs left (`mineWrap` / `theirWrap`).
- Moderated content changes only the border to amber — content is not hidden.

**Web:** direct CSS. `border-radius: 17px 17px 6px 17px` (mine) /
`17px 17px 17px 6px` (theirs), `max-width: min(84%, 48ch)` — cap by `ch` on desktop
so bubbles don't become unreadable full-width lines (see §10).

## 8 — Brand asset divergence

### 8.1 Logo files — mostly aligned, one orphan

| File | SHA1 (first 8) | Note |
|---|---|---|
| `mobile-native/assets/icon.png` | `39a39a26` | **Byte-identical** to the web logo below |
| `static/brand/pulsesoc-logo-20260813.png` | `39a39a26` | ✅ same file |
| `static/brand/pulsesoc-logo-20260606.png` | `f2ce85f6` | older generation, still shipped |
| `mobile-native/src/assets/brand/pulsesoc-mark.png` | `2e4dffce` | ⚠️ **in-app mark with no web counterpart** |
| `static/brand/pulsesoc-icon-512-20260813.png` | `c4e75192` | web PWA icon |

`static/brand/` also carries a **legacy `pulse-*` set alongside the `pulsesoc-*` set**
(`pulse-logo-20260606.png`, `pulse-favicon-*`, `pulse-icon-*`). Two brand generations
are live simultaneously.

**Flag:** the app icon and web logo agree. The **in-app mark
(`src/assets/brand/pulsesoc-mark.png`) does not exist on web**, and there is no SVG of
any mark anywhere in the repo. The rebuild needs an **SVG logotype + mark**, derived
from the native mark, as its first asset deliverable.

Also relevant: the app's animated wordmark is a *component*, not an asset —
`mobile-native/src/components/home/LivingPulseSocWordmark.tsx`. The web should port the
component, not export a PNG of it.

### 8.2 Colour — a port already exists, and it is good

`static/css/pulsesoc-tokens.css` (14.4 KB) **already ports the native palette verbatim**
and says so in its header: "Derived from the native palette at
`mobile-native/src/theme/colors.ts` … every colour below is the NATIVE value, not the
previous web value."

Section 1 of that file reproduces all 23 `colors.ts` tokens exactly — verified:
`--pulse-palette-background: #050910`, `--pulse-palette-accent: #32e6b3`, etc. all match.

Its own header documents the divergence it was written to fix:
- 23 native semantic tokens vs **159 CSS custom-property names across 19 stylesheets**
- **45 of those names had conflicting values in different files** (e.g. `--bg` was
  `#020711`, `#030811` and `#050b14` depending on cascade order; `--control-accent`
  was `#49ffc8`, `#4f8cff` **and** `#5ff4ff` simultaneously)
- **0 native tokens matched their nearest web equivalent exactly** before the port

It resolves this with a legacy-alias block (section 5 of that file) re-pointing ~40 old
variable names at the canonical tokens, because "151 page routes generate HTML inline
inside `bot.py` with hardcoded class names".

**But it is only half-adopted.** Measured now:

| Metric | Value |
|---|---|
| Templates loading `pulsesoc-tokens.css` | **15 of 20** |
| Distinct 6-digit hex literals still in `static/css/*.css` | **312** |
| Native distinct hex literals in `src/screens` + `src/components` | 160 |

Top web-only literals, none of which are native tokens:

| Hex | Uses | Nearest native token | Delta |
|---|---|---|---|
| `#36e58f` | **64** | `accent #32e6b3` | Different hue — greener, less teal |
| `#6edff6` | **58** | `accentStrong #61d8ff` | Lighter, desaturated |
| `#06101b` | 43 | `surface #0b141c` | Darker, bluer |
| `#dffcff` / `#f2fbff` / `#eaffff` | 24/20/8 | `text #f4f7fb` | Three cyan-tinted whites |
| `#f8fafc` | 21 | `text #f4f7fb` | Close but not equal |
| `#9fb5c0` | 17 | `muted #9aa8b7` | Close but not equal |
| `#ffd166` | 13 | `warning #f3c461` | More saturated |
| `#020617` / `#02050b` / `#020817` | 13/13/8 | `background #050910` | Three competing darks |

**So: the web's declared palette agrees with native; the web's actual pixels do not.**
`#36e58f` (64 uses) and `#6edff6` (58 uses) are the previous PulseSoc web brand and they
are still the most common colours on the site.

### 8.3 Typography — genuine divergence, must be resolved

| | Native | Web |
|---|---|---|
| Stack | **Platform system font** (SF Pro / Roboto). No `useFonts`, no font file in repo. | `Inter, ui-sans-serif, system-ui, -apple-system, …` (`pulsesoc-tokens.css:141`) |
| Webfont loaded? | n/a | **No.** Zero `@font-face`, no `static/fonts/`. Inter is named but never served, so it only renders for users who happen to have it installed. |
| Body weight | **600** (`logiNexus.typography.body`) | `--font-weight-regular: 400` |
| Heading weight | **900** | `--font-weight-bold: 700` |
| Type scale | 34/24/18/15/12 with line-heights 39/30/24/22/17 | 36/28/22/18/16/14/12 with ratio line-heights 1.25/1.5/1.7 |

**This is the sharpest divergence in the whole comparison.** Native PulseSoc is a
900-weight, tight-leading product. The web tokens describe a 400/700, 1.5-leading
product. Rendered side by side they look like different companies.

### 8.4 Gradient — completely absent from web

`grep` for every `pulseBackground` colour (`#101A4A`, `#13235C`, `#1E2B78`, `#31206E`,
`#7C4DFF`, `#A67CFF`, `#42E8D0`) across `static/css/*.css` returns **zero matches**.

The deep-space indigo→violet field and its node mesh — the strongest single brand signal
in the app — **does not exist on the website at all.**

### 8.5 The divergences the rebuild must decide

| # | Divergence | Recommendation |
|---|---|---|
| 1 | Web still renders `#36e58f`/`#6edff6` while declaring `#32e6b3`/`#61d8ff` | **Pick native.** Finish the `pulsesoc-tokens.css` migration; delete the 312 literals. |
| 2 | Native has no custom font; web names Inter but never loads it | **Decide and commit.** Either self-host Inter and adopt it in native too, or drop Inter from the web stack and use the system UI stack. Do not keep a font that only some visitors have. |
| 3 | Native body 600 / headings 900; web 400 / 700 | **Pick native.** The heavy weight is the identity. |
| 4 | `PulseBackground` gradient absent on web | **Port it.** Section 9 gives the CSS. |
| 5 | Two brand generations (`pulse-*` and `pulsesoc-*`) in `static/brand/` | Retire `pulse-*`. |
| 6 | No SVG mark anywhere | Produce one; it is the rebuild's first asset. |
| 7 | `pulsesoc-tokens.css` loaded by 15/20 templates | Load it first, everywhere, or inline the `:root` block in the base layout. |
| 8 | Web spacing/radius scale differs from native (`4/8/12/16/24/32/48` vs `4/8/12/16/20/24/32/40/48`; radius `6/10/16/24/999` vs `8/12/16/18/20/999`) | Minor. Adopt the native scale and keep the web's extra `--spacing-section: 64px` for desktop rhythm — that one is a legitimate web-only addition. |

**One thing the existing web tokens got right and should be kept:** the
`@media (prefers-reduced-motion: reduce)` block zeroing all motion vars
(`pulsesoc-tokens.css:182-188`) and the `@media (prefers-contrast: more)` block — these
correctly mirror native's `theme.duration()` and `HIGH_CONTRAST_*` behaviour.

## 9 — PROPOSED WEB TOKENS

A CSS custom-property set that is a **faithful translation** of `colors.ts`, not a
reinterpretation. Rules followed:

- Every value is copied exactly from native. No new hues are invented.
- Names are semantic, so a future palette change is a one-file edit.
- The four themes native already ships (dark, light, black/AMOLED, high-contrast ×2) map to
  `[data-theme]` attributes, not to separate stylesheets.
- The three genuine gaps identified in §3 — scrim ramp, `on-accent`, and desktop spacing —
  are added explicitly so they stop being met by literals.

```css
:root,
[data-theme="dark"] {
  /* Surfaces */
  --pulse-bg:              #050910;
  --pulse-surface:         #0b141c;
  --pulse-surface-raised:  #111f2a;
  --pulse-border:          #203746;

  /* Text */
  --pulse-text:            #f4f7fb;
  --pulse-muted:           #9aa8b7;
  --pulse-disabled:        #51606c;

  /* Brand + state */
  --pulse-accent:          #32e6b3;
  --pulse-accent-strong:   #61d8ff;
  --pulse-warning:         #f3c461;
  --pulse-danger:          #ff5f7e;
  --pulse-focus:           #8df7ff;

  /* Domain accents */
  --pulse-intelligence:    #9f7cff;
  --pulse-creator:         #42e7d4;
  --pulse-economy:         #f6c85d;
  --pulse-safety:          #3ff0a0;
  --pulse-crypto:          #62e0ff;

  /* Translucency */
  --pulse-glass:           rgba(11, 24, 34, 0.82);
  --pulse-glass-strong:    rgba(15, 36, 50, 0.94);
  --pulse-signal-dim:      rgba(50, 230, 179, 0.12);
  --pulse-signal-soft:     rgba(97, 216, 255, 0.12);
  --pulse-danger-soft:     rgba(255, 95, 126, 0.14);
  --pulse-warning-soft:    rgba(243, 196, 97, 0.14);

  /* NEW — the media/overlay ramp that native meets with literals (§3) */
  --pulse-scrim-1:         #08110f;   /* was 21 hardcoded uses */
  --pulse-scrim-2:         #03120f;
  --pulse-scrim-3:         #030812;
  --pulse-scrim-4:         #02050b;   /* deepest; below --pulse-bg */
  --pulse-scrim-grad:      linear-gradient(180deg, rgba(2,5,11,0) 0%, rgba(2,5,11,0.72) 100%);

  /* NEW — the on-accent need behind 29 uses of #ffffff */
  --pulse-on-accent:       #050910;   /* text ON the teal accent — NOT white */
  --pulse-on-media:        #ffffff;   /* text over photo/video only */
}

[data-theme="black"] {
  --pulse-bg:              #000000;
  --pulse-surface:         #070a0d;
  --pulse-surface-raised:  #10161c;
  --pulse-border:          #1b2b36;
  --pulse-glass:           rgba(5, 8, 10, 0.92);
  --pulse-glass-strong:    rgba(3, 5, 7, 0.97);
}

[data-theme="light"] {
  --pulse-bg:              #f6f8fb;
  --pulse-surface:         #ffffff;
  --pulse-surface-raised:  #eef2f7;
  --pulse-border:          #d3dde7;
  --pulse-text:            #0b141c;
  --pulse-muted:           #5b6b7c;
  --pulse-disabled:        #a3b0bc;
  --pulse-accent:          #00966f;   /* darkened for 4.5:1 — do not "correct" to #32e6b3 */
  --pulse-accent-strong:   #0071a6;
  --pulse-warning:         #a06a00;
  --pulse-danger:          #c02341;
  --pulse-focus:           #0071a6;
  --pulse-intelligence:    #6a3fd6;
  --pulse-creator:         #0f8f83;
  --pulse-economy:         #9a7413;
  --pulse-safety:          #0a8a52;
  --pulse-crypto:          #0a7ba3;
  --pulse-glass:           rgba(255, 255, 255, 0.86);
  --pulse-glass-strong:    rgba(246, 248, 251, 0.96);
  --pulse-signal-dim:      rgba(0, 150, 111, 0.10);
  --pulse-signal-soft:     rgba(0, 113, 166, 0.10);
  --pulse-danger-soft:     rgba(192, 35, 65, 0.10);
  --pulse-warning-soft:    rgba(160, 106, 0, 0.10);
  --pulse-on-accent:       #ffffff;   /* inverts on light — this is why it must be a token */
}

[data-theme="hc-dark"] {
  --pulse-bg: #000000;  --pulse-surface: #0a0a0a;  --pulse-surface-raised: #161616;
  --pulse-text: #ffffff; --pulse-muted: #d6dde4;   --pulse-border: #7b8b99;
  --pulse-accent: #4dffc8;
  --pulse-glass: #0a0a0a; --pulse-glass-strong: #000000;  /* opaque by design */
}

[data-theme="hc-light"] {
  --pulse-text: #000000; --pulse-muted: #2b3843; --pulse-border: #4a5a68;
  --pulse-accent: #00614a;
}
```

### 9.1 The gradient identity in CSS

`PulseBackground` is the strongest brand signal and is reproducible exactly. The six stops
exist specifically because three band on OLED (`pulseBackground.ts:178-181`) — that reasoning
applies identically to an OLED laptop or phone browser, so **do not simplify to three.**

```css
.pulse-field {
  background:
    linear-gradient(180deg,
      #101A4A   0%, #13235C  22%, #1E2B78  46%,
      #31206E  62%, #13235C  84%, #101A4A 100%);
}
.pulse-field::after {           /* bottom glow: height 45%, opacity 0.7 */
  content: ""; position: absolute; inset: auto 0 0 0; height: 45%;
  opacity: .7; pointer-events: none;
  background: linear-gradient(180deg,
    rgba(124,77,255,0)    0%,
    rgba(124,77,255,.08) 62%,
    rgba(16,26,74,.5)   100%);
}
[data-theme="light"] .pulse-field {
  background:
    linear-gradient(180deg,
      #F6F7FC   0%, #F1F2FA  22%, #ECEBF8  46%,
      #EFEDFB  62%, #F4F5FC  84%, #F8F8FD 100%);
}
[data-theme="light"] .pulse-field::after {
  background: linear-gradient(180deg,
    rgba(124,77,255,0) 0%, rgba(124,77,255,.05) 62%, rgba(255,255,255,0) 100%);
}
```

**The 14-node mesh must be an inline `<svg>`, and its node table must be copied verbatim from
`pulseBackground.ts:117-155`.** The positions are a fixed deterministic table, not random —
the source says so explicitly. A "close enough" hand-placed mesh is the single most likely way
for the web to look subtly off-brand. Only 2 of the 14 nodes carry `pulseCyan #42E8D0`; the
comment in source is that any more "stops being an accent". Keep it at 2.

Respect `prefers-reduced-motion` for the 4 breathing nodes by holding them at rest opacity,
not by removing them.

### 9.2 Migration rule for the 276 hex literals

Do not port them. Snap to the nearest token per §3. The near-misses (`#36f0cf`, `#68f3de`,
`#3bdfff` → `--pulse-accent` / `--pulse-accent-strong`; `#a77cff` → `--pulse-intelligence`)
are drift, not intent, and snapping them is a visual improvement. The near-black family
(`#08110f`, `#02050b`, `#030812`, `#03120f`) maps to `--pulse-scrim-*`. `#ffffff` maps to
`--pulse-on-media` when it sits over a photo and `--pulse-on-accent` otherwise — and those two
resolve differently in light theme, which is exactly the bug the literal was hiding.

---

## 10 — Responsive strategy

The mission's requirement is explicit: *do not simply stretch mobile UI onto desktop.* The
design system supports this because the native app already separates a **content column** from
**navigation chrome**; the web's job is to re-host the same column in a different chrome per
breakpoint.

### 10.1 Breakpoints

| Name | Range | Chrome | Content |
|---|---|---|---|
| `phone` | `< 768px` | Bottom tab bar (5 tabs), top app bar | Single column, full-bleed |
| `tablet` | `768–1119px` | Left icon rail (collapsed), top bar | Single column, max 720px, centred |
| `desktop` | `1120–1599px` | Left nav rail (labelled) + right context column | 2-col: feed 640px + rail 320px |
| `wide` | `≥ 1600px` | Left nav + right context, both wider | 3-col: nav 280 / feed 680 / context 380, centred with max 1800px |

These are content-derived, not device-derived: 640px is where a `PostCard` stops needing to
compromise, and 1120px is the first width that fits 640 + 320 + both gutters + a labelled rail.

### 10.2 The rule that prevents "stretched mobile"

> **The feed column never exceeds 680px at any breakpoint.** Extra width buys *additional
> columns*, never *wider rows*.

A 1920px-wide post is unreadable and instantly signals a mobile app stretched onto a monitor.
What desktop earns instead is persistent context that the phone has to navigate away to reach:

| Breakpoint | What the extra space is spent on |
|---|---|
| tablet | Nav rail becomes persistent (no drawer round-trip) |
| desktop | Right column: trending, suggested people, active live, cart summary |
| wide | Nav labels + a third column for the open detail (post → its thread stays in place) |

### 10.3 Per-area adaptation

| Area | Phone | Desktop |
|---|---|---|
| Feed | Full-bleed cards | 640px column + right context rail |
| Reels | Full-screen vertical, swipe | Centred 9:16 player, max 560px tall, arrow/scroll nav, comments in a side panel rather than an overlay sheet |
| Messages | List → conversation (two screens) | Persistent two-pane: list 320px + thread; the native "back" has no desktop equivalent |
| Marketplace | 1-col grid | 3-col at desktop, 4-col at wide; filters move from a sheet to a persistent left facet panel |
| Profile | Stacked | Header full-width, then 2-col: posts + about/details sidebar |
| Settings | Drilldown list | Two-pane master/detail — the `settings/<id>` registry maps cleanly onto this |
| Composer | Full-screen modal | Centred dialog, max 680px; inline on desktop feed for short posts |
| Live / calls | *Inventory only — out of scope* | *Inventory only — out of scope* |

### 10.4 Things that must not be ported literally

- **Bottom tab bar above 768px.** It is a thumb-reach solution to a problem desktop does not have.
- **Swipe-only affordances.** Reels, stories and carousels need visible keyboard and pointer
  controls on desktop. Native's gesture is an addition on touch, not the only path.
- **Pull-to-refresh as the only refresh.** Needs an explicit control plus polling on web.
- **Full-screen modal sheets.** Above `tablet` these become centred dialogs; a full-screen
  takeover on a 27" monitor to confirm one action reads as broken.
- **`44px` touch targets as a maximum.** They are a *minimum* and stay so; pointer devices can
  use denser layouts but must not go below 44px for anything also reachable by touch.

### 10.5 Implementation note

Use CSS container queries for the components (a `PostCard` should respond to *its column*, not
the viewport — it appears in the 640px feed and in a 380px context rail) and media queries only
for the page-level chrome switch. This is the single decision that makes the three layouts one
component set rather than three.

---

## 11 — App promotion styling

Standing product requirement: the website promotes the native iOS app throughout, not only on a
download page. This section covers **how it should look**. The link mechanics are non-negotiable
and are specified in `PULSESOC_WEB_TARGET_ARCHITECTURE.md` §5 — in short: never hand-write the
App Store URL, always go through `services/app_links.py` and the existing
`templates/_app_link_cta.html` macros (`app_cta()` and `app_store_badge()`).

### 11.1 The two components, and the difference between them

| | `app_cta(destination, id, source)` | `app_store_badge()` |
|---|---|---|
| Says | "Open **this** in PulseSoc" | "Download on the App Store" |
| For | A member who has the app, looking at a specific object | A visitor who does not have the app |
| Style | `--pulse-accent` filled button | Ghost/outline button |
| Placement | Beside the object it refers to | Footer, download page, post-signup |

They are complementary. The existing macro file states the rule: a store badge may appear
alongside a contextual CTA but never instead of one.

### 11.2 Styling

```css
.app-cta {
  background: var(--pulse-accent);
  color: var(--pulse-on-accent);          /* NOT #ffffff — fails contrast on teal */
  border: 1px solid transparent;
  border-radius: 12px;
  padding: 10px 18px;
  min-height: 44px;
  font-weight: 600;
}
.app-cta:hover  { background: color-mix(in srgb, var(--pulse-accent) 88%, #ffffff); }
.app-cta:focus-visible { outline: 2px solid var(--pulse-focus); outline-offset: 2px; }

.app-store-badge {
  background: transparent;
  color: var(--pulse-text);
  border: 1px solid var(--pulse-border);
  border-radius: 12px;
  padding: 10px 18px;
  min-height: 44px;
}
.app-store-badge:hover { background: var(--pulse-signal-dim); }
```

`--pulse-on-accent` resolving to `#050910` on dark is deliberate: white text on `#32e6b3`
measures roughly 1.8:1 and fails WCAG AA outright. This is the most likely accessibility
regression in the whole app-promotion surface, and it is prevented by using the token.

### 11.3 Placement map

| Surface | Component | Notes |
|---|---|---|
| Site footer (every page) | `app_store_badge()` | The baseline "throughout" guarantee |
| Post / reel / listing / profile page | `app_cta(...)` | Contextual; opens that exact object |
| Signed-out landing | Both | Badge in hero, CTA once a destination is known |
| Post-signup confirmation | Both | Highest-intent moment |
| Search results | `app_store_badge()` only | `/search` is claimed by AASA; a CTA here would double up |
| Feed | One CTA per session, near the top | Not per-card |

### 11.4 Hard limits

- **Never an interstitial, a modal on load, or a full-page takeover.** Dismissible inline
  banners at most, and not on the money paths.
- **Never convert web navigation into an app launch.** `is_web_intent_path()` already protects
  privacy, terms, support, login, checkout and `/dashboard`. A signed-in member clicking a nav
  item wants the web page.
- **Never place a CTA on a path the shipped binary cannot resolve.** The iOS entitlement claims
  only `pulsesoc.com`, and the published AASA claims only `/pulse/*` and `/search*`. Check the
  `native_supported` flag before writing button copy — `build_app_link()` raises `AppLinkError`
  rather than shipping a button whose label promises a destination the binary lands nowhere near.
- **The label must state the destination.** "Open this listing in PulseSoc", not "Open" — the
  accessible name has to be meaningful out of context.
- Never hover-revealed, never an overlay, never absolutely positioned. It must not shift layout
  on load.

### 11.5 Density guidance

At most **two** app-promotion elements per viewport, and never two of the same kind. The
requirement is that promotion is *present throughout*, which is satisfied by a persistent footer
badge plus one contextual CTA. Repeating the badge in header, sidebar, inline and footer reads
as a nag and measurably depresses conversion on the pages that matter.
