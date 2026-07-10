# PulseSoc LogiNexus Home Evolution

Status: scoped Home visual evolution milestone complete; final release polish and device QA remain separate.

## Objective

Evolve the current PulseSoc Home layout into a stronger LogiNexus native experience without replacing the architecture users already understand.

Production layout changes: none.

The preserved order is:

1. Global Header
2. Pulse Network Hero
3. Status Rail
4. Pulse Composer
5. Feed Categories
6. Feed
7. Mobile Bottom Navigation

Wide surfaces preserve the production structure:

1. Left Command Rail
2. Center Feed
3. Right Intelligence Rail

## Completed

- Added a lightweight native atmosphere layer behind Home: nebula fields, signal waves, and star points using static, low-cost React Native views.
- Added a wide-only native left command rail to match the production WebView desktop layout without affecting iPhone ordering.
- Preserved the existing center Home flow and right intelligence rail.
- Expanded the responsive canvas so wide QA/browser layouts can hold left rail, feed column, and right rail without squeezing the feed.
- Kept the Pulse Network hero, Status rail, Composer, feed filters, feed cards, and bottom navigation in their current positions.
- Preserved server-authoritative Home behavior: feed loading, cursor pagination, pull refresh, publish, draft recovery, upload queue, event invalidation, status routing, and feed card mutations.
- Added route-safe command rail entries using the existing native route dispatcher and dashboard route fallback.
- Kept Pulse Radio controls honest: native controls route to the existing Pulse Radio surface and do not fake unavailable playback state.
- Tightened the iPhone Pulse Network hero by using a compact metric row on small screens while preserving the richer telemetry map for wider canvases.
- Tightened the existing `HomePulseComposer` implementation rather than creating a duplicate composer, reducing first-viewport height while preserving publish, draft, media, and retry behavior.
- Added a dynamic Expo config bridge so QA/dev-client bundles can point at a local API through `EXPO_PUBLIC_PULSE_API_BASE_URL` while production defaults remain unchanged.
- Hardened the dev-only simulator QA login parser with a local `api_base` override for server-authoritative local authentication testing.

## Not Changed

- No backend routes changed.
- No production WebView paths changed.
- No Home workflow was removed.
- No feed ranking, recommendation, publishing, auth, notification, moderation, or media contracts were rewritten.
- No Android-specific work was started.

## Simulator QA

Primary QA target: Xcode iPhone Simulator.

Required follow-up simulator captures:

- iPhone 17 Pro
- iPhone 17 Pro Max
- compact iPhone

Evidence from this pass:

- iPhone 17 Pro simulator rebuilt from Metro with the local QA bundle.
- Fresh authenticated simulator Home capture was blocked: the dev-client accepted the bundle and opened the app, but the QA auth deep link/manual coordinate automation did not populate the Login form. This is recorded as a QA runtime/login automation issue, not a Home layout regression.
- Older simulator Home captures were used as directional evidence during iteration, but this commit does not stage them as final proof because the fresh authenticated capture path remained blocked.

Current expected iPhone behavior:

- Mobile Home remains the familiar vertical sequence.
- Wide-only left and right rails do not appear on iPhone widths.
- Bottom navigation remains the mobile primary navigation surface.

## Remaining

- Final motion polish for ambient hero/network animation after foundation coverage is complete.
- Reliable authenticated Xcode Simulator login automation so future Home visual QA can be captured from a fresh session without manual typing.
- Physical iPhone release QA for haptics, camera/media capture, push tap routing, background recovery, and performance feel.
- System-wide LogiNexus polish after the rest of the native foundation reaches parity.
