# Pulse Banuba Camera Foundation

Date: 2026-06-03

## Summary

Pulse Camera now has a safe Banuba-ready foundation behind `PULSE_CAMERA_ENABLED`, which defaults to `false`. The Banuba token remains server-only. The frontend receives only safe public state, including whether the feature is enabled, whether a token is present as a boolean, and the fallback upload configuration.

## What Changed

- Added `/api/pulse/camera/config` for safe camera configuration.
- Added route support for `/pulse/camera/status`, `/pulse/camera/reel`, and `/pulse/camera/post`.
- Extended the existing Pulse Camera page with provider/fallback metadata.
- Added native camera and device file picker fallback handling in the shared camera script.
- Wired camera entry points from Status, Reels, and the feed composer to the shared camera foundation.
- Kept upload publishing on the existing Pulse media pipeline:
  - Camera upload: `/api/pulse/media/upload`
  - Status publish: `/api/pulse/status`
  - Reel publish: `/api/pulse/reels/create`
  - Feed publish: `/api/pulse/posts/create-from-camera`

## Feature Flag

`PULSE_CAMERA_ENABLED=false` is the default. With the flag disabled, Banuba is not activated and users still get a working native camera/gallery fallback.

## Token Safety

`BANUBA_TOKEN` is read only on the backend to produce:

- `banuba_token_present=true`
- `banuba_token_present=false`

The token value is never logged, serialized to the page, or exposed to static frontend code.

## Fallback Behavior

If Banuba is disabled, unavailable, or no compatible SDK is loaded, Pulse Camera keeps the experience functional through:

- native browser camera capture when available
- device file picker fallback
- the existing R2/Mux-backed media upload pipeline

## Future Phase

Live, voice, and video calling remain placeholders for a later phase. The current foundation is scoped to Status, Reels, and feed video/photo posts.

## Audits Added

- `scripts/pulse_banuba_camera_audit.py`
- `scripts/pulse_camera_fallback_audit.py`

