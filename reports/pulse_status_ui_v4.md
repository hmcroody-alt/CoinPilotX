# PulseSoc Status UI V4

## Objective

Implement the PulseSoc Status V4 design language:

- One identity
- One soundtrack
- Content-first cinematic viewer
- No duplicated creator, song, or metadata panels

## Files Changed

- `static/js/pulse_status_viewer.js`
- `static/css/pulse_status_system.css`
- `bot.py`
- `scripts/pulse_status_ui_v4_audit.py`
- `scripts/status_viewer_layout_audit.py`
- `scripts/pulse_status_mobile_playback_audit.py`

## What Changed

### One Identity

The V4 runtime keeps the visible creator identity in one place only: the top-left status header.

The legacy bottom identity footer remains in the DOM as a compatibility data source, but the V4 runtime marks it as legacy metadata and the stylesheet hides it while the viewer is open.

Visible identity now contains:

- Avatar
- Creator name
- Status type and time/count context

### One Soundtrack

The old music mini-player was demoted into a subtle atmospheric music signature.

Collapsed state:

- Small `♪`
- Song title
- PulseSoc Music/artist line
- Quiet equalizer
- Low opacity

Expanded state:

- Triggered by interacting with the music/sound control
- Fades up for about three seconds
- Shows progress, play state, and secondary music actions
- Collapses automatically

The music title no longer falls back to the status caption/body. This prevents captions from being duplicated as fake music metadata.

When a Status has no attached soundtrack, the atmospheric music signature stays hidden. The V4 stylesheet explicitly preserves the `[hidden]` state even though the visible music signature uses high-priority layout overrides.

### Content Priority

The photo/video/text status remains full-screen and visually dominant. V4 overlays use translucent glass, subtle gradients, and low-opacity particles without blocking the main content.

### Runtime Behavior

- `pulse-status-v4-viewer` and `pulse-status-v4-shell` classes are added by the status viewer runtime.
- Existing status action rail remains available: Like, Comment, Repost, Share, Save, More, Sound.
- Auto-hide chrome behavior remains intact.
- Reduced motion support is preserved.

## Verification

Automated checks added:

```bash
venv/bin/python scripts/pulse_status_ui_v4_audit.py
```

Additional syntax checks:

```bash
node --check static/js/pulse_status_viewer.js
venv/bin/python -m py_compile bot.py scripts/pulse_status_ui_v4_audit.py
venv/bin/python scripts/status_viewer_layout_audit.py
venv/bin/python scripts/status_viewer_space_usage_audit.py
venv/bin/python scripts/pulse_status_mobile_playback_audit.py
```

Browser QA:

- Mobile 390px: `/pulse/status?v=status-ui-v4-qa` loaded the V4 viewer shell with no horizontal overflow.
- Desktop 1440px: `/pulse/status?v=status-ui-v4-desktop-qa` loaded the V4 viewer shell and V4 CSS with no horizontal overflow.
- The current authenticated session had no visible status cards, so real story playback/open-state QA could not be completed from browser data in this pass.

## Remaining Notes

The expanded music action labels are intentionally rendered as passive labels, not dead buttons. They preview the expanded soundtrack surface without pretending unavailable playlist, lyrics, or sound-reuse actions are wired.

No backend schema change was required.
