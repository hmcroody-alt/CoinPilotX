# Verification Center Report

Implemented `/dashboard/account/verification` as a backend-managed PulseSoc trust passport.

- Tracks: identity, creator, business, seller, advertiser, music partner, media partner, organization.
- States: not started, draft, submitted, in review, needs more info, approved, rejected, appealed, revoked, suspended.
- User actions submit real `verification_requests`; unsupported private document upload returns a safe unavailable response instead of pretending storage exists.
- Checklists and progress are derived from account fields, request rows, document rows, and approved review state.
- The public UI does not expose internal design names or private reviewer notes.

Verification-dependent capabilities are shown as unlocked only from approved backend state.
