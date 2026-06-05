from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = (root / "bot.py").read_text(encoding="utf-8")

for token in ["account_trust_level", "Basic User", "Verified User", "Trusted User", "Creator Verified", "Admin Verified"]:
    assert token in source, f"trust profile missing {token}"
print("account trust profile audit ok")
