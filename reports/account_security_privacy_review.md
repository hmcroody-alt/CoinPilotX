# Account Security And Privacy Review

## Server-Side Controls

- Dashboard account data is built server-side from the authenticated account.
- Normal users receive only their own profile, settings, verification, security, and health summaries.
- Admin account management requires existing admin permission checks.
- Verification documents are stored as private review assets and are not exposed through public APIs.

## Profile Safety

- Username format is validated.
- Reserved operational usernames are blocked.
- Profile changes are rate-limited through profile audit history.
- Avatar and banner uploads now check file signatures for JPG, PNG, and WEBP instead of trusting extensions only.
- Profile updates, avatar updates, banner updates, and removals create audit records.

## Verification Safety

- Verification status is backend-managed.
- Badge display is tied to approved verification.
- User appeals are accepted only for appealable statuses.
- Document uploads validate size, extension, MIME, and file signature.
- Document storage paths are not returned to normal users.

## Account Health Privacy

- User health pages show only owner-visible summaries.
- Reporter identities and internal moderation notes are not exposed.
- Admin-only health information remains inside admin routes.

## Settings Privacy

- Settings are stored server-side.
- Values are allow-listed before saving.
- Ads personalization and notification settings are separated from security state.

## Sensitive Data Review

The account state payload intentionally excludes:

- passwords and password hashes
- tokens
- private keys
- storage paths
- internal moderation notes
- database URLs
