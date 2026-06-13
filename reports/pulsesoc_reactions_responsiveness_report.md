# PulseSoc Reactions Responsiveness Report

Date: 2026-06-12

## Scope

Milestone 1 from the master execution contract: make PulseSoc reactions/emojis feel immediate, animated, rollback-safe, and correctly wired across posts, videos, reels, statuses/stories, comments, live posts, and chat/message reactions where available.

## Root Causes Found

- Feed UI rendered modern reactions such as `like`, `love`, `wow`, `rocket`, `clap`, `hundred`, `target`, and `shield`, but the backend allowed only the older trading-specific reaction set. Browser QA showed HTTP 400 rollback until the backend reaction whitelist was repaired.
- Message reaction UI posted to `/api/pulse/communications/v2/messages/<id>/reactions`, but the existing protected backend route is `/api/pulse/messages/<id>/react`.
- Message reaction backend returns normalized emoji values such as `🔥`, while the UI used semantic keys such as `fire`; selected state could not stay synced.
- Reel reaction frontend read `d.reactions.fire`, but the API returns `reaction_counts`; Browser QA showed optimistic state reverting/stale count after confirmation.
- Live reactions animated only after network success, making taps feel delayed.
- Video detail reactions did not update visible counts immediately.
- Status/story reactions lacked visible count feedback and robust rollback.

## Files Changed

- `bot.py`
- `services/pulse_feed_engine.py`
- `static/css/pulse_desktop_feed.css`
- `static/js/pulse_messages_v2.js`
- `static/js/pulse_live_studio.js`
- `static/js/pulse_live_studio_runtime.js`
- `scripts/pulse_reactions_responsiveness_audit.py`
- `reports/reactions_milestone1_reels_after.png`

## Improvements Implemented

- Added shared reaction pop and floating emoji animation styles with `prefers-reduced-motion` support.
- Added optimistic feed post reaction updates with rollback for counts, selected state, and totals.
- Added support for switching reaction type without duplicate count drift.
- Expanded backend-supported post reactions to match the UI reaction strip while preserving legacy reactions.
- Added immediate video detail count and selected-state updates with rollback.
- Fixed Reel reaction confirmation to consume `reaction_counts`.
- Added rapid-tap guarding for Reel reactions.
- Added optimistic Reel comment reaction handling.
- Changed live reaction animation to fire immediately before the network response and rollback button state on failure.
- Corrected message reaction endpoint wiring to the existing authorized API.
- Normalized backend emoji reaction values in message UI so selected state and summaries stay synced.
- Added status/story optimistic reaction animation/count handling with rollback.

## Security Checks

- Protected post reaction route remains behind `api_account_user()`.
- Protected video reaction route remains behind `api_account_user()`.
- Message reaction route still checks active conversation participation before writing.
- No environment variables or secrets are logged.
- No auth checks, validation, or rate limits were weakened.
- Reaction text and summaries continue to be escaped before render.

## QA Results

| Test | Result | Evidence |
| --- | --- | --- |
| React to a post | PASS | Browser QA: post `1016`, `like` changed from count `0` inactive to count `1` active immediately and stayed active after backend confirmation. |
| Change reaction | PASS | Browser QA: post `1016` switched from `like` to `love`; `like` count became `0`, `love` count became `1`, total stayed `1`. |
| Remove reaction | PASS | Browser QA: selected `love` removed; count and total returned to `0`. |
| Rapid taps | PASS | Browser QA: rapid double click settled at one selected reaction and count `1`, no duplicate count corruption. |
| Refresh persistence | PASS | Browser QA: selected post reaction persisted after refresh. |
| React on Reel | PASS | Browser QA: Reel `2` count incremented immediately, pop/burst animation appeared, and selected state stayed after backend confirmation. |
| Remove Reel reaction | PASS | Browser QA: Reel `2` selected state removed and persisted after refresh. |
| React on video | PASS | Browser QA: post-backed video `62` Like button changed immediately to `Liked 1`, floating animation appeared, and state remained after confirmation. |
| React on message | PASS | Flask test client: message `1836` added `🔥` reaction, then removed it through protected route with active participant session. |
| Mobile tap responsiveness | PASS | Browser QA at `390x844`: Reel reaction target rendered at roughly `57x57`, animated immediately, and backend confirmation completed. |
| Status/story reaction | PASS static/API wiring | Status rail had no active local story items during Browser QA; static audit confirms optimistic handler, rollback, and protected status API wiring. |
| Live reaction | PASS static/API wiring | Static audit confirms both live scripts animate before fetch and rollback button state on failure. |

## Validation Commands

- `.venv/bin/python -m py_compile bot.py services/pulse_feed_engine.py scripts/pulse_reactions_responsiveness_audit.py`
- `node --check static/js/pulse_messages_v2.js`
- `node --check static/js/pulse_live_studio.js`
- `node --check static/js/pulse_live_studio_runtime.js`
- `.venv/bin/python scripts/pulse_reactions_responsiveness_audit.py`

## Screenshot

- `reports/reactions_milestone1_reels_after.png`

## Known Remaining Issues

- Some video rows are not backed by a post/reel/feed source and the current backend intentionally rejects reactions for those videos. This is documented for the upcoming immersive video milestone so unsupported buttons do not pretend to work.
- No active local status/story item was available during Browser QA, so status/story reaction validation is currently static/API-contract based until a story fixture exists.
