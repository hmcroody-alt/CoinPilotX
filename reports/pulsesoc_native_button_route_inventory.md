# PulseSoc Native Button and Route Inventory

Date: 2026-07-09

## Inventory Method

Inventory was produced from static scans of:

- `mobile-native/src/navigation/AppNavigator.tsx`
- `mobile-native/src/navigation/types.ts`
- `mobile-native/src/screens/HomeScreen.tsx`
- `mobile-native/src/screens/SettingsScreen.tsx`
- `mobile-native/src/navigation/dashboardRouting.ts`
- `mobile-native/src/data/dashboardModules.ts`
- `mobile-native/src/components/HomePulseComposer.tsx`
- `mobile-native/src/components/PostCard.tsx`

## Counts

| Surface | Count | Status |
| --- | ---: | --- |
| Dashboard modules | 146 | Native shell/native/fallback classified |
| Dashboard quick actions | 12 | Native/shell/fallback classified |
| Bottom tabs | 15 | Native routed |
| Stack screens | 77 | Native routed |
| Home drawer actions | 32 | Native/fallback classified |
| Settings buttons | 26 | Native/fallback/provider classified |
| Home composer pressables | 9 | Native/server/fallback classified |
| Feed card pressables | 15 | Native/server/fallback classified |
| Total static action surfaces | 332 | Audited |

## Fixed This Mission

- Added `Create` tab to bottom navigation.
- Added Home top bar actions.
- Added Home hamburger drawer actions.
- Added explicit legal/support/provider actions in Settings.
- Added a reusable audit script for full wiring checks.

## Remaining Deferred Actions

- Final visual polish and animation polish.
- Android tooling and Android release QA.
- Real provider checkout, push, and LiveKit lock-screen behavior.
- Physical iPhone manual Home release evidence.

## Current Core Wiring Assessment

Core native wiring is foundation-complete for browser/simulator validation. Release confidence still depends on provider and device QA for hardware/provider-owned flows.
