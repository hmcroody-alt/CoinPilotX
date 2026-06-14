# Home Status Rail Click + Responsiveness QA

## Root Cause

The default `/pulse` page boots with `boot_profile=core`, which strips the heavier `data-pulse-shell-runtime` script. The Home status rail was server-rendering real statuses, but the core shell did not have a guaranteed always-on status opener. Result: cards appeared in the rail but could be inert in the core profile.

## Fix Implemented

- Added a lightweight `data-pulse-instant-core` runtime in the `/pulse` shell.
- Made Home rail status cards real tappable buttons with:
  - `data-open-status-id`
  - `data-status-id`
  - `data-status-open-url`
  - accessible `aria-label`
- Added pointerdown/touch-friendly pressed states and protected card media from swallowing taps.
- Added the same tappable contract to dynamically hydrated Home rail cards.
- Added core status viewer behavior for open, close, view count POST, and optimistic reaction updates.
- Added route/API prefetch hints for likely PulseSoc destinations and status rail data.
- Hardened MutationObserver startup in global media/status helpers so they never observe a non-Node.
- Bumped media/status JS asset query keys to avoid stale browser cache.

## Files Changed

- `bot.py`
- `static/css/pulse_status_system.css`
- `static/js/pulse_media_renderer.js`
- `static/js/pulse_status_viewer.js`
- `scripts/pulse_home_status_layout_audit.py`
- `scripts/pulse_status_audit.py`

## QA Evidence

Screenshots:

- `reports/qa-home-status-click-performance/mobile-home-status-clean.png`
- `reports/qa-home-status-click-performance/mobile-status-viewer-clean.png`
- `reports/qa-home-status-click-performance/desktop-home-status-clean.png`
- `reports/qa-home-status-click-performance/desktop-status-viewer-clean.png`

Browser QA:

- Mobile `/pulse` boot profile: `core`
- Home rail status cards present: PASS
- Cards are buttons with status id, open URL, and aria labels: PASS
- Tap first Home rail status on mobile: PASS
- Fullscreen status viewer opens: PASS
- View count payload renders in viewer: PASS
- Optimistic status reaction count update: PASS
- X/close closes viewer: PASS
- Create Status card remains available: PASS
- Desktop Home rail status tap opens viewer: PASS
- Fresh console errors after final reload: PASS, none observed
- Measured Home rail tap to viewer-open shell: 286 ms in QA Browser

Validation:

- `python3 -m py_compile bot.py scripts/pulse_status_audit.py scripts/pulse_home_status_layout_audit.py`: PASS
- `node --check static/js/pulse_media_renderer.js`: PASS
- `node --check static/js/pulse_status_viewer.js`: PASS
- `python3 scripts/pulse_home_status_layout_audit.py`: PASS
- `python3 scripts/pulse_status_audit.py`: PASS
- `git diff --check`: PASS

## Remaining Risk

This pass fixes the Home status rail and adds core-shell responsiveness hooks. It does not fully complete every broad platform performance item across all surfaces such as Messages, Alerts, and Profile navigation; those require separate surface-specific QA and should not be considered fully closed by this scoped commit.
