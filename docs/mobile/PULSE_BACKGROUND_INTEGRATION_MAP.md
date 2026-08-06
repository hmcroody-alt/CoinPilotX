# PulseBackground — integration map

A single default background for PulseSoc means one visual layer that every
surface sits on top of. The mobile app does not currently work that way: the
background is painted independently in ninety-six places, one screen at a time,
and in two more places by library code that does not appear in the app's source
at all. Putting a new layer at the root without first dealing with those paints
produces an app that looks exactly the same as it does today, because every
screen covers the new layer on its first frame.

This document is the survey the integration is built against. It records where
the layer can go, what each position actually covers, which paints have to be
made transparent for the position to mean anything, and which surfaces must be
left opaque on purpose. Every claim carries a file and a line. Where a claim
could only be settled on a device, it is marked unverified rather than guessed
at, because the integration will be built on this and a confident wrong answer
costs more than an admitted gap.

All paths are relative to `mobile-native/` unless stated otherwise.

## The shape of the problem

There are 81 screen modules in `src/screens/` plus 17 more in
`src/screens/settings/`. Only 13 of them import a shared shell from
`src/components/Screen.tsx`. The remaining 85 own their own root `View` and
paint it themselves. That is why this cannot be a one-line change at the root,
and also why it does not have to be 98 screen edits: the paints cluster into a
small number of shapes, and four of those shapes cover most of the app.

`backgroundColor: colors.background` appears 96 times across `src/` and
`App.tsx`. `colors.background` in any form appears 203 times. The 96 break down
as follows, classified by the style key each one sits under:

| Shape | Count | What it is |
| --- | --- | --- |
| Screen root / container / shell / navigator `contentStyle` | 44 | Fills the whole viewport whenever the screen is mounted |
| `center` state fills | 31 | Loading, empty and error states that fill the viewport |
| Lists, keyboard avoiders, sheets, panels | 9 | Span the viewport in practice |
| Genuinely component-local | 12 | Inputs, thumbnails, banners, the pre-navigation bootstrap views |

Eighty-four of the ninety-six are viewport-filling. That number is the real cost
of the change, and it is the number to plan against.

Two further opaque fills are not in that count and are easy to miss:

- `src/settings/components/SettingsShell.tsx:75` paints
  `theme.colors.background` inline rather than through `colors`, so it does not
  match a `colors.background` grep. It is the shell for all 17 settings screens
  (importers listed at `src/screens/SettingsScreen.tsx` and
  `src/screens/settings/*.tsx`).
- `@react-navigation/elements`' `Background` component paints
  `theme.colors.background` and is not in the app's source at all. See the tab
  navigator section below.

## Insertion points, in priority order

### 1. `Tabs.Navigator` `sceneContainerStyle` — mandatory, and currently absent

This is first because without it nothing else works for the twelve tab screens,
and it is the one finding that no amount of reading the app's own source would
surface.

`@react-navigation/bottom-tabs` wraps every tab scene in
`@react-navigation/elements`' `Screen`, which wraps its children in
`Background` (`node_modules/@react-navigation/elements/lib/module/Screen.js:27`,
`node_modules/@react-navigation/elements/lib/module/Background.js:14-16`).
`Background` renders `backgroundColor: colors.background` from the navigation
theme, unconditionally. The only override is the `sceneContainerStyle` prop on
`Tabs.Navigator`, which `BottomTabView` passes straight into that view's style
array (`node_modules/@react-navigation/bottom-tabs/lib/module/views/BottomTabView.js:113`).

`sceneContainerStyle` does not appear anywhere in `src/`. Grep returns nothing.
So today every tab scene — Dashboard, Home, Search, Saved, Groups, Live, Reels,
Create, Status, Messenger, Notifications, PulseAI, Profile, Marketplace,
Settings (`src/navigation/AppNavigator.tsx:166-182`) — sits on an opaque
theme-coloured view supplied by a library.

A background rendered above `<AppNavigator />` would be completely invisible on
all fifteen tabs, which is the app's entire primary surface. The fix is
`sceneContainerStyle={{ backgroundColor: "transparent" }}` on the
`Tabs.Navigator` at `src/navigation/AppNavigator.tsx:145`.

