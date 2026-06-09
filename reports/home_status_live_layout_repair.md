# PulseSoc Home Status + Live Layout Repair

Date: 2026-06-09

## Root Cause

Real iOS TestFlight screenshots showed two launch-blocking Home issues:

- Status media could sit behind card layers, causing black empty spaces and weak story tiles.
- The large `Realtime PulseSoc Discovery` Live Now block interrupted the Home feed between status and composer/feed content.

## Changes

- Status media layers now fill the full rounded status card with `object-fit: cover`.
- Status preview videos remain autoplaying but are explicitly muted.
- Status viewer videos now start muted by status-only policy.
- The large middle-of-Home Live Now hub is no longer injected into `/pulse`.
- Live discovery remains available through `/pulse/live`, compact rails, and active LIVE feed posts.
- The mobile WebView app now starts at `/login?next=/pulse`, letting logged-out users see the permanent premium PulseSoc auth/welcome screen while logged-in users are redirected into the feed.

## Affected Files

- `bot.py`
- `templates/account.html`
- `static/css/pulse_desktop_feed.css`
- `static/css/pulse_status_system.css`
- `static/js/pulse_status_viewer.js`
- `mobile/pulse-react-native/App.tsx`
- `scripts/feed_layout_audit.py`
- `scripts/pulse_feed_layout_audit.py`
- `scripts/realtime_feed_audit.py`
- `scripts/mobile_feed_density_audit.py`
- `scripts/home_status_autoplay_audit.py`
- `scripts/pulse_mobile_video_tag_audit.py`
- `scripts/pulse_video_no_forced_mute_audit.py`

## Acceptance Status

- Status cards: repaired to fill media edge-to-edge.
- Status autoplay: kept muted.
- Home Live Now block: removed from the middle of Home.
- Live functionality: preserved through live routes and live feed post service.
- Mobile parity: WebView inherits the same website fixes.

## QA Notes

Validation commands are run after this report is updated. New iOS and Android build status is tracked in `reports/mobile_release_update_after_home_live_status_fix.md`.
