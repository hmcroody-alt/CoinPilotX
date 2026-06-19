# Command Center Security And Scam Shield Engine

Date: 2026-06-19

## Architecture

Service 2E adds a Command Center detection layer. The main PulseSoc app keeps authentication, content creation, messaging, and admin controls. The worker accepts internal security events, scores them, stores reviewable records, and exposes protected lookup endpoints for risk summaries.

This phase does not auto-ban users, auto-delete messages, or block content. It flags events for moderation review.

## Event Model

New worker table:

- `command_center_security_events`
- `event_id`
- `user_id`
- `actor_id`
- `event_type`
- `severity`
- `score`
- `payload_json`
- `status`
- `created_at`

Prepared trust table:

- `user_trust_score`
- `user_id`
- `trust_score`
- `risk_score`
- `risk_level`
- `updated_at`

## Event Types

Supported event types:

- `login_failed`
- `login_bruteforce`
- `suspicious_signup`
- `mass_dm`
- `spam_post`
- `phishing_link`
- `scam_keyword`
- `rapid_following`
- `rapid_commenting`
- `unusual_device`
- `unusual_country`
- `account_takeover_risk`

## Scoring Model

Risk levels:

- `0-24`: Low
- `25-49`: Medium
- `50-74`: High
- `75-100`: Critical

The worker scores events using:

- base score per event type
- burst count
- recipient count
- repeat count
- suspicious link flags
- scam keyword hits
- unusual country/device flags
- existing blocked/challenge signals

## Link And Spam Detection

The worker scans post, comment, and message text payloads when provided by an event source. It flags:

- URL shorteners
- wallet verification and phishing path patterns
- suspicious domain shapes
- scam phrases such as seed phrase, recovery phrase, private key, guaranteed profit, and wallet verification
- repeated content
- mass-recipient activity
- rapid activity bursts

Flagged content remains intact for review.

## Moderation Workflow

The `/admin/security` page continues to show failed-login controls and now includes a Command Center risk signal panel when the worker is enabled. Admins retain reversible actions:

- Block IP
- Block Domain
- Mark Safe
- Investigate

These actions remain manual review controls. No automatic ban or deletion was added.

## Main App Integration

Failed login events dispatch asynchronously to the Command Center worker only when `COMMAND_CENTER_ENABLED=true`. If the worker is disabled or unavailable, login behavior continues through the existing local failed-login controls.

The main app now sends the worker-compatible `Authorization: Bearer` internal token header while preserving the existing internal-token header for backward compatibility.

## Security Notes

- Worker endpoints require the internal token.
- Payloads are sanitized recursively.
- Secret-like payload keys are removed before storage.
- Full emails are not sent to the worker; failed-login dispatch uses masked email and domain context.
- Endpoint responses do not expose tokens, filesystem paths, database URLs, or raw payload secrets.
- User trust score is stored for future internal use and is not exposed publicly.

## Future AI Integration

Future phases can add AI-assisted classification for ambiguous content, but the current implementation is deterministic, testable, and explainable. AI should only enrich review context and should not make irreversible enforcement decisions without moderation approval.

## QA Results

Validation commands:

- `python -m py_compile bot.py services/command_center_client.py services/command_center_worker/app.py services/command_center_worker/security_engine.py scripts/command_center_security_audit.py`
- `python scripts/command_center_security_audit.py`

Covered checks:

- scoring works
- events persist
- risk levels are correct
- protected endpoints reject missing token
- valid token accepts security events
- dashboard source includes risk panel and moderation controls
- disabled-worker main-app path is safe
- no secrets are exposed in endpoint responses
