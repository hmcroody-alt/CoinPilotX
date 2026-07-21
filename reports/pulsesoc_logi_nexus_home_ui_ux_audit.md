# PulseSoc LogiNexus Home UI/UX Audit

## Inspiration Alignment Update

- Reference image direction applied as inspiration, not a pixel-copy target.
- Command strip now better matches the centered PulseSoc identity, compact action controls, and visible `LOGINEXUS` label.
- Hero hierarchy now better matches the approved concept: network metric, live broadcast metric, ambient signal lines, and right-side UNDX / Pulse Radio / Safety Shield stack where space allows.
- Status rail now uses lighter orbital treatment with circular status nodes and reduced card weight.
- Composer now better matches the compact transmission surface in the reference while keeping the same server-authoritative publish contract.
- Signal Cards now carry stronger creator identity and media framing.
- Floating bottom dock moved closer to the reference with a more prominent center Create action.

This remains a foundation visual pass. Final motion polish, full animation timing, cross-device visual tuning, and physical-device haptics are not complete.

## Xcode iPhone Simulator Review

- Used the iPhone 17 Pro Simulator to verify the native Home surface rather than relying on the web QA browser alone.
- The first native simulator Home capture showed the Pulse Network hero text overlapping the orbit graphic on iPhone width.
- The Home hero now uses a compact stacked layout for narrow devices, resolving the overlap while preserving the reference-inspired hierarchy.
- The shared bottom navigation was tightened so primary labels are less likely to clip on iPhone widths.
- A dev warning overlay remains in development builds because the app still imports `expo-av`; replacing that dependency is a separate media-platform task, not a Home visual fix.

Status: first scoped Homefeed transformation pass.

## Improved

- Home now presents the intended hierarchy: command strip, Pulse Network, Your Orbit, Transmission Console, signal filters, and signal stream.
- The Pulse Network hero is no longer a plain metric panel. It now includes data-backed UNDX, Pulse Radio, and Safety Shield destinations.
- Status rail uses orbit language, circular identity treatment, unseen state, and clearer empty/cached language.
- Composer language and visual hierarchy now match the Transmission Console direction while keeping familiar Post/Reel/Live and media labels.
- Feed cards now emphasize author identity, media prominence, trust/verification, and readable action groups.

## Still Needs Later Polish

- Actual ambient motion is intentionally minimal in this milestone.
- Feed cards still need a full nested-state pass for deleted/moderated/sensitive media variants.
- Author badges should expand once profile badge payloads are consistently available in feed responses.
- The global bottom navigation already works but still needs the final approved floating dock visual treatment.
- Large-text, reduced-motion, and VoiceOver review are documented but not fully device-proven here.

## Accessibility Notes

- Existing test IDs and accessibility labels for publish, composer, feed actions, media, and post detail routing remain in place.
- Hero tiles and feed actions retain button roles.
- Status avatar rings are visual indicators only; seen/unseen state remains available through adjacent text.

## Performance Notes

- No canvas or high-density particle layer was added.
- Home continues to use `FlatList`, existing pagination, cache fallback, and event invalidation.
- Visual effects are low-cost static layers for this milestone.