This is the failure mode most likely to be diagnosed as "the component is
broken" rather than "something above it is opaque", because the component would
render correctly in isolation and in every unit test.

### 2. `Stack.Navigator` `screenOptions.contentStyle` — the app's own opaque fill

`src/navigation/AppNavigator.tsx:272` sets
`contentStyle: { backgroundColor: colors.background }` on every stack screen.
`contentStyle` lands on the content container view inside each screen
(`node_modules/@react-navigation/native-stack/lib/module/views/NativeStackView.js:122`).
The library's own `contentContainer` style is `{ flex: 1 }` with no colour
(same file, line 130), so this line is the sole source of the stack's opacity
and it is the app's, not the library's.

Changing it to `"transparent"` is a one-line edit that uncovers every stack
route at once — roughly 100 registrations between lines 294 and 511. It is the
highest-leverage single change in the file.

Note that this also applies to the two screens with a `presentation` option
(`ContentPreview`, `fullScreenModal`, line 306; `PulseShare`, `modal`, line
310), because `screenOptions` merges into every screen's options regardless of
presentation.

### 3. The shells in `src/components/Screen.tsx`

Two style keys carry the opacity:

- `styles.root` (`src/components/Screen.tsx:150-153`) — used by both `Screen`
  (line 22) and `LogiNexusScrollContainer` (line 63).
- `styles.shell` (`src/components/Screen.tsx:184-187`) — used by
  `LogiNexusScreenShell` (line 41).

Both must become transparent. Whether `PulseBackground` should also be
*rendered* inside these shells is a separate question, and the answer is
probably no: the shells are used by only 13 files, so rendering there would
cover a small fraction of the app while creating a double-up wherever a shell is
nested inside a screen that already draws atmosphere — which is exactly what
`ChatScreen` and `ProfileScreen` do today (`src/screens/ChatScreen.tsx:1063-1067`,
`src/screens/ProfileScreen.tsx:359-367`).

The 13 shell importers, for reference: `BusinessOsScreen.tsx:19`,
`ChatScreen.tsx:70`, `GroupsScreen.tsx:33`, `MessengerScreen.tsx:19`,
`NewChatScreen.tsx:18`, `PostDetailScreen.tsx:35`, `PremiumScreen.tsx:16`,
`ProfileScreen.tsx:18`, `RegionTimeScreen.tsx:3`,
`SellerApplicationScreen.tsx:39`, `SellerListingComposerScreen.tsx:5`,
`SellerStoreScreen.tsx:20`, `UserDashboardScreen.tsx:7`.

### 4. `src/settings/components/SettingsShell.tsx:75`

One inline `backgroundColor: theme.colors.background` covering 17 screens. The
single best ratio of edits to screens covered in the codebase, and it will be
missed by anyone grepping for `colors.background`.

### 5. `NavigationContainer` theme — chrome only, not a rendering surface

`App.tsx:332-346` builds the navigation theme from the live palette, with
`background: theme.colors.background` at line 338. This value feeds three
things: the bottom-tabs `Background` described above, the transition background
between stack cards, and header/card chrome.

It should be left pointing at a real dark colour, not made transparent. Its job
during a transition is to be the colour visible in the gap between two cards; a
transparent value there exposes whatever the platform puts behind the navigator,
which is not under the app's control. Set the atmosphere above it, not instead
of it.

### 6. `App.tsx` root — where `PulseBackground` should actually be rendered

`ThemedNavigationShell` (`App.tsx:330-357`) returns `NavigationContainer`
directly with no wrapper. The natural home for the layer is a `View` inside
`SafeAreaProvider` (line 278) with `PulseBackground` as its first child and the
navigation shell as its second, so the layer is mounted once for the app's whole
lifetime and never remounts on navigation.

Rendering it *inside* `NavigationContainer` is worse: the container is
deliberately not keyed on the theme epoch (see the comment at `App.tsx:325-328`,
which explains that remounting it throws the user out of Settings → Appearance),
and adding children to it complicates that reasoning for no benefit.

