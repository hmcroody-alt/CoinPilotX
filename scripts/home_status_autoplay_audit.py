from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = (root / "bot.py").read_text(encoding="utf-8")
viewer = (root / "static/js/pulse_status_viewer.js").read_text(encoding="utf-8")

for token in [
    "data-status-home-video",
    "autoplay loop playsinline webkit-playsinline preload=\"metadata\"",
    "openStatusViewerFeed('global'",
    "PulseStatusViewer?.render",
]:
    assert token in source or token in viewer, f"home status autoplay missing {token}"
print("home status autoplay audit ok")
