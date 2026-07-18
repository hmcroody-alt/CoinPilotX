# PulseSoc LogiNexus Home Visible QA

Status: representative visible QA completed; iPhone Simulator visual proof completed for the Home hero/navigation pass; full Homefeed LogiNexus QA remains pending.

## Current Pass Note

The latest inspiration-alignment changes were verified in the built-in QA browser and then on the Xcode iPhone 17 Pro Simulator. Homefeed can move from visual-system milestone to interaction-polished milestone, but it is not final LogiNexus polish-complete.

## Environment

- Built-in QA browser: used visibly.
- Native web QA: `http://127.0.0.1:8094`.
- Local backend: `http://127.0.0.1:5107`.
- Local QA proxy: `http://127.0.0.1:5108`.
- Authentication: disposable local-only QA account created through the existing mobile auth/register route. Credentials were not committed or written into reports.
- Xcode Simulator: iPhone 17 Pro, UDID `7B3BEEBC-6135-497D-91CD-A3E70C927D56`.
- Native bundle: installed/launched as `com.pulsesoc.nativeapp`.
- Simulator API base: local QA proxy `http://127.0.0.1:5108`.

## What Roody Could Watch

- Login through the visible native Login screen.
- Authenticated Home opened at `/pulse`.
- Pulse Network hero rendered with UNDX, Pulse Radio, and Safety Shield tiles.
- Your Orbit rail rendered.
- Transmission Console rendered.
- Feed filter rail rendered.
- UNDX tile opened `/pulse/ai`.
- Pulse Radio tile opened the native dashboard module shell for Pulse Radio.
- Safety Shield tile opened the native Safety Hub.
- Home scroll remained responsive after the transformed hierarchy rendered.
- Native iPhone Simulator showed the PulseSoc / LOGINEXUS command strip, Pulse Network hero, UNDX / Pulse Radio / Safety Shield tiles, and shared bottom dock.

## Runtime Notes

- No blank screen was observed during the representative Home walkthrough.
- No route loop was observed for the new hero tiles.
- Console warnings observed were non-blocking web/runtime warnings: Expo notifications web support, deprecated shadow props, Expo AV deprecation, and browser Badging API availability.
- The `pointerEvents` web warning introduced by this pass was fixed by moving it into style.
- Native simulator initially exposed an iPhone-width overlap in the Pulse Network hero. The hero now switches to a compact stacked layout under 430px wide, keeping metrics, orbit graphic, and action tiles readable.
- The remaining native dev overlay warning is caused by existing app-wide `expo-av` deprecation usage. It is not a Home crash and should be handled as a later media dependency migration rather than hidden in this pass.

## Simulator Evidence

- Native Home after compact hero fix, with existing dev-build Expo AV warning overlay visible: `reports/screenshots/logi-nexus-home-iphone17pro-native-home-with-dev-warning.png`
- Earlier invalid Safari-localhost screenshot was not used as native proof.

## Required Next Visible QA

- Open authenticated Home in the built-in QA browser.
- Verify the global command strip remains usable.
- Open the master drawer and return Home.
- Open UNDX, Pulse Radio, and Safety Shield from the hero tiles.
- Scroll Your Orbit and open a status.
- Use Transmission Console mode switches and media actions.
- Type a draft, reload, confirm recovery, and publish where safe.
- Switch feed filters and verify selected state.
- Open media viewer, profile, post detail, and safety actions from a feed card.
- Confirm no console/runtime errors.

## Current Confidence

- Static/type verification: passed for the code changes made before this report.
- Representative visible browser confidence: passed.
- Full visible browser confidence: representative pass complete; full interaction-polish pass pending.
- iPhone simulator visual confidence: passed for top command strip, hero layout, and shared bottom dock.
- Physical-device confidence: release QA only.
