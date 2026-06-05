from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = (root / "bot.py").read_text(encoding="utf-8")

checks = [
    '("Reels", "/pulse/reels")',
    '("Videos", "/pulse/videos")',
    "def pulse_desktop_top_nav_html",
    "pulse-desktop-nav",
]
missing = [token for token in checks if token not in source]
assert not missing, f"Missing Reels desktop nav tokens: {missing}"
print("pulse reels desktop nav audit ok")
