# PulseSoc WebView App Shell Strategy

## Decision

PulseSoc Mobile now uses a native WebView shell that loads the live PulseSoc website instead of rebuilding Feed, Reels, Videos, Messages, Notifications, Profile, and Premium as separate React Native screens.

Default app URL:

- `https://pulsesoc.com`

Preserved app identity:

- App name: `PulseSoc`
- iOS bundle identifier: `com.pulsesoc.app`
- Android package: `com.pulsesoc.app`
- Deep link scheme: `pulse`

## Why

The live website is the source of truth for PulseSoc product experience. A WebView shell avoids visual drift between web, iOS, and Android while still allowing native capabilities that the website cannot provide alone.

## Native Value Layer

The shell keeps these native capabilities:

- Push notification permission and Expo push token collection.
- Push token bridge from website JavaScript to native code.
- Push token registration back to `/api/push/subscribe` through the WebView session cookies.
- Notification tap routing into the WebView route.
- Deep link handling for `pulse://` and PulseSoc HTTPS links.
- External non-PulseSoc links opening in the system browser.
- Camera, microphone, photo library, and Android media permissions.
- Native share bridge through `window.PulseSocNative.share(...)`.
- Offline fallback screen.
- Pull-to-refresh.
- Native splash screen.

## WebView Behavior

Implemented in:

- `/Users/hmcherie/Desktop/CoinPilotX/mobile/pulse-react-native/App.tsx`

Key behaviors:

- Loads `https://pulsesoc.com` by default.
- Allows navigation within `pulsesoc.com` and `www.pulsesoc.com`.
- Converts `pulse://` deep links to matching `https://pulsesoc.com/...` routes.
- Opens non-PulseSoc external links through the system browser.
- Enables JavaScript and DOM storage.
- Enables shared cookies and third-party cookies.
- Allows inline media playback.
- Allows autoplay configuration needed by website media experiences.
- Supports Android hardware back through WebView history.
- Displays a native offline error screen when the website cannot load.

## Push Bridge

Website code can request native push registration with:

```js
window.PulseSocNative.registerPush()
```

Native code requests notification permission, gets the Expo push token, then injects a website-context request:

```txt
POST /api/push/subscribe
credentials: include
device_type: native_webview
```

That keeps entitlement/session alignment in the live PulseSoc web session.

Native code also emits:

```js
window.dispatchEvent(new CustomEvent("PulseSocNativeMessage", { detail }))
```

for push-registration results.

## Native Share Bridge

Website code can request native sharing with:

```js
window.PulseSocNative.share({
  title: "PulseSoc",
  text: "Shared from PulseSoc",
  url: "https://pulsesoc.com/pulse"
})
```

## Validation Plan

Required real-device validation:

- iOS build installs and opens the live website.
- Android build installs and opens the live website.
- Login works.
- Login/session cookies persist after app restart.
- Feed renders exactly as website.
- Reels render exactly as website and media plays.
- Videos render exactly as website and media plays.
- Messages render exactly as website and work.
- Notifications render exactly as website and work.
- Profile renders exactly as website.
- Premium renders exactly as website.
- Deep links open correct web routes inside the shell.
- External links open outside the shell.
- File uploads work for images/videos.
- Camera and photo library prompts work.
- Push token registration works through the bridge.
- Notification tap opens correct route.
- Offline fallback appears when the website is unavailable.

## Current Status

Implemented:

- WebView shell.
- Cookie persistence configuration.
- Internal/external navigation policy.
- Deep link mapping.
- Push bridge.
- Native share bridge.
- Offline fallback.
- Pull-to-refresh.
- Media playback configuration.
- Permission configuration remains in `app.json`.

Pending real-device proof:

- iOS build QA.
- Android build QA.
- Authenticated web session persistence QA.
- Upload/media QA.
- Push bridge live delivery QA.
