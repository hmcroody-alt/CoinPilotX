from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = (root / "bot.py").read_text(encoding="utf-8")

for token in ["user_recovery_codes", "/api/account/recovery-codes/generate", "recovery_email", "recovery_phone", "will not be shown again"]:
    assert token in source, f"account recovery missing {token}"
print("account recovery audit ok")
