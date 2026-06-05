from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = (root / "bot.py").read_text(encoding="utf-8")

for token in ["account_security_score", "email_verified", "phone_verified", "two_factor_enabled", "recovery_codes_ready", "/api/account/security"]:
    assert token in source, f"security score missing {token}"
print("account security score audit ok")
