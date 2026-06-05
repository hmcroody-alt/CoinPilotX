from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = (root / "bot.py").read_text(encoding="utf-8")

for token in ["Safety + Compliance", ".teacher-application .check-grid", "overflow-wrap:break-word", "word-break:normal", "white-space:normal"]:
    assert token in source, f"teacher layout missing {token}"
print("pulse teacher application layout audit ok")
