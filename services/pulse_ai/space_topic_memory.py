"""Topic memory for autonomous PulseSoc Spaces."""

import json
from datetime import datetime


def _row_dict(row):
    if row is None:
        return {}
    if hasattr(row, "keys"):
        return {key: row[key] for key in row.keys()}
    return dict(row)


def get_space_memory(cur, space_slug):
    cur.execute(
        """
        SELECT memory_json, recent_topics_json, recent_hooks_json, updated_at
        FROM pulse_ai_memory
        WHERE space_slug=?
        LIMIT 1
        """,
        (space_slug,),
    )
    row = _row_dict(cur.fetchone())
    if not row:
        return {"recent_topics": [], "recent_hooks": [], "raw": {}}
    def loads(value):
        try:
            return json.loads(value or "[]")
        except Exception:
            return []
    return {
        "recent_topics": loads(row.get("recent_topics_json")),
        "recent_hooks": loads(row.get("recent_hooks_json")),
        "raw": loads(row.get("memory_json")) if str(row.get("memory_json") or "").startswith("[") else {},
        "updated_at": row.get("updated_at") or "",
    }


def update_space_memory(cur, space_slug, topic, hook, tags=None):
    now = datetime.utcnow().isoformat(timespec="seconds")
    memory = get_space_memory(cur, space_slug)
    recent_topics = [topic] + [item for item in memory.get("recent_topics", []) if item != topic]
    recent_hooks = [hook] + [item for item in memory.get("recent_hooks", []) if item != hook]
    payload = {
        "last_topic": topic,
        "last_hook": hook,
        "last_tags": list(tags or []),
        "updated_at": now,
    }
    cur.execute(
        """
        INSERT INTO pulse_ai_memory
        (space_slug, memory_json, recent_topics_json, recent_hooks_json, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(space_slug) DO UPDATE SET
            memory_json=excluded.memory_json,
            recent_topics_json=excluded.recent_topics_json,
            recent_hooks_json=excluded.recent_hooks_json,
            updated_at=excluded.updated_at
        """,
        (
            space_slug,
            json.dumps(payload, ensure_ascii=True),
            json.dumps(recent_topics[:18], ensure_ascii=True),
            json.dumps(recent_hooks[:18], ensure_ascii=True),
            now,
        ),
    )

