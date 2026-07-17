# PulseSoc Native Home Call Overlay Removal

Date: 2026-07-16

## Requirement

The native app must never display the bottom active-call popup showing caller name, `Voice in progress`, or `End`, even when a legitimate voice or video call is active.

## Implementation

- Removed the active-call mini-controller render branch from the existing `IncomingCallLayer`.
- Removed the popup copy, active-call mini Pressable, End mini-button, route-specific visibility policy, navigation route subscription, and mini-controller pulse animation.
- Preserved active-call polling through `getActiveCalls()`, QA call fixture behavior, incoming accept/decline handling, and canonical `Call` route navigation.
- The app no longer relies on route gating for this popup because the product requirement is global removal.

## App Behavior

- Home never mounts the active-call popup.
- Messages, Chat, Profile, Reels, Create, and other native routes never mount the active-call popup.
- The app does not reserve layout space for the active-call popup.
- The app does not render the popup invisibly.
- Bottom navigation remains visible and tappable.
- No bottom padding is reserved for the removed popup.
- The removed popup cannot intercept touches because its visual branch is not mounted.

## Preserved Call Functionality

- Dedicated Call screen remains canonical for managing active calls.
- Active call state continues to refresh through `getActiveCalls()`.
- Incoming call handling remains unchanged.
- Ending a call remains available through the canonical Call UI and existing call APIs.
- Audio behavior is unchanged because no media/session logic was modified.

## QA Classification

Simulator verified:

- Native Home still opens with no call popup and no bottom spacing reserved.
- The bottom navigation remains visible and unobstructed in the inspected simulator state.

Code-path verified:

- Active call state still stores in `floatingCall`.
- Dedicated Call route navigation remains intact through incoming-call accept flow.
- The rejected mini-controller copy, Pressable, End button, route policy, route subscription, and visual animation are absent from `IncomingCallLayer`.
- QA active-call deep links seed `floatingCall`, but this simulator showed the iOS "Open in PulseSoc Native?" confirmation sheet and macOS Assistive Access is disabled, so the active-call Home state was verified by code path and audit rather than a clean no-prompt screenshot.

Screenshot evidence:

- `/Users/hmcherie/Desktop/CoinPilotX/reports/screenshots/native-home-call-overlay-removal/native-launch.png`
- `/Users/hmcherie/Desktop/CoinPilotX/reports/screenshots/native-home-call-overlay-removal/home-clean-no-call.png`
- `/Users/hmcherie/Desktop/CoinPilotX/reports/screenshots/native-home-call-overlay-removal/home-active-call-app-scheme.png`

Physical-device-only:

- Real microphone behavior.
- Real speaker/Bluetooth routing.
- Lock-screen push behavior.
- Background call audio.
- App-killed incoming call behavior.

## Remaining Work

- Physical-device release QA must verify audio/session behavior because Simulator cannot prove hardware routing.
