# PulseSoc Native Home Exact Parity Inventory

Date: 2026-07-11

## Directive

Current production PulseSoc is the design authority. The native Home must preserve the production Homefeed structure and workflows while translating browser presentation into native components. This is not a redesign pass.

## Source Map

| Surface | Production source | Native source | Backend/API source | Parity decision |
| --- | --- | --- | --- | --- |
| Desktop header | `static/css/pulse_home_os.css`, `static/css/pulse_desktop_feed.css` | `mobile-native/src/navigation/GlobalNavigation.tsx`, `mobile-native/src/screens/HomeScreen.tsx` | native identity/badge state | Reuse shared native command strip and remove non-production Home subtitle. |
| Mobile header | `static/css/pulse_home_os.css` mobile dock/header rules | `LogiNexusGlobalHeader` in Home mode | badge/session state | Preserve hamburger, PulseSoc, search, activity, profile. |
| Hamburger drawer | production rail/drawer routes | `MasterNavigationDrawer` | native route registry | Reuse existing drawer, no Home-only fork. |
| Left command rail | `pulse_home_os.css` left navigation and radio panel | `HomeCommandRail` | route registry, Pulse Radio provider | Keep wide-only rail with production grouping: Today, nav, Pulse Radio. |
| Right intelligence rail | production side cards | `HomeWebSideRail` | feed/status data | Keep wide-only right rail and server-derived counts. |
| Pulse Network hero | `pulse-home-hero` CSS and production screenshots | `PulseNetworkHero` | `listFeed`, `listStatuses` | Preserve hero position, metrics, radio, UNDX, safety, live actions. |
| Pulse Radio | production radio card/dock | hero tile, command rail, mobile radio dock | Pulse Radio route/provider | Preserve route and provider boundary. |
| UNDX/Pulse AI | production intelligence card | hero tile/right rail | Pulse AI route | Native label remains UNDX where current native route uses it. |
| Safety module | production Safety Scan/Shield | hero tile/right rail | SafetyHub | Preserve Safety route and counts derived from real feed safety terms. |
| Status rail | production Add Status + empty card | `StatusRail` | `listStatuses` | Restored production-facing `Status` and `No Status yet. Create one.` copy. |
| Pulse Composer | production composer card | `HomePulseComposer` | `createPost`, media upload | Reuse existing component; added production title and full production mode rail visibility. |
| Composer modes | production Post/Reel/Live/Marketplace/Music/Poll/Question/More | `HomePulseComposer` | native publish/media/live routes | Post/Reel/Live mutate or route; Marketplace/Poll/Question/More are explicit backend/provider boundaries. |
| Composer tools | production Photo/Video/Music/Feeling/Location/Mention/Topic/Public | `HomePulseComposer` | media upload, visibility, route handoffs | Preserve all labels/order with existing upload/publish contracts. |
| Feed categories | production feed filter rail | `FEED_TABS` in `HomeScreen` | `listFeed({feed, tab})` | Production order preserved: For You through My Posts. |
| Feed cards | `post-card-modern`, `pulse-feed-post-v3` CSS | `PostCard` | feed actions APIs | Native card preserves author, media, counts, production action row, social context, inline comments, and overflow safety controls. |
| Inline comments | `pulse-comment-composer-v2`, `/api/pulse/posts/:id/comments` | `PostCard`, `HomeScreen`, `addPostComment` | comment API, event sync | Native Home now uses the existing server-authoritative comment endpoint and updates count/preview from the response. |
| Media | production media frame/viewer | `MediaStrip`, `NativeMediaViewer` | media URLs from feed payload | Native viewer handoff preserved. |
| Bottom navigation | production mobile dock | `LogiNexusBottomNavigation` | tab navigator | Preserve Home/Reels/Create/Messages/Profile order. |
| Loading/empty/error/offline | production state language | `LogiNexusEmptyState`, cached feed/status fallbacks | cache helpers | Native states remain server-authoritative and cached where available. |

## Key Differences Remaining

- Production desktop browser top navigation includes nav pills that are not part of the native tab shell on iPhone; wide native web/simulator uses command rail plus shared route state instead.
- Inline comment attachment/photo/voice tools are explicit native boundaries until production feed-card attachment contracts are exposed to native.
- Dedicated backend indexes for some right-rail intelligence cards are not exposed to native yet, so native derives lightweight counts from authoritative feed/status data.

## No Duplicate Implementations

- No `HomeV2`, `NewHome`, `LogiHome`, `HomeExperimental`, `Composer2`, or `FeedCardNew` was created.
- Existing `HomeScreen`, `HomePulseComposer`, `PostCard`, shared navigation, and drawer were evolved in place.
