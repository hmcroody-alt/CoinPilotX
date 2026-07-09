# PulseSoc Native QA Runtime Stabilization

Date: 2026-07-09

## Scope

This mission stopped Home feature work and focused only on the native QA runtime. No production WebView routes were changed, no Android work was started, and no UI/UX polish was added.

## Root cause assessment

The Home publishing proof was blocked by the QA runtime, not by missing Home functionality. The previous visible QA run ended with built-in browser control timeouts and Metro resolver errors:

- `Unable to resolve "expo-modules-core" from "node_modules/expo/src/Expo.ts"`
- `Unable to resolve "nullthrows" from "node_modules/react-native-web/dist/vendor/react-native/VirtualizedList/index.js"`

Current investigation found:

- `npm ci --prefix mobile-native` restores both packages successfully.
- A clean Expo web run with `--clear` bundles `index.ts` and serves `http://localhost:8094/Login`.
- No existing process was occupying ports `8094`, `8095`, or `5108` before the clean run.
- The packages were present in `package-lock.json` only as transitive dependencies, which left the QA runtime vulnerable to stale Metro graphs or incomplete install/server overlap.
- Authenticated visible QA must use the same loopback host as the local API proxy. `localhost:8094` can render Login, but local session restoration is reliable through `127.0.0.1:8094` when the API base is `http://127.0.0.1:5108`.

Root cause classification:

- Primary: unstable QA startup discipline around Metro cache/server state after dependency reinstall or shutdown.
- Contributing: resolver-critical packages were not direct project dependencies even though Expo and React Native Web import them during web bundling.
- Contributing for authenticated visible QA: loopback host mismatch between app origin and local API session cookies when using `localhost` for the app and `127.0.0.1` for the API proxy.
- Not observed in the clean run: Expo SDK incompatibility, missing Babel config, WebView route conflict, or production backend failure.

## Fix Applied

Added one explicit mobile-native dependency:

- `nullthrows@1.1.1`

This does not change PulseSoc business logic. It pins `nullthrows` at the same version already used by React Native tooling and makes `npm ci` enforce its presence at the project level before Metro starts.

`expo-modules-core` intentionally remains managed by `expo@54.0.35`. Expo Doctor rejects direct installation of `expo-modules-core`, so the root fix is to audit that it is present and resolvable through Expo rather than declaring it directly.

## Required QA Startup Discipline

Use this sequence before visible browser QA:

```bash
npm ci --prefix mobile-native --no-audit --no-fund --progress=false
cd mobile-native
EXPO_PUBLIC_PULSE_API_BASE_URL=http://127.0.0.1:5108 npm run web:qa -- --clear
curl -I http://localhost:8094/Login
```

Only open the built-in QA browser after localhost responds with `200 OK`.

For authenticated local QA, open the app as `http://127.0.0.1:8094`, not `http://localhost:8094`, so the browser and API proxy share the same loopback site.

## Runtime Health

- `npm ci`: passed.
- Metro clean cache web bundle: passed.
- `curl -I http://localhost:8094/Login`: passed with `HTTP/1.1 200 OK`.
- `expo-modules-core` local package resolution through Expo-managed dependencies: passed.
- `nullthrows` local package resolution: passed.
- Built-in QA browser visible Login route: passed with no runtime console errors.
- Built-in QA browser authenticated route rendering through `127.0.0.1:8094`: passed for Home, Dashboard, Marketplace, and Messages.

## Remaining Risk

The built-in QA browser itself must still be exercised after the runtime fix. If the browser-control channel times out again while Metro remains healthy and localhost responds, the blocker is browser automation stability rather than Expo/Metro dependency resolution.

## Can visible QA now be trusted?

YES for runtime startup, web bundling, and authenticated visible QA after the clean startup sequence.

Condition: the QA browser should be opened only after `npm ci`, clean Metro startup, and localhost health verification. Authenticated local QA should use `127.0.0.1:8094` while the API base is `http://127.0.0.1:5108`.

If the built-in browser control channel times out again, treat it as a browser automation blocker and do not relabel it as a Home feature failure.
