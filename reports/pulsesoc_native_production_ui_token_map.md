# PulseSoc Production UI Token Map

Date: 2026-07-11

## Production CSS Sources

- `static/css/pulse_home_os.css`
- `static/css/pulse_desktop_feed.css`

## Native Token Targets

- `mobile-native/src/theme/colors.ts`
- `mobile-native/src/theme/logiNexus.ts`

## Token Mapping

| Production value | Native token | Usage |
| --- | --- | --- |
| `--home-bg: #02050a` | `logiNexus.colors.home.backgroundDeepSpace` / `colors.background` | Home background atmosphere |
| `--home-panel: rgba(7, 13, 23, .88)` | `logiNexus.colors.home.surfaceGlass` | Home cards, rails, panels |
| `--home-panel-strong: rgba(5, 10, 18, .96)` | `logiNexus.colors.home.surfaceGlassStrong` | Strong elevated surfaces |
| `--home-line: rgba(157, 193, 210, .14)` | `logiNexus.colors.home.borderSubtle` | Subtle card borders |
| `--home-line-cyan: rgba(83, 224, 245, .24)` | `colors.accentStrong` plus alpha usage | Active borders and focus accents |
| `--home-emerald: #31e6a1` | `colors.accent` | Primary PulseSoc action color |
| `--home-cyan: #53e0f5` | `colors.accentStrong` | Secondary signal color |
| `--home-purple: #9b66ff` | `colors.intelligence` | UNDX/intelligence accents |
| `--home-red: #ff4e68` | `colors.danger` | Live/danger accents |
| Hero `border-radius: 18px` | `logiNexus.radius.panel` / `large` | Hero and panel radii |
| Status rail `height: 174px` desktop | native adaptive status cards | Web desktop cards translate to compact native rail cards on iPhone |
| Composer `border-radius` and glass | `HomePulseComposer.styles.wrap` | Existing composer evolved in place |
| Feed cards `post-card-modern` glass | `PostCard.styles.card` | Native feed card surface |
| Mobile dock blur/glow | `GlobalNavigation.bottomPanel` | Native bottom tab dock |

## Required Deviation Notes

- Native uses React Native alpha strings and shadows instead of CSS `backdrop-filter`.
- Native iPhone layout follows safe-area, minimum touch target, and FlatList constraints, so exact desktop pixel dimensions are translated into device-class rules.
- Production hover states translate to native press states and accessibility states.
