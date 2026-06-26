# PulseSoc Ad Platform QA

## Automated QA

Run:

```bash
venv/bin/python -m py_compile bot.py services/pulse_ad_payments.py services/pulse_advertiser_portal.py services/pulse_ads_service.py scripts/advertiser_portal_audit.py scripts/ad_payments_audit.py scripts/ad_wallet_audit.py scripts/review_board_audit.py scripts/stripe_webhook_audit.py
venv/bin/python scripts/advertiser_portal_audit.py
venv/bin/python scripts/ad_payments_audit.py
venv/bin/python scripts/ad_wallet_audit.py
venv/bin/python scripts/review_board_audit.py
venv/bin/python scripts/stripe_webhook_audit.py
venv/bin/python scripts/pulse_ads_foundation_audit.py
venv/bin/python scripts/pulse_ads_delivery_engine_audit.py
venv/bin/python scripts/pulse_sci_fi_ads_layer_audit.py
venv/bin/python scripts/pulse_radio_ad_campaign_audit.py
git diff --check
```

All listed automated checks passed on the local isolated SQLite audit databases.

## QA Browser Verification

- Desktop: `http://127.0.0.1:5092/pulse/advertise` loaded `PulseSoc Advertiser Mission Control` with Campaign Wizard, Wallet, Billing, Review Status, Notifications, and Settings present.
- Mobile: iPhone-width viewport `390x844` loaded the same portal with Campaign Wizard and Wallet visible and no horizontal overflow (`scrollWidth=390`, `clientWidth=390`).
- Admin review board access: `http://127.0.0.1:5092/admin/pulse-ads-review-board` redirected to `/admin/login` without an admin session, confirming it is not public.

## Manual QA Checklist

- Advertiser portal loads on desktop and mobile.
- Create advertiser account.
- Create campaign draft.
- Upload/create creative draft.
- Submit creative for review.
- Admin review board loads.
- Admin approve/reject/needs changes/suspend works.
- Wallet summary loads.
- Funding route rejects when billing is disabled.
- Native iOS funding is blocked.
- Approved ads remain delivery-eligible only when campaign/account/placement/moderation/budget/wallet checks pass.
- No console errors, overflow, or payment data leaks.
