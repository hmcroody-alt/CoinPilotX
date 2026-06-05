from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = (root / "bot.py").read_text(encoding="utf-8")

for token in [
    "avatar_url_cache_busted",
    "SELECT avatar_url, avatar_thumbnail_url FROM users",
    "Profile picture save did not persist",
    "UPDATE users SET avatar_url=?",
]:
    assert token in source, f"avatar persistence missing {token}"
print("profile avatar persistence audit ok")
