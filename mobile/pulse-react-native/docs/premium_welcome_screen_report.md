# Premium PulseSoc Welcome Screen Report

Updated: 2026-06-08T23:18:00Z

## Summary

PulseSoc Mobile now opens with a premium native first-launch welcome screen for logged-out users before loading the live PulseSoc website shell.

The welcome screen is intentionally lightweight: it uses native layout, the existing optimized PulseSoc app icon, a simple native fade/scale animation, and a rotating neon ring. It does not replace any product screens. Feed, Reels, Videos, Messages, Notifications, Profile, and Premium still come from `https://pulsesoc.com` through the WebView shell.

## Implemented

- Language selector in the top safe area with:
  - English
  - Francais
  - Kreyol Ayisyen
  - Espanol
  - Portugues
- Saved language preference using local app storage.
- Centered PulseSoc logo with neon green/cyan glow and smooth fade/scale entrance.
- Headline: `Join PulseSoc`
- Slogan: `Connect. Create. Discover. Pulse the World.`
- Horizontal premium feature strip:
  - Join Communities
  - Watch Videos & Reels
  - Chat in Real Time
  - AI-Powered Discovery
  - Exclusive Premium
  - Privacy First
- Primary CTA: `Create Your PulseSoc Account`
- Secondary CTA: `Sign In to PulseSoc`
- Footer: `PulseSoc(TM) • Built by CoinPilotXAI Inc.`
- Signup routes into the live website at `/register`.
- Sign-in routes into the live website at `/login?next=/pulse`.
- Startup session check against the live PulseSoc mobile session endpoint using WebView cookies.
- Users with a valid saved website session skip the welcome screen and go directly to the live PulseSoc website.
- Logged-out users see the premium welcome screen first.

## Android Follow-Up

Android was not intentionally excluded from the welcome screen. The welcome screen lives in the shared React Native app shell and renders on both iOS and Android.

The Android issue was release and timing related:

- The Play Console internal testing release still had an older Android bundle before the premium welcome work.
- Android WebView can finish loading the hidden session check page after the injected startup script runs, which can make the first-launch decision inconsistent.

Fix applied:

- The hidden session WebView now reruns the session-check script on load completion, so Android reliably decides between:
  - valid saved session -> open the PulseSoc feed
  - no valid saved session -> show the premium welcome screen

The same welcome UI is now confirmed in code for Android:

- language selector
- centered glowing PulseSoc logo
- `Join PulseSoc`
- `Connect. Create. Discover. Pulse the World.`
- premium feature strip
- `Create Your PulseSoc Account`
- `Sign In to PulseSoc`
- `PulseSoc(TM) • Built by CoinPilotXAI Inc.`

## Performance Notes

- No heavy video, Lottie, blur, or large new asset was added.
- Animation uses React Native native-driver compatible opacity/scale/rotation.
- Product screens are still the WebView shell, preserving the web parity strategy.
- The hidden session check is a one-shot lightweight load of `/api/mobile/auth/session`.

## QA Results

- `npm run typecheck`: PASS
- `npm run audit`: PASS
- `npx expo-doctor`: PASS
- `npx expo config --type public`: PASS

## Real Device Validation Still Needed

- Install the next iOS TestFlight build and verify:
  - Logged-out first launch shows the welcome screen.
  - Saved logged-in session skips to the feed.
  - Create account opens the website signup flow.
  - Sign in opens the website login flow.
  - Language selector saves and restores.
  - No Dynamic Island/status bar overlap.
  - Animation feels smooth.
- Install the next Android internal testing build and repeat the same checks on a small Android phone.

## Store Screenshot Impact

New App Store screenshots should include the premium first-launch screen plus live web product screens after the next build is available.

Generated welcome screenshots:

- `mobile/pulse-react-native/store-metadata/screenshots/appstore/iphone-65-welcome-1284x2778.png`
- `mobile/pulse-react-native/store-metadata/screenshots/appstore/ipad-13-welcome-2048x2732.png`
