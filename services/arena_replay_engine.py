"""Replay and highlight generation for Alpha Arena matches."""

from __future__ import annotations

import json
from datetime import datetime

from . import arena_share_service, user_context


def now_iso():
    return datetime.utcnow().isoformat(timespec="seconds")


def _rows(cur):
    return [dict(row) for row in cur.fetchall()]


def generate_replay(match_id, winner_id=0, base_url="https://coinpilotx.app"):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM arena_matches WHERE id=? LIMIT 1", (int(match_id),))
    match = user_context.row_to_dict(cur.fetchone()) or {}
    cur.execute("SELECT * FROM arena_match_events WHERE match_id=? ORDER BY id ASC LIMIT 80", (int(match_id),))
    events = _rows(cur)
    cur.execute("SELECT * FROM arena_profiles WHERE user_id=? LIMIT 1", (int(winner_id or 0),))
    winner = user_context.row_to_dict(cur.fetchone()) or {}
    title = f"{winner.get('display_name') or 'Arena Pilot'} victory replay"
    highlights = []
    for event in events[-8:]:
        highlights.append({
            "title": event.get("title") or event.get("event_type") or "Arena moment",
            "body": event.get("body") or "",
            "created_at": event.get("created_at") or "",
        })
    if not highlights:
        highlights = [
            {"title": "Victory secured", "body": "Disciplined decisions carried the final result.", "created_at": now_iso()},
            {"title": "Crowd surge", "body": "Spectators reacted as the match closed.", "created_at": now_iso()},
        ]
    summary = "Replay generated with key Arena moments, AI commentary, and share-ready challenge hooks."
    share_card = arena_share_service.share_card(
        winner,
        {
            "type": "rank",
            "title": "Alpha Arena Victory",
            "match_id": int(match_id),
        },
        base_url=base_url,
    )
    replay_payload = {
        "match": match,
        "timeline": events,
        "highlights": highlights,
        "winner": winner,
        "disclaimer": "Alpha Arena is a simulated educational trading environment using virtual dollars. No real-money trading occurs inside Arena matches.",
    }
    created_at = now_iso()
    cur.execute("SELECT id FROM arena_replays WHERE match_id=? LIMIT 1", (int(match_id),))
    existing = cur.fetchone()
    if existing:
        replay_id = int(existing["id"])
        cur.execute(
            """
            UPDATE arena_replays
            SET recap=?, replay_json=?, user_id=?, title=?, summary=?, timeline_json=?, highlights_json=?, share_card_json=?, created_at=?
            WHERE id=?
            """,
            (
                summary,
                json.dumps(replay_payload),
                int(winner_id or 0),
                title,
                summary,
                json.dumps(events),
                json.dumps(highlights),
                json.dumps(share_card),
                created_at,
                replay_id,
            ),
        )
    else:
        cur.execute(
            """
            INSERT INTO arena_replays
            (match_id, recap, replay_json, user_id, title, summary, timeline_json, highlights_json, share_card_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(match_id),
                summary,
                json.dumps(replay_payload),
                int(winner_id or 0),
                title,
                summary,
                json.dumps(events),
                json.dumps(highlights),
                json.dumps(share_card),
                created_at,
            ),
        )
        replay_id = int(cur.lastrowid)
    cur.execute(
        """
        INSERT INTO arena_highlights (match_id, user_id, highlight_type, title, summary, payload_json, created_at)
        VALUES (?, ?, 'victory', ?, ?, ?, ?)
        """,
        (int(match_id), int(winner_id or 0), title, summary, json.dumps({"replay_id": replay_id, "highlights": highlights}), created_at),
    )
    highlight_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return {
        "ok": True,
        "replay_id": replay_id,
        "match_id": int(match_id),
        "title": title,
        "summary": summary,
        "timeline": events,
        "highlights": highlights,
        "share_card": share_card,
        "replay_url": f"/arena/replay/{replay_id}",
        "highlight_url": f"/arena/highlight/{highlight_id}",
    }


def get_replay(replay_id):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("SELECT * FROM arena_replays WHERE id=? OR match_id=? ORDER BY id DESC LIMIT 1", (int(replay_id), int(replay_id)))
    row = user_context.row_to_dict(cur.fetchone())
    conn.close()
    if not row:
        return None
    return row
