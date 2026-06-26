# PulseSoc Sci-Fi Ads QA Report

## Automated Validation

Run:

```bash
venv/bin/python -m py_compile bot.py services/pulse_ads_service.py scripts/pulse_sci_fi_ads_layer_audit.py
venv/bin/python scripts/pulse_ads_foundation_audit.py
venv/bin/python scripts/pulse_ads_delivery_engine_audit.py
venv/bin/python scripts/pulse_advertiser_portal_audit.py
venv/bin/python scripts/pulse_sci_fi_ads_layer_audit.py
git diff --check
```

## Expected Coverage

- Approved active ads serve.
- Unapproved creatives do not serve.
- Desktop UFO placement serves only desktop.
- Mobile UFO placement serves only mobile.
- Pulse Network hologram placement serves.
- Signed impression tracking works.
- Video quartile events work.
- Hide/report events work.
- Home loads the ad hook.
- Reduced-motion and visibility handling exist.
- No unsafe `innerHTML` rendering in the ad hook.

## Manual QA To Repeat After Deploy

- Desktop Home wide and medium widths.
- Mobile Home with inline sponsored card.
- Hide sponsored signal.
- Report sponsored signal.
- CTA click opens validated destination.
- Video preview starts muted only when visible.
- Tab hidden pauses media.
- Feed, composer, statuses, messages, live, Pulse Radio, marketplace, search, and dashboard remain usable.
