# Premium PulseSoc Welcome Screen Report

Updated: 2026-06-09

## Summary

Superseded by the WebView mirror strategy on 2026-06-09.

PulseSoc Mobile should mirror the live PulseSoc website. The native premium welcome screen has been removed from the app launch path so Android and iOS both open the live website directly in the WebView shell.

The live website is now responsible for logged-out welcome, signup, sign-in, language, and product presentation. This prevents Android from drifting away from the iOS/web experience.

## Implemented

- Native welcome screen launch branch removed from `App.tsx`.
- Hidden native session-check gate removed from startup.
- App launches `https://pulsesoc.com` directly.
- Internal PulseSoc navigation stays inside WebView.
- External links open outside the shell.
- Cookies, DOM storage, media playback, pull-to-refresh, push bridge, native share bridge, offline fallback, and Android hardware back remain intact.

## Android Follow-Up

Android must not show a separate native welcome screen. The next Android build should open the live website directly, matching iOS WebView behavior and the production website.

## Performance Notes

- Removing the native welcome/session-check gate reduces startup complexity.
- The WebView cache and hardware composition remain enabled.
- Website performance is now the source of truth for Feed, Reels, Videos, Messages, Notifications, Profile, Premium, signup, sign-in, and welcome.

## QA Results

- `npm run typecheck`: PASS
- `npm run audit:mobile-web-parity`: PASS
- `npm run audit:android-ui`: PASS
- `npm run audit:feed`: PASS
- `npm run audit:mobile-performance`: PASS

## Real Device Validation Still Needed

- Install the next Android internal testing build.
- Confirm first screen is the live `https://pulsesoc.com` website in the native shell.
- Confirm signup/sign-in/language behavior comes from the website, not native React Native screens.
- Confirm Feed, Reels, Videos, Messages, Notifications, Profile, and Premium are web surfaces.
- Confirm cookies persist after restart.
- Confirm push bridge, native share bridge, file upload, media playback, and Android back button still work.

## Store Screenshot Impact

Future store screenshots should show the live PulseSoc website experience inside the app shell.
