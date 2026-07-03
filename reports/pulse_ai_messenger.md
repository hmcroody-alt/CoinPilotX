# Pulse AI Messenger

Implementation marker: `pulse_ai_messenger`.

## What was added
- Added a real Pulse AI Messenger conversation with persistent user and assistant messages.
- Added a provider fallback router for OpenAI, Claude, Gemini, DeepSeek, and Groq.
- Added authenticated Pulse AI APIs:
  - `GET /api/pulse-ai/conversation`
  - `POST /api/pulse-ai/message`
  - `POST /api/pulse-ai/reset`
  - `GET /api/pulse-ai/status`
  - `GET/PATCH /api/pulse-ai/settings`
  - `POST /api/pulse-ai/feedback`
  - `POST /api/pulse-ai/memory/clear`
  - `GET /api/pulse-ai/memory/export`
- Wired Messenger V3 so Pulse AI appears as a pinned assistant conversation.

## Providers detected
The router checks runtime environment variables without exposing values:
- OpenAI: `OPENAI_API_KEY`
- Claude: `CLAUDE_AI_API` or `ANTHROPIC_API_KEY`
- Gemini: `GEMINI_AI_API`, `Gemini_AI_API`, or `GOOGLE_AI_API_KEY`
- DeepSeek: `DEEPSEEK_AI_API` or `DEEPSEEK_API_KEY`
- Groq: `GROQ_AI_API` or `GROQ_API_KEY`

If all providers are missing or fail, Pulse AI stores a safe assistant error and Messenger remains usable.

## Frontend files changed
- `static/js/pulse_messages_v2.js`
- `static/css/pulse_messages_v2.css`
- `templates/pulse_messages_v2.html`

## Backend files changed
- `services/pulse_ai_service.py`
- `services/pulse_ai_provider_router.py`
- `services/pulse_ai_knowledge.py`
- `pulse_communications_v2/routes.py`
- `migrations/pulse_ai_messenger.sql`

## How Pulse AI appears
- Conversation name: `Pulse AI`
- Subtitle: `Online · Galaxy Assistant`
- It appears as a single normal Messenger conversation row, sorted near the top when pinned.
- The duplicate quick action card, hero widget, active-rail entry, and pinned-card duplicate are intentionally not rendered.
- The normal composer sends text messages to Pulse AI.
- Attachments and voice are safely disabled for Pulse AI until future phases.

## Privacy and safety
- Pulse AI does not read private Messenger threads automatically.
- It only uses the user message, safe PulseSoc knowledge, optional Pulse AI chat history, and opt-in memory.
- Provider errors are logged safely and not exposed as raw provider messages.
- API routes require authentication.
- Users can clear/export Pulse AI memory.

## Known limitations
- Live provider QA depends on actual configured provider credentials in the runtime environment.
- Multimodal image/audio input is intentionally not wired in this phase.
- Pulse AI does not summarize other private chats unless a separate explicit user action is added.

## QA performed
- Static implementation audit added: `scripts/pulse_ai_messenger_audit.py`.
- Runtime command results are recorded in the final task response.
