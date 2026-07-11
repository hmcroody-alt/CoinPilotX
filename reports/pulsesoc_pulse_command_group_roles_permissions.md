# PulseSoc Pulse Command Group Roles and Permissions

Status: shared presentation-domain rules added; mutation depth remains gated by backend contracts.

## Completed

- Added typed native support for `PulseGroupMember` and `PulseGroupInvitation`.
- Added shared role helpers in `pulseCommand/domain.ts`:
  - `groupMemberRoleLabel`
  - `groupRolePriority`
  - `groupMemberAccessibilityLabel`
  - `groupMemberActionRules`
  - `groupInvitationStateLabel`
  - `groupInvitationAccessibilityLabel`
- Group member rows now render role, presence, verification/trust state, and available action labels when the backend returns members.
- Permissions are derived from current viewer role and `can_manage`; unauthorized admin actions remain hidden or boundary-labeled.

## Server Authority Preserved

- Native does not invent role names.
- Native does not perform local-only promote, demote, remove, approve, reject, or cancel actions.
- Destructive role/member actions are represented only when the shared rules say they are available and still require future server-backed mutation wiring.

## Remaining

- Wire promote/demote/remove to existing backend endpoints if available.
- Add accept/decline/cancel invitation mutations when exposed to native.
- Verify event cursor invalidation for role and invitation mutations.
