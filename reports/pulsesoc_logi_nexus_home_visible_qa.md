# PulseSoc LogiNexus Home Visible QA

Status: representative visible QA completed; full Homefeed LogiNexus QA remains pending.

## Environment

- Built-in QA browser: used visibly.
- Native web QA: `http://127.0.0.1:8094`.
- Local backend: `http://127.0.0.1:5107`.
- Local QA proxy: `http://127.0.0.1:5108`.
- Authentication: disposable local-only QA account created through the existing mobile auth/register route. Credentials were not committed or written into reports.

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

## Runtime Notes

- No blank screen was observed during the representative Home walkthrough.
- No route loop was observed for the new hero tiles.
- Console warnings observed were non-blocking web/runtime warnings: Expo notifications web support, deprecated shadow props, Expo AV deprecation, and browser Badging API availability.
- The `pointerEvents` web warning introduced by this pass was fixed by moving it into style.

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
- Full visible browser confidence: pending.
- iPhone simulator visual confidence: pending.
- Physical-device confidence: release QA only.
