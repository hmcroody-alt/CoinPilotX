# PulseSoc Chat And Alert Badge Separation Report

Date: 2026-06-12

## Scope

Milestone 2 from the master execution contract: direct message/chat unread counts must appear only on chat surfaces, while general alerts must appear only on notification/alerts surfaces. Badge numbers must be red and must not use a combined top-header count.

## Root Causes Found

- `static/notifications.js` used legacy fallbacks from `unread_count` and `count` for alert counts. That was risky because older payloads can represent combined unread values.
- Realtime handlers accepted generic `unread_count` for chat and alert events. This could mix counts when event payloads were not category-specific.
- Backend count separation was already mostly correct: `pulse_badge_counts()` excludes message notifications from alerts and sums conversation unread counts for chat badges.

## Files Changed

- `static/notifications.js`
- `scripts/pulse_badge_separation_audit.py`
- `reports/pulsesoc_badge_separation_report.md`

## Fix Applied

- Alert badge updates now use only `alert_unread_count`.
- Chat badge updates now use only `chat_unread_count`.
- Realtime chat events ignore generic `unread_count`.
- Realtime alert events ignore generic `unread_count`.
- Added a repeatable audit verifying backend split counts, frontend strict mapping, red badge markup, and no combined badge selectors.

## Security Checks

- Notification API remains authenticated through `api_account_user()`.
- Backend count query remains scoped by `user_id`.
- Chat count uses participant rows scoped by the current user.
- Alert count excludes message notifications and remains scoped to current-user notification rows.
- No secrets or environment variables are logged.

## QA Results

| Test | Result | Evidence |
| --- | --- | --- |
| Backend separates alert and chat counts | PASS | Audit fixture inserted one unread alert, one unread chat notification, and `unread_count=3`; API service returned `alert_unread_count=1`, `chat_unread_count=3`. |
| Legacy aliases remain alert-only | PASS | Audit verified `count` and `unread_count` equal alert count, not combined count. |
| Desktop header separate badges | PASS | Audit verified separate `data-chat-unread` and `data-alert-unread` targets. |
| Mobile topbar separate badges | PASS | Audit verified separate mobile chat and alert targets. |
| Bottom nav separate badges | PASS | Audit verified generated bottom nav badge targets exist separately. |
| Badges are red | PASS | Browser QA found chat and alert badge background `rgb(255, 51, 93)`. |
| No combined badge target | PASS | Browser QA found `0` combined `[data-total-unread]` or `[data-unread-count]` targets. |
| Browser DOM separation | PASS | Browser QA found `4` chat badge targets and `3` alert badge targets on `/pulse`. |

## Validation Commands

- `node --check static/notifications.js`
- `.venv/bin/python -m py_compile scripts/pulse_badge_separation_audit.py`
- `.venv/bin/python scripts/pulse_badge_separation_audit.py`
- `git diff --check`

## Known Remaining Issues

- This milestone separates counts and routing surfaces. Native push routing and deep-link delivery remain covered by the later realtime notification milestone.
