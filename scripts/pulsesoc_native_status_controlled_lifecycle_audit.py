#!/usr/bin/env python3
"""Controlled localhost Status lifecycle audit, independent of homepage presentation."""

from __future__ import annotations

import base64
import sys
import time
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import music_service  # noqa: E402

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII=")
MOV = b"\x00\x00\x00\x18ftypqt  \x00\x00\x00\x00qt  "


def ok(condition, label, details=""):
    if not condition:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def ensure_user(user_id, name):
    conn = bot.db(); cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO users (user_id, username, display_name, email, signup_time, onboarding_complete) VALUES (?, ?, ?, ?, ?, 1)",
        (user_id, name, name.replace("_", " ").title(), f"{name}@example.test", bot.datetime.utcnow().isoformat(timespec="seconds")),
    )
    conn.commit(); conn.close()


def client_for(user_id):
    client = bot.webhook_app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = user_id
    return client


def create(client, payload, label):
    response = client.post("/api/pulse/status", json=payload)
    data = response.get_json() or {}
    ok(response.status_code == 200 and data.get("ok") and data.get("status_id"), label, response.get_data(as_text=True)[:400])
    ok(int(data["status_id"]) == int((data.get("status") or {}).get("id") or 0), f"{label} returns canonical server ID")
    return int(data["status_id"])


