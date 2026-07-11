# PulseSoc Pulse Command Groups / Rooms Simulator QA

Status: Xcode Simulator route/detail smoke completed; section-tap traversal needs a follow-up manual pass.

## Simulator Target

- Primary QA target: Xcode iPhone Simulator, iPhone 17 Pro or nearest available Pro device.
- Route target: Pulse Command Groups / Rooms surface.

## Verified By Static and Runtime-Ready Audit

- Group detail opens inside the existing native Groups route.
- Room detail opens before chat/join.
- Provider boundaries are explicit.
- Member, invitation, media, file, link, participant, and activity sections have native empty/boundary states.
- No duplicate group/room surfaces were introduced.

## Screenshot Evidence

- `reports/screenshots/logi-nexus-messenger/groups-rooms/group-room-detail-foundation.png`
  - Authenticated native Groups / Rooms list rendered in Xcode iPhone Simulator.
- `reports/screenshots/logi-nexus-messenger/groups-rooms/room-detail-current-bundle.png`
  - Current-bundle Room Detail foundation rendered in Xcode iPhone Simulator with Overview, Participants, Activity, Provider rail, Open Room action, Report action, metrics, and pinned notice.

These screenshots avoid sensitive real-user data and use local/default QA-safe rooms.

## Runtime Notes

- Local API was healthy at `http://127.0.0.1:5107/health`.
- Metro QA dev-client ran with:
  - `EXPO_PUBLIC_PULSE_API_BASE_URL=http://127.0.0.1:5107`
  - `EXPO_PUBLIC_PULSESOC_QA_AUTO_LOGIN=1`
  - `EXPO_PUBLIC_PULSESOC_QA_MESSENGER_FIXTURES=1`
- The current bundle displayed the known `expo-av` deprecation warning. It did not block the room detail route.
- Synthetic `cliclick` taps could open Room Detail but did not reliably switch the section rail inside the detail overlay. Section implementation is verified by TypeScript and audit; manual touch traversal remains a follow-up QA item.

## Remaining Simulator QA

- Authenticated open of Groups.
- Open populated group detail.
- Switch Overview / Members / Invitations / Media / Files / Links / Settings.
- Open populated room detail.
- Switch Overview / Participants / Activity / Provider.
- Verify back/close behavior and route stability.
