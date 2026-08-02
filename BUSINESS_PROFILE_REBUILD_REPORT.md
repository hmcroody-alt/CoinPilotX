# Business Profile — live rebuild

Rebuild of the screen behind card #1 of the business dashboard's "Sections" grid.

## Files changed

| File | Change |
| --- | --- |
| `src/theme/logiNexus.ts` | Added the `colors.businessLive` palette, `radius.card: 18`, and motion tokens (`entrance`, `stagger`, `tickerCycle`, `borderShimmer`, `scanSweep`, `ringDraw`). Tokens extended, not bypassed — no hex is inlined anywhere in the new code. |
| `src/theme/businessLiveMotion.ts` | **New.** Four hooks on RN core `Animated`: `useBusinessLiveEntrance` (staggered fade/slide), `useBusinessLiveMarquee` (seamless ticker), `useBusinessLiveRing` (0→real % draw), `useBusinessLiveAmbient` (border sheen, scan stripe, badge ping). |
| `src/components/businessProfile/BusinessLiveParts.tsx` | **New.** All presentational parts — live badge, ticker, completeness ring, buyer preview card with perspective grid and rotating border, detail rows, connected rows, trust callout, footer actions. |
| `src/screens/BusinessProfileScreen.tsx` | **New.** The screen: cache-first load, draft state, save, navigation. |
| `src/api/businessOs.ts` | Section `profile` now routes to `BusinessProfile` (was `SellerStore mode="profile"`), `backed: true`. |
| `src/navigation/types.ts` | Added the `BusinessProfile` route. |
| `src/navigation/AppNavigator.tsx` | Registered the screen with `headerShown: false` (it draws its own header). |
| `src/screens/__tests__/BusinessProfileScreen.test.tsx` | **New.** 12 tests. |

No new dependencies. The animation runs on React Native's own `Animated` — `react-native-reanimated` is not in this project, and adding it for one screen was not worth the bundle. Everything animates transform/opacity on the native driver; the SVG ring's `strokeDashoffset` is the one documented JS-driven exception, because it has no native equivalent.

## Data: what is real, what is a placeholder

Real, wired to existing endpoints: business name, handle, category, contact, location, website, bio, profile completeness, next-step suggestion, follower count, verification status, listing and order counts, avatar, cover, verified badge, member-since year.

No API exists yet, so these render as explicit placeholders rather than plausible-looking defaults: opening hours, seller rating, on-time %, average reply time, profile views today, new-follower delta, store-clicks trend, next ship day, shipping info, and user-owned events. The ticker cells read "Not tracked yet"; the rating and on-time stats read "—". This is deliberate and it is pinned by a test — the registry that routes this screen says coverage "reflects verified live coverage, not aspiration", and a refactor that quietly filled these in would ship a lie about a seller.

## Deviations from the brief

**Two rows edit inline instead of navigating.** The brief said detail rows navigate to the existing edit screens, but then nothing on the screen could ever produce an unsaved edit and the Save footer could never enable — a permanently dead control, which the codebase's own principle forbids. Business name and Links now edit in place through the existing draft endpoint (the one that enforces the server-side writable-field whitelist). Every other row navigates as specified. When the application is locked for review, inline editing disappears and the row navigates instead.

**No blur on panels.** `expo-blur` is not installed and the brief said to flag new heavyweight dependencies rather than add them. Panels sit slightly more opaque than a true blur would need, which keeps text contrast honest where a panel overlaps a busy cover image.

**Opening hours is a non-tappable row.** It is in the spec but has no backing field at all. It renders present, explained, and disabled — a chevron leading nowhere is worse than an honest blank.

**`SellerStore mode="profile"` still resolves.** The old route was left working so existing deep links do not break. It fired no analytics events, so nothing was lost in the move.

## Verification

`tsc --noEmit` clean. `npm run i18n:validate` OK (923 keys × 11 locales). Full jest suite green: 113 suites, 1,900 tests, including the 12 new ones.

Reduce-motion is a first-class parameter of every motion hook, not an afterthought: under the OS setting no loop ever starts, entrance values are set directly to their final state, and the ring settles at its real percentage. Two tests cover the branch — content stays readable and the figures stay truthful when motion is off. Accessibility: the ring exposes its percentage as a `progressbar`, the ticker's moving copies are hidden from assistive tech with a single spoken summary on the container, and every row and button carries a label.

## Open questions

Screenshots and a screen recording could not be produced — this environment has no simulator, device, or build tooling. Everything else in the definition of done is met; the visual confirmation needs a run on your machine.

Beyond that: should the ten unbacked fields get endpoints, or should the rows be dropped? Opening hours and reply time in particular are the ones buyers ask about most, and right now the screen can only tell a seller that they are missing.
