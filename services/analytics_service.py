"""Privacy-conscious product analytics helpers."""

import json
from datetime import datetime

from .user_context import connect


def track_event(event_name, user_id=0, metadata=None, session_id=""):
    conn = connect()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO analytics_events
        (session_id, user_id, event_name, page_url, referrer, device_type, browser, ip_hash, country, metadata, created_at)
        VALUES (?, ?, ?, '', '', '', '', '', '', ?, ?)
        """,
        (session_id, user_id or 0, event_name, json.dumps(metadata or {})[:4000], datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()

