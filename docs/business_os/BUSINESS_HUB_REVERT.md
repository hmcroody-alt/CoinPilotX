# Business Hub revert — post-mortem and device-proof checklist

The light Business Hub redesign was reverted on the seller's front door. The
dark `BusinessOsScreen` is once again what every deep link, push notification
and tab that names `BusinessOs` renders. This document records why, what
changed, what deliberately did not, and what has to be true before anyone turns
the redesign back on.

## What was reverted, and what "reverted" means here

This is a presentation-layer revert. The redesign's data wiring was not undone,
because the wiring was not the thing that failed. `api/businessHub.ts`,
`core/hubBindings.ts` and `components/hub/` are intact and still under test;
`screens/BusinessHubScreen.tsx` still compiles and still renders correctly when
reached. The only change is which screen the route hands back.

The original screen did not have to be re-implemented from a screenshot. The
mission's preferred path — restore the presentation layer from version control —
was available in the strongest possible form: `BusinessOsScreen.tsx` was never
edited by the redesign. Its last touching commit is `266e400c`, which predates
the hub work. Three details were checked against the original design before
accepting it as pixel-faithful rather than merely plausible:

The header is not the screen's. `BusinessOsScreen` renders only a title and a
subtitle through the shared `Screen` component; the back chevron, search, chat,
bell, avatar and the two chips are `LogiNexusGlobalHeader`, supplied by the
stack's `screenOptions`. The "Native PulseSoc route" sub-line comes from
`subtitleForStack`, where `BusinessOs` falls through to the default. Restoring
the stack header therefore restores the entire original chrome, including the
business-identity block's absence — that block belonged to the redesign's own
navy header and disappears with it.

The card count is eleven, not ten. The sections registry marks `events` as
backed with its route supplied by `EVENTS_CARD_CONFIG.route` rather than a
string literal, which a naive scan misses. The eleventh card is `settings`.

The eleventh card is full-width by geometry, not by a special case. The tile
style is `flexBasis: "47%"` with `flexGrow: 1`, so a lone card on the final row
stretches across it. That matches the original exactly, and it matches it
without any code that knows about "the last card".

## The failure

The wiring succeeded. Live states, error retry and real zeros all rendered from
the correct sources; no card showed a fabricated number, and no card showed a
stale one. The failure was typography and layout robustness under real device
conditions — specifically, at large font scales on a narrow device.

Four defects were observed:

Card titles truncated. "Business profile" rendered as "Busine…" and "Payments"
as "Payme…". The title had a fixed-width column to live in and no room to
reflow, so it was clipped rather than wrapped.

State lines wrapped mid-word. "complete" broke as "complet e" and "campaign" as
"campai gn". The state line's container was narrow enough that a single long
word exceeded it, and the text was allowed to break anywhere rather than being
allowed to shrink, ellipsise, or take the full card width.

Card heights went uneven and the grid went cramped. Because each card's height
was driven by how many lines its own state line happened to need, rows staggered
and the two-column grid lost its rhythm. The original design's static two-line
subtitle gives every card the same height by construction; the redesign traded
that guarantee away for live text without replacing it with anything.

The state-line indent consumed the card. The LED dot plus its indented wrapped
text took most of the available width at scale, leaving the title and icon
squeezed into what remained.

The common cause is that the redesign's layout was validated at default font
scale on a wide device, where every string happened to fit. Nothing in the
design was robust to a string getting longer; it was only robust to the strings
that existed on the day it was built. Live text and fixed geometry were combined
without a rule for what gives when they conflict.

For the record: the correct conclusion is not that live per-card state is a bad
idea. It is that a card whose height and width depend on text the card does not
control needs a layout contract — a maximum line count, a shrink or ellipsis
policy, and a uniform card height — decided before the text is wired in.

## What changed

Four files, plus one new test file.

`mobile-native/src/api/businessOs.ts` gains `export const HUB_LIVE_CARDS =
false` with a comment explaining the revert and pointing here.

`mobile-native/src/screens/BusinessHubRoute.tsx` now returns `BusinessOsScreen`
unless both `HUB_LIVE_CARDS` is true and the caller passed `mode: "hub"`. The
import of the light screen is a deferred `require` inside that branch rather
than a module-scope `import`, so while the flag is false the light theme, the
binding store and every hub component stay out of the startup graph entirely.

`mobile-native/src/navigation/AppNavigator.tsx` restores the `BusinessOs` stack
screen to its original single-line form with an unconditional stack header. The
redesign had made `headerShown` conditional on `mode` because it drew its own
header; the dark screen depends on the stack header for its title, sub-line and
back chevron.

`mobile-native/src/navigation/types.ts` keeps the `mode?: "hub" | "classic"`
param and documents it as inert. The param is retained rather than dropped
because removing it would be a breaking change to a route that push payloads
already target, and because it is the opt-in half of the switch if the redesign
is revisited. `"classic"` is now a synonym for the default.

`mobile-native/src/screens/__tests__/BusinessHubRoute.test.tsx` is new.

