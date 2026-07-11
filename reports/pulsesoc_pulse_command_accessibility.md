# PulseSoc Pulse Command Accessibility

Status: improving; not complete.

## Completed This Slice

- Shared conversation accessibility labels now cover title, unread, muted, pinned, and presence state.
- Shared message accessibility labels now cover sender, message preview, and delivery/read state.
- Shared group accessibility labels now cover title, type, role, and member count.
- Shared room accessibility labels now cover room title, active count, unread count, and provider boundary.
- `GroupsScreen` now consumes the shared group/room accessibility labels.

## Still Needed

- VoiceOver walkthrough for long chat threads.
- Context menu focus restoration.
- Message multi-select semantics.
- Attachment media viewer semantics for all message media types.
- Simulator VoiceOver verification for group member and room participant labels.
- Dynamic Type verification in Xcode Simulator.
- Reduced Motion verification in Xcode Simulator.

## Group / Room Detail Slice

- Added shared member accessibility labels with display name, role, presence, and verification state.
- Added shared invitation accessibility labels with state and requested role.
- Added shared room participant accessibility labels with role, presence, and provider state.
- Group and room section rails use selected-state semantics.
- Detail close buttons include surface-specific labels.
