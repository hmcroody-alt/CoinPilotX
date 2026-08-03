# ADR-0002 — One adaptive BusinessShell

Status: Accepted
Owner: Business OS mobile
Date: 2026-08-01
Amends: every section mission that specified its own header and theme

## Context

The deep review reports that Verification introduces a third visual shell. That
specific claim is false — `VerificationCenterScreen.tsx` imports `Panel` and
`colors`, the same dark primitives `BusinessOsScreen` uses, and is on the
standard shell. But the underlying observation is correct and the true number is
much worse than three.

`theme/` contains nine independent light theme modules: `paymentsLight`,
`storeLight`, `insightsLight`, `adsLight`, `messagesLight`, `ordersLight`,
`eventsLight`, `marketplaceLight` and `hubLight`. Each has its own component
family beneath it — nine components under `components/payments/`, twelve under
`components/ads/`, seven under `components/insights/`, and so on. Each family
independently decided its own type ramp, spacing scale, header shape, tap-target
minimum and state vocabulary.

This is not a fork to be merged. It is nine parallel design systems, and it is
the direct cause of two Tier 0 defects. `StoreQuickLinkTile` is the one
component that *was* shared, and the same tile truncates in Marketplace and
Advertising because those screens compose it into four-across rows while Store
composes it into explicit two-tile rows. Nine implementations of a header is why
Payments is the only screen that forgot to hide the stack header above its own.

Every screen that draws its own header is registered with `headerShown: false` —
`BusinessOsOrders`, `BusinessOsMessages`, `BusinessOsEvents`,
`BusinessOsActivity`, `BusinessOsInsights`, `MarketplaceManager`,
`BusinessProfile`, `SellerStore` in dashboard mode, `BusinessOsAdvertising` in
manager mode. `BusinessOsPayments` is not. Nothing structural prevented that
mistake, because there was no shell to be right or wrong about.

## Decision

One `BusinessShell`. Appearance is a mode of the shell, not a property of a
screen.

The shell owns the header — back affordance, title, optional accessories, and
the safe-area handling above them. A screen supplies content and accessories; it
does not draw a header, and it does not decide whether a stack header exists
above it, because the shell and the navigator registration become one decision
made in one place.

The shell has two appearances, dark and light. The Business Hub keeps dark, per
the standing decision that the hub is not reopened by this review. Light is what
the section surfaces use. An appearance is selected by the shell, from a single
token set with a single type ramp and a single spacing scale; a screen may not
introduce a token.

The shell owns the layout primitives that proved fragile: the quick-link row,
the tile, and the equal-height grid. The tile's rules are hard and live in the
shell rather than in each caller — no mid-word breaks, the title never truncates
below full-word visibility, explicit minimum and maximum line counts,
equal-height rows by construction, and a disabled state that is visually
distinct without relying on grey truncated text. Four-across rows are not
expressible; the row primitive takes two.

The shell's layout invariants are asserted by automated render tests at large
`fontScale`. There is currently no Dynamic Type guard anywhere in the codebase —
no `useWindowDimensions`, no `PixelRatio.getFontScale`, no `allowFontScaling`
policy, no `maxFontSizeMultiplier`, no `adjustsFontSizeToFit`, and every font
size hard-coded. The shell is where that changes, and the tests are the gate.

## Consequences

This is the largest item in the v2 adoption and it cannot land in one change.
The staging is: build the shell and its tile and row primitives with their tests;
migrate Payments first, because it is a Tier 0 blocker and the smallest complete
case; migrate Marketplace and Advertising next, because they carry the Tier 0.1
truncation; then the remaining surfaces as each is touched for other reasons.

Nine token sets collapse into one, which means eight of the nine screens will
absorb visible change. That is the point — they are inconsistent today and users
notice — but it should be sequenced deliberately and captured in before-and-after
screenshots rather than arriving as a surprise.

The `headerShown` decision leaves the navigator and moves into the shell, which
removes an entire class of double-header defect rather than fixing the one
instance of it.

The nine motion modules (`adsMotion`, `paymentsMotion`, `storeMotion`,
`insightsMotion`, `marketplaceMotion`, `businessLiveMotion`, `logiNexusMotion`)
are in scope for the same consolidation but are sequenced after the static
tokens, because motion inconsistency is less costly than layout inconsistency
and merging it early would enlarge an already large change.

## Open question

Which of the nine existing light themes is the reference for the shell's light
appearance. Consolidating requires picking a winner, and the choice determines
how much visual change each of the other eight absorbs. `storeLight` is the
strongest candidate on the evidence — it is the one whose composition pattern
was correct, it is already shared across two screens (Store and Marketplace),
and `components/hub/index.ts` already re-exports from it — but this is a design
call, not an engineering one.
