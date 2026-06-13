# PulseSoc Platform Protection Guardrails

Generated: 2026-06-13

## Summary

Added a central protection layer for critical PulseSoc systems. The first pass is static, secret-safe, and designed to run locally and in CI without consuming LiveKit/Mux egress quota.

## Created

- `/scripts/protection/run_protection_suite.py`
- `/tests/protection/test_livestream_contract.py`
- `/tests/protection/test_media_playback_contract.py`
- `/tests/protection/test_core_platform_contract.py`
- `/.github/workflows/protection.yml`
- `/docs/protection/livestream_golden_path.md`
- `/docs/protection/platform_protection_matrix.md`

## Protected Contracts

- Host livestream preview remains muted to prevent echo.
- LiveKit room-composite egress and fallback handling remain present.
- Egress quota and Source closed errors remain classified.
- Stream secrets remain backend/host-only.
- Reels active playback requests sound and does not depend on hover.
- Reels preload the next two items.
- Status rail previews remain muted, while the active status viewer follows the user's saved sound preference.
- Mobile Videos page has a hamburger drawer with primary navigation.
- Camera capture includes 1080p-first and 720p fallback constraints.
- Camera/Banuba diagnostics are explicit and device IDs are masked.

## Remaining Protection Gaps

- Real livestream health can only be verified with deployed browser QA and available LiveKit egress minutes.
- Push notification hard-alert validation still requires real iOS/Android device tests.
- Stripe fulfillment protection should be expanded with signed webhook fixture tests.
- Automatic rollback depends on Railway/GitHub deployment integration and should be added after these gates are stable.
