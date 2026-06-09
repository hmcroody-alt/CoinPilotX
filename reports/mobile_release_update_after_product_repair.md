# Mobile Release Update After Product Repair

Date: 2026-06-09

- Local iOS build number prepared in manifest: `12`.
- Local Android versionCode prepared in manifest: `12`.
- App version prepared: `0.1.1`.
- Existing local Android artifacts before this pass were v11 in `mobile/pulse-react-native/builds`.
- EAS remote versioning is enabled, so EAS incremented the submitted production builds beyond the local manifest values.

## EAS Build IDs

- iOS production/TestFlight-targeted build: `f75204fb-f46e-4300-8367-ddedef75d06e`
- Android production AAB build: `dd1e3743-747a-4234-9d22-ba5b11dd4789`
- Android QA APK build: `71c34c21-bb46-4ed3-8531-cf3a9274c1f7`

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

Final downloadable artifact paths are available from the EAS build pages after cloud build completion.
