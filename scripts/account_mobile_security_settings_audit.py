from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = (root / "bot.py").read_text(encoding="utf-8")

for token in ["/pulse/settings/security", "security-grid", "@media(max-width:760px)", "pulse_security_settings_page"]:
    assert token in source, f"mobile security settings missing {token}"
print("account mobile security settings audit ok")
