from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = (root / "bot.py").read_text(encoding="utf-8")

for token in ["@media(max-width:767px)", ".teacher-application", "grid-template-columns:1fr!important", "body:has(.teacher-application) .pulse-fab"]:
    assert token in source, f"teacher mobile missing {token}"
print("pulse teacher application mobile audit ok")
