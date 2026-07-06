# PulseSoc Native Visible Dashboard QA

Date: 2026-07-06

Scope: visible built-in QA browser walkthrough for the native User Dashboard.

## Result

Visible built-in QA browser: completed.

Chrome Incognito used: no.

Production WebView routes changed: no.

## Setup

The native web QA server was started locally, localhost was verified before browser navigation, and the built-in QA browser was made visible for the walkthrough.

Authenticated local QA used a temporary local backend and CORS-enabled local API proxy so the native dashboard and signed-in modules could render without weakening production auth.

Observed QA infrastructure note:

- The same-origin proxy on `localhost:8094` served API correctly but did not mount the Expo web root reliably in the in-app browser.
- Direct Expo web on `localhost:8095` rendered correctly.
- The visible walkthrough was completed on `localhost:8095` with the local API proxy on `localhost:8094`.
- This is a QA tooling issue, not a production WebView change.

## What Roody visibly saw

- Native Dashboard tab in the PulseSoc bottom navigation.
- User Dashboard hero section.
- Activity, Orders, and System status chips.
- Dashboard module count chip showing the production dashboard module map.
- At A Glance cards.
- Quick Actions.
- Dashboard Systems cards.
- Production Dashboard Map rail with Account, Network, Creator, Intelligence, Economy, Media, Crypto, Safety, Ads, AI, and System groups.
- Production module cards with status labels, lock labels, native/fallback indicators, and action labels.
- Recent Activity timeline.
- Navigation from dashboard cards into existing native modules.
- Seller Store opened from the dashboard `Manage` action.
- Intelligence opened from the dashboard `Scan` action.
- Camera Studio opened from the dashboard `Capture` action and showed the browser-safe device fallback.
- Economy & Earnings `Marketplace` module opened the native Marketplace route from the visible dashboard UI.

## Screens paused for review

- `/pulse/dashboard`
- `/dashboard`
- Dashboard tab from the visible app UI
- Production Dashboard Map rail
- Account Command Center
- Pulse Network
- Creator Studio
- Intelligence
- Economy & Earnings
- Pulse Radio & Media
- Crypto Command Center
- Moderation / Safety
- Ads & Sponsorships
- PulseSoc AI
- System Status
- Dashboard Quick Actions
- Activity Inbox from a dashboard action
- Seller Store from a dashboard action
- Buyer Orders from a dashboard action
- Creator Studio from a dashboard action
- Intelligence from a dashboard action
- Camera Studio from a dashboard action
- Full dashboard scroll through At A Glance, Quick Actions, Dashboard Systems, and Recent Activity.

## Visible QA Notes

- Dashboard rendered as a native PulseSoc command surface.
- Dashboard cards reused backend-owned summary state.
- Safe partial warnings appeared only when a module API was unavailable.
- Dashboard did not expose internal design-language names as user-facing product copy.
- No Chrome Incognito window was used.
- Backup evidence saved under `reports/screenshots/native-user-dashboard-qa-2026-07-06/`.
- Parity pass backup evidence saved under `reports/screenshots/native-user-dashboard-parity-2026-07-06/`.
- The visible browser scroll pass used the app's rendered internal scroll surface, not direct route-only checks.

## Blocked or unfinished

- Physical iPhone camera/microphone behavior remains device-only.
- APNs/FCM push and lock-screen notification behavior remain device-only.
- Production-scale data richness requires persistent staging fixtures.
- Advanced provider/payment/payout/campaign/Live Studio flows remain safe web fallback.
- The `localhost:8094` same-origin proxy root did not mount the Expo app in this browser pass; direct Expo `localhost:8095` was used for visible review while API calls flowed through the local proxy.

## 2026-07-06 Dashboard Parity Visible Pass

Result: completed.

Server verification before opening the browser:

- `http://localhost:8094` returned HTML from Expo web.
- `http://localhost:5055/health` returned healthy local API status.
- Authenticated QA used a local runtime-only account. No password was committed to source or reports.

What was visible live:

- signed-in `/pulse/dashboard`
- dashboard hero and session summary
- At A Glance cards
- native quick actions
- Dashboard Systems summary cards
- full production module rail
- Account Command Center module cards
- Pulse Network module cards
- Creator Studio module cards
- Intelligence module cards
- Economy & Earnings module cards
- Pulse Radio & Media module cards
- Crypto Command Center module cards
- Moderation / Safety module cards
- Ads & Sponsorships module cards
- PulseSoc AI module cards
- System Status module cards
- Dashboard Quick Actions
- Recent Activity
- Marketplace route opened from the visible Economy & Earnings `Marketplace` dashboard card

Console notes:

- Web-only warnings remain for Expo Notifications web badging/listeners, deprecated shadow props, Expo AV deprecation, and React Native web animation fallback.
- No production WebView route was changed.

Not verified in this browser pass:

- physical iPhone camera/microphone capture
- APNs/FCM lock-screen behavior
- installed-app deep links
- provider billing/payout/payment pages
- final UI/UX polish
