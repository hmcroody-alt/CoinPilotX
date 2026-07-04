# PulseSoc Native

Parallel React Native + Expo foundation for the future PulseSoc native app.

This app is intentionally separate from the current production WebView shell. It talks to the existing PulseSoc Railway/backend APIs through `src/api/pulseApi.ts` and should be developed, tested, and released on its own QA track before it replaces any live app surface.

## Scope

- Phase 1 app shell, auth, session restore, push registration, Mission Control, Messenger list, basic chat, Pulse AI chat, Profile, and Settings.
- Phase 2 to Phase 4 screens are represented as roadmap surfaces only. Native Reels, Status, media creation, and LiveKit calls must pass dedicated device QA before release.

## Local commands

```bash
npm install
npm run typecheck
npm run start
```

Set `EXPO_PUBLIC_PULSE_API_BASE_URL` when testing against staging or production. The default is `https://pulsesoc.com`.
