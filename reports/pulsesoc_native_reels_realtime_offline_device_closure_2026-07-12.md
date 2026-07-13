# PulseSoc Native Reels Realtime, Offline, and Device Closure

Date: 2026-07-12

Subsystem: Reels

Disposition: **Active; not frozen**

## Outcome

This mission replaced the native Reels dead-black/raw-error failure path with a cached-first recovery state machine, added shared Reels invalidation to the existing event-sync layer, tightened comment ownership/report actions, completed populated and recovery-state simulator QA across four iPhone widths, and installed a newly signed development build on the connected iPhone 16 Pro.

The Reels subsystem is materially stronger but is not ready to freeze. Controlled multi-user realtime lifecycle proof, the complete comment editing/reply/moderation matrix, analytics verification, and hands-on physical-device gesture/audio/network testing remain open.

## Production failure diagnosis

The reported screen exposed the backend's generic `Reels are temporarily unavailable.` response twice: once in the top error pill and again in the empty-list component. This created a mostly black screen with a large red banner and no useful recovery behavior. The backend route deliberately returns that generic response when its feed assembly raises an exception; the authenticated production exception and trace were not available in this local session, so no unverified database or service root cause is claimed.

The native client now treats the backend failure as a recoverable service state. It does not render raw internal error text, and the tab rail plus Create entry point remain available.

## Implemented changes

- Added cached-first lane snapshots with cache timestamps.
- Added explicit connection states: loading, connecting, ready, cached, offline, server busy, maintenance, rate limited, authentication expired, and empty.
- Added bounded automatic retry delays of 1, 2, 5, and 10 seconds.
- Added a reduced-motion-aware animated galaxy field, reel skeleton, state-specific copy, retry action, and empty-state exploration actions.
- Added sanitized `PULSESOC_REELS_RECOVERY` diagnostics containing endpoint/status/code/lane/cache/retry/platform fields, without tokens or user identifiers.
- Registered Reels with the existing shared event-sync invalidation path and preserved the existing event duplicate suppression.
- Extended comment models with server ownership, edit/delete, moderation, reaction, and reply metadata.
- Added existing-server wrappers for comment edit, delete, and report operations.
- Guarded comment delete by server authorization/current-user ownership; non-owned comments expose Report.
- Added localhost-only deterministic QA fixtures/states. Production navigation and production data remain unchanged.
- Preserved hidden-by-default comments, canonical playback components, the top category rail, Create, and the production WebView implementation.

## Simulator QA

Xcode: 26.6 (17F113)

Runtime: iOS 26.5

Build: Release simulator bundle, unsigned

QA API: local repository backend at `127.0.0.1:5110`; localhost-only fixtures and auto-login

| Width class | Simulator | Populated Reels | Layout / safe area | Result |
| --- | --- | --- | --- | --- |
| Compact | iPhone 17e | Yes | Top rail, video, actions, caption and bottom navigation remain reachable | Pass |
| Standard | iPhone 17 | Yes | Populated reel and comments sheet fit without horizontal clipping | Pass |
| Pro | iPhone 17 Pro | Yes | Populated reel and redesigned offline state verified | Pass |
| Pro Max | iPhone 17 Pro Max | Yes | Video-first layout retains intended hierarchy | Pass |

Additional deterministic states:

- Offline: galaxy recovery, skeleton, friendly explanation, retry, tabs and Create verified.
- Comments: sheet is hidden until opened; Reply, Like, Report, Close and composer verified visually.
- Reduced-motion code path: system preference is read and disables the continuous galaxy loop. Static simulator accessibility toggling was not separately recorded.

Evidence directory: `reports/screenshots/native-reels-realtime-offline-device-closure-2026-07-12/`

- `compact-reel-main.png`
- `standard-reel-main.png`
- `pro-reel-main.png`
- `promax-reel-main.png`
- `pro-offline.png`
- `standard-comments-sheet.png`

## iPhone 16 Pro installation

Device: iPhone 16 Pro

iOS: 18.7.3

Connection: USB, paired and available

Workspace: `mobile-native/ios/PulseSocNative.xcworkspace`

Scheme: `PulseSocNative`

Configuration: Release with development-only bundle/display-name overrides

Development bundle: `com.pulsesoc.nativeapp.dev`

Display name: `PulseSoc Native Dev`

Signing team: `87ZC69AGSR`

Signing: Apple Development, automatic provisioning

Build: Passed

Install: Passed

Launch: Blocked from final confirmation because the iPhone relocked; the install is complete and the app is available to open from the Home Screen

Side-by-side inventory was queried after installation:

- Production: `PulseSoc`, `com.pulsesoc.app`, version 1.0.0 (27)
- Development: `PulseSoc Native Dev`, `com.pulsesoc.nativeapp.dev`, version 0.1.0 (1)

