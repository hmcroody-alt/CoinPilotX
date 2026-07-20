# PulseSoc Native Status Futuristic Icon Actions

Date: 2026-07-19

## Executive summary

The native Status viewer now renders a compact, icon-only action rail on the right edge of the media. The former visible `React`, `Reply`, and `Share` text buttons were removed at their source in `StatusViewerCard`. The replacement rail reuses the production Status reaction, reply, and share handlers and does not modify backend or WebView code.

## Before state

`StatusViewerCard` mounted three 66-point text controls through its local `Action` helper. The reaction button posted `fire`; reply and share called the existing screen handlers. The action column sat above the viewer's left/right navigation press zones.

## After state

`StatusViewerCard` mounts one memo-friendly `StatusActionRail` with three 56-by-56 translucent controls:

- React: Ionicons `heart-outline`, changing to `heart` for a selected reaction, with the canonical count centered below it.
- Reply: Ionicons `chatbubble-ellipses-outline`.
- Share: Ionicons `paper-plane-outline`.

The rail is positioned from the window height and safe-area insets, remains above captions/home-indicator clearance, and uses only opacity, scale, and translation for feedback.

## Files changed

- `mobile-native/src/components/StatusActionRail.tsx`
- `mobile-native/src/components/StatusViewerCard.tsx`
- `mobile-native/src/screens/StatusScreen.tsx`
- `mobile-native/src/api/status.ts`
- `mobile-native/src/components/__tests__/StatusActionRail.test.tsx`
- `mobile-native/package.json`
- `mobile-native/package-lock.json`
- `scripts/pulsesoc_native_status_icon_actions_audit.py`
- `reports/pulsesoc_native_status_futuristic_icon_actions_2026-07-19.md`

No backend or WebView files changed.

## Icon mappings

| Action | Inactive | Active | Visible text |
| --- | --- | --- | --- |
| React | `heart-outline` | `heart` | count only |
| Reply | `chatbubble-ellipses-outline` | n/a | none |
| Share | `paper-plane-outline` | n/a | none |

## Gesture compatibility

The rail is mounted above the Status navigation zones and each control stops responder propagation. Reaction long-press records a guard before the release event, so it opens the tray without also submitting the default reaction. The tray is conditionally mounted in a transparent modal and dismissed on outside press. Existing previous/next, sound, double-tap Like, and hold-to-pause code remains in place. Double-tap now uses the same production-default `love` reaction as the heart control.

Automated coverage verifies that rail presses call only their action handler and stop event propagation. Full physical gesture-priority observation remains pending user interaction on the connected phone.

## Backend routes preserved

| Operation | Existing production route | Change |
| --- | --- | --- |
| React | `POST /api/pulse/status/:statusId/react` | handler reused |
| Reply | `POST /api/pulse/status/:statusId/reply` | handler reused |
| Share | `POST /api/pulse/status/:statusId/share` | handler reused |

Notification creation remains server-side. No native-only reaction route, notification request, or WebView behavior was added.

## Reaction behavior

- Default tap submits `love`.
- Long press opens the supported reaction tray (`like`, `love`, `fire`, `funny`, `wow`, `rocket`).
- The screen performs an optimistic count/selection update and rolls back on failure.
- Per-Status pending keys prevent rapid duplicate requests.
- Per-Status version counters prevent older responses from replacing newer local state.
- Selection triggers a short native-driver bloom and existing haptics; reduced-motion mode uses no scale sequence.

The current production reaction route replaces a reaction but exposes no verified removal contract. Accordingly, the active control announces selected state rather than claiming an unsupported remove action.

## Reply behavior

The existing reply modal is preserved. Tapping the reply icon passes the active Status to the existing screen handler, opens the composer, and the `TextInput` now requests focus with `autoFocus`.

## Share behavior

The paper-plane control calls the existing Status share handler, which records the canonical share and opens the native share sheet using the existing Status URL.

## Accessibility

- Controls expose button roles in React, Reply, Share order.
- Labels include `React to Status`, count, selected state, long-press options, `Reply to Status`, and `Share Status`.
- Reaction choices expose selected state and explicit reaction names.
- Minimum touch target is 56 by 56 points.
- State is not communicated by color alone: the heart changes from outline to filled and selected state is announced.
- Reduced motion bypasses bloom/press scale sequences.

## Performance

- No permanent animation loop.
- Press and reaction feedback use native-driver opacity/scale only.
- Tray is not mounted until requested.
- No new runtime UI or animation dependency was added.
- Reaction state is localized to the rail/status item; playback media and audio coordination code were not changed.
- Video playback remained active during the simulator visual observation; instrumented frame-time profiling was not performed in this mission.

## Tests

- Jest: 4 suites passed, 40 tests passed.
- Focused rail tests: labels absent; icons/count present; accessibility state present; tap callbacks isolated; long-press tray works; pending taps deduplicated.
- Repository Status icon-action audit: passed.
- TypeScript (`npx tsc --noEmit` through the package script): passed.
- Expo Doctor: 17/17 checks passed.
- `git diff --check`: recorded in final validation.

## Simulator QA

- Target: PulseSoc iPhone 16 Pro simulator.
- Xcode: 26.6 (17F113).
- Debug workspace build: passed after native codegen outputs were regenerated in the isolated worktree.
- Release workspace build with embedded bundle: passed.
- Install: passed.
- Launch: passed.
- Authenticated production-backed Status opened through the native deep link.
- Visual observation: visible `React`, `Reply`, and `Share` labels are absent; heart/count, message, and paper-plane controls are present; controls remain clear of the caption and bottom safe area.
- Evidence captured locally at `/tmp/pulsesoc-status-icon-rail-new-install-2.png`.

## Physical-device QA

The paired iPhone 16 Pro was detected as available. A signed Release-configuration development build with the embedded JavaScript bundle was compiled, installed, and launched successfully under the side-by-side identity `com.pulsesoc.nativeapp.dev` and display name `PulseSoc Native Dev`. The production App Store bundle identity was not targeted.

CoreDevice confirmed installation and process launch. No remote screen-capture or touch-control facility was available for the physical phone, so action-level interaction, VoiceOver, reduced-motion, and playback-jank scenarios remain `NOT OBSERVED`; simulator results are not substituted for physical PASS claims.

## Known limitations

- Reaction removal is not presented because the verified production endpoint supports replacement but has no removal contract.
- Full VoiceOver rotor testing, reduced-motion visual observation, and all 23 physical interaction scenarios require hands-on device operation.
- Simulator click-coordinate automation is not used as proof of gesture correctness; behavior-level tests provide the automated evidence.

## Rollback notes

Revert the feature commit to restore the local `Action` helper and former text rail. Backend, WebView, data contracts, and notification behavior require no rollback.
