# PulseSoc System Speed Report

Date: 2026-07-28
Branch: `release/undx-nexus-core-v4`
Release SHA before this sweep: `e279434f6bf2d364381ebdd03f8a40a6ecbd1da8`

## Summary

The speed sweep repaired two concrete release-gate defects found by the existing audits:

- `/api/pulse/communications/conversations?limit=12` exceeded the DB-query budget.
- Direct uploaded Status music was not explicitly marked as requiring rights review.

No App Store build, TestFlight upload, or App Store submission was created. The final release-build gate is **NO-GO** until the remaining performance warnings and unverified production/device gates are resolved.

## Optimized Areas

### Messenger Conversation API

Before:

- Endpoint: `/api/pulse/communications/conversations?limit=12`
- DB query count: `209`
- Budget: `160`
- Result: `FAIL`

After:

- Endpoint: `/api/pulse/communications/conversations?limit=12`
- DB query count: `125`
- Budget: `160`
- Result: `PASS`

Changes:

- Batched PulseSoc identity hydration for conversation rows and participant previews.
- Batched unread-count calculation for the selected conversation page.
- Batched participant-preview reads for the selected conversation page.
- Batched default-room presence reads so room hydration does not call the presence service once per default room.

### Status Music Rights Review

Before:

- `scripts/audio_pipeline_audit.py` failed because direct uploaded music did not carry a review-required contract.

After:

- Direct Status music uploads include `music_upload_requires_review: true`.
- Status creation persists review-pending metadata in `ai_context_json`.
- Approved catalog music continues through the existing creator-safe `music_track_id` path.

## Validation Evidence

### Backend Performance

`venv` note: validation used `.venv/bin/python`; system `python3` does not have the full repository dependency set.

- `.venv/bin/python scripts/performance_audit.py`: `PASS`, `failures=0`, `warnings=8`
- `.venv/bin/python scripts/api_latency_audit.py`: `PASS`
- `.venv/bin/python scripts/database_query_audit.py`: `PASS`
- `.venv/bin/python scripts/database_integrity_audit.py`: `PASS`
- `.venv/bin/python scripts/live_audio_audit.py`: `PASS`
- `.venv/bin/python scripts/audio_pipeline_audit.py`: `PASS`
- `.venv/bin/python scripts/media_audio_priority_audit.py`: `PASS`

### Business OS

- Script-style Business OS suite: `80` files executed, `0` failures.

### UNDX

- `.venv/bin/python -m unittest discover tests/undx_agent`: `185` tests, `OK`.

### Native

- `npm ci --prefix mobile-native --no-audit --no-fund --progress=false`: `PASS`
- `npm run typecheck` in `mobile-native`: `PASS`
- `npm test -- --runInBand` in `mobile-native`: `101` suites, `1761` tests, `PASS`
- `EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose`: `16/16`, `PASS`
- `git diff --check`: `PASS`

Native simulator install was previously verified on iPhone 17 Pro Max in this sweep. Runtime logs showed session restore and app interactive success, plus warnings listed below.

## Remaining Bottlenecks And Warnings

These prevent an honest full release-build PASS:

- `scripts/performance_audit.py` still reports `8` warnings.
- `/pulse/premium/undx` response payload is approximately `1.94 MB`.
- `static/vendor/livekit-client.umd.js` remains above the static payload budget.
- Existing polling warnings remain in call/live scripts and the LiveKit vendor bundle.
- Native Jest passes but still emits existing React `act(...)` warnings in several suites.
- Xcode simulator runtime still reports `expo-av` deprecation and legacy architecture warnings.
- Physical-device and production backend deployment gates were not re-verified during this sweep.

## Release Gate Decision

Final System Speed Operation status: **PARTIAL**

Reason:

- All critical tests and the previously failing audits now pass.
- A measurable API query-count improvement was verified.
- Remaining audit warnings and unverified production/device gates mean the operation has not reached the required full PASS state.

Release build decision: **NO-GO**

No production build, TestFlight upload, App Store Connect upload, or App Review submission was created.

## Next Required Actions

1. Reduce `/pulse/premium/undx` payload size or explicitly split/lazy-load heavy data.
2. Remove or replace oversized LiveKit vendor payload where practical, or document why the bundle is unavoidable.
3. Review sub-second polling loops in call/live scripts and replace with event-driven or backoff behavior where safe.
4. Resolve native runtime warnings that affect release confidence: `expo-av` migration plan and legacy architecture status.
5. Re-run simulator and physical-device validation after the warning cleanup.
6. Only then re-evaluate the final release-build gate.
