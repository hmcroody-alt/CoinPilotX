# PulseSoc Native Global Navigation Visible QA

Status: completed for the global navigation foundation milestone.

## Intended Visible Walkthrough

Use the built-in QA browser, not Chrome Incognito.

Representative flow:

- Login.
- Open Home.
- Open the master navigation drawer.
- Navigate to Dashboard.
- Return Home.
- Use bottom navigation to open Reels.
- Use Create to reopen the Home composer.
- Open Messages.
- Open Profile.
- Open UNDX through the drawer or route.
- Open Activity / Notifications through the global header.
- Open Marketplace through the drawer.
- Verify back navigation.
- Verify active tab state.
- Verify unread badges where the local QA fixture exposes counts.
- Verify drawer identity header.

## Static QA Completed

- Typecheck passed after the shared navigation changes.
- Global navigation audit covers shared header, bottom navigation, badge wiring, drawer identity, route dispatch, and report artifacts.

## Visible QA Result

Passed in the built-in QA browser on `http://localhost:8094` using the local API/proxy stack.

Roody could visibly watch:

- Authenticated app shell open to Mission Control with the shared global command strip.
- Activity badge count visible in the global header.
- Profile initials visible in the global header.
- Master navigation drawer open from the global command strip.
- Drawer identity header render for `Global Navigation QA`.
- Drawer route back to Home.
- Bottom navigation open Reels.
- Bottom Create route reopen Home with `openComposer=true`.
- Bottom navigation open Messages.
- Bottom navigation open Profile.
- Header Activity action open `/pulse/notifications`.
- Drawer action open Marketplace.
- Drawer action open UNDX.
- Xcode iPhone 17 Pro Simulator reached authenticated Home through the repaired local QA bootstrap.
- Authenticated Home now uses the shared global command-strip state for profile identity and activity/message badges.

Simulator evidence:

- `reports/screenshots/logi-nexus-global-navigation-home-identity-badges.png`

Console/runtime:

- No browser console errors were captured during the visible walkthrough.

Observed limitation:

- React Navigation keeps inactive tab screens in the web DOM, so raw duplicate test-id counts can include preserved inactive headers. Visible-only locator checks resolved to a single active control.
- Physical iPhone badge clear behavior remains provider/device QA.
- The UNDX route global header uses `UNDX`, but the inner AI screen still contains legacy `Pulse AI` body copy. That belongs to the next Messenger/UNDX subsystem hardening pass, not this shared navigation foundation.

## Hardware / Provider Checks Not Claimed

- APNs push tap behavior.
- Background badge updates.
- Physical iPhone safe-area validation.
- Dynamic Island overlap validation.
- Hardware notification delivery.
