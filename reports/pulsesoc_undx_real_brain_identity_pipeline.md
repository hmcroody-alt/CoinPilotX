# PulseSoc UNDX Real Brain Identity Pipeline

Date: 2026-07-19

## Objective

Connect the native UNDX Messenger conversation to the production assistant backend with a canonical UNDX identity. The fix preserves the existing `/api/pulse-ai/*` route names, database tables, message persistence, feedback, safety, provider routing, web search, and conversation history contracts while changing the server-owned assistant identity from the legacy `Pulse AI` persona to `UNDX`.

## Root Causes

| Defect | Root cause | Fix |
| --- | --- | --- |
| UNDX did not recognize itself | `services/pulse_ai_knowledge.py` built the provider system prompt as `You are Pulse AI`. | Replaced the core identity with server-owned `UNDX`, PulseSOC AGI-class digital intelligence companion instructions. |
| Assistant replied `I am Pulse AI` | `services/pulse_ai_service.py` returned `Pulse AI` in conversation title, sender identity, greeting, status, feedback, and fallback copy. | Added canonical UNDX constants and changed user-facing payload/persistence fields to UNDX. |
| Generic fallback identity leaked | `services/pulse_ai_provider_router.py` returned `Pulse AI is temporarily unavailable` when providers were missing or failed. | Changed provider-unavailable fallback to `UNDX is temporarily unavailable`. |
| Native route was ambiguous | Native posted to `/api/pulse-ai/message` with only body/client message id. | Native now includes canonical `agent_id=undx`, `assistant_id=undx`, `conversation_type=undx_intelligence`, participant id, and conversation id. |
| Provider could still drift | Provider output was stored directly as assistant text. | Added server-side `_enforce_undx_reply_identity` before persistence and response payloads. |

## End-to-End Trace

| Stage | File/service | Current input | Current output | Expected UNDX behavior | Resolution |
| --- | --- | --- | --- | --- | --- |
| 1. User taps Send | `mobile-native/src/screens/ChatScreen.tsx` | Composer body in canonical `ChatScreen` for conversation `-9001001` | `sendPulseAiMessage` call | Use standard Messenger UI and UNDX conversation | Already in canonical ChatScreen from prior mission. |
| 2. Native request builder | `mobile-native/src/api/messenger.ts` | Body and `client_message_id` | POST `/api/pulse-ai/message` | Include canonical UNDX metadata | Added `agent_id`, `assistant_id`, `participant_id`, `conversation_type`, and `identity`. |
| 3. Authenticated route | `pulse_communications_v2/routes.py` | Authenticated PulseSoc user | `pulse_ai_service.send_message(user_id, payload)` | Reuse production route and session | Preserved. |
| 4. Conversation lookup | `services/pulse_ai_service.py` | Authenticated user id | One per-user `pulse_ai_conversations` row | Preserve existing conversation history | Preserved DB contract; new rows store UNDX metadata/title. |
| 5. Prompt/context assembly | `services/pulse_ai_knowledge.py` | Body, history, approved knowledge, user memory | Provider messages | Server-enforced UNDX identity and PulseSOC context | Core prompt now says `You are UNDX`; service inserts a second anti-drift identity instruction. |
| 6. Provider routing | `services/pulse_ai_provider_router.py` | Prompt messages and task | Provider reply or unavailable result | Never fallback to Pulse AI identity | Fallback copy changed to UNDX. |
| 7. Response enforcement | `services/pulse_ai_service.py` | Provider reply | Persisted assistant message | Store canonical UNDX response | `_enforce_undx_reply_identity` normalizes legacy identity and identity-question failures. |
| 8. Message persistence | `services/pulse_ai_service.py` | Assistant body | `pulse_ai_messages` row | Canonical message id/conversation id with UNDX sender | Preserved table/ids; message payload sender is UNDX. |
| 9. Native render | `mobile-native/src/api/messenger.ts`, `ChatScreen.tsx` | Conversation/messages payload | Normal Messenger bubbles | Header/row/bubble sender show UNDX | Existing native normalization preserved. |

