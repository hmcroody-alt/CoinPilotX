# Mobile Release Update After Home Live/Status Fix

Date: 2026-06-09

## Change Summary

This update fixes the logged-out welcome/auth experience, Home status media fill, status-muted autoplay, and the disruptive Live Now block on the website. Since the iOS and Android apps are WebView shells, both platforms inherit these web fixes once the app loads the updated site.

## Mobile App Change

- Default WebView start URL changed to `https://pulsesoc.com/login?next=/pulse`.
- Logged-out users now land on the permanent premium PulseSoc welcome/login/signup screen.
- Logged-in users are redirected by the website to `/pulse`.
- Package and bundle remain `com.pulsesoc.app`.

## Build Tracking

- iOS: new build required after this fix.
- Android: new build required after this fix.
- Android EAS quota was previously blocked for production and QA builds; retry result will be recorded after validation.

## Validation Plan

- Python compile
- JavaScript/TypeScript parse
- feed/status/live audits
- site functional audit where available
- mobile `npm run typecheck`
- mobile `npm run audit`
- `npx expo-doctor`
- `npx expo config`
- iOS build
- Android build or documented quota blocker

## Store Notes

Do not submit a new iOS or Android binary until the build IDs/artifacts below are filled from successful post-fix builds.

## Post-Fix Build IDs

- iOS build: pending
- Android AAB: pending
- Android APK: pending
