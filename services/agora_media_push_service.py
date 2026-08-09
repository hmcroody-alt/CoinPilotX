"""Server-only Agora Media Push bridge for PulseSoc Live -> Mux."""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen


def diagnostics() -> dict:
    return {
        "configured": bool(
            os.getenv("AGORA_APP_ID")
            and os.getenv("AGORA_REST_CUSTOMER_ID")
            and os.getenv("AGORA_REST_CUSTOMER_SECRET")
        ),
        "app_id_configured": bool(os.getenv("AGORA_APP_ID")),
        "customer_id_configured": bool(os.getenv("AGORA_REST_CUSTOMER_ID")),
        "customer_secret_configured": bool(os.getenv("AGORA_REST_CUSTOMER_SECRET")),
        "region": (os.getenv("AGORA_MEDIA_PUSH_REGION") or "na").strip().lower(),
    }


def _safe_message(value: object, fallback: str) -> str:
    text = str(value or fallback)[:500]
    text = re.sub(r"rtmps?://[^\s\"'<>]+", "rtmp://[redacted]", text)
    text = re.sub(r"(?i)(authorization|token|secret|stream[_ -]?key)[=:]\s*[^\s\"'<>]+", r"\1=[redacted]", text)
    return text


def _request(path: str, *, method: str = "GET", payload: dict | None = None, timeout: float = 12) -> dict:
    app_id = os.getenv("AGORA_APP_ID", "").strip()
    customer_id = os.getenv("AGORA_REST_CUSTOMER_ID", "").strip()
    customer_secret = os.getenv("AGORA_REST_CUSTOMER_SECRET", "").strip()
    region = (os.getenv("AGORA_MEDIA_PUSH_REGION") or "na").strip().lower()
    if not app_id or not customer_id or not customer_secret:
        return {"ok": False, "reason": "not_configured", "message": "Agora Media Push REST credentials are not configured."}
    auth = base64.b64encode(f"{customer_id}:{customer_secret}".encode()).decode("ascii")
    body = json.dumps(payload).encode() if payload is not None else None
    url = f"https://api.agora.io/{quote(region)}/v1/projects/{quote(app_id)}/{path.lstrip('/')}"
    request = Request(url, data=body, method=method, headers={
        "Authorization": f"Basic {auth}",
        "Content-Type": "application/json",
        "User-Agent": "PulseSoc-AgoraMuxBridge/1.0",
    })
    try:
        with urlopen(request, timeout=timeout) as response:
            parsed = json.loads(response.read().decode("utf-8", "replace") or "{}")
            data = parsed.get("data") or parsed
            return {"ok": True, "status_code": int(getattr(response, "status", 200)), "data": data}
    except HTTPError as exc:
        try:
            parsed = json.loads(exc.read().decode("utf-8", "replace") or "{}")
        except Exception:
            parsed = {}
        message = _safe_message(parsed.get("message"), "Agora Media Push request was rejected.")
        logging.warning("AGORA_MEDIA_PUSH_REJECTED method=%s status=%s", method, exc.code)
        return {"ok": False, "reason": "api_rejected", "status_code": exc.code, "message": message}
    except Exception as exc:
        logging.warning("AGORA_MEDIA_PUSH_FAILED method=%s error_type=%s", method, type(exc).__name__)
        return {"ok": False, "reason": "api_failed", "message": _safe_message(exc, "Agora Media Push request failed.")}


def start_mux_bridge(*, live_id: int, channel_name: str, rtmp_url: str, host_uid: int) -> dict:
    """Create one transcoded converter. Audio includes all approved publishers.

    Agora vertical layout automatically follows channel publishers, so an
    authorized co-host is included without exposing or changing the Mux target.
    """
    if not channel_name or not rtmp_url or not host_uid:
        return {"ok": False, "reason": "missing_bridge_input", "message": "Agora channel, host UID, and Mux destination are required."}
    payload = {"converter": {
        "name": f"pulsesoc_live_{int(live_id)}_mux",
        "transcodeOptions": {
            "rtcChannel": str(channel_name)[:64],
            "audioOptions": {"codecProfile": "LC-AAC", "sampleRate": 48000, "bitrate": 128, "audioChannels": 1},
            "videoOptions": {
                "canvas": {"width": 720, "height": 1280, "color": 0},
                "codec": "H.264", "codecProfile": "high", "frameRate": 30, "gop": 60, "bitrate": 2500,
                "layoutType": 1,
                "vertical": {"maxResolutionUid": int(host_uid), "fillMode": "fill", "refreshIntervalSec": 4},
            },
        },
        "rtmpUrl": rtmp_url,
        "idleTimeout": int(os.getenv("AGORA_MEDIA_PUSH_IDLE_TIMEOUT_SECONDS", "300")),
    }}
    result = _request("rtmp-converters", method="POST", payload=payload)
    if not result.get("ok"):
        return result
    data = result.get("data") or {}
    converter = data.get("converter") if isinstance(data.get("converter"), dict) else data
    return {"ok": True, "converter_id": converter.get("id") or data.get("id") or "", "state": converter.get("state") or data.get("state") or "connecting"}


def get_bridge(converter_id: str) -> dict:
    if not converter_id:
        return {"ok": False, "reason": "missing_converter"}
    result = _request(f"rtmp-converters/{quote(str(converter_id))}")
    if not result.get("ok"):
        return result
    data = result.get("data") or {}
    converter = data.get("converter") if isinstance(data.get("converter"), dict) else data
    return {"ok": True, "converter_id": converter.get("id") or converter_id, "state": converter.get("state") or ""}


def stop_mux_bridge(converter_id: str) -> dict:
    if not converter_id:
        return {"ok": True, "already_stopped": True}
    return _request(f"rtmp-converters/{quote(str(converter_id))}", method="DELETE")
