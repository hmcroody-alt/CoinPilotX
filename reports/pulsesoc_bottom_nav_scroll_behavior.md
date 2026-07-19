# PulseSoc Bottom Nav Scroll Behavior

## Goal

Make the PulseSoc mobile bottom navigation hide while the user scrolls down and reappear as soon as the user scrolls up.

## Implementation

- Updated the production home dock controller in `static/js/pulse_home_core.js`.
- Kept the existing `mobile-bottom-nav pulse-universal-dock` system instead of adding a second dock.
- Preserved the existing CSS transform animation in `static/css/pulse_home_os.css`.
- Updated the `pulse_home_core.js` cache-bust query in `bot.py` so the PWA/browser loads the new behavior.

## Scroll Behavior

- Scroll down past the top reveal zone: hide the dock.
- Scroll up: show the dock immediately.
- Near the top of the page: keep the dock visible.
- Short pages that cannot meaningfully scroll: keep the dock visible.

## Pinned States

The dock remains visible while the user is interacting with:

- text inputs, textareas, or selects
- the composer
- drawer/menu/search overlays
- status viewer/editor
- music picker
- create sheet
- media lightbox
- promotion modal

## Performance

- Uses the existing `requestAnimationFrame` scroll controller.
- Uses passive scroll/resize/page-show listeners.
- Uses transform/opacity only for the dock animation.
- Does not change feed layout or trigger layout shifts.

## Verification

- `venv/bin/python scripts/pulsesoc_bottom_nav_scroll_audit.py`
- `node --check static/js/pulse_home_core.js`
- `venv/bin/python -m py_compile bot.py scripts/pulsesoc_bottom_nav_scroll_audit.py`
- `git diff --check`

## Manual QA

Recommended mobile QA:

1. Open `/pulse` on a phone-sized viewport.
2. Scroll down through the home feed.
3. Confirm the bottom dock slides out and no longer covers content.
4. Scroll up slightly.
5. Confirm the dock reappears immediately.
6. Open search, drawer, composer, and create sheet.
7. Confirm the dock stays visible or pinned where interaction requires it.
