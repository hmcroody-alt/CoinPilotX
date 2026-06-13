# PulseSoc Android Real Device Test Plan

Date: 2026-06-08

## Goal

Install PulseSoc on a real Android phone today and begin validation against the production backend at `https://pulsesoc.com`.

## Fastest Install Path Decision

Fastest path today: **QA APK install from the completed versionCode 11 AAB**.

Google Play Internal Testing remains the preferred long-term path, but it is blocked for CLI upload because the Play service account key is not present locally:

`mobile/pulse-react-native/credentials/google-play-service-account.json`

The completed versionCode 11 store artifact is available and was converted locally into an installable signed QA APK.

## VersionCode 11 Build

- Build ID: `e5bc979b-e09a-4b6c-baac-8c994b395bec`
- Profile: `production`
- Distribution: `store`
- Package: `com.pulsesoc.app`
- Version: `0.1.0`
- Version code: `11`
- Commit: `591ff13465e2aaaa5baff8955d259aa2b7b5be86`
- EAS AAB: `https://expo.dev/artifacts/eas/unoS8dwQwNwgydMAFFLmDb.aab`
- Local AAB: `mobile/pulse-react-native/builds/pulsesoc-android-v11.aab`
- Local signed QA APK: `mobile/pulse-react-native/builds/pulsesoc-android-v11-universal-signed.apk`
- AAB SHA-256: `be0b8e73a4e9551c9c3375a80ddf9c7b38d6857f151f93356d24ed6dd4f7e07f`
- Signed APK SHA-256: `48ce06a225bb4957a000d9231857432ed081f5cb206e6cd28bad46d4717efd0b`

## Google Play Internal Testing Path

Use this when Play Console upload access is ready.

1. Open Google Play Console.
2. Select the PulseSoc app for package `com.pulsesoc.app`.
3. Go to Testing -> Internal testing.
4. Create or open the active internal testing track.
5. Upload the versionCode 11 AAB:
   `mobile/pulse-react-native/builds/pulsesoc-android-v11.aab`
6. Add the tester Google account to the internal tester list.
7. Save changes and roll out the internal testing release.
8. Copy the internal testing opt-in link.
9. On the Android phone, open the opt-in link while signed into the tester Google account.
10. Tap Become a tester.
11. Open the Play Store install link from the opt-in page.
12. Install PulseSoc.

Current blocker: Play upload cannot be automated until the Google Play service account JSON exists at the expected local path or the Play Console is used manually.

## QA APK Install Path

Use this today if Play Internal Testing is not ready.

1. Transfer this file to the Android phone:
   `mobile/pulse-react-native/builds/pulsesoc-android-v11-universal-signed.apk`
2. If PulseSoc is already installed from Google Play or a different signing key, uninstall it first.
3. On Android, open Settings -> Security/Privacy -> Install unknown apps.
4. Allow the file app/browser used for the APK transfer to install unknown apps.
5. Open the APK file.
6. Tap Install.
7. Open PulseSoc.
8. Confirm it loads production PulseSoc at `https://pulsesoc.com`.

Important: this QA APK is signed with a local QA keystore created only for direct device testing. It is not a Play Store release signature. If the Play-installed app is already present, Android may reject the APK until the existing app is uninstalled.

## Install Verification

After launch, verify:

- App name displays as `PulseSoc`.
- Android package is `com.pulsesoc.app`.
- App opens without a blank WebView.
- The visible experience is the production PulseSoc web shell.
- Login uses real production accounts.
- No mock/demo/debug screens appear.

## QA Checklist

### Login

- Open app.
- Log in with a confirmed production test account.
- Confirm redirect lands in PulseSoc.
- Close and reopen app.
- Confirm session persists.
- Log out.
- Confirm logged-out state appears.

### Signup

- Create a new account with display name, username, email, password, country, and age confirmation.
- Confirm app asks for email confirmation.
- Confirm confirmation email arrives.
- Open confirmation link.
- Log in after confirmation.

### Password Reset

- Request password reset from the app.
- Confirm reset email arrives.
- Open reset link.
- Set a new password.
- Log in with new password.
- Confirm old password fails.
- Confirm reset link cannot be reused.

### Feed

- Open Feed.
- Scroll at least 30 posts.
- Confirm images load.
- Confirm videos render and play.
- Confirm reaction/comment/share controls are visible.
- Confirm no huge layout gaps.
- Confirm refresh works.

### Reels

- Open Reels.
- Confirm first Reel plays.
- Swipe through several Reels.
- Confirm active Reel plays and offscreen Reels pause.
- Test sound on/off.
- Confirm no desktop layout panels appear.

### Videos

- Open Videos.
- Confirm video list/grid loads.
- Open a video.
- Confirm Mux/R2 playback works.
- Test landscape and portrait videos if available.

### Messages

- Open Messages.
- Confirm conversation list loads.
- Open a conversation.
- Send a text message.
- Confirm message persists after refresh/reopen.
- Confirm attachments/voice controls are visible.
- Confirm unread indicators update if another account sends a message.

### Notifications

- Open Notifications.
- Confirm inbox loads.
- Mark notification read.
- Delete notification if available.
- Trigger a message/reaction notification from another account.
- Confirm notification appears without manual refresh where possible.

### Profile

- Open Profile.
- Confirm avatar, cover, display name, username, and stats load.
- Navigate away and back.
- Confirm profile data persists.

### Premium

- Open Premium.
- Confirm current user state appears correctly.
- Open Portfolio.
- Open Intelligence.
- Open Alerts.
- Open Saved.
- Open Creator Studio.
- Open Security Center.
- Confirm no dead buttons.

### Push Notifications

- Grant notification permission when prompted.
- Confirm app registers a push token.
- Trigger a test notification.
- Confirm notification appears with sound/vibration if device settings allow.
- Tap notification.
- Confirm deep link opens the correct PulseSoc screen.

### Scrolling Performance

- Test Feed, Reels, Videos, Messages, Notifications, Profile, Premium.
- Confirm scrolling feels smooth.
- Confirm no repeated blank screens.
- Confirm no long freezes.
- Confirm media does not cause jank during scroll.

### Deep Links

Open these on the Android phone:

- `https://pulsesoc.com/pulse`
- `https://pulsesoc.com/pulse/reels`
- `https://pulsesoc.com/pulse/videos`
- `https://pulsesoc.com/pulse/messages-v2`
- `https://pulsesoc.com/pulse/notifications`
- `https://pulsesoc.com/pulse/profile`
- `https://pulsesoc.com/pulse/premium`

Confirm each route opens inside PulseSoc or prompts to open PulseSoc.

## Notes

- A new EAS Android build would currently use remote versionCode `12`, so no new versionCode 11 cloud APK build was started.
- The QA APK was produced from the existing versionCode 11 AAB to keep the test artifact aligned with the requested build.
- Play Internal Testing can be completed later from the same AAB once the Play service account or manual Play Console upload is available.
