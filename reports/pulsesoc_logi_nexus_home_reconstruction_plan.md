# PulseSoc LogiNexus Home Reconstruction Plan

Status: implemented as a native Homefeed milestone.

## Scope

- Use the generated Homefeed image as inspiration only.
- Keep the current backend, session, feed, status, publishing, media, and event-sync contracts intact.
- Reconstruct the native Homefeed surface rather than creating a static mock or a second app.
- Use the Xcode iPhone Simulator as the primary QA target.

## Implementation Plan Executed

- Tighten the Home command strip through the shared native global header.
- Rebuild the Pulse Network hero into a compact, server-derived live state panel.
- Keep the Status rail native and route-safe while improving the orbital/social signal presentation.
- Preserve the existing `HomePulseComposer` publish, draft, upload, retry, and feed invalidation behavior while making the surface feel like a transmission console.
- Preserve native feed cards, post detail routing, profile routing, server-authoritative interactions, and `NativeMediaViewer` handoff.
- Keep bottom navigation shared and stable; no duplicate composer or feed logic was introduced.

## Boundaries

- No production WebView path was touched.
- No Android-specific work was started.
- No final animation/haptic polish was claimed.
- No fake concept metrics were introduced into native source.
- Physical iPhone release QA remains tracked separately.
