# PulseSoc Native Home Visible Publish QA

Date: 2026-07-08

## Required Visible Flow

- Open Home.
- Open Pulse Composer.
- Type a real text post.
- Verify character counter updates.
- Refresh page.
- Show draft automatically restored.
- Continue editing.
- Publish.
- Verify composer reset, draft disappearance, publish success, feed refresh, Activity invalidation, Notifications invalidation, and no duplicate publish.

## Browser Setup

- QA backend: disposable local SQLite backend on `127.0.0.1:5107`.
- QA API proxy: local CORS/session proxy on `127.0.0.1:5108`.
- Native web QA build: `localhost:8094`.
- Credentials: runtime-only local QA account. Password was not printed in reports or committed.

## Visible QA Result

Result: blocked before final visible proof.

The built-in QA browser control channel repeatedly timed out when reading the selected tab or navigating. Because of that, this report does not claim that Roody watched the final text publish proof.

The QA web server log also surfaced Metro dependency-resolution errors during the run:

- `Unable to resolve "expo-modules-core" from "node_modules/expo/src/Expo.ts"`
- `Unable to resolve "nullthrows" from "node_modules/react-native-web/dist/vendor/react-native/VirtualizedList/index.js"`

Because `npm ci`, TypeScript, and Expo Doctor passed, this is tracked as a web QA runtime blocker that must be resolved or proven transient before the final visible publish proof.

## Contract Evidence Completed

- Local QA login succeeded.
- Text-only post publish succeeded through `/api/pulse/posts`.
- Feed query returned the newly published post.
- Sync cursor endpoint responded successfully after publish.
- Composer test handles were added so the next visible run can target controls reliably.

## Not Yet Visibly Proven

- Character counter update after typing.
- Draft recovery after page refresh.
- Visible publish button execution.
- Visible composer reset.
- Visible feed refresh.
- Visible Activity and Notifications invalidation.
- Visible retry after server failure.

## Device-Only Items

- Camera permission.
- Microphone permission.
- Native gallery picker.
- Physical photo/video capture.
- Large video upload.
- Background interruption recovery.

## Conclusion

The Home publishing contract is structurally and backend-contract verified, but the Home foundation cannot be marked complete until visible browser publish proof succeeds and the QA web runtime blocker is resolved or proven transient.
