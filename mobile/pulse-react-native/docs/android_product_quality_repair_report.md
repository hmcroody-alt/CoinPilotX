# PulseSoc Android Product Quality Repair

Date: 2026-06-07

## Root Cause

The Android build was launching the retired `src/App.tsx` demo shell instead of the production mobile navigator. That shell rendered API preview cards, placeholder-style profile and premium surfaces, no real video/reels UI, and no explicit tab icon setup.

A second issue was that the production navigator existed but was not mounted from the app root. TypeScript also only covered `src/**`, so the production folders were not fully protected by compile checks.

## Fixes Applied

### App Boot and Navigation

- Replaced the root app entry with the production `AppNavigator`.
- Mounted the real authenticated main stack and tab navigator.
- Added `Videos` as a first-class mobile tab.
- Preserved the app name `PulseSoc` and package identifier `com.pulsesoc.app`.
- Kept notification deep-link wiring and push registration in the production root.

### Bottom Navigation Icons

- Added `@expo/vector-icons`.
- Added SDK-compatible `expo-font`.
- Registered the `expo-font` plugin.
- Wired stable `MaterialCommunityIcons` for Feed, Reels, Videos, Messages, Notifications, and Profile.
- Verified the Android export bundled icon font assets, including `MaterialCommunityIcons.ttf`.

### Feed Polish

- Feed remains backed by `/api/pulse/feed`.
- Post cards now use formatted relative timestamps instead of raw dates.
- Author fallback avatars show initials instead of empty debug boxes.
- Cards retain author, body/title, media preview, reactions, comments, repost/share/edit/delete actions, pull-to-refresh, pagination, loading, empty, and error states.
- AI/space-style posts render through the same polished post card surface with title/body/tags/category-ready metadata rather than raw debug cards.

### Reels

- Replaced API preview cards with a full-screen vertical Reels experience.
- Active reel autoplays and offscreen reels pause.
- Added muted sound toggle.
- Added creator info, caption/title, description, engagement actions, loading, processing, failed, and empty states.
- Mux playback is supported through the shared media resolver.

### Videos

- Added a playable Videos tab backed by `/api/pulse/videos`.
- Supports thumbnail/poster display, `expo-video` playback, Mux HLS URL resolution, duration labels, creator info, processing/failed states, and engagement chips.

### Messages

- Removed the normal-user API preview screen.
- Routed the Messages tab to the Communications V2 production surface.
- Keeps conversation filters, latest message previews, unread counts, presence, read receipts, typing heartbeat, composer shell, and offline queue handling.

### Notifications

- Removed the normal-user API preview screen.
- Routed the Notifications tab to the production notification card surface.
- Keeps preview/original context, open target, mark read, delete, and quick reply support.

### Profile

- Removed placeholder profile rendering.
- Profile now loads `/api/pulse/profile/me`.
- Displays logged-in user name, username/email, avatar or initials, bio, posts/followers/following counts, premium/founder status, settings, UNDX, premium link, and logout.

### Premium

- Removed empty endpoint placeholder page.
- Premium now loads `/api/account/status`.
- Displays current plan, active/free state, founder status/number when present, benefits, and upgrade/manage billing action.

### Compile and Audit Coverage

- Expanded `tsconfig.json` so production folders are checked, not only `src/**`.
- Updated existing feed and notification audits to check the production app root.
- Added `scripts/android-ui-quality-audit.js`.
- Added the Android UI audit to `npm run audit`.

## Validation

- `npm run typecheck`: passed
- `npm run audit`: passed
- `npx expo-doctor`: passed
- `npx expo config --type public`: passed, confirmed `PulseSoc` and `com.pulsesoc.app`
- `npx expo export --platform android`: passed
- `git diff --check`: passed

## Android Production Build

- EAS build ID: `12fd51d7-a904-441f-b40d-00a00dcfb02e`
- Build profile: `production`
- Platform: Android
- Version code: `6`
- Status: failed
- Failure: AndroidX `activity` / `core` dependencies require `compileSdkVersion 36`; the app was compiling against 35.
- Fix: raised Android `compileSdkVersion` to `36` while keeping `targetSdkVersion` at `35`.

Corrected production build:

- EAS build ID: `8728f6f1-c11b-4117-b040-578f98a03265`
- Build profile: `production`
- Platform: Android
- Version code: `7`
- Status: finished
- Artifact: `https://expo.dev/artifacts/eas/hG2BEMUuXB8Fv4JPVkireX.aab`

Installable real-device smoke build:

- EAS build ID: `38928041-7e61-4ddf-b794-715b91b3d237`
- Build profile: `preview`
- Platform: Android
- Status: in progress

## Remaining QA Requirement

The code and production build pipeline are repaired, but final completion still requires installing the preview Android build on a real device and confirming:

- no broken bottom navigation icons
- no raw timestamps
- no placeholder profile data
- no diagnostic/API preview screens in normal tabs
- feed cards look polished
- reels are full-screen
- videos are playable
- messages look like a real messaging product
- profile uses the logged-in account
- premium uses real account status
