# Pulse Spaces Auto-Post Rotation

## Change Summary

Pulse Spaces automated posting now uses one persisted rotation instead of the old per-Space morning/evening schedule.

New behavior:

- One automated Space post is created per scheduler tick.
- Ticks are spaced every 3 hours.
- The scheduler picks the next Space in rotation.
- After the last Space receives a post, the pointer wraps back to the first Space.
- Rotation state is stored in `pulse_ai_rotation_state`, so deploys and restarts do not reset the cycle.

## Duplicate Protection

Each rotation tick receives a `rotation_key` based on the current 3-hour window. The scheduler checks the last persisted run key and existing AI post metadata before creating a new post, which prevents duplicate posts if the scheduler is invoked twice in the same tick.

## Legacy Schedule Handling

Legacy daily schedules with `morning`, `afternoon`, or `evening` slots are disabled whenever the scheduler seeds or runs. The old two-posts-per-day-per-Space schedule is no longer used for automated due runs.

## Logs Added

The scheduler logs:

- `SPACE_AI_ROTATION_SELECTED` with the selected Space and scheduled time.
- `SPACE_AI_ROTATION_POST_CREATED` with AI post and Pulse post identifiers.
- `SPACE_AI_ROTATION_NEXT` with the next Space and next run time.
- `SPACE_AI_ROTATION_DUPLICATE_SKIP` when a duplicate scheduler tick is safely skipped.

## Audit

Added `scripts/pulse_spaces_scheduler_audit.py` to verify:

- 3-hour interval configuration.
- Persisted rotation state.
- Duplicate tick guard.
- One-Space-per-run behavior.
- Required rotation logs.
- Legacy morning/afternoon/evening schedules are disabled.
