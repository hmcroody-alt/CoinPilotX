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
    for token in ["Browser Live -> LiveKit/WebRTC -> LiveKit Egress -> Mux RTMP/HLS", "Advanced backup mode", "stream keys remain host/admin-only and are masked"]:
        expect(token in REPORT, f"publish truth report includes {token}")
    for token in ["does not include a browser-to-Mux RTMP bridge", "Browser Live mode remains future work", "Mode B — Browser Live Mode requires"]:
        expect(token not in REPORT + BOT, f"old preview-only statement removed: {token}")
    expect("Browser Live is publishing through LiveKit and forwarding to Mux" in BOT, "studio labels LiveKit/Mux publish mode")
    expect("publish_state = \"browser_live_egress\"" in BOT and "\"requires_rtmp_encoder\": False" in BOT, "browser publish starts LiveKit egress instead of requiring OBS")
    print("pulse mux live publish path audit ok")

if __name__ == "__main__":
    main()
