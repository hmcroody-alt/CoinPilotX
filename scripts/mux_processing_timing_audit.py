from pathlib import Path

root = Path(__file__).resolve().parents[1]
bot = (root / "bot.py").read_text(encoding="utf-8")
media = (root / "services/media_service.py").read_text(encoding="utf-8")

for token in [
    "upload_complete_at",
    "mux_asset_created_at",
    "mux_ready_at",
    "webhook_received_at",
    "db_ready_update_at",
    "/api/pulse/media/<int:media_id>/status",
]:
    assert token in bot + media, f"Mux timing missing {token}"
print("mux processing timing audit ok")