What a root-level layer will not cover: the two pre-navigation states at
`App.tsx:243-252` (i18n/auth bootstrap) and `App.tsx:254-274` (recoverable and
fatal error), both of which return before `SafeAreaProvider` is ever reached and
both of which paint `colors.background` themselves (lines 247 and 257). These
are the correct two of the ninety-six to leave alone — they render before the
theme provider exists, so they have no theme to read.

It will also not cover anything rendered in an RN `Modal`, which is a separate
native window. See the layering section.

## The exclusion list

These surfaces must keep their own background. In every case the reason is that
the content is the background — a camera preview, a video, a remote video track
— and an atmosphere behind it is either invisible or actively wrong.

| Surface | File | Evidence |
| --- | --- | --- |
| Camera capture | `src/screens/CameraStudioScreen.tsx` | `#02050b` full-bleed fill at line 914; `expo-camera` |
| Reels pager | `src/screens/ReelsScreen.tsx` | `galaxy` absolute fill `#02050b` at line 1099; skeleton fill line 1165 |
| Live host session | `src/screens/LiveHostSessionScreen.tsx` | `#02040a` at line 1020, `#05070f` at 1050/1066/1216 |
| Live studio | `src/screens/LiveStudioScreen.tsx` | `#02050b` at line 543 |
| Live browse/detail | `src/screens/LiveScreen.tsx` | `#02050b` at line 1108 |
| Replay viewer | `src/screens/ReplayViewerScreen.tsx` | `#02040a` at line 176 |
| Voice/video call | `src/screens/CallScreen.tsx` | `screen` `#030812` line 516, `audioBackground` line 518 |
| Incoming-call layer | `src/calls/IncomingCallLayer.tsx` | `#06090f` at line 311; sibling of the navigator at `App.tsx:301` |
| Full-screen media viewer | `src/components/NativeMediaViewer.tsx` | RN `Modal` at line 225, `#02050b` at line 494 |
| Status viewer | `src/components/StatusViewerCard.tsx` | `#02050b` at line 364; presented in a `Modal` from `src/screens/StatusScreen.tsx:280` |
| Content preview | `src/screens/ContentPreviewScreen.tsx` | `fullScreenModal` (`AppNavigator.tsx:306`); overlays assume dark at lines 183-186 |
| Pre-live configuration | `src/live/PreLiveConfigurationSheet.tsx` | `sheet` fill at line 289 |
| Identity QR | `src/screens/PulseIdentityScreen.tsx` | QR needs its white quiet zone — `backgroundColor="#FFFFFF"` at line 118, `qr` panel at line 145 |

A second exclusion category is the Business OS / commerce family, which is
deliberately **light**. These screens paint a fixed `#EAEDED` page colour
(`src/theme/storeLight.ts:46`, re-exported by `adsLight`, `eventsLight`,
`messagesLight`, `ordersLight`, `paymentsLight`, `insightsLight`) and do not
follow the app theme at all. A dark space layer under them would never show, and
if any of them were made transparent it would look broken.

`ActivityScreen.tsx:311`, `AdsManagerScreen.tsx:1335`,
`AdsSubPageScreen.tsx:1014`, `BusinessHubScreen.tsx:590`,
`BusinessOsAdvertisingScreen.tsx:633`, `BusinessOsInsightsScreen.tsx:951`,
`BusinessOsPaymentsScreen.tsx:679`, `CommerceInboxScreen.tsx:438`,
`EventsManagerScreen.tsx:333`, `MarketplaceCartScreen.tsx:412`,
`MarketplaceManagerScreen.tsx:1955`, `OrdersManagerScreen.tsx:296`,
`StoreDashboardScreen.tsx:716`.

Two more in that family use the dark `businessLive` palette
(`src/theme/logiNexus.ts:43-44`, `background: "#03070C"`) and are likewise
self-contained: `BusinessProfileScreen.tsx:932` and
`BusinessBuyerPreviewScreen.tsx:624`.

Finally, the construction gate draws its own space:
`src/screens/GalacticConstructionScreen.tsx:129` (`safe: #030716`) and its
loading fallback `src/screens/ProtectedBusinessRoutes.tsx:102` (`#030716`).

There is no QR *scanner* screen in the app. `react-native-qrcode-svg` is used
for generation only (`src/screens/PulseIdentityScreen.tsx:21`); no
`BarCodeScanner` or equivalent import exists in `src/`.

