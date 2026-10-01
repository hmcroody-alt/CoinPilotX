#!/usr/bin/env python3
"""Audit owner-safe edit/delete contracts for PulseSoc content."""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


def expect(ok: bool, label: str, details: str = ""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def ensure_user(prefix: str) -> int:
    user_id = 981000 + int(time.time() * 1000) % 100000
    conn = bot.db()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT OR IGNORE INTO users
        (user_id, username, display_name, email, signup_time, onboarding_complete)
        VALUES (?, ?, ?, ?, ?, 1)
        """,
        (
            user_id,
            f"{prefix}_{user_id}",
            f"{prefix.replace('_', ' ').title()}",
            f"{prefix}-{user_id}@example.test",
            bot.datetime.utcnow().isoformat(timespec="seconds"),
        ),
    )
    conn.commit()
    conn.close()
    return user_id


def set_session_user(client, user_id: int) -> None:
    with client.session_transaction() as sess:
        sess["account_user_id"] = int(user_id)


def main():
    bot.init_db()
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    home_js = (ROOT / "static/js/pulse_home_core.js").read_text(encoding="utf-8")

    for token in [
        '@webhook_app.route("/api/pulse/status/<int:status_id>", methods=["PATCH", "DELETE"])',
        "You can only manage your own PulseSoc Status.",
        '"can_edit": is_owner',
        '"can_delete": is_owner',
        "pulse_status_updated",
        "pulse_status_deleted",
        '@webhook_app.route("/api/pulse/reels/<int:reel_id>", methods=["PATCH", "DELETE"])',
        '@webhook_app.route("/api/pulse/videos/<int:video_id>", methods=["PATCH", "DELETE"])',
        '@webhook_app.route("/api/pulse/posts/<int:post_id>", methods=["GET", "PATCH", "DELETE"])',
    ]:
        expect(token in source, f"management source contains {token}")

    for token in ["data-edit-post", "Edit", "PATCH", "Post updated."]:
        expect(token in home_js + source, f"post edit UI contains {token}")

    owner_id = ensure_user("pulse_manage_owner")
    intruder_id = ensure_user("pulse_manage_intruder")
    client = bot.webhook_app.test_client()
    set_session_user(client, owner_id)

    status_create = client.post(
        "/api/pulse/status",
        json={"status_type": "text", "body": "Original status body", "visibility": "public"},
    )
    status_payload = status_create.get_json() or {}
    expect(status_create.status_code == 200 and status_payload.get("status_id"), "owner can create status", status_create.get_data(as_text=True)[:400])
    status_id = int(status_payload["status_id"])
    expect((status_payload.get("status") or {}).get("can_edit") is True, "owner status payload exposes edit control")
    expect((status_payload.get("status") or {}).get("can_delete") is True, "owner status payload exposes delete control")

    set_session_user(client, intruder_id)
    intruder_patch = client.patch(f"/api/pulse/status/{status_id}", json={"body": "Intruder edit"})
    expect(intruder_patch.status_code == 403, "non-owner cannot edit status", intruder_patch.get_data(as_text=True)[:400])
    intruder_delete = client.delete(f"/api/pulse/status/{status_id}")
    expect(intruder_delete.status_code == 403, "non-owner cannot delete status", intruder_delete.get_data(as_text=True)[:400])

    set_session_user(client, owner_id)
    owner_patch = client.patch(f"/api/pulse/status/{status_id}", json={"body": "Updated status body", "visibility": "followers"})
    owner_patch_payload = owner_patch.get_json() or {}
    expect(owner_patch.status_code == 200 and owner_patch_payload.get("ok"), "owner can edit status", owner_patch.get_data(as_text=True)[:400])
    expect((owner_patch_payload.get("status") or {}).get("body") == "Updated status body", "edited status body returns in payload")
    expect((owner_patch_payload.get("status") or {}).get("visibility") == "followers", "edited status visibility returns in payload")

    owner_delete = client.delete(f"/api/pulse/status/{status_id}")
    expect(owner_delete.status_code == 200 and (owner_delete.get_json() or {}).get("ok"), "owner can delete status", owner_delete.get_data(as_text=True)[:400])
    missing_after_delete = client.post(f"/api/pulse/status/{status_id}/view", json={})
    expect(missing_after_delete.status_code == 404, "deleted status is hidden from viewer API")

    post_create = client.post("/api/pulse/posts", json={"body": "Original post body", "title": "Original title", "post_type": "text", "visibility": "public"})
    post_payload = post_create.get_json() or {}
    post_id = int(post_payload.get("post_id") or (post_payload.get("post") or {}).get("id") or 0)
    expect(post_create.status_code in {200, 201} and post_id, "owner can create post for management audit", post_create.get_data(as_text=True)[:500])

    set_session_user(client, intruder_id)
    post_intruder = client.patch(f"/api/pulse/posts/{post_id}", json={"body": "Intruder post edit", "title": "Bad"})
    expect(post_intruder.status_code == 403, "non-owner cannot edit post", post_intruder.get_data(as_text=True)[:400])

    set_session_user(client, owner_id)
    post_patch = client.patch(f"/api/pulse/posts/{post_id}", json={"body": "Updated post body", "title": "Updated title", "visibility": "private"})
    expect(post_patch.status_code == 200 and (post_patch.get_json() or {}).get("ok"), "owner can edit post", post_patch.get_data(as_text=True)[:400])
    post_delete = client.delete(f"/api/pulse/posts/{post_id}")
    expect(post_delete.status_code == 200 and (post_delete.get_json() or {}).get("ok"), "owner can delete post", post_delete.get_data(as_text=True)[:400])

    print("pulse content management audit ok")


if __name__ == "__main__":
    main()
