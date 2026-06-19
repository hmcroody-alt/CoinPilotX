# Command Center AI Messaging Layer

## Scope

Service 2F adds an optional Pulse AI foundation for messaging and security review. It does not replace existing messaging, does not auto-send replies, and does not require AI for chat delivery.

## Architecture

- Main App remains the owner of authenticated web routes, Communications v2 pages, conversation permissions, and message rendering.
- Command Center Worker exposes protected internal AI endpoints for future intelligent features.
- `services/command_center_client.py` contains passive request helpers that return safe unavailable responses when Command Center or Pulse AI is disabled.
- `services/command_center_worker/ai_messaging.py` owns AI task validation, redaction, disabled-mode responses, provider adapter boundaries, and AI audit logging.

## Configuration

Pulse AI defaults to disabled:

- `PULSE_AI_ENABLED=false`
- `PULSE_AI_PROVIDER=`
- `PULSE_AI_MODEL=`
- `PULSE_AI_INTERNAL_ONLY=true`
- `PULSE_AI_MAX_CONTEXT_MESSAGES=30`

When disabled, every AI method returns an explicit disabled response and messaging continues normally.

## AI Event Model

`command_center_ai_events` stores scrubbed audit records:

- `event_id`
- `user_id`
- `conversation_id`
- `message_id`
- `ai_task_type`
- `input_summary`
- `output_json`
- `status`
- `error_reason`
- `created_at`
- `processed_at`

Task types:

- `chat_summary`
- `smart_replies`
- `scam_explanation`
- `translation_prepare`
- `moderation_insight`

The table stores redacted summaries and provider output metadata, not raw secrets or credentials.

## Internal Endpoints

All endpoints require the existing internal Command Center token:

- `POST /internal/command-center/ai/summary`
- `POST /internal/command-center/ai/smart-replies`
- `POST /internal/command-center/ai/scam-explanation`
- `POST /internal/command-center/ai/moderation-insight`

Without a valid token, endpoints return `401`.

## UI Hooks

Messages:

- `Summarize chat` appears only when Command Center and Pulse AI are enabled.
- `Smart replies` appears only in the same enabled state.
- Disabled mode does not show fake AI output.

Admin Security:

- Command Center risk rows expose an `Explain Risk` action only when AI is enabled.
- Disabled mode shows `AI analysis not enabled`.

## Privacy Protections

- Conversation AI context is built only after the existing Communications v2 participant permission check succeeds.
- Blocked or unauthorized conversations return the existing forbidden responses.
- Message context is bounded by `PULSE_AI_MAX_CONTEXT_MESSAGES`.
- Emails, token-like strings, payment-like numbers, and secret-key payload fields are redacted.
- Internal token, filesystem paths, and database paths are not returned in endpoint responses.
- The provider adapter is a single boundary and remains unavailable by default.

## Scam Shield Integration

Security events from Service 2E can request a scam explanation through `request_scam_explanation`. This is review-only. It does not ban users, delete content, or change account state.

## Future Provider Plan

When a provider is approved:

1. Keep `PULSE_AI_INTERNAL_ONLY=true` until data handling, retention, and consent rules are finalized.
2. Implement the provider inside `_provider_adapter`.
3. Pass only redacted, bounded context.
4. Add provider response validation and per-task rate limits before exposing output broadly.

## QA Results

Validation target:

- `python -m py_compile bot.py services/command_center_client.py services/command_center_worker/app.py services/command_center_worker/ai_messaging.py scripts/command_center_ai_messaging_audit.py`
- `python scripts/command_center_ai_messaging_audit.py`
- Existing Command Center audits for security, notifications, messaging, presence, and worker skeleton.
- `git diff --check`

Result: passed in the current run.

Additional checks passed:

- AI endpoints reject missing internal token.
- Valid internal token receives safe disabled/unavailable responses without a provider.
- Messages and Admin Security routes load without unauthenticated crashes.
- PostgreSQL compatibility audit returned `fail=0 warn=0`.
