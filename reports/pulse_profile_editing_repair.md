# Pulse Profile Editing Repair

Date: 2026-06-03

## Repairs

- Profile editor exposes core safe fields users expect:
  - profile picture
  - cover image
  - display name
  - username/handle
  - bio
  - website/social links
  - expertise tags
  - profile privacy
- Save profile now shows an inline status message in addition to the toast.
- Username changes are validated with a safe character set.
- Duplicate usernames are rejected.
- Updates remain scoped to the authenticated user.

## Not Added

Location and business-specific profile fields were not added because the active users table does not currently expose dedicated location/business profile columns for this page. Those should be added as an explicit schema expansion rather than packed into unrelated fields.

## Audit

`scripts/pulse_profile_edit_audit.py` verifies the page, endpoints, fields, auth checks, and username protection.
