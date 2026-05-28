#!/usr/bin/env python3
"""Full CoinPilotXAI Pulse platform audit gate.

Audit plan:
1. Database/migration integrity.
2. Feed visibility, deletion safety, media hydration, and cross-device determinism.
3. R2/CDN media integrity and upload progress.
4. Direct/group/room chat load/send/recovery.
5. Reels load/upload/publish/playback.
6. Status create/view/publish tools.
7. Live discovery, transport, replay, multistream, audio, scenes.
8. Camera capture/preview/upload/publish.
9. Mobile/PWA safe-area and non-decorative UX contracts.
10. Admin/worker/Railway environment diagnostics.
"""

from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

AUDIT_PLAN = [
    ("database", "database_integrity_audit.py"),
    ("feed layout", "pulse_feed_layout_audit.py"),
    ("media attachments", "media_attachment_audit.py"),
    ("daily mentor", "daily_mentor_audit.py"),
    ("feed", "pulse_feed_audit.py"),
    ("pulse search", "pulse_search_audit.py"),
    ("global feed", "pulse_global_feed_audit.py"),
    ("visibility", "pulse_visibility_audit.py"),
    ("cross-device feed", "cross_device_feed_audit.py"),
    ("media integrity", "media_integrity_audit.py"),
    ("pulse media", "pulse_media_audit.py"),
    ("phase 2 media CDN", "phase2_media_cdn_audit.py"),
    ("upload progress", "upload_progress_audit.py"),
    ("chat system", "chat_system_audit.py"),
    ("chat actual load", "chat_actual_load_audit.py"),
    ("chat send/receive", "chat_send_receive_audit.py"),
    ("chat parity", "chat_desktop_mobile_parity_audit.py"),
    ("chat mobile responsive", "chat_mobile_audit.py"),
    ("chat security", "chat_security_audit.py"),
    ("reels pipeline", "reels_pipeline_audit.py"),
    ("status system", "status_system_audit.py"),
    ("create status parity", "create_status_parity_audit.py"),
    ("music system", "music_system_audit.py"),
    ("AI story", "ai_story_audit.py"),
    ("live pipeline", "live_pipeline_audit.py"),
    ("live audio", "live_audio_audit.py"),
    ("mobile experience", "mobile_experience_audit.py"),
    ("realtime feed", "realtime_feed_audit.py"),
    ("camera publish", "camera_publish_audit.py"),
    ("mobile PWA", "mobile_pwa_audit.py"),
    ("media worker", "media_engine_audit.py"),
]

REQUIRED_PRODUCTION_ENV = [
    "MEDIA_STORAGE_PROVIDER",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
    "R2_BUCKET",
    "R2_PUBLIC_BASE_URL",
]


def is_production_like() -> bool:
    return bool(os.getenv("RAILWAY_ENVIRONMENT") or os.getenv("RAILWAY_PROJECT_ID") or os.getenv("RENDER") or os.getenv("FLY_APP_NAME") or os.getenv("ENV") == "production" or os.getenv("FLASK_ENV") == "production")


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def run(script: str):
    path = ROOT / "scripts" / script
    require(path.exists(), f"{script} exists")
    print(f"\n=== running {script} ===")
    result = subprocess.run([sys.executable, str(path)], cwd=ROOT, text=True, capture_output=True)
    print(result.stdout, end="")
    if result.returncode:
        print(result.stderr, end="")
        raise AssertionError(f"{script} failed with exit code {result.returncode}")


def static_contract_audit():
    source = (ROOT / "bot.py").read_text()
    js = "\n".join(path.read_text(errors="ignore") for path in (ROOT / "static" / "js").glob("*.js"))
    css = "\n".join(path.read_text(errors="ignore") for path in (ROOT / "static" / "css").glob("*.css"))
    combined = source + "\n" + js + "\n" + css
    require("Something needs attention. Please try again." not in combined, "no generic dead error copy remains")
    require("data-upload-progress" in combined and "Uploading" in combined and "Posted successfully" in combined, "upload flows expose progress and success states")
    require("Media could not load" in combined and "data-retry-media" in combined, "broken media fallback and retry exist")
    require("env(safe-area-inset-bottom)" in combined and "100dvh" in combined, "mobile safe-area and dynamic viewport contracts exist")
    require("pulseApi(" in source or "async function api(" in source, "frontend API calls use stable JSON helpers")
    require("/admin/messages-health" in source and "/admin/media" in source and "/admin/performance" in source, "admin diagnostics routes are present")


def railway_env_audit():
    procfile = ROOT / "Procfile"
    nixpacks = ROOT / "nixpacks.toml"
    require(procfile.exists(), "Procfile exists for Railway process definitions")
    require(nixpacks.exists(), "nixpacks.toml exists for Railway/system dependencies")
    if is_production_like():
        missing = [key for key in REQUIRED_PRODUCTION_ENV if not os.getenv(key)]
        missing_endpoint = not (os.getenv("R2_ENDPOINT") or os.getenv("R2_ENDPOINT_URL") or os.getenv("R2_ACCOUNT_ID"))
        require(not missing, f"production R2/CDN env vars configured ({', '.join(missing) if missing else 'none missing'})")
        require(not missing_endpoint, "production R2 endpoint or account id configured")
        require(os.getenv("MEDIA_STORAGE_PROVIDER", "").lower() == "r2", "production uses R2 media storage")
        require(os.getenv("R2_BUCKET") == "pulse-media2", "production R2 bucket is pulse-media2")
        require(os.getenv("R2_PUBLIC_BASE_URL", "").rstrip("/") == "https://cdn.coinpilotx.app", "production CDN is cdn.coinpilotx.app")
    else:
        print("ok - local/non-production environment may use local media fallback; production gate will require R2")


def main():
    print("CoinPilotXAI Pulse full platform audit")
    print("Audit plan:")
    for label, script in AUDIT_PLAN:
        print(f"- {label}: {script}")
    static_contract_audit()
    railway_env_audit()
    for _, script in AUDIT_PLAN:
        run(script)
    print("\nfull platform audit ok")


if __name__ == "__main__":
    main()
