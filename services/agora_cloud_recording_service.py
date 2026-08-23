"""Server-only Agora Cloud Recording lifecycle for PulseSoc Live."""

from __future__ import annotations

import base64
import json
import logging
import os
import posixpath
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
        # Best-fit fills the 9:16 canvas for a host-only Live and produces an
        # intentional equal-tile layout when approved co-hosts publish. Agora's
        # vertical layout (2) requires maxResolutionUid; without it, the large
        # pane remains black and the host is relegated to a small side pane.
        "recordingConfig": {"channelType": 1, "streamTypes": 2, "streamMode": "default", "videoStreamType": 0, "maxIdleTime": 120, "subscribeUidGroup": 0, "transcodingConfig": {"width": 720, "height": 1280, "fps": 30, "bitrate": 2500, "mixedVideoLayout": 1, "backgroundColor": "#000000"}},
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
    prefix = str(prefix or "").strip("/")
    filename = str(filename or "").strip("/")
    key = filename if filename.startswith(f"{prefix}/") else f"{prefix}/{filename}".strip("/")
    parts = [quote(p, safe="") for p in key.split("/")]
    return f"{base}/{'/'.join(parts)}" if base and filename else ""


def prepare_private_mux_input(prefix: str, filename: str) -> dict:
    """Build a private HLS input Mux can fetch directly from R2.

    Agora returns a finalized HLS playlist whose segments remain private in R2.
    Replace only its relative segment references with short-lived SigV4 URLs;
    Mux then reads the original packets provider-to-provider. This avoids
    downloading and re-uploading the complete recording through PulseSoc.
    """
    try:
        import boto3
        from botocore.config import Config

        prefix = str(prefix or "").strip("/")
        filename = str(filename or "").strip("/")
        manifest_key = filename if filename.startswith(f"{prefix}/") else f"{prefix}/{filename}".strip("/")
        if not manifest_key.endswith(".m3u8"):
            return {"ok": False, "reason": "invalid_manifest"}
        client = boto3.client(
            "s3",
            endpoint_url=os.environ["R2_ENDPOINT_URL"],
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"],
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"],
            config=Config(signature_version="s3v4"),
        )
        bucket = os.environ["R2_BUCKET"]
        manifest = client.get_object(Bucket=bucket, Key=manifest_key)["Body"].read().decode("utf-8", "replace")
        base_dir = posixpath.dirname(manifest_key)
        expires = max(900, min(int(os.getenv("R2_MUX_SIGNED_URL_TTL_SECONDS", "7200")), 21600))
        rewritten = []
        segment_count = 0
        for line in manifest.splitlines():
            uri = line.strip()
            if not uri or uri.startswith("#"):
                rewritten.append(line)
                continue
            if "://" in uri:
                return {"ok": False, "reason": "external_segment"}
            segment_key = posixpath.normpath(posixpath.join(base_dir, uri))
            if not segment_key.startswith(f"{base_dir}/"):
                return {"ok": False, "reason": "invalid_segment_path"}
            rewritten.append(client.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": segment_key}, ExpiresIn=expires))
            segment_count += 1
        if not segment_count:
            return {"ok": False, "reason": "empty_recording"}
        mux_key = posixpath.join(base_dir, "mux-ingest.m3u8")
        mux_manifest = ("\n".join(rewritten) + "\n").encode("utf-8")
        client.put_object(Bucket=bucket, Key=mux_key, Body=mux_manifest, ContentType="application/vnd.apple.mpegurl")
        input_url = client.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": mux_key}, ExpiresIn=expires)
        return {"ok": True, "input_url": input_url, "object_key": mux_key, "bytes": len(mux_manifest), "segments": segment_count}
    except Exception as exc:
        logging.warning("AGORA_RECORDING_MUX_INPUT_FAILED error_type=%s", type(exc).__name__)
        return {"ok": False, "reason": "mux_input_failed", "message": "The private recording could not be prepared for Mux."}
