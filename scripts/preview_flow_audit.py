#!/usr/bin/env python3
"""Audit immersive Pulse Camera preview publishing flows."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import preview_service  # noqa: E402


def expect(ok, label, details=""):
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"ok - {label}")


def ensure_user():
    conn = bot.db()
    cur = conn.cursor()
    now = bot.datetime.utcnow().isoformat(timespec="seconds")
    cur.execute(
        "INSERT OR IGNORE INTO users (user_id, username, display_name, email, created_at) VALUES (?, ?, ?, ?, ?)",
        (950032, "preview_flow_audit", "Preview Flow Audit", "preview-flow-audit@example.test", now),
    )
    conn.commit()
    conn.close()


def main():
    bot.init_db()
    ensure_user()
    conn = bot.db()
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pulse_camera_previews LIMIT 1")
    cur.fetchone()
    expect(True, "pulse_camera_previews table exists")
    conn.commit()
    conn.close()

    draft = preview_service.create_preview(
        950032,
        "reel",
        {"id": 123456, "media_url": "https://cdn.coinpilotx.app/audit/preview.jpg", "thumbnail_url": "https://cdn.coinpilotx.app/audit/preview-thumb.jpg", "media_type": "image"},
        {"caption": "Preview audit", "privacy": "public"},
    )
    expect(draft.get("ok") and draft.get("preview_token"), "preview service stores draft")
    latest = preview_service.latest_draft(950032)
    expect(latest.get("ok"), "preview draft restores")
    marked = preview_service.mark_published(draft["preview_token"], 950032, "reel", 777)
    expect(marked.get("ok"), "preview marks published")

    html = bot.webhook_app.test_client().get("/pulse/camera?target=status")
    expect(html.status_code in {200, 302}, "camera route available")
    if html.status_code == 200:
        body = html.get_data(as_text=True)
        for token in ["pulseCameraPreviewFlow", "pulsePreviewStage", "pulsePreviewPublish", "Publish Status", "Publish Reels", "Publish Pulse"]:
            expect(token in body, f"preview HTML token exists: {token}")
    js = (ROOT / "static/js/pulse_camera_engine.js").read_text(encoding="utf-8")
    css = (ROOT / "static/css/pulse_camera_engine.css").read_text(encoding="utf-8")
    for token in ["previewMarkup", "pulse-preview-status", "pulse-preview-reel", "pulse-preview-feed-card", "markPreviewPublished"]:
        expect(token in js + css, f"preview flow token exists: {token}")
    print("preview flow audit ok")


if __name__ == "__main__":
    main()
