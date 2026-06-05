from pathlib import Path

root = Path(__file__).resolve().parents[1]
bot = (root / "bot.py").read_text(encoding="utf-8")
renderer = (root / "static/js/pulse_media_renderer.js").read_text(encoding="utf-8")

for token in [
    "/api/pulse/media/<int:media_id>/repair",
    "data-repair-media",
    "Preparing video...",
    "Processing is taking longer than usual",
]:
    assert token in bot + renderer, f"reel repair missing {token}"
print("reel processing repair audit ok")
