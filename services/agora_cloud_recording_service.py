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


def query(*, resource_id: str, sid: str) -> dict:
    """Read-only status probe for an in-flight recording.

    Returns ok=True only while the recording session is still known to the
    provider. Once the recorder exits (stopped, idle timeout, kicked), the
    provider answers 404 and this returns ok=False, which callers use to
    decide whether an existing sid can be reused or a fresh start is needed.
    """
    if not resource_id or not sid:
        return {"ok": False, "reason": "missing_identifiers"}
    result = _request(f"cloud_recording/resourceid/{quote(resource_id)}/sid/{quote(sid)}/mode/{MODE}/query", method="GET")
    server = (result.get("data") or {}).get("serverResponse") or {}
    return {**result, "recording_status": server.get("status"), "upload_status": server.get("uploadingStatus") or ""}


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


def find_finalized_recording(prefix: str) -> dict:
    """Recover a stopped recorder when its stop response was lost on restart."""
    prefix = str(prefix or "").strip("/")
    if not prefix:
        return {"ok": False}
    try:
        import boto3
        from botocore.config import Config
        client = boto3.client("s3", endpoint_url=os.environ["R2_ENDPOINT_URL"], aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"], aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"], config=Config(signature_version="s3v4"))
        options = {"Bucket": os.environ["R2_BUCKET"], "Prefix": prefix + "/", "MaxKeys": 1000}
        for _ in range(3):
            page = client.list_objects_v2(**options)
            candidates = [item for item in page.get("Contents", []) if item["Key"].endswith(".m3u8") and not item["Key"].endswith("mux-ingest.m3u8")]
            for item in sorted(candidates, key=lambda item: item.get("LastModified", ""), reverse=True)[:10]:
                body = client.get_object(Bucket=options["Bucket"], Key=item["Key"])["Body"].read().decode("utf-8", "replace")
                if "#EXT-X-ENDLIST" in body and "#EXTINF:" in body:
                    return {"ok": True, "filename": item["Key"]}
            if not page.get("NextContinuationToken"):
                break
            options["ContinuationToken"] = page["NextContinuationToken"]
    except Exception:
        logging.warning("AGORA_RECORDING_RECOVERY_UNAVAILABLE")
    return {"ok": False}


def prepare_private_mux_input(prefix: str, filename: str) -> dict:
    """Build a private single-file input Mux can fetch directly from R2.

    Agora returns a finalized HLS playlist whose segments remain private in R2.
    Mux VOD ingest takes a muxed media file (MP4/MOV/MKV/TS) and rejects a
    playlist with ``invalid_input``, so the playlist itself can never be the
    input. The segments are MPEG-TS from one encoder with stable PIDs, which
    concatenate byte-for-byte into a single valid TS.

    The join streams R2 -> R2 through a bounded buffer so a long recording
    never lands on disk or sits in memory, and the result is reused when it is
    already present because the replay job retries.
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
        if "#EXT-X-ENDLIST" not in manifest:
            return {"ok": False, "reason": "recording_upload_pending", "message": "The recording is still uploading."}
        base_dir = posixpath.dirname(manifest_key)
        expires = max(900, min(int(os.getenv("R2_MUX_SIGNED_URL_TTL_SECONDS", "7200")), 21600))
        segment_keys = []
        for line in manifest.splitlines():
            uri = line.strip()
            if not uri or uri.startswith("#"):
                continue
            if "://" in uri:
                return {"ok": False, "reason": "external_segment"}
            segment_key = posixpath.normpath(posixpath.join(base_dir, uri))
            if not segment_key.startswith(f"{base_dir}/"):
                return {"ok": False, "reason": "invalid_segment_path"}
            segment_keys.append(segment_key)
        if not segment_keys:
            return {"ok": False, "reason": "empty_recording"}

        mux_key = posixpath.join(base_dir, "mux-ingest.ts")
        expected_bytes = 0
        for segment_key in segment_keys:
            expected_bytes += int(client.head_object(Bucket=bucket, Key=segment_key)["ContentLength"])
        if expected_bytes > int(os.getenv("R2_MUX_INPUT_MAX_BYTES", str(32 * 1024 ** 3))):
            return {"ok": False, "reason": "recording_too_large"}

        try:
            existing = int(client.head_object(Bucket=bucket, Key=mux_key)["ContentLength"])
        except Exception:
            existing = -1
        if existing != expected_bytes:
            _concatenate_segments(client, bucket, segment_keys, mux_key)

        input_url = client.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": mux_key}, ExpiresIn=expires)
        return {"ok": True, "input_url": input_url, "object_key": mux_key, "bytes": expected_bytes, "segments": len(segment_keys)}
    except Exception as exc:
        logging.warning("AGORA_RECORDING_MUX_INPUT_FAILED error_type=%s", type(exc).__name__)
        return {"ok": False, "reason": "mux_input_failed", "message": "The private recording could not be prepared for Mux."}


def _concatenate_segments(client, bucket: str, segment_keys: list, destination_key: str) -> None:
    """Join TS segments into one R2 object, flushing fixed-size parts.

    R2 requires every multipart part except the last to be the same size, so
    the buffer is drained in exact ``part_size`` slices rather than once per
    segment.
    """
    part_size = max(5 * 1024 ** 2, int(os.getenv("R2_MUX_INPUT_PART_BYTES", str(16 * 1024 ** 2))))
    upload_id = ""
    parts = []
    buffer = bytearray()

    def flush(final: bool) -> None:
        nonlocal upload_id, buffer
        while len(buffer) >= part_size or (final and buffer):
            chunk = bytes(buffer[:part_size])
            del buffer[:len(chunk)]
            if not upload_id:
                upload_id = client.create_multipart_upload(Bucket=bucket, Key=destination_key, ContentType="video/mp2t")["UploadId"]
            result = client.upload_part(Bucket=bucket, Key=destination_key, PartNumber=len(parts) + 1, UploadId=upload_id, Body=chunk)
            parts.append({"ETag": result["ETag"], "PartNumber": len(parts) + 1})
            if final and not buffer:
                return

    try:
        for segment_key in segment_keys:
            body = client.get_object(Bucket=bucket, Key=segment_key)["Body"]
            while True:
                block = body.read(1024 ** 2)
                if not block:
                    break
                buffer += block
            flush(False)
        if not upload_id:
            client.put_object(Bucket=bucket, Key=destination_key, Body=bytes(buffer), ContentType="video/mp2t")
            return
        flush(True)
        client.complete_multipart_upload(Bucket=bucket, Key=destination_key, UploadId=upload_id, MultipartUpload={"Parts": parts})
    except Exception:
        if upload_id:
            try:
                client.abort_multipart_upload(Bucket=bucket, Key=destination_key, UploadId=upload_id)
            except Exception:
                logging.warning("AGORA_RECORDING_MUX_INPUT_ABORT_FAILED")
        raise
