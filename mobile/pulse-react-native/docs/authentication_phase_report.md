# PulseSoc Mobile Authentication Phase Report

## API Endpoints Used

- `GET /api/mobile/auth/session`
- `POST /api/mobile/auth/refresh`
- `POST /api/mobile/auth/login`
- `POST /api/mobile/auth/register`
- `POST /api/mobile/auth/resend-confirmation`
- `POST /api/mobile/auth/confirmation-status`
- `POST /api/mobile/auth/confirm-email`
- `POST /api/mobile/auth/recover`
- `POST /api/mobile/auth/reset-password`
- `POST /api/mobile/auth/logout`
- `GET /api/pulse/profile/me`
- `GET /api/account/status`
- `GET /api/pulse/notifications/unread-count`

## Session Architecture

The mobile app uses the existing CoinPilotX/PulseSoc server session model. Session cookies are captured by the API client and persisted with Expo SecureStore. Refresh-token storage is present but only populated if the backend returns a refresh token; the current production backend refresh behavior is session validation through `/api/mobile/auth/refresh`.

Logout calls the backend logout endpoint and clears all SecureStore session material. Passwords are never persisted and no auth tokens are logged.

## Deep Link Architecture

The app supports `pulse://`, `https://pulsesoc.com`, and `https://coinpilotx.app` prefixes.

- `pulse://verify-email/:token` routes to the confirmation pending screen and calls the mobile confirmation API.
- `pulse://reset-password/:token` routes to the reset password screen and calls the mobile reset API.
- Future notification links continue to route through the existing React Navigation linking foundation.

## Implemented UX States

- Loading states on login, signup, resend confirmation, confirmation refresh, forgot password, and password reset.
- Error states for API failures, validation failures, unconfirmed accounts, invalid links, and offline detection.
- Empty/pending states for confirmation waiting and profile bootstrap.
- Offline state on the authenticated Home Feed shell.

## Validation Results

- TypeScript: `npm run typecheck`
- Foundation audit: `npm run audit:foundation`
- Authentication audit: `npm run audit:authentication`
- Expo launch: verified Metro reaches `Waiting on http://localhost:8099`

Manual live account tests are environment-dependent and should be completed with non-production test accounts only:

- New account signup and confirmation email delivery.
- Existing login.
- Logout.
- Session persistence after app restart.
- Password reset email and deep link.
- Email confirmation deep link.
- Unconfirmed user login path.

## Known Issues

- The backend uses cookie sessions as the source of truth. Refresh-token storage is implemented client-side and backend-compatible, but the current backend does not issue separate refresh tokens.
- Production email confirmation and reset links must be configured to open `pulse://` links or universal links for full native handoff. The mobile app also supports the live HTTPS prefixes.
- The authenticated Home Feed is intentionally a Phase 2 auth/bootstrap shell. Feed, Reels, Messaging, Marketplace, and Notifications product work is deferred to Phase 3.
