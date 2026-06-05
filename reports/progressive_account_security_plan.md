# Progressive Account Security Plan

## Principle
Signup stays fast. Users enter Pulse quickly, then improve security over time.

## Fast Signup Fields
- Display name
- Username / handle
- Email or phone
- Password
- Country
- Age confirmation
- Terms/privacy agreement

## Progressive Prompt
Profile and settings now expose “Secure your Pulse account” steps:
- Verify email
- Verify phone
- Enable two-factor authentication
- Add recovery options
- Generate recovery codes

## Routes
- `/pulse/settings/security`
- `/pulse/settings/account`
- `/pulse/settings/privacy`
- `/pulse/settings/devices`
- `/pulse/settings/recovery`

## APIs
- `GET /api/account/security`
- `POST /api/account/verify-email`
- `POST /api/account/verify-phone`
- `POST /api/account/2fa/enable`
- `POST /api/account/2fa/disable`
- `POST /api/account/recovery-codes/generate`
- `GET /api/account/security-events`
- `GET /api/account/trusted-devices`
- `DELETE /api/account/trusted-devices/<id>`
- `POST /api/account/reauthenticate`
