# Account Dashboard Remaining Blockers

## Remaining Work

- Full secure document viewing/download for verification reviewers should be implemented as a dedicated admin-only streaming endpoint before reviewers need to inspect files directly in-browser.
- Passkeys are represented by the Security Center architecture but are not enabled unless the platform adds WebAuthn support.
- Password-change UX still relies on existing account/security routes and should be expanded with reauthentication when the full password reset module is revisited.
- Advanced Security remains premium-gated. Additional device intelligence can be layered on the existing tables without changing the Dashboard contract.

## Not Blockers For This Release

- Profile management is wired through the existing `/pulse/profile/edit` manager and newly audited backend update endpoints.
- Verification requests, appeals, and private document upload foundation are backend-managed.
- Account Health is server-side and owner-scoped.
- Settings are server-side and validated.
- Admin verification decisions are available through `/admin/account-command`.