## The double-up list

Seven call sites render `GalacticAtmosphere` today. Each is a place where
`PulseBackground` would stack a second animated space layer on the first.

| Call site | Line | Variant |
| --- | --- | --- |
| `src/screens/HomeScreen.tsx` | 576 | `feed`, full-screen under the feed |
| `src/screens/HomeScreen.tsx` | 1008 | `feed`, scoped inside the Pulse Network hero panel |
| `src/screens/ReelsScreen.tsx` | 996 | `feed`, via `GalaxyField()` |
| `src/screens/ChatScreen.tsx` | 1067 | `messages` |
| `src/screens/ProfileScreen.tsx` | 367 | `profile`, driven by `scrollY` |
| `src/screens/GalacticConstructionScreen.tsx` | 75 | `business` |
| `src/components/auth/LoginBackground.tsx` | 5 | `feed`; used by `LoginScreen.tsx:312` and `SignupScreen.tsx:231` |

The HomeScreen hero at line 1008 is different in kind from the other six: it is
a *decorative panel* atmosphere, deliberately clipped inside a card
(`styles.heroAtmosphere`), not a screen background. Removing it would change the
hero's design, not just its background. It should survive the migration.

`ChatScreen`'s usage carries a comment at lines 1064-1066 explaining that the
atmosphere is intentionally rendered *after* the shell so its opaque gradient
covers the identity and call controls. That ordering is load-bearing; anything
that changes the paint order in that screen changes what is visible.

Note that `GalacticAtmosphere` itself paints an opaque gradient
(`src/components/GalacticAtmosphere.tsx:100-104`, `#02050A → #06101C`), so
wherever it survives it will hide `PulseBackground` regardless of what the
screen root does.

## Layering

**Stack modals.** Only two routes declare a presentation:
`ContentPreview` (`fullScreenModal`, `AppNavigator.tsx:306`) and `PulseShare`
(`modal`, line 310). Both are stack screens, so a root-level `PulseBackground`
sits behind them. `PulseShare` paints its own root
(`src/screens/PulseShareScreen.tsx:231`) — on iOS a `modal` presentation leaves
the previous card partially visible behind a card-style sheet, so making
`PulseShare` transparent would let the *underlying screen* show through the
sheet, not the atmosphere. Leave `PulseShare`'s root opaque.

`react-native-screens` only forces `backgroundColor: 'transparent'` for the
transparent presentations (`NativeStackView.js:116-118`), which neither of these
is.

**RN `Modal`s are a separate window and will not see a root background at all.**
Nineteen files use RN `Modal`: `ReelsScreen`, `StatusScreen`,
`GalacticConstructionScreen`, `ChatScreen`, `MarketplaceScreen`,
`MarketplaceManagerScreen`, `ConversationControlCenter`,
`MasterNavigationDrawer`, `BusinessProfileParts`, `WelcomeUfoOverlay`,
`NativeMediaViewer`, `StatusCreator`, `ContentTranslation`,
`EngineerAccessModal`, `FeedComposer`, `PreLiveConfigurationSheet`,
`liveHostUi`. Each keeps whatever background it has today. If any of them should
show the atmosphere, `PulseBackground` has to be rendered inside that modal
explicitly — there is no inheritance path.

**Overlays that sit above the navigator.** `MasterNavigationDrawer` is rendered
as a sibling of `Stack.Navigator` (`AppNavigator.tsx:513`) and is a `Modal`.
`InAppNotificationBanner` (`App.tsx:300`) is absolutely positioned
(`src/components/InAppNotificationBanner.tsx:176`) with a `glassStrong` fill at
line 184 — it will read correctly over a new background.
`IncomingCallLayer` (`App.tsx:301`) is opaque `#06090f` and is on the exclusion
list.

**Global navigation chrome is already translucent and will benefit.** The header
and dock in `src/navigation/GlobalNavigation.tsx` use semi-transparent dark
fills — `rgba(3, 10, 21, 0.88)` at line 618, `rgba(3, 9, 18, 0.96)` at line 655,
`rgba(8, 16, 29, 0.94)` at line 769 — over a `#030712` deep-space base at line
662. These will composite over the new layer without change, which is the
strongest argument that the design intent was always a shared background.

