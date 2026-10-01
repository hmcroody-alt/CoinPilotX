"""Mux Live Streaming helpers for PulseSoc Live."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from urllib.parse import urlparse
from urllib.request import Request, urlopen


MUX_API_BASE = "https://api.mux.com/video/v1"
MUX_RTMP_INGEST_URL = "rtmp://global-live.mux.com:5222/app"


def diagnostics() -> dict:
    return {
        "configured": bool(os.getenv("MUX_TOKEN_ID") and os.getenv("MUX_TOKEN_SECRET")),
        "token_id_configured": bool(os.getenv("MUX_TOKEN_ID")),
        "token_secret_configured": bool(os.getenv("MUX_TOKEN_SECRET")),
        "webhook_secret_configured": bool(os.getenv("MUX_WEBHOOK_SECRET")),
        "data_env_key_configured": bool(os.getenv("MUX_DATA_ENV_KEY")),
        "data_env_key_used": bool((os.getenv("MUX_DATA_ANALYTICS_ENABLED") or "").strip().lower() == "true" and os.getenv("MUX_DATA_ENV_KEY")),
    }


def _auth_header() -> str:
    token_id = os.getenv("MUX_TOKEN_ID", "").strip()
    token_secret = os.getenv("MUX_TOKEN_SECRET", "").strip()
    if not token_id or not token_secret:
        return ""
    return "Basic " + base64.b64encode(f"{token_id}:{token_secret}".encode("utf-8")).decode("ascii")


def _request(path: str, *, method: str = "GET", payload: dict | None = None, timeout: float = 10) -> dict:
    auth = _auth_header()
    if not auth:
        return {"ok": False, "status": "not_configured", "message": "Mux credentials are not configured."}
    data = json.dumps(payload or {}).encode("utf-8") if payload is not None else None
    request = Request(
        MUX_API_BASE + path,
        data=data,
        method=method,
        headers={
            "Authorization": auth,
            "Content-Type": "application/json",
            "User-Agent": "CoinPilotX-MuxLive/1.0",
        },
    )
    try:
        with urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
            parsed = json.loads(body or "{}")
            return {"ok": True, "status_code": int(getattr(response, "status", 200)), "data": parsed.get("data") or parsed}
    except Exception as exc:
        logging.warning("MUX_LIVE_API_FAILED method=%s path=%s error=%s", method, path, str(exc)[:500])
        return {"ok": False, "status": "api_failed", "message": str(exc)[:500]}


def _playback_id_from_live_stream(data: dict) -> str:
    for item in data.get("playback_ids") or []:
        if item.get("policy") == "public" or item.get("id"):
            return item.get("id") or ""
    return ""


def playback_url(playback_id: str) -> str:
    playback_id = "".join(ch for ch in str(playback_id or "").strip() if ch.isalnum() or ch in {"_", "-"})
    return f"https://stream.mux.com/{playback_id}.m3u8" if playback_id else ""


def signed_playback_url(playback_id: str) -> str:
    """Mint a short-lived viewer token; never return an unsigned fallback."""
    key_id = os.getenv("MUX_SIGNING_KEY_ID", "")
    private_key = os.getenv("MUX_SIGNING_PRIVATE_KEY", "")
    if not key_id or not private_key:
        return ""
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding
    def encoded(value):
        return base64.urlsafe_b64encode(value).rstrip(b"=")
    try:
        pem = private_key.encode() if "BEGIN" in private_key else base64.b64decode(private_key)
        key = serialization.load_pem_private_key(pem, password=None)
        header = encoded(json.dumps({"alg": "RS256", "typ": "JWT", "kid": key_id}).encode())
        # Stable within an hour so status refreshes do not restart the player.
        body = encoded(json.dumps({"sub": playback_id, "aud": "v", "exp": (int(time.time()) // 3600 + 6) * 3600}).encode())
        message = header + b"." + body
        signature = encoded(key.sign(message, padding.PKCS1v15(), hashes.SHA256()))
        return playback_url(playback_id) + "?token=" + (message + b"." + signature).decode()
    except Exception:
        logging.warning("MUX_REPLAY_SIGNING_UNAVAILABLE")
        return ""


def refresh_signed_replay_url(url: str) -> str:
    parsed = urlparse(url or "")
    if parsed.hostname == "stream.mux.com" and "token=" in parsed.query:
        return signed_playback_url(parsed.path.rsplit("/", 1)[-1].removesuffix(".m3u8"))
    return url or ""


def find_recording_asset(marker: str) -> dict:
    """Bounded recovery after an ambiguous create; never create a second asset."""
    for page in range(1, 4):
        response = _request(f"/assets?limit=100&page={page}")
        if not response.get("ok"):
            return response
        assets = response.get("data") or []
        for asset in assets:
            if asset.get("passthrough") == marker:
                return {"ok": True, "mux_recording_asset_id": asset["id"]}
        if len(assets) < 100:
            break
    return {"ok": True, "mux_recording_asset_id": ""}


def create_mux_live_stream(*, title: str = "PulseSoc Live", record: bool = True, low_latency: bool = True, metadata: dict | None = None) -> dict:
    payload = {
        "playback_policy": ["public"],
        "new_asset_settings": {"playback_policy": ["public"]},
        "latency_mode": "low" if low_latency else "standard",
        "reconnect_window": int(os.getenv("MUX_LIVE_RECONNECT_WINDOW_SECONDS", "60")),
        "metadata": {"title": str(title or "PulseSoc Live")[:255], **(metadata or {})},
    }
    if not record:
        payload.pop("new_asset_settings", None)
    response = _request("/live-streams", method="POST", payload=payload, timeout=float(os.getenv("MUX_LIVE_CREATE_TIMEOUT_SECONDS", "10")))
    if not response.get("ok"):
        return response
    data = response.get("data") or {}
    playback_id = _playback_id_from_live_stream(data)
    return {
        "ok": True,
        "provider": "mux",
        "mux_live_stream_id": data.get("id") or "",
        "mux_stream_key": data.get("stream_key") or "",
        "mux_playback_id": playback_id,
        "mux_live_status": data.get("status") or "idle",
        "mux_recording_asset_id": data.get("recent_asset_ids", [""])[-1] if data.get("recent_asset_ids") else "",
        "playback_url": playback_url(playback_id),
        "ingest_url": MUX_RTMP_INGEST_URL,
        "rtmp_url": MUX_RTMP_INGEST_URL,
        "raw": data,
    }


def get_mux_live_stream(live_stream_id: str) -> dict:
    live_stream_id = str(live_stream_id or "").strip()
    if not live_stream_id:
        return {"ok": False, "message": "Mux live stream id is required."}
    response = _request(f"/live-streams/{live_stream_id}", timeout=float(os.getenv("MUX_LIVE_GET_TIMEOUT_SECONDS", "8")))
    if not response.get("ok"):
        return response
    data = response.get("data") or {}
    playback_id = _playback_id_from_live_stream(data)
    return {
        "ok": True,
        "provider": "mux",
        "mux_live_stream_id": data.get("id") or live_stream_id,
        "mux_stream_key": data.get("stream_key") or "",
        "mux_playback_id": playback_id,
        "mux_live_status": data.get("status") or "",
        "mux_recording_asset_id": data.get("recent_asset_ids", [""])[-1] if data.get("recent_asset_ids") else "",
        "playback_url": playback_url(playback_id),
        "raw": data,
    }


def disable_mux_live_stream(live_stream_id: str) -> dict:
    live_stream_id = str(live_stream_id or "").strip()
    if not live_stream_id:
        return {"ok": False, "message": "Mux live stream id is required."}
    response = _request(f"/live-streams/{live_stream_id}/disable", method="PUT", timeout=float(os.getenv("MUX_LIVE_DISABLE_TIMEOUT_SECONDS", "8")))
    if not response.get("ok"):
        return response
    data = response.get("data") or {}
    return {"ok": True, "mux_live_stream_id": data.get("id") or live_stream_id, "mux_live_status": data.get("status") or "disabled", "raw": data}


def create_mux_asset_from_live_recording(*, recording_asset_id: str = "", source_url: str = "") -> dict:
    recording_asset_id = str(recording_asset_id or "").strip()
    if recording_asset_id:
        response = _request(f"/assets/{recording_asset_id}", timeout=float(os.getenv("MUX_ASSET_GET_TIMEOUT_SECONDS", "8")))
        if not response.get("ok"):
            return response
        data = response.get("data") or {}
        playback_id = ""
        for item in data.get("playback_ids") or []:
            if item.get("policy") == "public" or item.get("id"):
                playback_id = item.get("id") or ""
                break
        return {
            "ok": True,
            "mux_recording_asset_id": data.get("id") or recording_asset_id,
            "mux_recording_playback_id": playback_id,
            "playback_url": signed_playback_url(playback_id) if any(item.get("id") == playback_id and item.get("policy") == "signed" for item in data.get("playback_ids") or []) else playback_url(playback_id),
            "mux_status": "preparing" if data.get("is_live") else data.get("status") or "",
            "raw": data,
        }
    if source_url:
        from . import media_service

        created = media_service.create_mux_asset_from_url(source_url, trace_id="live-recording")
        return {
            "ok": bool(created.get("ok")),
            "mux_recording_asset_id": created.get("asset_id") or "",
            "mux_recording_playback_id": created.get("playback_id") or "",
            "playback_url": media_service.mux_playback_urls(created.get("playback_id") or "").get("hls_url") or "",
            "mux_status": created.get("status") or "",
            "raw": created,
        }
    return {"ok": False, "message": "Recording asset id or source URL is required."}


def create_mux_asset_from_private_recording(source_url: str, *, marker: str = "", private: bool = False) -> dict:
    """Create a Mux VOD asset without logging or preflighting its signed URL."""
    source_url = str(source_url or "").strip()
    if not source_url.startswith("https://"):
        return {"ok": False, "message": "A private HTTPS recording URL is required."}
    response = _request(
        "/assets",
        method="POST",
        payload={"inputs": [{"url": source_url}], "playback_policies": ["signed" if private else "public"], "passthrough": marker},
        timeout=float(os.getenv("MUX_ASSET_CREATE_TIMEOUT_SECONDS", "15")),
    )
    if not response.get("ok"):
        return response
    data = response.get("data") or {}
    playback_id = next((item.get("id") for item in data.get("playback_ids") or [] if item.get("id")), "")
    return {
        "ok": bool(data.get("id")),
        "mux_recording_asset_id": data.get("id") or "",
        "mux_recording_playback_id": playback_id,
        "playback_url": playback_url(playback_id),
        "mux_status": data.get("status") or "",
        "raw": data,
    }


def verify_mux_webhook_signature(payload: bytes, signature_header: str | None, *, tolerance_seconds: int = 300) -> dict:
    secret = os.getenv("MUX_WEBHOOK_SECRET", "").strip()
    if not secret:
        return {"ok": False, "message": "Mux webhook secret is not configured.", "reason": "missing_secret"}
    header = str(signature_header or "")
    parts = {}
    for item in header.split(","):
        if "=" in item:
            key, value = item.split("=", 1)
            parts[key.strip()] = value.strip()
    timestamp = parts.get("t") or ""
    signatures = [item.split("=", 1)[1].strip() for item in header.split(",") if item.strip().startswith("v1=")]
    expected = parts.get("v1") or ""
    if not timestamp or not expected:
        return {"ok": False, "message": "Mux webhook signature header is malformed.", "reason": "malformed_header"}
    try:
        ts = int(timestamp)
    except ValueError:
        return {"ok": False, "message": "Mux webhook timestamp is invalid.", "reason": "invalid_timestamp"}
    if abs(int(time.time()) - ts) > int(tolerance_seconds or 300):
        return {"ok": False, "message": "Mux webhook timestamp is outside the allowed window.", "reason": "stale_timestamp"}
    signed = f"{timestamp}.".encode("utf-8") + (payload or b"")
    digest = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(digest, candidate) for candidate in signatures):
        return {"ok": False, "message": "Mux webhook signature did not match.", "reason": "signature_mismatch"}
    return {"ok": True, "reason": "verified"}
