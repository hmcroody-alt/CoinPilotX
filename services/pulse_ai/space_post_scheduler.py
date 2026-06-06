"""Restart-safe scheduler helpers for autonomous Pulse Space posts."""

from datetime import datetime, timedelta
import json
import logging

from .space_ai_engine import generate_space_post, live_space_slugs
from .space_prompt_builder import DEFAULT_FORMATS
from .space_quality_guard import duplicate_risk
from .space_topic_memory import get_space_memory, update_space_memory

ROTATION_INTERVAL_HOURS = 3
ROTATION_SLOT = "rotation"
ROTATION_STATE_ID = 1


def _now():
    return datetime.utcnow()


def _iso(dt):
    return dt.isoformat(timespec="seconds")


def _row_dict(row):
    if row is None:
        return {}
    if hasattr(row, "keys"):
        return {key: row[key] for key in row.keys()}
    return dict(row)


def _next_three_hour_boundary(base=None):
    base = base or _now()
    candidate = base.replace(minute=0, second=0, microsecond=0)
    hours_to_add = ROTATION_INTERVAL_HOURS - (candidate.hour % ROTATION_INTERVAL_HOURS)
    if hours_to_add == 0 and candidate <= base:
        hours_to_add = ROTATION_INTERVAL_HOURS
    candidate += timedelta(hours=hours_to_add)
    return _iso(candidate)


def _rotation_run_key(base=None):
    base = base or _now()
    slot_start = base.replace(minute=0, second=0, microsecond=0)
    slot_start -= timedelta(hours=slot_start.hour % ROTATION_INTERVAL_HOURS)
    return _iso(slot_start)


def next_rotation_run(base=None):
    base = base or _now()
    candidate = base + timedelta(hours=ROTATION_INTERVAL_HOURS)
    if candidate <= base:
        candidate += timedelta(hours=ROTATION_INTERVAL_HOURS)
    return _iso(candidate)