## Startup and transition risk

**The iOS splash is white or black depending on the device's appearance
setting, and PulseSoc has no say in it.** `app.json` declares no `splash` key
and `expo-splash-screen` is not a dependency. The generated storyboard
`ios/PulseSocNative/SplashScreen.storyboard:17` uses
`<color key="backgroundColor" systemColor="systemBackgroundColor"/>`, a dynamic
system colour: white in light appearance, black in dark. `app.json:9` sets
`"userInterfaceStyle": "automatic"`, so a user with the phone in light mode gets
a white splash regardless of their PulseSoc theme. There is also an unused
`SplashScreenBackground.colorset` at
`ios/PulseSocNative/Images.xcassets/SplashScreenBackground.colorset/Contents.json`
holding pure white (rgb 1,1,1) — the storyboard does not reference it.

This is a pre-existing defect, not one the background work creates, but the
background work makes it more visible: a white splash handing off to a deep-space
layer is a harsher cut than a white splash handing off to `#050910`. Worth
fixing in the same pass by adding an explicit `splash.backgroundColor`.

**The first React frame is `colors.background` = `#050910`**
(`src/theme/colors.ts:2`), painted by `App.tsx:247` during i18n/auth bootstrap.
That is the correct colour to keep — it is the base of the intended navy-black
palette.

**Transitions use the navigation theme's background**, `theme.colors.background`
at `App.tsx:338`. Per theme that resolves to `#050910` (dark),
`#000000` (black, `ThemeContext.tsx:41`), `#f6f8fb` (light futuristic, line 55)
or `#ffffff` (white, line 84). On the two light themes a stack push will briefly
show a light colour behind the cards. If the design intent is that the space
layer is universal, that value has to change — but changing it also changes
header and card chrome, so it is a decision, not a cleanup.

**Unverified:** whether the iOS root view or `RNSScreen`'s native view has an
opaque default background once every JS-level fill is transparent.
`ios/PulseSocNative/AppDelegate.swift` sets no root view background colour, and
`react-native-screens` only forces transparency for transparent presentations.
This can only be settled on a device. It is the one item on this list that could
make a correct-looking integration render as a plain white or black app.

## Test blast radius

180 test files exist under `src/`. The following are affected.

**Source-scanning navigator tests.** These read `AppNavigator.tsx` as text, so a
`screenOptions` change is in scope by construction. All three were checked
against the specific edits proposed above:

- `src/navigation/__tests__/businessOsRoutes.test.ts` — extracts
  `<Stack.Screen name="...">` route names (line 18) and pins per-screen
  `headerShown` rules (lines 76-87), reading each registration from `name="X"`
  to the next `/>` (lines 91-96). It does not look at `screenOptions`,
  `contentStyle` or `sceneContainerStyle`. **Changing `contentStyle` at line 272
  or adding `sceneContainerStyle` at line 145 will not trip it.** It would trip
  on any change to a `headerShown` value or the removal of a route.
- `src/navigation/__tests__/navigatorLocalization.test.ts` — pins
  `TITLE_OPTIONS.length` to exactly 117 (line 37) via a regex on `title:`
  (line 24). Safe for the proposed edits; it will fail on any change that adds
  or removes a `title:` option, which the background work does not do.
- `src/navigation/__tests__/bottomNavCoverage.test.ts` and
  `src/navigation/__tests__/badgeSources.test.ts` — read screen sources for
  scroll-clearance wiring and badge imports respectively. Neither inspects
  backgrounds. `src/settings/__tests__/registry.test.ts` reads the navigator for
  route coverage; also unaffected.

**Tests that render a shell and would newly mount whatever `PulseBackground`
imports.** Five render shell-using screens without mocking
`src/components/Screen.tsx`:

- `src/screens/__tests__/PostDetailScreen.actions.test.tsx`
- `src/screens/__tests__/PostDetailScreen.comments.test.tsx`
- `src/screens/__tests__/SellerStoreScreen.mode.test.tsx`
- `src/screens/__tests__/SellerApplicationScreen.test.tsx`
- `src/screens/__tests__/BusinessOsScreen.test.tsx`

