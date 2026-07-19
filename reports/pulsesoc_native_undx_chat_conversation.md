# PulseSoc Native UNDX Chat Conversation

## Mission

Convert UNDX from a standalone command/form surface into a normal native Messenger conversation.

## Production Sources Inspected

- `static/js/pulse_messages_v2.js`
  - Canonical conversation id: `-9001001`
  - Production routes: `/api/pulse-ai/conversation`, `/api/pulse-ai/message`
  - Production behavior: text-first assistant conversation inside Messenger, attachments and voice unsupported for Pulse AI.
- `pulse_communications_v2/routes.py`
  - Backend endpoints for conversation history, message send, settings, feedback, memory clear/export.
- `services/pulse_ai_service.py`
  - Server-authoritative assistant history and response persistence.

## Native Sources Updated

- `mobile-native/src/api/messenger.ts`
  - Added canonical UNDX constants.
  - Added `getPulseAiConversation` and `sendPulseAiMessage` adapters over existing production `/api/pulse-ai` endpoints.
  - Allowed canonical negative conversation id `-9001001` in native conversation normalization.
  - Kept attachment upload blocked for UNDX with a text-first boundary instead of fabricating assistant media support.
- `mobile-native/src/screens/PulseAiScreen.tsx`
  - Removed the command-style screen UI.
  - Converted the tab/shell route into a bridge to the canonical `Chat` route.
- `mobile-native/src/screens/MessengerScreen.tsx`
  - Replaced the temporary `-900001` UNDX conversation with canonical `-9001001`.
  - UNDX row now opens `ChatScreen` directly.
- `mobile-native/src/screens/ChatScreen.tsx`
  - Loads UNDX history through `/api/pulse-ai/conversation`.
  - Sends standard composer text through `/api/pulse-ai/message`.
  - Uses normal message bubbles, list, composer, draft, search/control-sheet entry, and keyboard behavior.
  - Hides audio/video call buttons for UNDX.
  - Disables attachment and voice-message controls with a clear text-first backend boundary.
- `mobile-native/src/components/ConversationControlCenter.tsx`
  - Added assistant-aware control profile.
  - Disabled voice/video call actions, mute/archive/block, and unsupported media actions for UNDX.
  - Preserved local message search/export where safe.
- `mobile-native/src/navigation/AppNavigator.tsx`
  - Normalized the UNDX tab subtitle to `PulseSoc Intelligence`.

## Result

UNDX now behaves as a canonical native Messenger participant instead of a separate command form. The user sees the normal conversation header, message list, composer, and control menu. The implementation reuses the production assistant backend and existing native Messenger presentation.

## Verification

- `venv/bin/python scripts/pulsesoc_native_undx_chat_conversation_audit.py`
- `npm run --prefix mobile-native typecheck`
- `git diff --check`

## Known Verification Limitation

Typecheck is currently blocked before it reaches these files by unrelated merge-conflict markers in `mobile-native/src/components/ReelPlayerCard.tsx`. This mission did not modify that file.

## Simulator QA

Xcode Simulator visual QA remains required for final release confirmation:

- Open Messenger.
- Open UNDX row.
- Confirm ChatScreen opens with `UNDX` header.
- Confirm no old command card, `Ask UNDX` field, or separate `Ask` button appears.
- Send a text prompt.
- Confirm assistant response returns from production `/api/pulse-ai/message`.
- Confirm attachment and microphone controls show the text-first boundary and do not upload.

