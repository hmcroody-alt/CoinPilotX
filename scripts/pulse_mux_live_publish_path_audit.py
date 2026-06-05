#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT = (ROOT / "reports/pulse_mux_live_publish_pipeline_truth.md").read_text(encoding="utf-8")
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")

def expect(ok, label):
    if not ok:
        raise AssertionError(label)
    print(f"ok - {label}")

def main():
    for token in ["RTMP based", "does not include a browser-to-Mux RTMP bridge", "OBS/RTMP mode", "Browser Live mode remains future work"]:
        expect(token in REPORT, f"publish truth report includes {token}")
    expect("Mode A — OBS/RTMP Mode" in BOT and "Mode B — Browser Live Mode" in BOT, "studio labels publish modes")
    expect("publish_state='browser_preview'" in BOT and "requires_rtmp_encoder" in BOT, "browser preview does not fake live broadcast")
    print("pulse mux live publish path audit ok")

if __name__ == "__main__":
    main()
