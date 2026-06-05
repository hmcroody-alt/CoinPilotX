from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = (root / "bot.py").read_text(encoding="utf-8")

for token in [
    '@webhook_app.route("/pulse/portfolio"',
    '@webhook_app.route("/pulse/premium/portfolio"',
    "Go to Portfolio",
    "Portfolio Intelligence",
    "['portfolio','Portfolio']",
]:
    assert token in source, f"portfolio surface missing {token}"
print("pulse portfolio surface audit ok")
