# PulseSoc Pulse Command Room Detail

Status: native room detail foundation added.

## Room Detail

- Rooms still use the existing `/api/pulse/communications/rooms` list and `/api/pulse/messages/rooms/<room_id>/join` join/open path.
- Tapping a room now opens a native `RoomDetail` layer instead of immediately entering chat.
- The detail surface includes Overview, Participants, Activity, and Provider sections.

## Provider Boundary Handling

- `roomProviderStateLabel` is now centralized in the Pulse Command domain.
- Provider-boundary rooms do not show a fake successful Join/Open action.
- Simulator-verifiable states are separated from physical-device-only provider checks.

## Native Sections

- Overview: active count, unread count, privacy, provider state, pinned notice.
- Participants: renders authoritative participant roster when returned; otherwise shows live-presence boundary.
- Activity: renders authoritative room activity/assets when returned; otherwise shows persistent activity boundary.
- Provider: documents provider, room type, current role, conversation availability, and device-only checks.

## Remaining

- Full provider-backed participant roster.
- Room activity feed if production exposes persistent room events.
- Physical-device validation for microphone, camera, Bluetooth, background audio, and multi-participant media.
