# Verification Backend Management QA

Implemented backend management surfaces:

- `/admin/command-center/account/verification`
- `/admin/verification`
- `/admin/verification/badges`
- `/admin/verification/appeals`
- `/api/admin/verification/action`

Admin actions supported:

- approve
- reject
- request more info
- suspend
- revoke
- restore

All decisions route through role checks and write verification audit logs.

QA command:

```bash
venv/bin/python scripts/verification_center_audit.py
```
