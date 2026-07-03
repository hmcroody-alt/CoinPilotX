# Pulse AI Intelligence Upgrade

## What was added
- Expanded Pulse AI from a basic Messenger assistant into a structured PulseSoc guide with retrieval-backed feature knowledge, cybersecurity safety, optional live web search, task-aware provider routing, and admin diagnostics.
- Added machine-readable PulseSoc product knowledge, cybersecurity guidance, and feature registry data.
- Added safety-first handling for harmful cyber requests before provider calls.
- Added live web search service for fresh/current questions with provider detection, timeout limits, caching, and graceful fallback.
- Added provider, web search, and safety event logs for admin observability.

## Files changed
- `data/pulse_ai/pulsesoc_knowledge.json`
- `data/pulse_ai/cybersecurity_knowledge.json`
- `data/pulse_ai/pulsesoc_feature_map.json`
- `services/pulse_ai_knowledge.py`
- `services/pulse_ai_safety.py`
- `services/pulse_ai_web_search.py`
- `services/pulse_ai_router.py`
- `services/pulse_ai_provider_router.py`
- `services/pulse_ai_service.py`
- `services/db.py`
- `migrations/pulse_ai_messenger.sql`
- `templates/admin_pulse_ai_learning_center.html`
- `scripts/pulse_ai_intelligence_upgrade_audit.py`
- `reports/pulse_ai_intelligence_upgrade.md`

## PulseSoc feature coverage
The structured knowledge base now covers:
- Home feed
- Reels
- Status / Stories
- Messenger
- Audio calls
- Video calls
- Pulse AI chat
- PulseSoc Music
- Notifications
- Crypto alerts
- Market alerts
- Intelligence Streams
- Manage My Alerts
- Conversation Control Center
- Profile
- Privacy settings
- Security settings
- Creator tools
- Premium / Founder access
- Verification badges
- Live streaming
- Search
- Trending content
- Communities / groups / rooms
- Reporting users
- Blocking users
- Account recovery
- Password reset
- App Store availability
- Mobile PWA behavior
- Push notifications
- Lock-screen notifications

Each feature stores summary, location, usage guidance, common questions, troubleshooting, related features, and safety notes.

## Cybersecurity coverage
Pulse AI now has defensive cybersecurity knowledge for:
- Password safety
- Phishing prevention
- Scam detection
- Two-factor authentication
- Device security
- Public Wi-Fi risks
- Malware prevention
- Social engineering awareness
- Crypto wallet safety
- Fake investment scams
- Romance scams
- SIM swapping
- Email security
- Browser safety
- App permissions
- Data privacy through safety prompts
- Incident response basics
- Small business cybersecurity
- WordPress security basics
- Secure backups
- Update hygiene

Supported response modes:
- Beginner Safety
- Account Protection
- Scam Shield
- Small Business Security
- Incident Response
- Learning Mode

The safety layer refuses requests for hacking accounts, credential theft, phishing kits, malware creation, MFA bypass, detection evasion, exploit weaponization, doxxing, and crypto theft. It redirects users to defensive guidance.

## Web search behavior
`services/pulse_ai_web_search.py` decides when live search is needed. It searches only when the user asks for current/latest/recent information or the query is time-sensitive, such as crypto prices, market news, breaking events, cybersecurity advisories, App Store status, regulations, or vulnerabilities.

Supported provider readiness:
- Brave Search through `BRAVE_SEARCH_API_KEY`
- Bing Search through `BING_SEARCH_API_KEY` or `BING_SEARCH_V7_SUBSCRIPTION_KEY`
- SerpAPI through `SERPAPI_API_KEY`
- Tavily through `TAVILY_API_KEY`
- DuckDuckGo Instant Answer fallback without secrets

Search uses short timeouts, cache, safe snippets, and source-quality hints. If live search fails, Pulse AI tells the user it could not reach live sources and continues with general guidance.

## Provider router behavior
The existing provider router remains the single provider path. It now supports task-aware ordering:
- General help: configured default/fallback order
- Cyber/security: Claude, OpenAI, Gemini, DeepSeek, Groq preference
- Technical/coding: DeepSeek, OpenAI, Claude, Gemini, Groq preference
- Web/current questions: OpenAI, Gemini, Claude, Groq, DeepSeek preference
- Fast fallback: Groq first when explicitly classified fast

If one provider fails, the router falls back to the next configured provider. Raw provider errors are logged safely and not shown to users.

## Memory and privacy rules
- Pulse AI does not automatically scan private conversations.
- Pulse AI uses current Pulse AI chat history only when the user setting allows it.
- User memory remains opt-in through existing settings.
- Clear memory updates stored memory to deleted.
- Export memory returns only the current user's memory.
- Sensitive strings are redacted before storage where possible.
- Private chats, calls, private media, payment data, passwords, tokens, and secrets are not used for hidden training.

## Safety rules
- Harmful cyber requests are blocked before provider calls.
- Safety events are logged without storing secrets.
- Defensive cybersecurity education remains allowed.
- Admin diagnostics show counts and trends, not secrets.

## Speed strategy
- Basic PulseSoc help uses local JSON and DB retrieval.
- Web search is skipped unless needed.
- Search has a short timeout and in-process cache.
- Prompts include only retrieved relevant knowledge, not the full knowledge base.
- Messenger failure paths return safe responses without crashing normal chat.

## Admin visibility
The existing Pulse AI Learning Center now shows:
- Provider readiness
- Web search status
- Safety modes
- Web search count
- Provider event count
- Safety event count
- Provider event trends
- Web search usage
- Safety event trends
- Knowledge items
- Feedback and safety review queue

## QA results
- `venv/bin/python -m py_compile services/pulse_ai_service.py services/pulse_ai_provider_router.py services/pulse_ai_knowledge.py services/pulse_ai_safety.py services/pulse_ai_web_search.py services/pulse_ai_router.py services/db.py scripts/pulse_ai_intelligence_upgrade_audit.py scripts/pulse_ai_messenger_audit.py`
- `venv/bin/python -m py_compile bot.py services/*.py scripts/pulse_ai_intelligence_upgrade_audit.py`
- `node --check static/js/pulse_messages_v2.js`
- `venv/bin/python scripts/pulse_ai_intelligence_upgrade_audit.py`
- `venv/bin/python scripts/pulse_ai_messenger_audit.py`
- `curl -fsS http://127.0.0.1:5069/health`
- Provider status smoke: local shell reports no AI provider keys configured and no secrets exposed.
- Web search status smoke: Brave/Bing/SerpAPI/Tavily are config-missing locally; DuckDuckGo Instant Answer fallback is available.
- Routing smoke:
  - "How do I create a Status?" classifies as `pulsesoc_help` without web search.
  - "What are the latest cybersecurity alerts?" classifies as `web_search`.
  - Harmful "hack account / bypass MFA" request classifies as blocked.
- Safety path smoke: `pulse_ai_service.send_message(...)` returned a safe refusal from `pulse_ai_safety` without calling external AI providers.

## Remaining limitations
- Live web search quality depends on configured provider credentials or the DuckDuckGo fallback.
- Responses are not streamed yet.
- Admin knowledge editing UI is still limited to existing dashboard visibility; direct admin CRUD can be added next.
- No vector database is required yet; retrieval uses indexed JSON plus DB keyword matching for speed and reliability.
- Web search citations are provided as compact source context to the provider, not rendered as a separate citation UI in Messenger yet.
