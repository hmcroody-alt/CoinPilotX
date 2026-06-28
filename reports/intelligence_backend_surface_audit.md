# Intelligence Backend Surface Audit

Date: 2026-06-27

## Backend Command Center

Backend Command Center now includes an Intelligence Command Center surface:

- `/admin/intelligence-command-center`

Admin-only detail surfaces:

- `/admin/intelligence-command-center/scam-intelligence`
- `/admin/intelligence-command-center/alert-management`
- `/admin/intelligence-command-center/pulse-brain`
- `/admin/intelligence-command-center/ai-advisor`
- `/admin/intelligence-command-center/safety-scanner`
- `/admin/intelligence-command-center/recommendation-engine`
- `/admin/intelligence-command-center/security-operations`
- `/admin/intelligence-command-center/threat-intelligence`
- `/admin/intelligence-command-center/risk-assessment`
- `/admin/intelligence-command-center/trust-intelligence`
- `/admin/intelligence-command-center/signal-intelligence`
- `/admin/intelligence-command-center/research-engine`
- `/admin/intelligence-command-center/feed-intelligence`
- `/admin/intelligence-command-center/prediction-engine`
- `/admin/intelligence-command-center/heatmap-engine`
- `/admin/intelligence-command-center/audit`

## Registry Integration

The backend management registry now includes:

- Required module: `intelligence`
- Operating blueprint: Intelligence Command Center
- Feature registry entries for every Intelligence backend surface

## Admin Surface Behavior

Each backend surface includes:

- Overview
- Health state
- Confidence score
- Evidence summary
- Automation/recovery notes
- Audit posture
- Launch readiness
- Protected links to existing admin tools

## Access Control

Admin routes are protected by `require_admin_page("command_center.view")`. Non-admin users are blocked from backend Intelligence pages.

## Privacy Controls

The admin surface summarizes sensitive systems without exposing:

- Raw tokens
- Secrets
- Private keys
- Database URLs
- Private message bodies
- Private user settings

## Verification

Covered by:

- `scripts/intelligence_loginexus_operating_system_audit.py`
