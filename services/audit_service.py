import json
from datetime import datetime

from . import db


def log_admin_action(admin_id, action, target_type="", target_id="", metadata=None, admin_email=""):
    try:
        conn = db.connect()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO admin_audit_logs
            (admin_user_id, admin_email, action, target_type, target_id, metadata, ip_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                admin_id or 0,
                admin_email or "",
                action,
                target_type,
                str(target_id or ""),
                json.dumps(metadata or {}),
                "",
                datetime.now().isoformat(),
            ),
        )
        conn.commit()
        conn.close()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
