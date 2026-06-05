from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = (root / "bot.py").read_text(encoding="utf-8")
viewer = (root / "static/js/pulse_status_viewer.js").read_text(encoding="utf-8")

for token in [
    "/static/js/pulse_status_viewer.js",
    "PulseStatusViewer",
    "https://stream.mux.com/${media.mux_playback_id}.m3u8",
    "playsinline",
]:
    assert token in source + viewer, f"shared status viewer missing {token}"
print("status shared viewer playback audit ok")
