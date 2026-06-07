# PulseSoc Mobile Foundation Architecture Report

## Scope

The `mobile/` workspace is a React Native + Expo + TypeScript application named PulseSoc. It reuses the existing CoinPilotX backend and does not redesign backend routes, replace the web app, or touch production data.

## App Structure

- `components/`: shared UI scaffolds and theme primitives.
- `screens/`: auth and main PulseSoc surfaces.
- `services/`: API client, auth API wrappers, secure session storage, push notifications, media upload, and environment config.
- `hooks/`: reusable data loading helpers.
- `navigation/`: auth stack, main tabs, profile stack, linking, and route types.
- `store/`: app state foundation using Zustand.
- `assets/`: reserved for Expo-managed images, icons, splash artwork, and media.

## Runtime Flow

1. `App.tsx` restores the secure session.
2. Unauthenticated users enter the auth stack: Splash, Login, Signup, Forgot Password.
3. Authenticated users enter tab navigation: Home Feed, Reels, Messages, Notifications, Marketplace, Profile.
4. Profile owns secondary routes: Settings, Premium, UNDX.
5. Push notification responses and deep links route into the React Navigation tree.

## Backend Contract

The app calls existing CoinPilotX JSON APIs through `services/apiClient.ts`. Authentication uses the existing `/api/mobile/auth/*` endpoints and stores the returned session cookie in Expo SecureStore for native-safe reuse.

## Production Readiness Notes

This is a foundation, not a store-ready release. Before app store submission, add production icons/splash art, EAS project ID, native push credentials, real media pickers, full error states, and device QA across iOS and Android.
