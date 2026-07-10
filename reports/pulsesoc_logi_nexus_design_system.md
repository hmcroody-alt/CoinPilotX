# PulseSoc LogiNexus Design System

Status: phase 1 foundation started.

## Purpose

The native app is moving from feature parity into a unified PulseSoc experience. This design system is the shared foundation for the LogiNexus transformation while keeping the backend, routes, permissions, and business logic server-authoritative.

## Implemented Tokens

- Color tokens: space background, elevated surfaces, glass panels, active signal, success/accent, warning, danger, intelligence, creator, economy, safety, crypto, neutral text, muted text, border, focus, disabled.
- Typography tokens: display, title, section title, body, metadata, label, button, numeric metric.
- Spacing tokens: 4, 8, 12, 16, 20, 24, 32, 40, 48.
- Radius tokens: small, medium, large, panel, capsule, circular.
- Motion duration tokens: instant, quick, standard, reveal, ambient.
- Depth tokens: none, subtle, panel, floating, modal.

## Implemented Primitives

- `LogiNexusPanel`
- `LogiNexusCard`
- `LogiNexusBadge`
- `LogiNexusMetric`
- `LogiNexusButton`
- `LogiNexusEmptyState`
- `LogiNexusSignalIndicator`

## Current Use

Phase 1 applies these primitives to native Home:

- PulseSoc command strip top bar.
- Pulse Network hero.
- Transmission Console composer wrapper.
- Feed empty state.
- Feed card shell and audience badge.

## Boundaries

- No backend identifiers were renamed.
- No WebView production paths were changed.
- UNDX public-facing copy was introduced only where requested by the transformation mission.
- This is not final UI polish. It is the reusable foundation for app-wide consistency.

## Next Design-System Work

- Add shared drawer/search primitives during the master navigation drawer transformation.
- Add modal/bottom-sheet/toast/progress primitives when the next subsystem needs them.
- Add reduced-motion hooks when motion is expanded beyond static signal indicators.
