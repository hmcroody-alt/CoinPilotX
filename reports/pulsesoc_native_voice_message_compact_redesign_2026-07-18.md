# PulseSoc Native Voice Message Compact Redesign

Date: 2026-07-18

## Outcome

The canonical native Messenger row now renders voice/audio attachments as one compact horizontal player inside the existing incoming/outgoing bubble. The internal filename, redundant `VOICE PULSE` header, duplicate duration, and per-message security subtitle are absent. Production message, attachment, URL, duration, waveform, delivery, and read identifiers remain unchanged.

## Root causes

| Defect | Exact cause | Correction |
| --- | --- | --- |
| Oversized bubble | A second decorated player card, heading, duration header, current/total row, and security subtitle were nested inside the normal message bubble. | Kept the canonical message bubble and replaced the nested card with one 44-point horizontal control row. |
| Filename visibility | Voice recording upload originally assigned the generated `.m4a` name to `body`; legacy records can carry the same body. | New sends use an empty body; normalizers suppress standalone audio filenames, URLs, paths, and storage-key strings without mutating records. |
| Duplicate metadata | Duration appeared in the card header and timeline while timestamp/delivery rendered in the parent bubble. | Duration renders once beside the waveform; the existing authoritative timestamp/delivery row remains once. |
| Legacy fallback | Native only normalized direct duration/URL fields and one generated filename pattern. | Normalization now reads attachment duration, waveform, playback URL, and attachment ID while suppressing broader technical legacy bodies. |
| Playback rerenders | Every mounted voice row owned an `Audio.Sound` and its own status callback. | A shared coordinator owns one sound and notifies only the active message ID's listeners. |

## Field mapping

| Backend field | Native normalized field | Previous usage | User-visible | Required behavior |
| --- | --- | --- | --- | --- |
| `message_id` / `id` | `message_id` | Row identity | No | Preserved unchanged. |
| `attachment_id` / attachment `id` | `attachment_id` | Implicit attachment | No | Preserved for canonical identity. |
| `url` / `cdn_url` / `playback_url` | `media_url` | Playback source | No | Used internally by shared player. |
| `duration_seconds` / `duration` | `duration_seconds` | Direct message only | Yes, formatted | Reads message or attachment and renders once as `m:ss`. |
| `waveform` / `waveform_json` | `waveform` | Ignored natively | Visualization | Normalized to 0–1; deterministic lightweight fallback only when absent. |
| `body` / `content` / `text` | `body` | Could expose filename | Caption only | Technical filename/path/URL is suppressed; a real caption remains eligible. |
| `storage_key` / `object_key` | Internal attachment metadata | Not intentionally rendered | Never | Never used as visible fallback. |
| `created_at` | `created_at` | Outer metadata | Yes | Existing timestamp remains authoritative. |
| `delivery_status` / `seen_at` | delivery/read state | Outer metadata | Outgoing only | Existing Sent/Delivered/Read mapping preserved. |

## Implementation inspection

| Area | Existing file/component | Defect | Change | Reused production logic | QA |
| --- | --- | --- | --- | --- | --- |
| Bubble | `ChatScreen.MessageBubble` | Nested visual layers | One compact player row inside existing bubble | Incoming/outgoing alignment and palette | Audit + simulator |
| Player | `VoiceMessageCard` | Duplicate labels and local sound owner | Memoized row bound to shared coordinator | Expo AV and canonical media URL | Audit + runtime |
| Normalizer | `api/messenger.normalizeMessages` | Incomplete legacy mapping | Attachment duration/waveform/ID normalization | Communications V2 payload | Audit |
| Playback | `core/voiceMessagePlayback` | One player per row | Single active sound, localized subscribers, seek/rate/replay/error | Expo AV; Pulse Radio pause policy | Audit + runtime |
| Calls | `CallScreen` | Voice playback could overlap call entry | Stop playback as dedicated call UI opens | Existing call route | Audit |
| WebView | `static/js/pulse_messages_v2.js` | Compatibility reference | No change | Existing seek/speed/audio element | Source inspection |
| Backend | `pulse_communications_v2/service.py` | Compatibility reference | No change | Canonical attachment IDs, duration, waveform | Source inspection |

## Interaction and accessibility

- Play/pause/replay use one shared `Audio.Sound`.
- Tapping the waveform seeks; VoiceOver adjustable actions move by five seconds.
- Speed cycles through `1x`, `1.5x`, and `2x` and announces the result.
- Loading preserves dimensions; errors show a compact retry action without exposing URLs or backend text.
- The play target remains 44 points; speed receives expanded hit slop.
- A semantic summary includes sender, voice duration, timestamp, and delivery state; controls remain separately focusable.
- App backgrounding, row unmount, and call entry release playback. Starting playback pauses Pulse Radio.

## Production compatibility

- Upload flow remains `/api/messages/media/init` → upload → complete → Communications V2 send with `attachment_ids`.
- WebView playback markup and backend attachment serialization were inspected and not changed.
- No database migration, message mutation, identity change, duplicate audio schema, or native-only attachment record was introduced.
- Native uses the same canonical message/attachment IDs and media URL fields as WebView.

## Verification status

### Passed

- `npm run --prefix mobile-native typecheck`
- `EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose` (`17/17` checks)
- `python3 scripts/pulsesoc_voice_message_bubble_audit.py`
- `python3 scripts/pulsesoc_native_voice_message_audit.py`
- `python3 scripts/pulsesoc_native_messenger_compact_inbox_audit.py`
- `git diff --check`
- Release Xcode Simulator build for iPhone 16 Pro (`PulseSocNative.xcworkspace`, `PulseSocNative`, `** BUILD SUCCEEDED **`)
- Release app installation and cold launch on iPhone 16 Pro and iPhone 16 Pro Max simulators
- Apple Development arm64 device build (`** BUILD SUCCEEDED **`)
- Side-by-side physical installation as `com.pulsesoc.nativeapp.dev` / `PulseSoc Native Dev`
- Physical iPhone 16 Pro launch and direct canonical conversation deep-link; the process remained alive after the route opened

### Evidence and limits

- Simulator builds were installed and cold-launched on both target sizes; the authenticated voice-bubble surface was not captured because the clean Release simulator required a real account session.
- The clean simulator installation correctly required authentication, so no private credentials were copied into it and no authenticated voice-message screenshot is claimed.
- The connected device retained the development bundle's session and received the canonical conversation payload URL. Installation and route stability are tool-verified; an automated physical screenshot or audio-output assertion is not available through `devicectl`.
- The connected device app inventory contained the development build but did not expose a separately installed App Store PulseSoc bundle to `devicectl`; the distinct development identity makes replacement impossible, but presence of a production installation is not claimed.
- Real cross-client playback, Bluetooth/headset routing, interruption behavior, Dynamic Type/Increased Contrast interaction, memory profiling, measured startup latency, and audible physical playback still require controlled human interaction. They are not inferred from source inspection.

## Release judgment

The normalization, compact rendering, playback coordinator, accessibility semantics, and cleanup changes are suitable for internal beta. Full production release remains gated on an authenticated simulator visual pass plus audible physical-device play/pause/seek/speed, interruption, Bluetooth/headset, WebView-to-native, and native-to-WebView playback checks.
