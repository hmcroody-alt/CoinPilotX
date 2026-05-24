"""Professional audio chain primitives for Pulse Live."""

from __future__ import annotations

DEFAULT_CHAIN = [
    {"key": "noise_suppression", "label": "Noise suppression", "enabled": True, "latency_ms": 4},
    {"key": "echo_cancellation", "label": "Echo cancellation", "enabled": True, "latency_ms": 3},
    {"key": "auto_gain", "label": "Automatic gain", "enabled": True, "latency_ms": 2},
    {"key": "gate", "label": "Gate", "enabled": True, "threshold_db": -48, "latency_ms": 1},
    {"key": "compressor", "label": "Voice compressor", "enabled": True, "ratio": 3.0, "latency_ms": 2},
    {"key": "de_esser", "label": "De-esser", "enabled": True, "frequency_hz": 6200, "latency_ms": 1},
    {"key": "limiter", "label": "Limiter", "enabled": True, "ceiling_db": -1, "latency_ms": 1},
]


def audio_constraints(device_id: str = "") -> dict:
    audio = {"echoCancellation": True, "noiseSuppression": True, "autoGainControl": True}
    if device_id:
        audio["deviceId"] = {"exact": device_id}
    return audio


def default_audio_chain() -> list[dict]:
    return [dict(stage) for stage in DEFAULT_CHAIN]


def score_audio_health(metrics: dict | None = None) -> dict:
    metrics = metrics or {}
    clipping = int(metrics.get("clipping_events") or 0)
    muted = bool(metrics.get("muted"))
    rms = float(metrics.get("rms_db") or -36)
    drift = abs(float(metrics.get("sync_drift_ms") or 0))
    score = 100
    if muted:
        score -= 45
    score -= min(30, clipping * 10)
    if rms < -52:
        score -= 18
    if rms > -6:
        score -= 14
    score -= min(20, int(drift / 10))
    score = max(0, min(100, score))
    level = "excellent" if score >= 88 else "stable" if score >= 72 else "watch" if score >= 50 else "critical"
    return {
        "score": score,
        "level": level,
        "muted": muted,
        "clipping": clipping > 0,
        "rms_db": rms,
        "sync_drift_ms": drift,
        "chain": default_audio_chain(),
        "monitoring": {
            "meters": True,
            "clipping_detection": True,
            "muted_mic_detection": True,
            "headphone_safe_monitoring": True,
            "music_ducking": True,
        },
    }


def device_support_matrix() -> dict:
    return {
        "mic_switching": True,
        "browser_mic_selection": True,
        "external_audio_devices": True,
        "bluetooth_devices": True,
        "usb_mixers": True,
        "airpods_handling": True,
        "webcam_audio": True,
        "desktop_audio_capture": "screen-share-dependent",
    }