The production WebView app was not uninstalled, overwritten, renamed, or re-signed.

The physical build uses the normal Release API configuration; no localhost fixture, QA auto-login, temporary LAN address, or simulator-only environment value was embedded. The user must unlock the phone and open `PulseSoc Native Dev` to complete launch confirmation. Authentication, Reels backend connectivity, gestures, audio, background/foreground behavior, comments, and recovery transitions still require hands-on testing by the user on the device.

## Realtime lifecycle assessment

| Area | Result | Evidence / limitation |
| --- | --- | --- |
| Shared invalidation | Implemented | Reels events and reel API paths invalidate Reels/activity through `eventSync` |
| Duplicate suppression | Preserved | Existing normalized-event `seen` set prevents repeated dispatch |
| Reconnect refresh | Implemented | Recovery path refreshes canonical server state with bounded backoff |
| Multi-device create/update/delete | Not proven | Requires two authenticated users/devices and server event observation |
| Comment/reply/reaction/live lifecycle | Partial | Existing APIs and invalidation are wired; end-to-end event payload proof remains |

No second queue or competing synchronization authority was introduced.

## Offline authorization matrix

The production WebView/backend remains server-authoritative. The native client does not invent offline authorization or a second mutation queue.

| Action | Offline policy | Current behavior |
| --- | --- | --- |
| Feed viewing | Allowed from cache | Cached-first snapshot with cache age |
| Reaction | Online required | Optimistic UI rolls back on server failure |
| Save | Online required | Optimistic UI rolls back on server failure |
| Follow | Online required | Optimistic UI rolls back on server failure |
| Comment / reply | Online required | Composer remains available; server failure is shown without fake success |
| Report / block / delete | Online required | Never queued; authorization stays server-side |
| Create/upload/live/music actions | Online required except local creator draft behavior already owned by creator subsystem | No new Reels queue added |

## Comment ownership and moderation

Completed:

- Server ownership/capability metadata is represented by the native model.
- Delete uses the canonical comment endpoint and is hidden unless authorized.
- Report uses the canonical Pulse report endpoint for `reel_comment`.
- Edit endpoint wrapper is available for the native surface.

Still open:

- Edit UI and edited-state presentation.
- Nested reply pagination and reply ownership actions.
- Complete moderator/admin action presentation and moderation-result lifecycle.
- Controlled two-user proof for create, edit, delete, report, reaction, and reply.

## Verification

Passed:

- `npm ci --prefix mobile-native --no-audit --no-fund --progress=false`
- `npm run --prefix mobile-native typecheck`
- `EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose` (17/17)
- `scripts/pulsesoc_native_reels_realtime_offline_closure_audit.py`
- `scripts/pulsesoc_native_reels_futuristic_deep_wiring_audit.py`
- `scripts/pulsesoc_native_reels_audit.py`
- `scripts/pulse_reels_playback_audit.py`
- `scripts/pulse_reels_mobile_playback_audit.py`
- `scripts/pulse_reels_music_audit.py`
- `scripts/live_inside_reels_audit.py`
- `scripts/pulsesoc_native_mission_standard_audit.py`
- Release simulator build
- Signed Release physical-device build
- Side-by-side installation inventory
- `git diff --check`

## Completion estimates

These percentages are evidence-based estimates, not release declarations.

| Area | Estimate |
| --- | ---: |
| Overall Reels parity | 79% |
| UI / visual parity | 88% |
| Core interaction parity | 76% |
| Deep backend wiring | 72% |
| Realtime lifecycle proof | 55% |
| Offline / recovery behavior | 72% |
| Loading / error / empty states | 90% |
| Comments ownership / moderation | 64% |
| Replies | 50% |
| Analytics | 44% |
| Audio / playback | 58% |
| Live inside Reels | 65% |
| Simulator coverage | 82% |
| Physical-device coverage | 24% |

## Known limitations and next exact test

Reels stays the active subsystem. The next exact test is a two-user physical-device lifecycle run on the iPhone 16 Pro and one second client:

1. Create a Reel from user A.
2. Observe arrival for user B without manual reload.
3. React, save, follow, comment, and reply from user B.
4. Edit and delete the owned comment/reply from user B; confirm user A cannot delete it.
5. Report the content from user B and verify server-authoritative moderation state.
6. Disconnect/reconnect the iPhone during viewing and one attempted mutation; verify cached-first viewing, rollback/no fake success, one canonical refresh, and no duplicate event.
7. Repeat playback with silent mode, Bluetooth, interruption, background/foreground, and thermal observation.

Reason: this is the smallest remaining test that simultaneously proves realtime propagation, duplicate suppression, ownership boundaries, offline authorization, and actual iPhone playback behavior. Reels should not be frozen until it passes.