def main():
    bot.init_db()
    seed = int(time.time()) % 10000
    owner_id, viewer_id = 983000 + seed, 993000 + seed
    ensure_user(owner_id, f"native_status_owner_{seed}")
    ensure_user(viewer_id, f"native_status_viewer_{seed}")
    owner, viewer = client_for(owner_id), client_for(viewer_id)

    image_upload = owner.post("/api/pulse/media/upload", data={"file": (BytesIO(PNG), "status-life.png"), "context_type": "pulse_status", "context_id": "controlled"}, content_type="multipart/form-data").get_json() or {}
    video_upload = owner.post("/api/pulse/media/upload", data={"file": (BytesIO(MOV), "status-life.mov"), "context_type": "pulse_status", "context_id": "controlled"}, content_type="multipart/form-data").get_json() or {}
    image_id = int((image_upload.get("media") or {}).get("id") or 0)
    video_id = int((video_upload.get("media") or {}).get("id") or 0)
    ok(image_id > 0 and video_id > 0, "controlled image and video uploads")

    tracks = music_service.search_tracks("pulse", limit=2)
    ok(bool(tracks and tracks[0].get("is_creator_safe")), "controlled creator-safe music selection")
    ai = owner.post("/api/pulse/status/ai-story", json={"prompt": "A calm PulseSoc orbit", "style": "cinematic"}).get_json() or {}
    ok(ai.get("ok") and (ai.get("story") or {}).get("caption"), "controlled AI-assisted caption")

    text_id = create(owner, {"status_type": "text", "body": "Controlled text Status", "visibility": "public"}, "text Status create")
    image_status_id = create(owner, {"status_type": "photo", "body": "Controlled image Status", "media_ids": [image_id], "visibility": "followers"}, "image Status create")
    video_status_id = create(owner, {"status_type": "video", "body": "Controlled video Status", "media_ids": [video_id], "visibility": "public"}, "video Status create")
    music_id = create(owner, {"status_type": "music", "body": "Controlled music Status", "music_track_id": tracks[0]["id"], "visibility": "public"}, "music Status create")
    ai_id = create(owner, {"status_type": "ai", "body": ai["story"]["caption"], "ai_context": ai["story"], "visibility": "private"}, "AI Status create")

    owner_rail = owner.get("/api/pulse/status/rail?lane=for_you").get_json() or {}
    owner_ids = {int(row.get("id") or 0) for row in owner_rail.get("items", [])}
    ok({text_id, image_status_id, video_status_id, music_id, ai_id}.issubset(owner_ids), "canonical rail insertion for all Status types")
    viewer_rail = viewer.get("/api/pulse/status/rail?lane=for_you").get_json() or {}
    viewer_ids = {int(row.get("id") or 0) for row in viewer_rail.get("items", [])}
    ok(text_id in viewer_ids and video_status_id in viewer_ids and music_id in viewer_ids, "public Status viewer authorization")
    ok(ai_id not in viewer_ids, "private Status server authorization")

    first = viewer.post(f"/api/pulse/status/{text_id}/view", json={"completion_ratio": .5, "watch_ms": 1200}).get_json() or {}
    second = viewer.post(f"/api/pulse/status/{text_id}/view", json={"completed": True, "completion_ratio": 1, "watch_ms": 2200}).get_json() or {}
    ok(first.get("view_count") == second.get("view_count"), "seen-state deduplication")
    fire = viewer.post(f"/api/pulse/status/{text_id}/react", json={"reaction_type": "fire"}).get_json() or {}
    love = viewer.post(f"/api/pulse/status/{text_id}/react", json={"reaction_type": "love"}).get_json() or {}
    ok(fire.get("reaction_count") == love.get("reaction_count") and love.get("reaction_type") == "love", "reaction replacement without duplicate count")
    reply = viewer.post(f"/api/pulse/status/{text_id}/reply", json={"body": "Controlled direct reply"}).get_json() or {}
    ok((reply.get("reply") or {}).get("id"), "reply creation and notification routing")
    share = viewer.post(f"/api/pulse/status/{text_id}/share", json={"surface": "native_controlled"}).get_json() or {}
    ok(share.get("ok") and int(share.get("share_count") or 0) >= 1, "authorized Status share")

    owner_view = owner.post(f"/api/pulse/status/{text_id}/view", json={"completed": True}).get_json() or {}
    analytics = owner_view.get("owner_analytics") or {}
    ok("views" in analytics and "completion_rate" in analytics, "owner aggregate analytics authorization")
    unauthorized_analytics = viewer.post(f"/api/pulse/status/{text_id}/view", json={}).get_json() or {}
    ok(not unauthorized_analytics.get("owner_analytics"), "non-owner analytics rejection")

    privacy = owner.patch(f"/api/pulse/status/{text_id}", json={"visibility": "private", "body": "Controlled text Status updated"}).get_json() or {}
    ok((privacy.get("status") or {}).get("visibility") == "private", "owner privacy update")
    ok(text_id not in {int(row.get("id") or 0) for row in (viewer.get("/api/pulse/status/rail?lane=for_you").get_json() or {}).get("items", [])}, "privacy change revokes viewer rail access")
    ok(viewer.post(f"/api/pulse/status/{text_id}/share", json={}).status_code == 404, "privacy change revokes share access")

    report = viewer.post("/api/pulse/report", json={"target_type": "status", "target_id": video_status_id, "reason": "controlled lifecycle"}).get_json() or {}
    ok(report.get("ok"), "Status report action")
    mute = viewer.post("/api/pulse/users/mute", json={"muted_user_id": owner_id, "reason": "controlled lifecycle"}).get_json() or {}
    ok(mute.get("ok"), "Status creator mute action")
    block = viewer.post("/api/pulse/block", json={"blocked_user_id": owner_id, "reason": "controlled lifecycle"}).get_json() or {}
    ok(block.get("ok"), "Status creator block action")

    deleted = owner.delete(f"/api/pulse/status/{video_status_id}").get_json() or {}
    ok(deleted.get("ok"), "Status delete")
    ok(video_status_id not in {int(row.get("id") or 0) for row in (owner.get("/api/pulse/status/rail?lane=for_you").get_json() or {}).get("items", [])}, "deleted Status rail removal")
    ok(owner.post(f"/api/pulse/status/{video_status_id}/view", json={}).status_code == 404, "deleted deep-link fallback contract")

    conn = bot.db(); cur = conn.cursor()
    cur.execute("UPDATE pulse_status SET expires_at=? WHERE id=?", ((bot.datetime.utcnow() - bot.timedelta(seconds=1)).isoformat(timespec="seconds"), music_id))
    conn.commit(); conn.close()
    ok(music_id not in {int(row.get("id") or 0) for row in (owner.get("/api/pulse/status/rail?lane=for_you").get_json() or {}).get("items", [])}, "expired Status rail cleanup")
    ok(owner.post(f"/api/pulse/status/{music_id}/view", json={}).status_code == 404, "expired viewer fallback contract")

    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    for event in ("pulse_status_created", "pulse_status_viewed", "pulse_status_reaction", "pulse_status_reply", "pulse_status_shared", "pulse_status_updated", "pulse_status_deleted"):
        ok(f'pulse_emit_event("{event}"' in source, f"realtime producer {event}")
    print("PulseSoc native controlled Status lifecycle audit passed.")


if __name__ == "__main__":
    main()
