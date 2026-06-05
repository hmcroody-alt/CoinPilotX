#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "static/js/pulse_messages_v2.js").read_text(encoding="utf-8")
CSS = (ROOT / "static/css/pulse_messages_v2.css").read_text(encoding="utf-8")

def expect(ok, label):
    if not ok:
        raise AssertionError(label)
    print(f"ok - {label}")

def main():
    for token in ["voiceAttachmentHtml", "data-voice-play", "data-voice-speed", "data-voice-playback-waveform", "duration_seconds", "waveform_json"]:
        expect(token in JS, f"voice note UI includes {token}")
    expect(".voice-message" in CSS and ".voice-waveform" in CSS, "voice note styling exists")
    print("pulse comm v2 voice note audit ok")

if __name__ == "__main__":
    main()
