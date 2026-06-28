# System Mission Control QA

## Automated Checks

Planned validation:

- `venv/bin/python -m py_compile bot.py services/system_mission_control.py services/pulse_dashboard_mission_control.py scripts/system_mission_control_audit.py`
- `venv/bin/python scripts/system_mission_control_audit.py`
- `venv/bin/python scripts/pulsesoc_mission_control_dashboard_audit.py`
- `venv/bin/python scripts/dashboard_user_admin_boundary_audit.py`
- `git diff --check`

## QA Coverage

The audit verifies:

- User System Mission Control route loads.
- User subsystem route loads.
- Admin System Mission Control route loads.
- Admin subsystem route loads.
- Non-admin users are blocked from admin routes.
- Dashboard System Status cards use real mission-control routes.
- Public output does not expose internal design names, secrets, raw tokens, database URLs, private keys, or private message bodies.

## Manual Notes

The UI is server-rendered and CSS-first for fast loading. Animation respects reduced-motion preferences and avoids canvas/WebGL loops.
