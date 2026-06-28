# Crypto Dashboard Visibility QA

## Scope

This QA covers the PulseSoc Dashboard crypto visibility upgrade.

## Verified Surfaces

- Dashboard category registration includes `Crypto Command Center`.
- The section exposes 18 crypto cards.
- Each card uses contextual actions:
  - `View Market Pulse`
  - `Create Alert`
  - `Manage Alerts`
  - `View Watchlists`
  - `Ask Crypto AI`
  - `Review Portfolio`
  - `Manage Wallet`
  - `Scan Market`
  - `Track Whales`
  - `View Trending`
  - `View Gainers`
  - `View Losers`
  - `Scan Token`
  - `Read Crypto News`
  - `View Calendar`
  - `Analyze Market`
  - `View Favorites`
  - `Continue Watching`

## Feed QA

- `/pulse?feed=crypto` is registered through the feed engine.
- The Home feed tab row exposes `Crypto`.
- The tab maps to `/pulse?feed=crypto`.

## Mobile/Responsive Notes

The Crypto Command Center uses the same responsive dashboard shell pattern as other Mission Control modules. Cards collapse to mobile-safe grids and include bottom padding for mobile navigation.

## Truthful States

No crypto module shows fake `ACTIVE`.

Provider-backed modules that are not fully connected are labeled `PARTIAL` or `BETA` with explanatory copy.

## Automated QA

Run:

```bash
venv/bin/python scripts/crypto_command_center_audit.py
```

The audit validates no 404s for registered crypto routes, contextual labels, strict states, user/admin boundaries, and sensitive-data redaction.