def _ensure_rotation_state(cur):
    now = _iso(_now())
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pulse_ai_rotation_state (
            id INTEGER PRIMARY KEY,
            next_index INTEGER DEFAULT 0,
            next_run_at TEXT,
            last_run_at TEXT,
            last_run_key TEXT,
            last_space_slug TEXT,
            cycle_count INTEGER DEFAULT 0,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    cur.execute(
        """
        INSERT OR IGNORE INTO pulse_ai_rotation_state
        (id, next_index, next_run_at, created_at, updated_at)
        VALUES (?, 0, ?, ?, ?)
        """,
        (ROTATION_STATE_ID, _next_three_hour_boundary(), now, now),
    )


def _disable_legacy_daily_schedules(cur):
    now = _iso(_now())
    cur.execute(
        """
        UPDATE pulse_ai_schedules
        SET enabled=0, updated_at=?
        WHERE COALESCE(schedule_type, '')='daily'
           OR COALESCE(slot, '') IN ('morning', 'afternoon', 'evening')
        """,
        (now,),
    )


def seed_space_ai_schedules(cur, spaces):
    _ensure_rotation_state(cur)
    _disable_legacy_daily_schedules(cur)


def _rotation_spaces(spaces):
    active_slugs = set(live_space_slugs() or [])
    valid_spaces = [space for space in spaces or [] if space.get("slug")]
    active_spaces = [space for space in valid_spaces if space.get("slug") in active_slugs]
    return active_spaces or valid_spaces


def _rotation_state(cur):
    _ensure_rotation_state(cur)
    cur.execute("SELECT * FROM pulse_ai_rotation_state WHERE id=?", (ROTATION_STATE_ID,))
    return _row_dict(cur.fetchone())


def _already_ran_rotation_key(cur, run_key):
    cur.execute(
        """
        SELECT COUNT(*) AS total
        FROM pulse_ai_posts
        WHERE schedule_slot=? AND metadata_json LIKE ?
        """,
        (ROTATION_SLOT, f'%"rotation_key": "{run_key}"%'),
    )
    row = _row_dict(cur.fetchone())
    return int(row.get("total") or 0) > 0


def _insert_ai_post(cur, generated, status="published", pulse_post_id=0):
    now = _iso(_now())
    cur.execute(
        """
        INSERT INTO pulse_ai_posts
        (space_slug, pulse_post_id, title, body, post_type, topic, tags_json, quality_score,
         topic_score, trust_score, energy_score, sentiment_score, status, metadata_json,
         schedule_slot, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            generated["space_slug"],
            int(pulse_post_id or 0),
            generated["title"],
            generated["body"],
            generated["post_type"],
            generated["topic"],
            json.dumps(generated.get("tags") or [], ensure_ascii=True),
            int(generated.get("quality_score") or 0),
            int(generated.get("topic_score") or 0),
            int(generated.get("trust_score") or 0),
            int(generated.get("energy_score") or 0),
            int(generated.get("sentiment_score") or 0),
            status,
            generated.get("metadata_json") or "{}",
            (generated.get("metadata") or {}).get("schedule_slot") or "",
            now,
            now,
        ),
    )
    return int(cur.lastrowid)


def generate_publishable_post(cur, space, slot):
    memory = get_space_memory(cur, space.get("slug") or "")
    recent = recent_ai_posts(cur, space.get("slug") or "", limit=10)
    last_generated = None
    slot_offset = 0 if slot in {"morning", ROTATION_SLOT} else 4
    for attempt in range(8):
        post_type = DEFAULT_FORMATS[(attempt + slot_offset) % len(DEFAULT_FORMATS)]
        generated = generate_space_post(space, post_type=post_type, memory=memory, schedule_slot=slot, attempt=attempt)
        risk = duplicate_risk(generated, recent)
        generated.setdefault("metadata", {})["duplicate_risk"] = risk
        if generated.get("metadata_json"):
            try:
                payload = json.loads(generated["metadata_json"])
                payload["duplicate_risk"] = risk
                generated["metadata_json"] = json.dumps(payload, ensure_ascii=True)
            except Exception:
                pass
        last_generated = generated
        if generated.get("ok") and not risk.get("duplicate"):
            return generated
    return last_generated or generated


def recent_ai_posts(cur, space_slug, limit=10):
    cur.execute(
        """
        SELECT title, body, topic, metadata_json
        FROM pulse_ai_posts
        WHERE space_slug=? AND status!='rejected'
        ORDER BY id DESC LIMIT ?
        """,
        (space_slug, int(limit or 10)),
    )
    rows = []
    for row in cur.fetchall():
        item = _row_dict(row)
        try:
            item["metadata"] = json.loads(item.get("metadata_json") or "{}")
        except Exception:
            item["metadata"] = {}
        rows.append(item)
    return rows


def publish_space_ai_post(cur, space, slot, pulse_create_post=None, approve_only=False, rotation_meta=None):
    generated = generate_publishable_post(cur, space, slot)
    if rotation_meta:
        metadata = generated.setdefault("metadata", {})
        metadata.update(rotation_meta)
        generated["metadata_json"] = json.dumps(metadata, ensure_ascii=True)
    status = "pending_approval" if approve_only else "published"
    pulse_post_id = 0
    if not generated.get("ok"):
        status = "rejected"
    elif pulse_create_post and not approve_only:
        result = pulse_create_post(
            0,
            generated["body"],
            "text",
            generated["title"],
            tags=generated.get("tags") or [],
            visibility="public",
            media_ids=[],
            enqueue_background=True,
        )
        if result.get("ok"):
            pulse_post_id = int(result.get("post_id") or 0)
        else:
            logging.warning("SPACE_AI_PULSE_POST_FAILED space=%s message=%s", space.get("slug"), result.get("message"))
            status = "queued"
    ai_post_id = _insert_ai_post(cur, generated, status=status, pulse_post_id=pulse_post_id)
    update_space_memory(cur, generated["space_slug"], generated["topic"], generated["hook"], tags=generated.get("tags"))
    return {"ok": status in {"published", "pending_approval", "queued"}, "ai_post_id": ai_post_id, "pulse_post_id": pulse_post_id, "status": status, "post": generated}


def run_due_space_ai_posts(cur, spaces, pulse_create_post=None, force=False, limit=80):
    del limit
    seed_space_ai_schedules(cur, spaces)
    now_dt = _now()
    now = _iso(now_dt)
    rotation_spaces = _rotation_spaces(spaces)
    if not rotation_spaces:
        return {"ok": True, "ran": 0, "results": [], "message": "No Spaces are available for rotation."}

    state = _rotation_state(cur)
    next_run_at = state.get("next_run_at") or _next_three_hour_boundary(now_dt)
    if not force and next_run_at > now:
        return {"ok": True, "ran": 0, "results": [], "next_run_at": next_run_at}

    run_key = _rotation_run_key(now_dt)
    if not force and (state.get("last_run_key") == run_key or _already_ran_rotation_key(cur, run_key)):
        logging.info("SPACE_AI_ROTATION_DUPLICATE_SKIP scheduled_time=%s", run_key)
        return {"ok": True, "ran": 0, "results": [], "skipped": "duplicate_tick", "scheduled_time": run_key}

    current_index = int(state.get("next_index") or 0) % len(rotation_spaces)
    next_index = (current_index + 1) % len(rotation_spaces)
    selected_space = rotation_spaces[current_index]
    next_space = rotation_spaces[next_index]
    slug = selected_space.get("slug") or ""
    next_slug = next_space.get("slug") or ""
    next_run_at = next_rotation_run(now_dt)
    cycle_count = int(state.get("cycle_count") or 0) + (1 if next_index == 0 else 0)

    logging.info(
        "SPACE_AI_ROTATION_SELECTED selected_space=%s scheduled_time=%s rotation_index=%s total_spaces=%s",
        slug,
        run_key,
        current_index,
        len(rotation_spaces),
    )
    result = publish_space_ai_post(
        cur,
        selected_space,
        ROTATION_SLOT,
        pulse_create_post=pulse_create_post,
        approve_only=False,
        rotation_meta={
            "rotation_key": run_key,
            "rotation_index": current_index,
            "rotation_total": len(rotation_spaces),
            "next_space_slug": next_slug,
        },
    )
    logging.info(
        "SPACE_AI_ROTATION_POST_CREATED selected_space=%s scheduled_time=%s ai_post_id=%s pulse_post_id=%s status=%s",
        slug,
        run_key,
        result.get("ai_post_id"),
        result.get("pulse_post_id"),
        result.get("status"),
    )
    cur.execute(
        """
        UPDATE pulse_ai_rotation_state
        SET next_index=?, next_run_at=?, last_run_at=?, last_run_key=?, last_space_slug=?,
            cycle_count=?, updated_at=?
        WHERE id=?
        """,
        (next_index, next_run_at, now, run_key, slug, cycle_count, now, ROTATION_STATE_ID),
    )
    _disable_legacy_daily_schedules(cur)
    logging.info(
        "SPACE_AI_ROTATION_NEXT next_space=%s next_index=%s next_run_at=%s",
        next_slug,
        next_index,
        next_run_at,
    )
    try:
        cur.connection.commit()
    except Exception:
        pass
    return {
        "ok": True,
        "ran": 1,
        "results": [result],
        "rotation": {
            "selected_space": slug,
            "scheduled_time": run_key,
            "next_space": next_slug,
            "next_run_at": next_run_at,
            "cycle_count": cycle_count,
        },
    }
