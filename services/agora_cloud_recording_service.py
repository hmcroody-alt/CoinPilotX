"""Server-only Agora Cloud Recording lifecycle for PulseSoc Live."""

from __future__ import annotations

import base64
import json
import logging
import os
from urllib.error import HTTPError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


API_BASE = "https://api.sd-rtn.com/v1/apps"
MODE = "mix"


def diagnostics() -> dict:
    required = ("AGORA_APP_ID", "AGORA_APP_CERTIFICATE", "AGORA_REST_CUSTOMER_ID", "AGORA_REST_CUSTOMER_SECRET", "R2_BUCKET", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_ENDPOINT_URL", "R2_PUBLIC_BASE_URL")
    return {"configured": all(os.getenv(k) for k in required), "fields": {k: bool(os.getenv(k)) for k in required}}


def _request(path: str, *, method: str = "POST", payload: dict | None = None) -> dict:
    customer = os.getenv("AGORA_REST_CUSTOMER_ID", "").strip()
    secret = os.getenv("AGORA_REST_CUSTOMER_SECRET", "").strip()
    app_id = os.getenv("AGORA_APP_ID", "").strip()
    if not customer or not secret or not app_id:
        return {"ok": False, "reason": "not_configured", "message": "Agora Cloud Recording is not configured."}
    auth = base64.b64encode(f"{customer}:{secret}".encode()).decode("ascii")
    req = Request(f"{API_BASE}/{quote(app_id)}/{path.lstrip('/')}", data=json.dumps(payload).encode() if payload is not None else None, method=method, headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json", "User-Agent": "PulseSoc-CloudRecording/1.0"})
    try:
        with urlopen(req, timeout=float(os.getenv("AGORA_RECORDING_TIMEOUT_SECONDS", "20"))) as response:
            return {"ok": True, "status_code": int(getattr(response, "status", 200)), "data": json.loads(response.read().decode("utf-8", "replace") or "{}")}
    except HTTPError as exc:
        try:
            body = json.loads(exc.read().decode("utf-8", "replace") or "{}")
        except Exception:
            body = {}
        reason = str(body.get("reason") or body.get("message") or "provider_rejected")[:240]
        logging.warning("AGORA_CLOUD_RECORDING_REJECTED operation=%s status=%s reason=%s", path.rsplit("/", 1)[-1], exc.code, reason)
        return {"ok": False, "status_code": exc.code, "reason": "provider_rejected", "message": reason}
    except Exception as exc:
        logging.warning("AGORA_CLOUD_RECORDING_FAILED operation=%s error_type=%s", path.rsplit("/", 1)[-1], type(exc).__name__)
        return {"ok": False, "reason": "request_failed", "message": "Agora Cloud Recording request failed."}


def recorder_uid(live_id: int) -> int:
    return 3_000_000_000 + (int(live_id) % 1_000_000_000)


def _rtc_token(channel_name: str, uid: int) -> str:
    from agora_token_builder import RtcTokenBuilder
    import time
    return RtcTokenBuilder.buildTokenWithUid(os.environ["AGORA_APP_ID"], os.environ["AGORA_APP_CERTIFICATE"], channel_name, uid, 2, int(time.time()) + 7200)


def acquire(*, live_id: int, channel_name: str) -> dict:
    uid = recorder_uid(live_id)
    result = _request("cloud_recording/acquire", payload={"cname": channel_name, "uid": str(uid), "clientRequest": {"scene": 0, "resourceExpiredHour": 24}})
    data = result.get("data") or {}
    return {**result, "resource_id": data.get("resourceId") or "", "recording_uid": str(uid)}


def start(*, live_id: int, channel_name: str, resource_id: str, recording_uid: str) -> dict:
    endpoint = urlparse(os.getenv("R2_ENDPOINT_URL", "").strip()).netloc
    prefix = ["pulsesoc", "live-recordings", str(int(live_id))]
    client = {
        "token": _rtc_token(channel_name, int(recording_uid)),
        "recordingConfig": {"channelType": 1, "streamTypes": 2, "streamMode": "default", "videoStreamType": 0, "maxIdleTime": 120, "subscribeUidGroup": 0, "transcodingConfig": {"width": 720, "height": 1280, "fps": 30, "bitrate": 2500, "mixedVideoLayout": 2}},
        "recordingFileConfig": {"avFileType": ["hls"]},
        "storageConfig": {"vendor": 11, "region": 0, "bucket": os.environ["R2_BUCKET"], "accessKey": os.environ["R2_ACCESS_KEY_ID"], "secretKey": os.environ["R2_SECRET_ACCESS_KEY"], "fileNamePrefix": prefix, "extensionParams": {"endpoint": endpoint}},
    }
    result = _request(f"cloud_recording/resourceid/{quote(resource_id)}/mode/{MODE}/start", payload={"cname": channel_name, "uid": str(recording_uid), "clientRequest": client})
    data = result.get("data") or {}
    return {**result, "sid": data.get("sid") or "", "prefix": "/".join(prefix)}


def stop(*, channel_name: str, resource_id: str, sid: str, recording_uid: str) -> dict:
    result = _request(f"cloud_recording/resourceid/{quote(resource_id)}/sid/{quote(sid)}/mode/{MODE}/stop", payload={"cname": channel_name, "uid": str(recording_uid), "clientRequest": {"async_stop": False}})
    server = (result.get("data") or {}).get("serverResponse") or {}
    files = server.get("fileList") or []
    filename = files if isinstance(files, str) else next((x.get("fileName") or x.get("filename") for x in files if isinstance(x, dict) and (x.get("fileName") or x.get("filename"))), "")
    return {**result, "uploading_status": server.get("uploadingStatus") or "", "filename": filename or ""}


def public_recording_url(prefix: str, filename: str) -> str:
    base = os.getenv("R2_PUBLIC_BASE_URL", "").rstrip("/")
    parts = [quote(p, safe="") for p in f"{prefix}/{filename}".strip("/").split("/")]
    return f"{base}/{'/'.join(parts)}" if base and filename else ""
