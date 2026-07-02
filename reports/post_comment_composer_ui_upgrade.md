# PulseSoc Post Comment Composer UI Upgrade

## Summary

The feed post action and comment composer section was upgraded into a compact Galaxy Comment Dock while preserving the existing comment API path and comment notification behavior.

## What Caused the Random `Y` Avatar

The shared feed `avatarNode()` rendered the first letter of its fallback name when no image was available. The comment composer called:

```text
currentViewerAvatar(post) -> avatarNode(..., "You")
```

That caused a large circular `Y` to appear whenever the current viewer payload did not include an avatar image.

## How It Was Fixed

- `avatarNode()` now renders a real image when one exists.
- `PULSESOC MEMBER #000` uses its official generated avatar asset.
- Normal missing-avatar users render a branded PulseSoc orb fallback.
- No shared feed avatar fallback renders arbitrary initials anymore.

## Components Changed

- Shared feed avatar renderer
- Shared post action row
- Reaction/stats strip styling
- Comment composer dock
- Comment composer action buttons

## CSS/JS Changed

- `static/js/pulse_home_core.js`
- `static/css/pulse_reaction_system.css`

## Button States Added

Post/feed actions now retain the shared reaction-button behavior and have clearer active/tap styling for:

- Like
- Comment
- Repost
- Share
- Save
- More

Comment dock states:

- empty comment disables send
- typing enables send
- sending disables send and shows loading state
- success clears input and updates counts
- error preserves input text

## Composer Behavior

The composer now renders as:

```text
[avatar/orb] [Write a comment...] [photo] [voice] [emoji] [attach] [send]
```

Safe unavailable states:

- Photo comments: `Photo comments are not enabled on feed cards yet.`
- Voice comments: `Voice comments are coming soon.`
- Attachments: `Comment attachments are coming soon.`

Emoji remains wired and inserts into the input. Send continues to call:

```text
POST /api/pulse/posts/<post_id>/comments
```

## Notification Safety

The comment creation API route was not changed. The redesign only changes the frontend composer shell and preserves the existing comment submission path, count update path, and notification-triggering backend route.

## Mobile QA Notes

The composer grid has explicit responsive layouts for:

- default/desktop: five 38px action buttons
- mobile: five 34px action buttons
- narrow mobile: five 32px action buttons

This keeps the send button reachable and avoids horizontal overflow around 360px.

## Desktop QA Notes

Desktop keeps the full glass dock, larger avatar/orb, full stats strip, and shared lightweight action row.

## Verification

Added:

```bash
venv/bin/python scripts/post_comment_composer_ui_audit.py
```

The audit checks the dock class, all expected actions, safe unavailable messaging, send state machine, preserved comment API path, branded fallback orb, mobile grid support, and action styling.
