from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = (root / "bot.py").read_text(encoding="utf-8")

for token in ["/api/account/reauthenticate", "recent_reauth_at", "Sensitive actions", "reauth"]:
    assert token in source, f"reauth missing {token}"
print("account sensitive action reauth audit ok")
