# PulseSoc Intelligence Push Copy Fix

## Problem

Locked-screen BTC alerts still showed the old formatter:

- `PulseSoc Alert: BTC crossed $61,...`
- `BTC crossed above $61,000. Live observed value...`

The Intelligence alert visual system existed, but user-configured crypto threshold alerts were still dispatched through `services/alert_engine.py` with the legacy crypto title before central notification and push jobs were created.

## Root Cause

`services/alert_engine.py::dispatch_alert_event()` built this title directly:

```text
PulseSoc Alert: <SYMBOL> crossed <TARGET>
```

It then passed that title into `pulsesoc_notification_system.notify_crypto_alert()` before any Intelligence copy normalization could run. Because the notification payload stayed categorized as a crypto alert instead of Intelligence metadata, the service worker treated it as a generic PulseSoc notification and displayed the old raw title.

## Fix Applied

- Added `_crypto_intelligence_push_copy()` in `services/alert_engine.py`.
- The helper builds a `crypto_pulse` signal and runs `normalize_intelligence_alert_copy()` before central notification creation.
- `dispatch_alert_event()` now sends:
  - `title`: `PULSESOC ALERT`
  - `body`: short normalized observed-value body
  - metadata `headline`: `BTC BREAKOUT DETECTED`
  - metadata `category`: `intelligence`
  - metadata `notification_type`: `intelligence_pulse`
  - metadata `sound_key`: `pulse_signal` or `alert`
  - metadata `show_on_lock_screen`: `True`
- This lets `static/service-worker.js` render the compact lock-screen format:

```text
PULSESOC ALERT
BTC BREAKOUT DETECTED
Bitcoin crossed $61,000. Live observed value: $62,558.
```

The underlying source remains `crypto_alert` with the same alert id, so Manage My Alerts and delivery diagnostics still map back to the user-owned alert.

## Files Changed

- `services/alert_engine.py`
- `scripts/pulsesoc_intelligence_push_copy_audit.py`
- `reports/pulsesoc_intelligence_push_copy_fix.md`

## QA Result

Automated audit simulates a real BTC alert dispatch and verifies the central notification handoff has:

- no `PulseSoc Alert:` prefix
- normalized `PULSESOC ALERT` title
- normalized `BTC BREAKOUT DETECTED` headline
- short observed-value body
- Intelligence category/type metadata
- service worker compatible headline fields

Manual locked-phone QA still needs a live device send after deployment propagation to confirm the rendered lock-screen card exactly matches the new payload.

## Remaining Notes

Native APNs can use the normalized headline as subtitle. Web/PWA push uses the service worker Intelligence path, which displays `PULSESOC ALERT` as the title and prepends the normalized headline to the body.
