# Reels Nav, Profile Avatar, Status Playback, and Mux UX Fix

## Summary
- Reels is restored as a desktop Pulse navigation destination and remains separate from Videos.
- Profile avatar upload now verifies the database write and returns a cache-busted persisted URL.
- Homepage Status uses the shared Status viewer and mobile-safe video playback.
- Processing video media now exposes status and repair endpoints, with shared frontend polling.

## Key Routes
- `/pulse/reels`
- `/pulse/videos`
- `/pulse/profile`
- `/pulse/status`
- `/api/pulse/media/<media_id>/status`
- `/api/pulse/media/<media_id>/repair`

## Validation Focus
- Desktop navigation at 1440px and 1920px.
- Avatar upload, refresh, leave profile, return.
- Homepage status click opens the same story playback surface as `/pulse/status`.
- Processing reels and feed videos show a clear preparing state, then refresh when Mux is ready.
