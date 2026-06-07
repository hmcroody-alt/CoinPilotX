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
- EAS login completed as Expo account `hmcroody`.
- EAS project created and linked as `@hmcroody/pulsesoc`.
- EAS project ID is `712c1e38-a984-433f-bce1-f517693bd3fb`.
- Firebase iOS/Android config files are stored as EAS production secret file variables.

## Current App Config Result

- Name: `PulseSoc`
- Slug: `pulsesoc`
- Scheme: `pulse`
- iOS bundle identifier: `com.pulsesoc.app`
- Android package: `com.pulsesoc.app`
- API base URL: `https://pulsesoc.com`
- EAS project ID: `712c1e38-a984-433f-bce1-f517693bd3fb`

## Remaining App Store Blockers

- iOS build requires Apple Developer credential setup in EAS.
- Real-device notification QA still requires a signed build installed on a physical iPhone.
- Android EAS build is in progress and still needs installation on a real Android device after it completes.
- App Store Connect final metadata, screenshots, privacy answers, review notes, and test account credentials still need to be entered or confirmed in the browser.

## Recommended Next Step

Resume iOS signing/build setup:

```bash
npx eas-cli build --platform ios --profile production --no-wait --message "PulseSoc real device push QA"
```

When EAS asks for Apple ID, password, or 2FA, the account owner must enter those directly. After builds are available, install them on real devices and complete the notification QA matrix in `reports/push_real_device_validation.md`.

Before submitting to App Review, rerun:

```bash
npm run typecheck
npm run audit
```
