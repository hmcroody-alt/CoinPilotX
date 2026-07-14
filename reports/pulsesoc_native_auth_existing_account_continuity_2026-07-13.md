# PulseSoc native authentication and existing-account continuity

Date: 2026-07-13

Decision: **NOT READY TO FREEZE**

## Outcome established

Native authentication reuses the production PulseSoc backend and canonical `users.user_id`. It does not contain a native user table, password database, mirrored profile, or email-only identity mapping. The production login route loads an existing user, assigns that exact `user_id` to the production session, and returns the same ID to native.

The native app now also provides:

- device-bound iOS Keychain storage for the production session cookies
- one automatic production refresh/rotation attempt after an authenticated API request expires
- safe cached-identity startup while offline, without caching a password
- password-recovery and email-verification entry points for existing accounts
- pending PulseSoc deep-link restoration after authentication
- push-device registration after a canonical session is established
- local logout and an explicitly destructive logout-all-devices control
- copy that directs current WebView users to sign in with their existing PulseSoc account

Production route smoke: `GET https://pulsesoc.com/api/mobile/auth/session` returned HTTP 200 with the expected unauthenticated canonical session envelope. No production credentials were used.

## Identity invariants

| Invariant | Result | Evidence |
| --- | --- | --- |
| Same production login service | PASSED | Native calls `/api/mobile/auth/login` |
| Canonical production user ID | PASSED (contract) | Backend loads `users` and returns `user_id`; native rejects zero/missing IDs |
| No separate native user DB | PASSED | No native identity schema or local password store |
| No forced account recreation | PASSED | Login path never calls registration |
| Existing data remains keyed to same user | PASSED (contract) | Native profile/feed/messages/status/reels/settings APIs use the authenticated production session |
| Exact WebView/native data reconciliation | BLOCKED | Controlled production validation accounts were not provided |
| No duplicate user/profile under real login | BLOCKED (runtime proof) | Requires controlled production DB/account observation |

## Authentication method matrix

| Method | Result | Notes |
| --- | --- | --- |
| Existing email/password | READY FOR CONTROLLED QA | Production route supports it |
| Existing username/password | READY FOR CONTROLLED QA | Production route supports it |
| Existing phone/password or OTP | BLOCKED | Current production mobile login does not resolve phone identifiers |
| Sign in with Apple | BLOCKED | No verified production/native Apple auth contract found |
| Google/social login | BLOCKED | No verified production/native social auth contract found |
| Password reset | IMPLEMENTED; DELIVERY QA BLOCKED | Native uses generic non-enumerating recovery response; email delivery needs a controlled account |
| Email verification resend | IMPLEMENTED; DELIVERY QA BLOCKED | Native reuses the production confirmation route |
| Phone verification | BLOCKED | No production native phone-verification login flow proven |
| Two-factor authentication | CRITICAL BLOCKER | Production has a `two_factor_enabled` flag but mobile login does not enforce or complete a second-factor challenge |

Unsupported methods are not shown as fake native buttons.

## Session and device behavior

| Behavior | Result | Notes |
| --- | --- | --- |
| iOS Keychain storage | PASSED (implementation) | Session cookies use `expo-secure-store` with `AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY` |
| Refresh rotation | PASSED (implementation) | Backend hashes refresh tokens, rotates families, detects reuse and device mismatch |
| Automatic refresh | PASSED (implementation) | One refresh and one replay maximum; failed refresh clears local session credential |
| Session expiration | READY FOR CONTROLLED QA | Backend returns `session_expired`; runtime expiry still needs a controlled token |
| Offline cached-session startup | PASSED (implementation) | Safe user metadata may restore UI only when a Keychain session credential exists |
| Logout | PASSED (implementation) | Current refresh session is revoked and local credentials are cleared |
| Logout all devices | PASSED (implementation) | Explicit confirmation calls the production revocation route |
| Account switching | BLOCKED | No account-switcher UX exists |
| Push registration after login | PASSED (implementation); DEVICE QA BLOCKED | Runs after canonical sign-in; permission/token/backend result needs unlocked-device QA |
| Deep-link restoration | PASSED (implementation); RUNTIME QA BLOCKED | PulseSoc links are held until signed in, then routed |
| WebView/native simultaneous sessions | PASSED (server design); RUNTIME QA BLOCKED | Login creates an independent mobile security session and does not revoke other sessions |

## App Store WebView-to-native update path

Chosen policy: **PATH B — SECURE REAUTHENTICATION** unless a later signed update proves a shared, secure credential container already exists.

The existing WebView cookie store must not be scraped or copied into native Keychain storage. If the completed native build replaces the WebView app, it should ask the user to sign in again, then load the exact same canonical production account. This avoids insecure cookie extraction and does not lose server-side account data.

The side-by-side development app has a different bundle identifier and therefore must not be treated as evidence that App Store session transfer works.

## Required controlled-account matrix

No credentials, database snapshots, or approved controlled production accounts were available for the mandatory matrix. These rows remain BLOCKED:

- normal existing user
- long-standing user with posts and messages
- verified user
- premium/founder user
- user with Statuses and Reels
- user with groups and communities
- user with notification preferences
- user with two-factor authentication
- user requiring email verification
- user requiring password reset
- suspended account
- restricted account
- blocked account
- deleted/deactivated account
- expired session
- multiple-device session

## Required reconciliation test

For every approved controlled account, record only safe metadata and prove:

1. WebView and native return the exact same canonical `user_id`.
2. Profile, follower/following counts, posts, Statuses, Reels, messages, groups, communities, subscription, and verification state agree.
3. A safe setting changed in native appears in WebView.
4. A safe setting changed in WebView appears in native.
5. Logging out of native leaves WebView valid.
6. Logging back into native restores the same canonical account.

Do not create substitute production users merely to make this matrix green.

## Verification

- `venv/bin/python scripts/pulsesoc_native_auth_continuity_audit.py` — PASSED
- `npm run --prefix mobile-native typecheck` — PASSED
- Embedded Release simulator build and standalone launch — PASSED
- Signed iPhone development build and side-by-side reinstall — PASSED
- Signed iPhone launch — BLOCKED because the connected phone remained locked
- Production unauthenticated session-envelope smoke — PASSED
- Secrets committed — NONE
- Screenshot: `reports/screenshots/native-auth-existing-account-continuity-2026-07-13/login-existing-account.png`

## Next exact authentication test

First, implement and prove the production two-factor challenge contract for existing 2FA users. Then run the canonical-ID reconciliation with one approved long-standing production test account in WebView and native simultaneously. Phone and social methods should remain absent until their existing production contracts are identified and verified.
