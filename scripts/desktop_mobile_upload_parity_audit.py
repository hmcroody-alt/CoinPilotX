from pathlib import Path

root = Path(__file__).resolve().parents[1]
upload = (root / "static/js/pulse_upload_manager.js").read_text(encoding="utf-8")
bot = (root / "bot.py").read_text(encoding="utf-8")

for token in [
    "preferDirectMux",
    "8 * 1024 * 1024",
    "/api/pulse/media/mux/direct-upload",
    "/api/pulse/media/mux/direct-upload/complete",
    "context_type",
]:
    assert token in upload + bot, f"upload parity missing {token}"
print("desktop mobile upload parity audit ok")
