# PulseSoc Notification Defaults

## What Changed

PulseSoc notification preferences now default to enabled for new and existing users when a reliable explicit opt-out is not already stored.

Enabled-by-default channels:

- In-app
- Push
- Email
- SMS

Enabled-by-default categories include chat messages, group messages, room messages, comments, replies, likes, reactions, social activity, status activity, live, live invites, crypto alerts, market alerts, intelligence alerts, marketplace, marketplace orders, purchases, payments, premium, admin security, marketing, roast battle, and security.

## Backend Behavior

- Added `ensure_user_notification_defaults(user_id)` in `services/pulsesoc_notification_system.py`.
- Added `backfill_notification_defaults(limit)` for bounded existing-user provisioning.
- New accounts call the provisioning helper immediately during account creation.
- Preference reads and notification delivery reads provision missing rows before evaluating rules.
- Startup runs a bounded backfill using `PULSESOC_NOTIFICATION_DEFAULT_BACKFILL_LIMIT`, defaulting to `1000`.
- Existing saved category rows are not overwritten, so a stored `0` remains treated as a deliberate user disable.
- Null or missing channel values are filled as enabled.

## UI Behavior

- Notification Settings renders missing category preferences as checked.
- Market Alerts and Intelligence Alerts are visible in the category table.
- Push status separates PulseSoc preference from OS-level permission:
  - Enabled
  - Blocked by device
  - Unsupported
  - Needs permission
- The settings screen tells users: “Enable Push to receive PulseSoc alerts on your lock screen.”

## Security And Platform Notes

- PulseSoc cannot force iOS, Android, or browser push permission.
- “Disable Push” only disables PulseSoc push preference/device routing; it does not pretend to change OS settings.
- Quiet hours still require explicit user enablement.
- User-disabled categories remain disabled when an existing saved row provides that history.

## Verification

- `scripts/pulsesoc_notification_defaults_audit.py` checks category coverage, channel defaults, provisioning/backfill hooks, UI push status labels, and report presence.

## Known Limitations

- Existing saved `0` values are treated as reliable opt-outs because older records do not have a separate explicit-choice audit column.
- Users without OS push permission still need to grant device/browser permission before locked-screen push can appear.
