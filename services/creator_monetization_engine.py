"""Creator monetization foundation without activating real payouts."""

from __future__ import annotations

from datetime import datetime

from . import user_context


def ensure_creator_profile(user_id, public_player_id="", display_name="", call_sign=""):
    conn = user_context.connect()
    cur = conn.cursor()
    now = datetime.utcnow().isoformat(timespec="seconds")
    cur.execute(
        """
        INSERT INTO creator_profiles
        (user_id, public_player_id, display_name, call_sign, verification_status, creator_score, follower_count, monetization_enabled, created_at, updated_at)
        VALUES (?, ?, ?, ?, 'unverified', 0, 0, 0, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            public_player_id=COALESCE(NULLIF(excluded.public_player_id,''), creator_profiles.public_player_id),
            display_name=COALESCE(NULLIF(excluded.display_name,''), creator_profiles.display_name),
            call_sign=COALESCE(NULLIF(excluded.call_sign,''), creator_profiles.call_sign),
            updated_at=excluded.updated_at
        """,
        (int(user_id), public_player_id or "", display_name or "", call_sign or "", now, now),
    )
    conn.commit()
    cur.execute("SELECT * FROM creator_profiles WHERE user_id=? LIMIT 1", (int(user_id),))
    profile = dict(cur.fetchone() or {})
    conn.close()
    return profile


def creator_candidates(limit=20):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT u.user_id, COALESCE(u.roast_call_sign, u.display_name, u.username, 'Arena Pilot') AS display_name,
               COUNT(p.id) AS posts, COALESCE(cp.verification_status,'candidate') AS verification_status
        FROM users u
        LEFT JOIN pulse_posts p ON p.user_id=u.user_id
        LEFT JOIN creator_profiles cp ON cp.user_id=u.user_id
        GROUP BY u.user_id
        ORDER BY posts DESC, u.user_id DESC
        LIMIT ?
        """,
        (int(limit or 20),),
    )
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows
