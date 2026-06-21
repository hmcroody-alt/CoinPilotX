#!/usr/bin/env python3
"""Audit PulseSoc admin music review workflow."""

from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import music_service  # noqa: E402


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def ensure_admin_and_track() -> tuple[int, int]:
    bot.init_db()
    now = datetime.utcnow().isoformat(timespec="seconds")
    conn = bot.db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT id FROM admin_users WHERE status='active' ORDER BY id LIMIT 1")
    row = cur.fetchone()
    if row:
        admin_id = int(row["id"])
    else:
        cur.execute(
            """
            INSERT INTO admin_users
            (full_name, email, phone, password_hash, role, status, company_role, must_change_password, created_at, updated_at)
            VALUES ('Audit Admin', 'audit-admin@example.com', '', 'audit-only', 'owner', 'active', 'Audit', 0, ?, ?)
            """,
            (now, now),
        )
        admin_id = int(cur.lastrowid)
    cur.execute(
        """
        INSERT INTO pulse_audio_tracks
        (title, artist, uploader_user_id, audio_url, source_type, safety_status, source_provider, license_type,
         commercial_use_allowed, remix_edit_allowed, attribution_required, proof_url, approved_by_admin, active,
         rights_confirmed, rights_confirmed_at, rights_statement, created_at, updated_at)
        VALUES ('Audit Pending Track', 'PulseSoc Audit', 1, '/static/test-audio.mp3', 'artist_upload', 'pending',
                'original_pulse_sound', 'artist rights confirmed upload', 1, 1, 0, 'artist-upload:audit', 0, 0,
                1, ?, 'I confirm that I own this music or have the legal right to upload it.', ?, ?)
        """,
        (now, now, now),
    )
    track_id = int(cur.lastrowid)
    conn.commit()
    conn.close()
    return admin_id, track_id


def main() -> int:
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    require("/admin/pulse-music-review" in source, "Admin Music Review route/link is missing.")
    require("Approve Music" in source and "Reject / Remove" in source, "Review actions are missing.")
    require("verify_csrf()" in source, "Music review POST must verify CSRF.")
    require("insert_admin_audit_with_cursor" in source, "Music review actions must be audited.")

    admin_id, track_id = ensure_admin_and_track()
    client = bot.webhook_app.test_client()
    public = client.get("/admin/pulse-music-review")
    require(public.status_code in {302, 401, 403}, f"Music review was not protected: {public.status_code}")

    with client.session_transaction() as session:
        session["admin_user_id"] = admin_id
        session["csrf_token"] = "audit-csrf-token"

    page = client.get("/admin/dashboard")
    require(page.status_code == 200 and b"Music Review" in page.data, "Admin dashboard does not show Music Review link.")

    review = client.get("/admin/pulse-music-review")
    text = review.get_data(as_text=True)
    require(review.status_code == 200, f"Music review did not render: {review.status_code}")
    require("Audit Pending Track" in text and "Approve Music" in text, "Pending track was not visible in review page.")

    bad_csrf = client.post("/admin/pulse-music-review", data={"track_id": track_id, "action": "approve", "csrf_token": "wrong"})
    require(bad_csrf.status_code == 400, "Invalid CSRF token did not fail.")

    approved = client.post(
        "/admin/pulse-music-review",
        data={"track_id": track_id, "action": "approve", "csrf_token": "audit-csrf-token", "note": "Audit approval."},
        follow_redirects=False,
    )
    require(approved.status_code == 200 and b"Music approved and activated." in approved.data, "Approve action did not succeed.")

    conn = bot.db()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM pulse_audio_tracks WHERE id=?", (track_id,))
    saved = dict(cur.fetchone() or {})
    conn.close()
    require(int(saved.get("approved_by_admin") or 0) == 1, "Approved track was not marked approved.")
    require(int(saved.get("active") or 0) == 1, "Approved track was not activated.")
    require(saved.get("safety_status") == "approved", "Approved track safety status was not updated.")
    require(not music_service.public_visibility_reasons(saved), "Approved track visibility diagnostics rejected a valid track.")

    with client.session_transaction() as session:
        session["account_user_id"] = 1
    public_search = client.get("/api/pulse/music/search?q=Audit%20Pending%20Track&limit=40")
    payload = public_search.get_json(silent=True) or {}
    require(public_search.status_code == 200 and payload.get("ok"), "Public music search did not return a valid response.")
    require(
        any(str(item.get("id")) == str(track_id) for item in payload.get("items") or []),
        "Approved music did not appear in public PulseSoc Music search.",
    )

    print("Pulse music review audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
