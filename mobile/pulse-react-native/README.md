# Pulse React Native Foundation

This is a foundation scaffold only. It is not an App Store or Play Store production build.

## Scope

- Reuses existing Pulse APIs.
- Provides native-safe auth/session structure through `/api/mobile/auth/*`.
- Provides Feed, Reels, Videos, Messages, Notifications, and Profile navigation.
- Registers native push tokens through the existing push subscription endpoint.
- Supports `pulse://`, `https://pulsesoc.com`, and `https://coinpilotx.app` deep-link prefixes.

## Environment

Set `EXPO_PUBLIC_PULSE_API_BASE_URL` for non-production API targets.

```sh
EXPO_PUBLIC_PULSE_API_BASE_URL=https://pulsesoc.com npm start
```

## Next Steps

- Confirm native cookie/session persistence on iOS and Android before production mobile QA.
- Add media players after web video/reels stabilization is fully verified.
- Configure Firebase/APNs only after notification delivery is verified end to end.
