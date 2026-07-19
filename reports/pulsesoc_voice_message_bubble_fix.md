# PulseSoc Voice Message Bubble Fix

## Problem

Native Messenger voice recordings were rendering the generated upload filename, for example `pulsesoc-voice-1784432743856.m4a`, as visible message text below the Voice Pulse player. That made the outgoing audio bubble too tall and exposed implementation/file naming details to users.

## Root Cause

The native chat upload path sent `input.name` as the message body for every attachment, including voice recordings. The shared bubble renderer then displayed `message.body` below the voice player.

## Fix

- Voice uploads now send an empty body instead of the generated `.m4a` filename.
- Voice/audio message bubbles suppress body text entirely and show only the Voice Pulse player, metadata row, and delivery state.
- Existing cached/server messages with generated `pulsesoc-voice-*.m4a` values are normalized out of message bodies and conversation previews.
- Action sheets and accessibility previews describe voice/audio messages as `Voice message` before considering raw body text.
- The Voice Pulse card was tightened with smaller padding, controls, waveform count, and minimum width.

## Files Changed

- `mobile-native/src/screens/ChatScreen.tsx`
- `mobile-native/src/api/messenger.ts`
- `mobile-native/src/pulseCommand/domain.ts`
- `scripts/pulsesoc_voice_message_bubble_audit.py`

## QA Result

Static verification confirms:

- voice upload payloads no longer use generated filenames as message bodies
- generated voice filenames are stripped from normalized messages/previews
- voice previews do not expose raw body text
- the native Voice Pulse card uses compact dimensions

Real-device visual QA should verify a newly recorded voice message appears as a compact Voice Pulse bubble with no filename underneath.
