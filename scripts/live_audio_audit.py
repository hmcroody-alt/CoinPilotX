#!/usr/bin/env python3
"""Audit professional Pulse Live audio primitives."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
from services import audio_engine  # noqa: E402


def require(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    bot.init_db()
    chain = audio_engine.default_audio_chain()
    keys = {stage["key"] for stage in chain}
    require({"noise_suppression", "echo_cancellation", "auto_gain", "compressor", "limiter", "gate", "de_esser"}.issubset(keys), "audio chain includes professional voice processing")
    health = audio_engine.score_audio_health({"rms_db": -24, "sync_drift_ms": 4, "clipping_events": 0})
    require(health["score"] >= 80, "clean audio scores healthy")
    muted = audio_engine.score_audio_health({"muted": True, "rms_db": -80})
    require(muted["level"] in {"watch", "critical"}, "muted mic is detected")
    support = audio_engine.device_support_matrix()
    require(support["mic_switching"] and support["usb_mixers"], "audio engine supports creator device routing")
    print("live audio audit ok")


if __name__ == "__main__":
    main()
