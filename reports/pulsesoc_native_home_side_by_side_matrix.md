# PulseSoc Native Home Side-by-Side Matrix

Date: 2026-07-11

Production references:

- `/Users/hmcherie/Desktop/Screenshot 2026-07-10 at 12.21.16 PM.png`
- `/Users/hmcherie/Desktop/Screenshot 2026-07-10 at 12.21.36 PM.png`

Native evidence:

- `reports/screenshots/native-home-production-parity/native-home-feed-card-inline-comment-verified.png`
- `reports/screenshots/native-home-production-parity/native-home-qa5110-home-return.png`
- `reports/screenshots/native-home-production-parity/native-home-final-parity-nativeapp-iphone17pro.png`
- Existing current-bundle Home evidence under `reports/screenshots/native-home-production-parity/`

## Acceptance Classes

- A: exact match.
- B: acceptable native substitution.
- C: accessibility-required deviation.
- D: performance-required deviation.
- E: remaining mismatch.

| Section | Production reference | Native implementation | Decision | Notes |
| --- | --- | --- | --- | --- |
| Header | Hamburger, PulseSoc, search, activity, profile | Shared `LogiNexusGlobalHeader` in Home mode | B | Same control order and route intent. Native safe-area handling is an accessibility/device substitution. |
| Pulse Network hero | Compact dark card, network visual, metrics, Pulse Radio, Live, Safety | Existing `PulseNetworkHero` tightened in place | B | Final iPhone 17 Pro native capture confirms reduced hero title, metric blocks, glow, and spacing. Visualization remains native-built, not DOM/CSS copied. |
| Status rail | `Status`, Add Status, empty copy | Existing `StatusRail` | A | Production copy and section order retained. |
| Pulse Composer | `Pulse Composer`, production modes/actions, text area, counter, publish | Existing `HomePulseComposer` | B | Final capture confirms tighter composer proportions. All production modes retained. Native compact rail uses horizontal access instead of browser layout. |
| Feed filters | Horizontal production category rail | Existing `FEED_TABS` rendered in Home | A | Production order preserved. Final pass tightened rail height and pill spacing. |
| Feed card | Author, Follow, overflow, text/media, action row, social context | Existing `PostCard` | B | Production action order and inline comments retained. Native sizes tightened for iPhone density. |
| Inline comment | Avatar, placeholder, tools, submit | Existing `PostCard` inline comment composer | B | Server-authoritative `addPostComment` reused. Photo/emoji are boundary controls, not fake local mutations. |
| Left command rail | Desktop command/navigation/radio rail | Existing `HomeCommandRail` | B | Width and gaps adjusted closer to production; route order unchanged. |
| Right rail | Intelligence/trending/sponsored/live modules | Existing `HomeWebSideRail` | B | Width, spacing, and module cards adjusted closer to production. Data remains truthful; unsupported contracts are boundary states. |
| Bottom navigation | Home, Reels, Create, Messages, Profile | Shared `LogiNexusBottomNavigation` | B | Same order and primary route behavior; native dock safe-area treatment is device-specific. |
| Loading/empty/error | Production wording where available | Existing native states | B | Exact where implemented; proxy-backed authenticated full-state capture still blocked by `5108` health. |

## Remaining E-Class Items

None in code-level parity after this pass. Fresh authenticated side-by-side screenshots remain dependent on restoring a healthy local QA proxy/backend path.
