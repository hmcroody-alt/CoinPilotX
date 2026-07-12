# PulseSoc Native Home Responsive Matrix

Date: 2026-07-11

## Device Classes

| Class | Simulator | Status | Result |
| --- | --- | --- | --- |
| Compact iPhone | iPhone 17e available | Code verified, fresh capture pending | Composer modes and feed filters remain horizontally accessible; no controls removed. |
| Pro iPhone | iPhone 17 Pro booted | Verified by existing simulator evidence | Home layout, feed card, inline comment, and bottom navigation render in the native app. |
| Pro Max | iPhone 17 Pro Max available | Code verified, fresh capture pending | Max-width relationships preserved through central primary column and wider side-rail rules only on wide canvas. |
| Wide/browser | Production screenshots supplied | Code verified | Left command rail, center feed, and right intelligence rail structure preserved. |

## Final Responsive Decisions

- Compact mobile keeps every production composer mode and feed tab through horizontal access rather than hiding features.
- Feed-card density was reduced without dropping below semantic action availability.
- Bottom navigation retains content clearance through the shared safe-area-aware dock.
- Wide layout preserves production three-column order: left command rail, center feed, right intelligence rail.
- Right rail width increased to better match production while preserving central feed width through flex layout.

## State Coverage

- Populated Home: covered by simulator evidence and native code path.
- Empty feed/status: native state components retained.
- Loading/error/offline: native state handling retained; exact authenticated full-state proof requires healthy `127.0.0.1:5108`.
- Composer draft/upload/publish/failure: existing publishing contract remains unchanged.