## Flag rather than deletion, and why

The mission allowed either, preferring clean removal where the subscriptions
were isolated and the flag where removal would ripple. The redesign's
subscriptions were isolated — per-card, exactly as designed — so removal was
genuinely available. The flag was chosen anyway, on the mission's own framing:
this is a visuals-only revert that keeps the wiring. Deleting the screen would
delete the wiring's only consumer and make "keep the wiring" a claim about dead
code rather than about reachable code.

The flag's one real cost is that the light components remain in the bundle. The
deferred `require` mitigates the part that matters: nothing behind the flag is
*evaluated* at startup, so the revert costs no launch time and no module-init
work. Bundle bytes remain; that is the price of undoability, and it is recorded
here rather than hidden.

## What was verified

`HUB_LIVE_CARDS` has exactly one non-test reader: `BusinessHubRoute.tsx`.
`BusinessHubScreen` has exactly one non-test importer: the deferred `require`
inside the flag branch. `components/hub/` is imported only by
`BusinessHubScreen`. There is no second path by which a light-theme hub
component can reach the screen.

No shared state source, section screen or deep link was altered. The four
changed files are the whole diff; `api/businessHub.ts`, `core/hubBindings.ts`,
`components/hub/`, `BusinessHubScreen.tsx`, `BusinessOsScreen.tsx` and every
section screen are byte-identical to before the revert.

All eleven cards resolve to registered destinations, enumerated through
`businessOsNavigationArgs`: `BusinessProfile`; `SellerStore` with `mode:
"dashboard"`; `MarketplaceManager`; `BusinessOsAdvertising`; `BusinessOsOrders`
with `perspective: "seller"`; `BusinessOsMessages`; `BusinessOsInsights`;
`BusinessOsPayments`; `BusinessOsEvents`; `VerificationCenter` with `track:
"business"`; and the `Settings` tab. `businessOsRoutes.test.ts` independently
asserts that every section's route is registered in the navigator.

The bell and its count are the global header's, not the screen's, and were not
touched: `LogiNexusGlobalHeader`'s `onOpenActivity` navigates to
`ActivityInbox`, and its badges come from the same source they always did. This
is the original behaviour — the redesign's own bell, which pointed at
`BusinessOsActivity`, went away with its header.

Back navigation is the stack's `goBack`, restored with the stack header.

No caller anywhere passes `mode: "hub"`. The only in-tree navigation to this
route, from `ProfileScreen`, passes `{ title: "Business OS" }`.

Test and build state: the new route suite passes 6/6; the full Jest suite passes
2,526 tests across 140 suites with zero failures; `tsc --noEmit` is clean;
`npm run i18n:validate` reports OK across 11 locales at 923/923 keys, with four
warnings that pre-date this work and concern plural forms in `ar`, `es`, `fr`
and `pt` rather than any hub key.

One thing the revert surfaced: collapsing the navigator's `BusinessOs` entry
back to its original single-line form was not cosmetic. `navigatorLocalization.
test.ts` counts `title:` options by reading the navigator's text, and the
multi-line shape the redesign had introduced did not match its pattern — the
count read 113 against an expected 114. The redesign had been passing only
because it added a second option (`headerShown`) that changed the line shape
while the assertion happened to be updated alongside it. Restoring the original
one-line form restored the count. Worth knowing that this test is sensitive to
formatting, not just to content.

## Device-proof checklist

Nothing below has been executed. The revert is proven by tests, types and source
inspection; it is not proven on a device, and this section exists so that the
gap is explicit rather than implied.

Side-by-side against the original screenshot at default font scale, on the
narrowest supported device. Confirm: near-black background, dark card fills,
green line icons, white titles, gray subtitles, nothing light-themed anywhere;
the stack header with back chevron, search, chat, bell, avatar and both chips
(PULSESOC, 99+ ALERTS); no business-identity block; no today strip; no live
banner; the "At a glance" panel above the Sections panel; eleven cards in
two columns with Settings alone and full-width on the last row; every card
showing icon, full untruncated title and a static two-line subtitle, with no
state line, badge, urgent variant or progress bar; no entrance stagger, LED
animation or ambient effect.

The same comparison at maximum font scale, on the same device. This is the
condition the redesign failed, so it is the condition that matters most — and
the original must be verified here too, not assumed. Confirm specifically that
no title truncates, no subtitle breaks mid-word, and the two columns stay even.

Tap all eleven cards and confirm each lands on the screen named above and that
back returns to the hub.

Confirm the bell opens the Activity inbox and that its count matches the badge
the header shows elsewhere.

Warm-launch TTI, measured, and compared against a build with the light hub
present, to confirm the deferred `require` did what it is claimed to do.

## Before turning `HUB_LIVE_CARDS` back on

The redesign is undoable by setting the flag true and passing `mode: "hub"` —
that path is covered by a test precisely so that it stays working. But the
defects are still in the code. A revisit is not a flag flip; it is the layout
contract described above, followed by proof at maximum font scale on a narrow
device *before* it ships. That proof was the missing step the first time.
