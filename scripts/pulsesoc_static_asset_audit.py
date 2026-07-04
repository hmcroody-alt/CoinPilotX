#!/usr/bin/env python3
"""Audit PulseSoc asset weight, delivery headers, and critical loading behavior."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


ASSET_SUFFIXES = {".js", ".css", ".png", ".jpg", ".jpeg", ".webp", ".mp4", ".mp3", ".m4a"}


def emit(status: str, item: str, detail: str) -> None:
    print(f"{status}\t{item}\t{detail}")


def main() -> int:
    failures = 0
    warnings = 0
    assets = [
        path
        for path in (ROOT / "static").rglob("*")
        if path.is_file() and path.suffix.lower() in ASSET_SUFFIXES
    ]
    assets.sort(key=lambda path: path.stat().st_size, reverse=True)

    for path in assets[:25]:
        rel = path.relative_to(ROOT)
        size = path.stat().st_size
        if path.suffix.lower() in {".js", ".css"} and size > 1_000_000:
            failures += 1
            emit("FAIL", str(rel), f"{size} bytes exceeds 1 MB code budget")
        elif size > 1_000_000:
            warnings += 1
            emit("WARN", str(rel), f"{size} bytes; verify compression and page scoping")
        elif path.suffix.lower() in {".js", ".css"} and size > 320_000:
            warnings += 1
            emit("WARN", str(rel), f"{size} bytes; large page-scoped dependency")
        else:
            emit("PASS", str(rel), f"{size} bytes")

    messenger = (ROOT / "templates/pulse_messages_v2.html").read_text(errors="ignore")
    script_tags = re.findall(r"<script\b[^>]*>", messenger, flags=re.IGNORECASE)

    def deferred_script(name: str) -> bool:
        return any(name in tag and re.search(r"\bdefer\b", tag) for tag in script_tags)

    checks = {
        "Messenger media renderer is deferred": deferred_script("pulse_media_renderer.js"),
        "LiveKit bundle is Messenger-scoped": "livekit-client.umd.js" in messenger,
        "Messenger call client is deferred": deferred_script("pulsesoc_calls.js"),
        "Messenger app client is deferred": deferred_script("pulse_messages_v2.js"),
    }
    for label, ok in checks.items():
        if ok:
            emit("PASS", label, "verified")
        else:
            failures += 1
            emit("FAIL", label, "missing")

    client = bot.webhook_app.test_client()
    static_response = client.get("/static/js/pulse_realtime.js")
    cache_control = static_response.headers.get("Cache-Control", "")
    if static_response.status_code == 200 and "immutable" in cache_control and "max-age" in cache_control:
        emit("PASS", "static cache headers", cache_control)
    else:
        failures += 1
        emit("FAIL", "static cache headers", cache_control or f"HTTP {static_response.status_code}")

    media_source = (ROOT / "static/js/pulse_media_renderer.js").read_text(errors="ignore")
    for marker in ("IntersectionObserver", 'loading = "lazy"', "content-visibility"):
        present = marker in media_source or marker in "\n".join(
            path.read_text(errors="ignore")
            for path in (ROOT / "static/css").glob("pulse_*.css")
        )
        if present:
            emit("PASS", f"media optimization {marker}", "present")
        else:
            warnings += 1
            emit("WARN", f"media optimization {marker}", "not detected")

    print(f"SUMMARY\tfailures={failures}\twarnings={warnings}\tassets={len(assets)}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
