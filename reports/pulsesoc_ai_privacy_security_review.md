# PulseSoc AI Privacy and Security Review

## Boundaries Enforced

- No raw prompts are exposed in dashboard state.
- No private message bodies are exposed.
- No provider credentials are exposed.
- No internal tokens are exposed.
- No database URLs or storage paths are exposed.
- User-facing AI state is owner-scoped.
- Admin AI views are permission-gated through existing admin middleware.

## Safe Defaults

`PULSE_AI_ENABLED=false` remains supported. When provider execution is disabled, the AI command center shows local-safe state and guidance instead of failing or pretending provider calls succeeded.

## Admin Access

Admin command-center pages show aggregate operational metrics only. Sensitive content access is intentionally excluded from the new surfaces.

## Automation Guardrails

Automation is represented as review-gated operational state. Sensitive actions are not executed silently from the dashboard.

## Audit Coverage

Added `scripts/pulsesoc_ai_command_center_audit.py` to validate:

- route registration
- contextual buttons
- owner-scoped state
- admin-only route protection
- forbidden sensitive strings are absent from public AI payloads
- Mission Control PulseSoc AI card routing
