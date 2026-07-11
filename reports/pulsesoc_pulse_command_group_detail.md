# PulseSoc Pulse Command Group Detail

Status: native detail foundation added; not yet fully complete.

## Group Detail

- `GroupDetail` remains inside the existing authoritative `GroupsScreen` route.
- No `GroupDetail2`, `GroupsV2`, or parallel membership store was introduced.
- The detail surface now opens from existing group cards and the `GroupDetail` route target.
- The native detail includes section navigation for Overview, Members, Invitations, Media, Files, Links, and Settings.

## Reused Contracts

- `/api/pulse/groups`
- `/api/pulse/groups/<slug>`
- `/api/pulse/groups/<slug>/join`
- `/api/pulse/groups/<slug>/leave`
- `/api/pulse/groups/<slug>/chat/open`
- `/api/pulse/groups/<slug>/report`

## Native Depth Added

- Overview metrics for members, posts, current role, notification state, privacy, trust, and status.
- Group rules panel when the server returns rules.
- Community feed rendering from existing group post payloads.
- Native member, invitation, asset, and settings sections.
- Explicit contract-boundary panels when the server does not expose roster, invitation, file, or link indexes.

## Remaining Group Detail Work

- Full member roster endpoint or native access to an existing roster contract.
- Server-authoritative invitation mutation UI once pending invitations/requests are exposed.
- Dedicated media/files/links indexes if production adds them to native contracts.
- Owner/admin edit settings and destructive controls.
