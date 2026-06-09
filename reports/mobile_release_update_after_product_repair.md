# Mobile Release Update After Product Repair

Date: 2026-06-09

- Local iOS build number prepared in manifest: `12`.
- Local Android versionCode prepared in manifest: `12`.
- App version prepared: `0.1.1`.
- Existing local Android artifacts before this pass were v11 in `mobile/pulse-react-native/builds`.
- EAS remote versioning is enabled, so EAS incremented the submitted production builds beyond the local manifest values.

## EAS Build IDs

- Current iOS production/App Store upload from latest `main`: `3e2cfe78-7857-4f76-9651-b0ac9d9317fb`
  - Status: `finished`
  - Version/build: `0.1.1` / build number `22`
  - IPA: `https://expo.dev/artifacts/eas/8FiSNjfY8tsugzuafELb6R.ipa`
  - Local IPA: `/tmp/pulsesoc-ios/pulsesoc-ios-build22-full-repair.ipa`
  - Build page: `https://expo.dev/accounts/hmcroody/projects/pulsesoc/builds/3e2cfe78-7857-4f76-9651-b0ac9d9317fb`
  - EAS Submit: `e0dd8092-596e-464a-a5d0-d1a0de99a739`
  - Upload status: uploaded to App Store Connect; processing by Apple.
- Current Android production AAB from latest `main`:
  - Blocked before queueing because the EAS account has used its Android builds for the free plan this month.
  - EAS reset date shown by CLI: `Wed Jul 01 2026`.
  - Required next action: upgrade/restore Android build quota, then rerun `npx eas-cli build --platform android --profile production --non-interactive --no-wait --message "PulseSoc full repair Android release"`.
- Current Android QA APK from latest `main`:
  - Blocked by the same EAS Android monthly build quota.
  - Required next action after quota is available: rerun `npx eas-cli build --platform android --profile qa-apk --non-interactive --no-wait --message "PulseSoc full repair Android QA APK"`.

Historical product-repair builds created before the latest repair commits:

- iOS production/TestFlight-targeted build: `f75204fb-f46e-4300-8367-ddedef75d06e`
  - Status: `finished`
  - Version/build: `0.1.1` / build number `21`
  - IPA: `https://expo.dev/artifacts/eas/wxk9P7vUMNPopcQzerPbEA.ipa`
  - Build page: `https://expo.dev/accounts/hmcroody/projects/pulsesoc/builds/f75204fb-f46e-4300-8367-ddedef75d06e`
- Android production AAB build: `dd1e3743-747a-4234-9d22-ba5b11dd4789`
  - Status: `in queue` as of the latest check on 2026-06-09
  - Version/versionCode: `0.1.1` / `17`
  - Artifact URL: pending EAS completion
  - Build page: `https://expo.dev/accounts/hmcroody/projects/pulsesoc/builds/dd1e3743-747a-4234-9d22-ba5b11dd4789`
- Android QA APK build: `71c34c21-bb46-4ed3-8531-cf3a9274c1f7`
  - Status: `in queue` as of the latest check on 2026-06-09
  - Version/versionCode: `0.1.1` / `16`
  - Artifact URL: pending EAS completion
  - Build page: `https://expo.dev/accounts/hmcroody/projects/pulsesoc/builds/71c34c21-bb46-4ed3-8531-cf3a9274c1f7`

## EAS Remote Versioning

- Android production versionCode incremented by EAS from `16` to `17`.
- iOS production buildNumber incremented by EAS from `20` to `21`.

## Build Notes

- EAS account: `hmcroody`.
- Android remote keystore was available.
- iOS distribution certificate and provisioning profile were available.
- EAS warned that Firebase config files referenced by `googleServicesFile` are not checked into the repository and should be supplied through EAS file environment variables if the cloud builders need them.

Builds were submitted with:

- `npx eas-cli build --platform ios --profile production`
- `npx eas-cli build --platform android --profile production`
- `npx eas-cli build --platform android --profile qa-apk`

Validation after the follow-up pass:

- `npm run typecheck`
- `npm run audit`
- `npx expo-doctor`
- `npx expo config --type public`

Final Android downloadable artifact paths for the latest `main` are not available because EAS Android build quota blocked queueing. Do not upload older Android artifacts for this full repair pass.
