from pathlib import Path

root = Path(__file__).resolve().parents[1]
source = (root / "bot.py").read_text(encoding="utf-8")

for token in ["input[type=file]", "clip-path:none!important", "max-width:100%", "min-width:0!important"]:
    assert token in source, f"teacher overflow missing {token}"
print("pulse teacher application overflow audit ok")
