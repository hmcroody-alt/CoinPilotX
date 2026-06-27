# Creator Backend Surface Audit

Date: 2026-06-27

## Protected Surfaces

The Backend Creator Command Center exposes management surfaces for:

- Posts Manager
- Reels Manager
- Videos Manager
- Statuses Manager
- Live Studio Manager
- Audience Intelligence
- Content Performance
- Timing Intelligence
- Creator Score
- Creator Tools
- Trend Intelligence
- Planner
- Scheduler
- Draft Manager
- Creator AI
- Engagement Prediction
- Creator Reputation
- Viral Opportunity Scanner
- Media Processing Health
- Content Moderation
- Creator Audit Logs

## Permission Model

Admin routes require admin authentication through the existing admin page guard. Non-admin access is redirected.

## Link Integrity

`scripts/creator_command_center_audit.py` checks that every admin link exposed by the Creator Command Center resolves without `404` or server error.

## Data Safety

The admin surfaces are aggregate/diagnostic first. They do not expose raw media storage URLs, private moderation notes, private viewer identities, provider secrets, database URLs, private keys, or raw tokens.
