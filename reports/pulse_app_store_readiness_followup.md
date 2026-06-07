# PulseSoc App Store Readiness Follow-up

Date: 2026-06-07

## Completed This Pass

- Expo app display name now resolves as `PulseSoc`.
- Expo slug now resolves as `pulsesoc`.
- iOS bundle identifier remains `com.pulsesoc.app`.
- Android package remains `com.pulsesoc.app`.
- App Store Connect app id remains configured in EAS submit as `6777591572`.
- App icons, adaptive icon, splash, notification icon, Firebase config references, iOS usage descriptions, associated domains, Android app links, and notification permission are present.
- Store metadata drafts already use PulseSoc branding.
- Native notification permission, token registration, token cleanup, deep-link routing, sound, vibration, and high-priority push behavior are wired.

## Current App Config Result

- Name: `PulseSoc`
- Slug: `pulsesoc`
- Scheme: `pulse`
- iOS bundle identifier: `com.pulsesoc.app`
- Android package: `com.pulsesoc.app`
- API base URL: `https://pulsesoc.com`

## Remaining App Store Blockers

- EAS CLI is not logged in locally, so the Expo/EAS project cannot be linked from this workstation yet.
- `extra.eas.projectId` is still empty until the Expo project is created or linked.
- iOS build/upload cannot start until Expo/EAS login is completed and credentials are configured.
- Real-device notification QA still requires a signed build installed on a physical iPhone.
- App Store Connect final metadata, screenshots, privacy answers, review notes, and test account credentials still need to be entered or confirmed in the browser.

## Recommended Next Step

Log in to Expo/EAS locally, then run:

```bash
npx eas-cli project:init
npx eas-cli build --platform ios --profile production
```

After EAS writes the real project ID, rerun:

```bash
npm run typecheck
npm run audit
```

Then install the build through TestFlight/internal distribution and complete real-device push QA before App Store submission.
