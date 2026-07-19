# PulseSoc Native Home Mockup Alignment

Date: 2026-07-18

## Scope

Redesigned the native PulseSoc Home surface using the supplied image as visual direction, not a literal clone.

Mockup used as inspiration only. The delivered UI keeps PulseSoc identity, preserves the existing native data flow, and does not expose internal LogiNexus naming as user-facing copy.

## Files changed

- `mobile-native/src/screens/HomeScreen.tsx`
- `mobile-native/src/components/HomePulseComposer.tsx`
- `mobile-native/src/navigation/GlobalNavigation.tsx`
- `scripts/pulsesoc_native_home_mockup_alignment_audit.py`
- `reports/pulsesoc_native_home_mockup_alignment.md`

## Design changes

- Expanded the Pulse Network hero into a larger cinematic card with stronger depth, skyline, network-orbit background, large “Curious” mood headline, health pill, and premium Pulse Radio control.
- Reworked empty Status rail behavior into circular “No status yet” placeholders so the rail matches the mockup rhythm without fabricating statuses.
- Tuned native Home spacing so Status, composer, feed tabs, and hero sections feel connected and edge-to-edge.
- Updated the collapsed composer to read as a clear “CREATE A SIGNAL” card with larger avatar, compact input, media tools, and create button.
- Tuned Home-only top header controls and shared bottom navigation sizing to better match the mockup’s premium native shell.

## Wiring boundary

No fake native route or fake playback was added.

- Pulse Radio still uses `togglePulseRadio()`.
- Status add/view/open paths still route through the existing Status screens.
- Composer media buttons still use the native media queue.
- Composer publish still uses `createPost` / `createReel`.
- Header, activity, search, profile, drawer, and bottom nav remain wired through the existing navigation.

## QA commands

Expected gates for this pass:

```bash
npm run --prefix mobile-native typecheck
venv/bin/python -m py_compile scripts/pulsesoc_native_home_mockup_alignment_audit.py
venv/bin/python scripts/pulsesoc_native_home_mockup_alignment_audit.py
git diff --check
```

## Known limitations

- This is a code/static QA pass. Real iPhone/Android simulator visual screenshots are still needed for final visual signoff.
- The mockup includes operating-system status bar details that remain controlled by the device OS, not this React Native screen.
