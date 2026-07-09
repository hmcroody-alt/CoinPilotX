# PulseSoc Native Visible QA Runtime

Date: 2026-07-09

## Goal

Restore a stable native web QA runtime so Roody can visibly review Home, Dashboard, Composer, Publishing, Feed, Marketplace, and Messages without Metro dependency failures.

## Runtime Proof

The QA web runtime was started from a clean Metro cache:

```bash
EXPO_PUBLIC_PULSE_API_BASE_URL=http://127.0.0.1:5108 npm run web:qa -- --clear
```

Observed result:

- Metro reported `Web is waiting on http://localhost:8094`.
- `index.ts` bundled successfully.
- No `expo-modules-core` or `nullthrows` resolver failure reproduced.
- `curl -I http://localhost:8094/Login` returned `HTTP/1.1 200 OK`.
- Built-in QA browser opened visibly and rendered Login.
- A disposable local QA account signed in through the visible Login form after local SQLite email verification.
- Authenticated visible route checks passed through `http://127.0.0.1:8094` for Home, Dashboard, Marketplace, and Messages.
- No Metro dependency-resolution errors appeared during the visible route pass.

## Authenticated QA Host Rule

Use `http://127.0.0.1:8094` for authenticated local browser QA when the API proxy is `http://127.0.0.1:5108`.

Using `http://localhost:8094` still proves unauthenticated runtime rendering, but it can lose local session restore because the API cookies belong to the `127.0.0.1` loopback host.

## Visible QA Readiness

Ready for a follow-up visible browser pass across:

- Home
- Dashboard
- Composer
- Publishing
- Feed
- Marketplace
- Messages

## Visible Walkthrough Status

Runtime-level proof completed. Home, Dashboard, Marketplace, and Messages were visibly rendered in an authenticated built-in QA browser session. Full Home walkthrough should resume next from the visible publish proof, because Home remains the only known foundation blocker after this runtime fix.

## Guardrails

- Do not use Chrome Incognito.
- Do not open localhost before `curl` confirms the server is listening.
- Restart Metro with `--clear` after `npm ci` or dependency changes.
- Use `127.0.0.1:8094` for authenticated local QA when the API proxy is `127.0.0.1:5108`.
- If localhost responds but the in-app browser control channel times out, document the browser automation failure separately from Metro/Expo health.
