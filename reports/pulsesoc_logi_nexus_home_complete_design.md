# PulseSoc LogiNexus Homefeed Complete Design

Status: scoped visual-system milestone completed; this is not Homefeed LogiNexus-complete.

## Inspiration Alignment Pass

- Used the approved Home reference image as directional inspiration for hierarchy, density, and atmosphere.
- Tightened the command strip into a more centered PulseSoc / LOGINEXUS identity treatment.
- Rebalanced the Pulse Network hero around stronger live-network depth, signal lines, compact metric hierarchy, and stacked UNDX / Pulse Radio / Safety Shield action tiles.
- Reduced Status rail weight so `Your Orbit` reads as an orbital social signal rail instead of a card carousel.
- Compactified the Transmission Console into a first-viewport-friendly composer with identity orb, inline audience selector, horizontal action controls, and preserved draft/publish/upload behavior.
- Strengthened Signal Cards with creator pill treatment, verified marker continuity, deeper glass surface, and media framing.
- Advanced the floating bottom dock toward the approved center-create composition.

## iPhone Simulator Alignment Fix

- Ran the native app on the Xcode iPhone 17 Pro Simulator using `com.pulsesoc.nativeapp`.
- The simulator caught a narrow-device overlap in the Pulse Network hero that browser QA did not expose.
- Added a compact hero layout under 430px width so the network metric, live count, orbit graphic, UNDX, Pulse Radio, and Safety Shield controls remain readable.
- Tightened shared bottom navigation spacing and replaced the iOS-unfriendly Home activity glyph with a legible symbol.
- Cleaned the React Native web `pointerEvents` warning path by moving decorative pointer-event behavior into styles.

## Completed This Milestone

- Extended the shared `logiNexus` token system with Home-specific colors, typography, and depth tokens.
- Reworked the Pulse Network hero into the approved hierarchy: network metric, live count, UNDX tile, Pulse Radio tile, and Safety Shield tile.
- Preserved server-authoritative Home data: feed posts, status rail, live status count, safety/UNDX count, and refresh behavior.
- Transformed Status into a clearer `Your Orbit` rail with circular avatar rings, unseen state, online indicator, and empty-state language.
- Updated the Home composer presentation into the `Transmission Console` while preserving draft recovery, upload queue, validation, retry, and publish behavior.
- Updated Home feed cards toward the `Signal Card` direction with stronger identity hierarchy, media framing, verified marker treatment, and action clarity.

## Reused Existing Logic

- Feed API, feed cursor pagination, offline cache, and pull-to-refresh.
- Status rail API and cached status fallback.
- Existing native media upload, camera handoff, media preview, and `NativeMediaViewer`.
- Existing post create, reaction, save, repost, share, follow, hide, mute, report, post detail, and profile routing.
- Existing event sync invalidation for Home, Activity, and Notifications.
- Existing global header, bottom navigation, and master drawer route architecture.

## Not Changed

- No backend business logic was duplicated.
- No production WebView path was touched.
- No Android-specific work was started.
- No fake production data was introduced.
- No final motion, haptics, or full subsystem polish was claimed.

## Remaining Home LogiNexus Work

- Complete reduced-motion-aware ambient motion for network nodes and publish success.
- Continue bottom dock fidelity work across all primary tabs after shared navigation QA.
- Add richer real UNDX summary copy when the backend exposes Home-specific intelligence summaries.
- Run full visible QA and simulator QA after this inspiration-alignment pass.
- Run physical-device-only checks for haptics, push taps, camera/media capture, and background recovery.

## Native Reconstruction Update

- Reworked the hero again for iPhone simulator width after treating the generated concept as inspiration only.
- Removed fake concept-style values from native Home source; hero metrics now derive from feed posts and status data.
- Replaced the earlier oversized hero structure with a compact health pill, server-derived metric row, and three route-safe quick tiles.
- Preserved Home publishing, draft recovery, upload queue, feed invalidation, Status rail, feed interactions, and media handoff contracts.
- Xcode iPhone Simulator remains the primary QA surface for this Homefeed pass.
- Fixed a local Metro runtime resolver issue for Expo Notifications' `@ide/backoff` dependency so simulator QA can run against the native dev build instead of a stale redbox.
- Captured authenticated iPhone 17 Pro Simulator evidence after the resolver fix; the remaining visible overlay is the existing app-wide Expo AV deprecation warning toast from the dev client.
