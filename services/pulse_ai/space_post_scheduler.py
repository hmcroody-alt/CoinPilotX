"""Restart-safe scheduler helpers for autonomous Pulse Space posts."""

from datetime import datetime, timedelta
import json
import logging

from .space_ai_engine import generate_space_post, live_space_slugs
from .space_prompt_builder import DEFAULT_FORMATS
from .space_topic_memory import get_space_memory, update_space_memory

SCHEDULE_SLOTS = (("morning", "09:00"), ("evening", "19:00"))


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


def next_run_for_slot(slot, base=None):
    base = base or _now()
    hour = 9 if slot == "morning" else 19
    candidate = base.replace(hour=hour, minute=0, second=0, microsecond=0)
    if candidate <= base:
        candidate += timedelta(days=1)
    return _iso(candidate)


def seed_space_ai_schedules(cur, spaces):
    now = _iso(_now())
    active_first = live_space_slugs()
    for space in spaces or []:
        slug = space.get("slug") or ""
        if not slug:
            continue
        for slot, run_time in SCHEDULE_SLOTS:
            cur.execute(
                """
                INSERT OR IGNORE INTO pulse_ai_schedules
                (space_slug, schedule_type, slot, local_time, enabled, approve_only, next_run_at, created_at, updated_at)
                VALUES (?, 'daily', ?, ?, 1, 0, ?, ?, ?)
                """,
                (slug, slot, run_time, next_run_for_slot(slot), now, now),
            )
            if slug in active_first:
                cur.execute(
                    "UPDATE pulse_ai_schedules SET enabled=1, updated_at=? WHERE space_slug=? AND slot=?",
                    (now, slug, slot),
                )


def _already_posted_today(cur, space_slug, slot):
    today = _now().strftime("%Y-%m-%d")
    cur.execute(
        """
        SELECT COUNT(*) AS total FROM pulse_ai_posts
        WHERE space_slug=? AND schedule_slot=? AND substr(created_at, 1, 10)=?
        """,
        (space_slug, slot, today),
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
    for attempt in range(6):
        post_type = DEFAULT_FORMATS[(attempt + (0 if slot == "morning" else 4)) % len(DEFAULT_FORMATS)]
        generated = generate_space_post(space, post_type=post_type, memory=memory, schedule_slot=slot, attempt=attempt)
        if generated.get("ok"):
            return generated
    return generated


def publish_space_ai_post(cur, space, slot, pulse_create_post=None, approve_only=False):
    generated = generate_publishable_post(cur, space, slot)
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
    now = _iso(_now())
    spaces_by_slug = {space.get("slug"): space for space in spaces or []}
    cur.execute(
        """
        SELECT * FROM pulse_ai_schedules
        WHERE enabled=1 AND (?=1 OR COALESCE(next_run_at, '') <= ?)
        ORDER BY COALESCE(next_run_at, created_at) ASC
        LIMIT ?
        """,
        (1 if force else 0, now, int(limit or 80)),
    )
    rows = [_row_dict(row) for row in cur.fetchall()]
    results = []
    for schedule in rows:
        slug = schedule.get("space_slug") or ""
        slot = schedule.get("slot") or "morning"
        space = spaces_by_slug.get(slug)
        if not space:
            continue
        if not force and _already_posted_today(cur, slug, slot):
            cur.execute("UPDATE pulse_ai_schedules SET next_run_at=?, updated_at=? WHERE id=?", (next_run_for_slot(slot), now, schedule.get("id")))
            continue
        result = publish_space_ai_post(cur, space, slot, pulse_create_post=pulse_create_post, approve_only=bool(schedule.get("approve_only")))
        cur.execute(
            "UPDATE pulse_ai_schedules SET last_run_at=?, next_run_at=?, updated_at=? WHERE id=?",
            (now, next_run_for_slot(slot), now, schedule.get("id")),
        )
        results.append(result)
    return {"ok": True, "ran": len(results), "results": results}