## Production Integration

- Production route reused: `/api/pulse-ai/message`
- Conversation route reused: `/api/pulse-ai/conversation`
- Canonical conversation id: `-9001001`
- Canonical assistant participant id: `-9001001`
- Canonical agent id: `undx`
- Canonical assistant id: `undx`
- Canonical conversation type: `undx_intelligence`
- Message persistence: `pulse_ai_messages`
- Conversation persistence: `pulse_ai_conversations`
- Provider routing: `services/pulse_ai_provider_router.py`
- Prompt and knowledge: `services/pulse_ai_knowledge.py`
- Safety classifier/refusals: `services/pulse_ai_safety.py`
- Web search context: `services/pulse_ai_web_search.py`

## Security And Fallback Policy

- Server-side identity no longer depends on native copy.
- System prompt and provider metadata are not exposed to users.
- Provider failures return `UNDX is temporarily unavailable. Please try again soon.`
- The backend preserves the user message and returns retryable error payloads through the same production route.
- No provider credentials, hidden prompts, internal provider errors, or unrelated private user data are returned.

## QA Status

| Check | Status | Evidence |
| --- | --- | --- |
| Backend identity audit | PASS | `venv/bin/python scripts/pulsesoc_undx_identity_backend_audit.py` |
| Native UNDX chat audit | PASS | `venv/bin/python scripts/pulsesoc_native_undx_chat_conversation_audit.py` |
| Python compile | PASS | `python -m py_compile services/pulse_ai_service.py services/pulse_ai_knowledge.py services/pulse_ai_provider_router.py scripts/pulsesoc_undx_identity_backend_audit.py` |
| Native typecheck | PASS | `npm run --prefix mobile-native typecheck` |
| Dependency install | PASS | `npm ci --prefix mobile-native --no-audit --no-fund --progress=false` |
| Expo Doctor | PASS | `cd mobile-native && EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose` |
| Xcode iPhone Simulator build | PASS | `npm run --prefix mobile-native ios -- --device "PulseSoc iPhone 16 Pro"` exited successfully. |
| Xcode iPhone Simulator visual prompt-response | BLOCKED | Debug build initially displayed `No script URL provided`; Metro was started on port 8082, but the final screenshot/relaunch step was blocked by the environment escalated-action usage limit. Screenshot path: `reports/screenshots/native-undx-real-brain-identity-2026-07-19/simulator-after-undx-identity-build.png`. |
| Physical iPhone | BLOCKED | The requested physical-device continuation requires additional escalated Xcode/devicectl execution, which is blocked by the current escalated-action usage limit. Prior UNDX mission proved physical build/install/launch, but this exact identity response was not manually verified on device. |

## Verification Matrix

| Requirement | Result |
| --- | --- |
| `Who is UNDX?` identity path | Code-path PASS; live response requires authenticated manual send |
| `Are you UNDX?` identity path | Code-path PASS; live response requires authenticated manual send |
| `Are you Pulse AI?` identity path | Code-path PASS; server prompt and post-processing prevent Pulse AI self-identification |
| Conversation id preserved | YES |
| UNDX participant id preserved | YES |
| Message IDs canonical | YES, server persists through `pulse_ai_messages` |
| Duplicate conversation created | NO code path found |
| Generic fallback removed | YES for production provider fallback and service errors |
| Cross-client history | Server contract preserved; WebView/native both use `/api/pulse-ai/*` |

## Remaining Manual Proof

- Open native Messenger in Xcode iPhone Simulator.
- Tap UNDX.
- Send:
  - `Who is UNDX?`
  - `Are you UNDX?`
  - `Are you Pulse AI?`
- Confirm response identity, persistence after relaunch, and no duplicate responses.
- Repeat on physical iPhone for release evidence.
