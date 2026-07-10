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

## iPhone Size Balance Pass

- Treated the generated image as inspiration for density and hierarchy, not a fixed pixel target.
- Reduced the shared Home command strip and bottom dock proportions so they do not consume too much vertical space on iPhone.
- Converted the Pulse Network quick actions from dashboard-like tiles into compact mobile signal chips.
- Tightened Status rail cards, empty state, avatar rings, and spacing.
- Made the default Transmission Console compact by showing publish/status/camera-route controls only when there is a draft, media, error, text, retry state, or Live context.
- Verified on the Xcode iPhone 17 Pro Simulator that the first Home viewport now shows the command strip, hero, Status rail, compact composer, and the beginning of the feed section.

## Blueprint-Inspired Native Reconstruction Pass

- Used the provided blueprint as inspiration only for hierarchy, proportion, and atmosphere.
- Rebuilt the Pulse Network hero into a compact live overview: server-derived signal metric, live broadcast count, ambient network panel, route-safe UNDX, Pulse Radio, and Safety Shield chips.
- Kept Home server-authoritative: no fake concept metrics, no backend contract changes, no feed/ranking/publish logic changes.
- Converted the Transmission Console action controls into a horizontal native action rail so iPhone touch targets remain large without pushing the feed too far down the first viewport.
- Removed the extra feed-tab title/subtitle block so the signal filter rail behaves like a compact channel selector.
- Verified the route with `pulsesoc:///pulse` on the Xcode iPhone 17 Pro Simulator after a fresh dev-client Metro bundle.
- Final simulator evidence: `reports/screenshots/logi-nexus-home-iphone17pro-blueprint-final.png`.

## Space Efficiency Pass

- Further tightened the native Home surface after comparing the simulator render against the generated blueprint's efficient use of space.
- Reduced Home-mode command strip height, icon button size, and brand subtitle footprint while preserving safe-area behavior.
- Reduced Pulse Network hero padding, network row height, metric tile height, quick chip height, and ambient map decoration size.
- Reduced `Your Orbit` rail height, avatar rings, empty-state card, section spacing, and online indicators.
- Reduced the default Transmission Console footprint: smaller identity orb, tighter text field, compact mode selector, tighter action rail, and smaller counter.
- Reduced the feed filter rail padding so the first Signal Card appears sooner in the opening iPhone viewport.
- Preserved existing Home logic: publishing, draft recovery, feed invalidation, media upload handoff, status routing, feed filtering, and server-authoritative mutations were not changed.
- Xcode iPhone 17 Pro Simulator evidence: `reports/screenshots/logi-nexus-home-iphone17pro-space-efficient-clean.png`.
