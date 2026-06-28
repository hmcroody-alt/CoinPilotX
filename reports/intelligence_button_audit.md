# Intelligence Button And State Audit

Date: 2026-06-27

## Button Standard

Generic `Open` buttons were removed from the Dashboard Intelligence Center. Each visible Intelligence module now has a contextual action:

| Module | Action |
| --- | --- |
| Scam Shield | Protection Center |
| Scam Alerts | Alert Center |
| Pulse Brain | Open Pulse Brain |
| AI Advisor | Ask AI Advisor |
| Safety Scan | Scan My Account |
| Smart Recommendations | Explore Recommendations |
| Security Intelligence | Review Security |
| Threat Intelligence | Analyze Threats |
| Risk Assessment | Assess Risk |
| Trust Intelligence | Review Trust |
| Signal Intelligence | Analyze Signals |
| Research Workspace | Start Research |
| Feed Intelligence | View Feed Intelligence |
| Prediction Center | View Predictions |
| Pulse Heatmap | Explore Heatmaps |

`Open Pulse Brain` is retained because it is a product-specific action, not a generic fallback.

## State Standard

Allowed Intelligence states:

- READY
- ACTION
- REVIEW
- WARNING
- LOCKED
- PREMIUM
- BETA
- PARTIAL
- COMING SOON
- ADMIN

The Intelligence Center no longer uses misleading `ACTIVE` labels for modules whose state is not actually backed by data or configuration.

## Route Audit

Every user-facing Intelligence action resolves to:

- `/dashboard/intelligence`
- `/dashboard/intelligence/<subsystem_key>`

Every admin Intelligence action resolves to:

- `/admin/intelligence-command-center`
- `/admin/intelligence-command-center/<section_key>`

## User-Facing Naming Boundary

The internal technology name is not rendered in dashboard or admin HTML responses. The product UI uses PulseSoc Intelligence naming only.

## Verification

Covered by:

- `scripts/intelligence_loginexus_operating_system_audit.py`
