# Pulse AI Learning Foundation

## Goal
Pulse AI can improve through safe knowledge, feedback, and opt-in memory without secretly learning from private conversations.

## Learning sources allowed
- Approved PulseSoc feature knowledge.
- Public product guidance.
- User feedback such as Helpful, Not helpful, Wrong, Unsafe, and Outdated.
- User-approved memory.
- Admin-reviewed corrections.
- Feature registry updates.
- Aggregated learning events and support-style trends.

## Learning sources prohibited by default
- Private messages.
- Private calls.
- Private media.
- Passwords, tokens, secrets, recovery phrases, or payment data.
- Sensitive account data.
- Cross-user private data.

Private conversation context requires explicit opt-in and is not part of this phase.

## Tables added
- `pulse_ai_knowledge_items`
- `pulse_ai_user_memory`
- `pulse_ai_feedback`
- `pulse_ai_learning_events`
- `pulse_ai_safety_reviews`
- `pulse_ai_feature_registry`
- `pulse_ai_conversation_context_permissions`
- Runtime persistence also adds `pulse_ai_conversations` and `pulse_ai_messages`.

## User settings added
- Allow Pulse AI to remember my preferences.
- Allow Pulse AI to use my Pulse AI chat history.
- Allow Pulse AI to help with my messages when I ask.
- Allow Pulse AI to improve from my feedback.
- Clear Pulse AI memory.
- Export Pulse AI memory.

## Admin Learning Center
Added:
- `/admin/pulse-ai/learning`
- `/api/admin/pulse-ai/learning`
- `/api/admin/pulse-ai/health`

The admin page shows provider presence, feedback trends, approved knowledge, learning events, and safety reviews. It does not expose provider secrets.

## Safety behavior
- Wrong, Unsafe, and Outdated feedback is queued for review.
- Feedback does not retrain or update global knowledge automatically.
- User memory can be cleared and exported.
- User settings are per-user.

## Known limitations
- Embedding/vector search is represented by the schema and retrieval layer foundation; a production vector provider can be connected later.
- Admin approval workflows are visible as data queues but do not yet include full edit/approve UI actions.
- Provider-specific safety classifiers are not activated unless provider credentials are present.

## QA performed
- Static learning audit added: `scripts/pulse_ai_learning_foundation_audit.py`.
- Runtime command results are recorded in the final task response.
