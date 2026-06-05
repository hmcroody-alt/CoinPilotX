from pathlib import Path

root = Path(__file__).resolve().parents[1]
bot = (root / "bot.py").read_text(encoding="utf-8")
tpl = (root / "templates/account.html").read_text(encoding="utf-8")

for token in ["name=\"username\"", "age_confirmed", "not email and not phone", "government ID"]:
    if token == "government ID":
        assert token not in tpl.lower(), "signup asks for government ID"
    else:
        assert token in bot + tpl, f"signup minimal field missing {token}"
print("account signup minimal fields audit ok")