One mocks the shell out and would be unaffected:
`src/screens/__tests__/ProfileScreen.actions.test.tsx:24`.

`jest.setup.js` mocks only `@react-native-async-storage/async-storage`. There is
no global mock for `expo-battery` or `expo-linear-gradient` — every test that
needs them mocks them per file (`expo-battery`:
`src/components/__tests__/GalacticAtmosphere.test.tsx:4-8`,
`src/screens/__tests__/HomeScreen.actions.test.tsx`; `expo-linear-gradient`:
those two plus `StoreDashboardScreen.test.tsx`,
`MarketplaceManagerScreen.test.tsx`, `BusinessProfileScreen.test.tsx`,
`ProfileHeader.test.tsx`). **If `PulseBackground` reaches for `expo-battery`,
`expo-linear-gradient`, `react-native-svg` or `react-native-reanimated`, those
five tests will fail at mount for reasons unrelated to what they assert.** The
cheapest fix is a global mock in `jest.setup.js` rather than five per-file
mocks — and if `PulseBackground` is mounted at the app root rather than in the
shells, the problem does not arise at all. That is a further argument for
insertion point 6.

**Tests that assert on the existing atmosphere.**
`src/components/__tests__/GalacticAtmosphere.test.tsx` pins the star colour
`#CDEBFA` and star size ≤ 2px (lines 27-33). `src/theme/__tests__/ThemeContext.test.ts:91-107`
pins the `galacticBackground` profile per theme: White returns
`{ enabled: false, intensity: 0, variant: "light" }`, Black's intensity is
strictly less than Dark's, and System inherits. **If `PulseBackground` reads the
same `galacticBackground` profile — and it should, otherwise the White theme
gains an atmosphere it was deliberately denied — these are the tests that pin
the contract.** They do not need changing; they need honouring.

## Risks

**The tab scene `Background` is the one that will be missed.** Fifteen tab
screens sitting on an opaque view that exists only in `node_modules`. It cannot
be found by grepping the app, it will not show up in a unit test, and the
symptom — "the background works on detail screens but not on Home" — points
investigators at the screens rather than at the navigator.

**The `expo-av` allowlist and the realtime-audio gate.** Per
`docs/realtime_audio_change_policy.md`, protected paths must not be edited by a
mission that is not about audio. Several files on the exclusion list are
plausibly protected — `CallScreen`, `LiveHostSessionScreen`, `LiveStudioScreen`,
`ReelsScreen`, `CameraStudioScreen`. Cross-check
`config/realtime-audio-protected-paths.json` before touching any of them. This
is one more reason the exclusion list is worth respecting: those screens should
not be edited at all by this work.

**Making a `center` state fill transparent is a legibility change, not a cosmetic
one.** The 31 `center` fills back loading spinners and error copy. Over a moving
starfield, low-contrast muted text (`colors.muted`) on an animated gradient can
drop below 4.5:1. These should be converted deliberately and checked, not
swept.

**Light themes.** `galacticProfileFor` (`src/theme/ThemeContext.tsx:197-205`)
returns `enabled: false` for White and a light-variant profile at 0.35 intensity
for Light Futuristic. A "deep-space" layer honouring that contract renders
nothing on White — so on White, every screen root made transparent falls through
to whatever is beneath, which after these edits is the navigation theme's
`#ffffff`. That works, but only because the navigation theme was left alone.
Making the navigation theme transparent as well would break White specifically,
and White is the theme least likely to be in the QA rotation.

**`ChatScreen`'s paint order.** The comment at
`src/screens/ChatScreen.tsx:1064-1066` records that the atmosphere is placed
after the header on purpose so its opaque gradient covers the identity and call
controls. Anything that removes the atmosphere from that screen without
replacing that coverage changes what the user sees at the top of every
conversation, and the change will look like a layout bug rather than a
background one.

**The unresolved native question.** Whether iOS shows white once every JS fill
is transparent is unverified above and cannot be settled by reading. It should
be the first thing checked on a device, before any of the 84 fills are
converted — a five-minute check that either confirms the whole plan or changes
it entirely.
