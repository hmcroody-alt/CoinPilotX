# PulseSoc Native Home Generated Concept Mapping

Date: 2026-07-18

## Scope

This pass maps the user-approved generated Home concept onto the existing native Home implementation without creating a second Home screen or changing backend behavior.

## Existing Implementations Reused

- `mobile-native/src/screens/HomeScreen.tsx`
- `mobile-native/src/components/HomePulseComposer.tsx`
- `mobile-native/src/navigation/GlobalNavigation.tsx`
- Existing Home feed API, Status API, composer publish/upload queue, Pulse Radio coordinator, UNDX/Safety/Live routes, drawer, bottom tab navigator, and event-sync invalidation.

## Concept Mapping

| Generated reference area | Native implementation | Result |
| --- | --- | --- |
| Compact command strip | `LogiNexusGlobalHeader` home mode | Rebalanced into the generated concept's centered brand rhythm, circular command buttons, avatar treatment, compact signal underline, and production drawer/search/activity/profile wiring. |
| Pulse Network hero | `PulseNetworkHero` | Rebuilt in place as a compact cinematic glass panel with static atmospheric planet, skyline, signal lines, Pulse Radio module, metrics, and three functional quick tiles. |
| Background atmosphere | `HomeScreen` static atmosphere views | Uses lightweight static React Native views only. No timer-driven background animation or moving image loop was added. |
| Status row | `StatusRail` | Tightened avatar/add-status proportions and spacing to match the concept while preserving Status API data and routes. |
| Create a signal composer | `HomePulseComposer` collapsed state | Uses one compact wired quick-action strip plus Create button. Photo/video/camera still call the existing picker/camera routes; Create expands the existing composer. |
| Feed filter rail | `HomeScreen` feed tabs | Positioned inside the first viewport above the dock while preserving feed selection keys, persistence, and API reload behavior. |
| Floating dock | `LogiNexusBottomNavigation` | Retains glass dock, active Home treatment, and centered Create affordance while preserving the existing tab navigator and route dispatch. |

## Wiring Preservation

- Feed loading still uses `listFeed`.
- Status rail still uses `listStatuses` and `loadCachedStatuses`.
- Composer publishing still uses `createPost`, `createReel`, upload queue, draft recovery, and retry logic.
- Pulse Radio still uses `getPulseRadioState`, `subscribePulseRadio`, and `togglePulseRadio`.
- Hero quick tiles still route to UNDX, Pulse Radio library, Live, and Safety through existing navigation.
- Bottom navigation still dispatches through the shared tab navigator.

## Performance Decision

The generated concept implies living/moving background imagery. This implementation uses static layered geometry and static signal lines to create motion-like depth without extra render loops, image animation, or per-frame JavaScript work. Runtime animation should be added later only where it is reduced-motion aware and profiler-safe.

## QA Status

- Code-path verified through source inspection.
- Xcode iPhone Simulator Release build/install/launch passed on `PulseSoc iPhone 16 Pro`.
- Authenticated real-account Home was opened through visible simulator navigation from the native bottom dock.
- Final clean simulator evidence: `reports/screenshots/native-home-generated-concept/iphone16pro-native-concept-final-home.png`.
- Earlier comparison evidence retained:
  - `reports/screenshots/native-home-generated-concept/iphone16pro-native-after-real-login.png`
  - `reports/screenshots/native-home-generated-concept/iphone16pro-native-concept-pass3.png`
  - `reports/screenshots/native-home-generated-concept/iphone16pro-native-concept-final-home-clean.png`
- Remaining visual substitutions are intentional: the background is static code-native atmosphere rather than animated bitmap imagery, and live production data may alter copy width and status density by account state.
