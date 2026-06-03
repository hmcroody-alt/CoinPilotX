"""Canonical durable media service for Pulse, Messenger, Reels, and uploads."""

import mimetypes
import os
import secrets
import logging
import base64
import json
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from werkzeug.utils import secure_filename

from . import media_storage
from . import user_context


ALLOWED_TYPES = {x.strip().lower() for x in os.getenv("MEDIA_UPLOAD_ALLOWED_TYPES", "jpg,jpeg,png,webp,gif,mp4,webm,mov,mp3,m4a,wav,ogg,pdf,txt,doc,docx").split(",")}
IMAGE_EXTS = {"jpg", "jpeg", "png", "webp"}
GIF_EXTS = {"gif"}
VIDEO_EXTS = {"mp4", "webm", "mov"}
AUDIO_EXTS = {"mp3", "m4a", "wav", "ogg"}
FILE_EXTS = {"pdf", "txt", "doc", "docx"}
UPLOAD_ROOT = Path(os.getenv("MEDIA_UPLOAD_DIR", "static/uploads/chat_media"))
STATIC_ROOT = Path(os.getenv("STATIC_ROOT", "static")).resolve()
FALLBACK_URL = "/static/img/media-unavailable.svg"
_MUX_DIAGNOSTICS_LOGGED = False


def _clean_env_secret(value):
    return str(value or "").strip().strip("\"'")


def _now():
    return datetime.utcnow().isoformat(timespec="seconds")


def _limit_bytes(ext):
    if ext in IMAGE_EXTS:
        return int(float(os.getenv("MEDIA_UPLOAD_MAX_IMAGE_MB", "5")) * 1024 * 1024)
    if ext in GIF_EXTS:
        return int(float(os.getenv("MEDIA_UPLOAD_MAX_GIF_MB", "8")) * 1024 * 1024)
    if ext in AUDIO_EXTS:
        return int(float(os.getenv("MEDIA_UPLOAD_MAX_AUDIO_MB", "15")) * 1024 * 1024)
    if ext in FILE_EXTS:
        return int(float(os.getenv("MEDIA_UPLOAD_MAX_FILE_MB", "12")) * 1024 * 1024)
    return int(float(os.getenv("MEDIA_UPLOAD_MAX_VIDEO_MB", "150")) * 1024 * 1024)


def _media_type(ext):
    if ext in IMAGE_EXTS:
        return "image"
    if ext in GIF_EXTS:
        return "gif"
    if ext in VIDEO_EXTS:
        return "video"
    if ext in AUDIO_EXTS:
        return "audio"
    if ext in FILE_EXTS:
        return "file"
    return ""


def _is_video_url(value):
    lowered = str(value or "").split("?", 1)[0].split("#", 1)[0].lower()
    return any(lowered.endswith(f".{ext}") for ext in VIDEO_EXTS | {"m4v", "qt"})


def _is_image_url(value):
    lowered = str(value or "").split("?", 1)[0].split("#", 1)[0].lower()
    return any(lowered.endswith(f".{ext}") for ext in IMAGE_EXTS | GIF_EXTS | {"avif"})


def mux_playback_urls(playback_id):
    """Return safe public Mux playback URLs for a playback id, without secrets."""
    playback_id = str(playback_id or "").strip()
    if not playback_id:
        return {"hls_url": "", "thumbnail_url": ""}
    safe_id = "".join(ch for ch in playback_id if ch.isalnum() or ch in {"_", "-"})
    if not safe_id:
        return {"hls_url": "", "thumbnail_url": ""}
    return {
        "hls_url": f"https://stream.mux.com/{safe_id}.m3u8",
        "thumbnail_url": f"https://image.mux.com/{safe_id}/thumbnail.jpg",
    }


def mux_diagnostics():
    """Expose Mux readiness without revealing token values."""
    token_id = _clean_env_secret(os.getenv("MUX_TOKEN_ID"))
    token_secret = _clean_env_secret(os.getenv("MUX_TOKEN_SECRET"))
    webhook_secret = _clean_env_secret(os.getenv("MUX_WEBHOOK_SECRET"))
    data_env_key = _clean_env_secret(os.getenv("MUX_DATA_ENV_KEY"))
    return {
        "configured": bool(token_id and token_secret),
        "token_id_configured": bool(token_id),
        "token_secret_configured": bool(token_secret),
        "webhook_secret_configured": bool(webhook_secret),
        "data_env_key_configured": bool(data_env_key),
        "data_env_key_used": bool((os.getenv("MUX_DATA_ANALYTICS_ENABLED") or "").strip().lower() == "true" and data_env_key),
    }


def log_mux_diagnostics_once():
    global _MUX_DIAGNOSTICS_LOGGED
    if _MUX_DIAGNOSTICS_LOGGED:
        return
    _MUX_DIAGNOSTICS_LOGGED = True
    diag = mux_diagnostics()
    logging.info(
        "PULSE_MUX_ENV_DIAGNOSTICS MUX_TOKEN_ID_loaded=%s MUX_TOKEN_SECRET_loaded=%s MUX_WEBHOOK_SECRET_loaded=%s MUX_DATA_ENV_KEY_loaded=%s MUX_DATA_ENV_KEY_used=%s",
        bool(diag.get("token_id_configured")),
        bool(diag.get("token_secret_configured")),
        bool(diag.get("webhook_secret_configured")),
        bool(diag.get("data_env_key_configured")),
        bool(diag.get("data_env_key_used")),
    )


def _mux_auth_header():
    token_id = _clean_env_secret(os.getenv("MUX_TOKEN_ID"))
    token_secret = _clean_env_secret(os.getenv("MUX_TOKEN_SECRET"))
    if not token_id or not token_secret:
        return ""
    raw = f"{token_id}:{token_secret}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def create_mux_asset_from_url(input_url, *, trace_id="", media_id=0):
    """Create a public Mux playback asset from an already durable media URL."""
    log_mux_diagnostics_once()
    input_url = normalize_url(input_url)
    auth_header = _mux_auth_header()
    configured = bool(auth_header)
    logging.info(
        "PULSE_MUX_ASSET_CREATE_ATTEMPT trace_id=%s media_id=%s mux_token_id_present=%s mux_token_secret_present=%s mux_asset_create_attempt=%s input_url_present=%s input_url_https=%s",
        trace_id,
        media_id,
        bool(_clean_env_secret(os.getenv("MUX_TOKEN_ID"))),
        bool(_clean_env_secret(os.getenv("MUX_TOKEN_SECRET"))),
        bool(configured and input_url and input_url.startswith("https://")),
        bool(input_url),
        bool(input_url.startswith("https://")),
    )
    if not input_url or not input_url.startswith("https://") or not auth_header:
        error_type = "not_configured" if not auth_header else "missing_public_https_input"
        logging.warning(
            "PULSE_MUX_ASSET_CREATE_SKIPPED trace_id=%s media_id=%s mux_asset_create_status=skipped mux_asset_create_error_type=%s",
            trace_id,
            media_id,
            error_type,
        )
        return {
            "ok": False,
            "status": "not_configured" if not auth_header else "missing_input",
            "error_type": error_type,
            "status_code": 0,
            "message": "Mux credentials are missing." if not auth_header else "Mux needs a public HTTPS media URL.",
        }
    source_check = inspect_mux_source_url(input_url)
    logging.info(
        "MUX_SOURCE_URL trace_id=%s media_id=%s url=%s",
        trace_id,
        media_id,
        input_url,
    )
    logging.info(
        "MUX_SOURCE_FETCH_STATUS trace_id=%s media_id=%s status=%s content_type=%s content_length=%s redirects=%s final_url=%s error_type=%s",
        trace_id,
        media_id,
        source_check.get("status") or 0,
        source_check.get("content_type") or "",
        source_check.get("content_length") or "",
        source_check.get("redirects") or 0,
        source_check.get("final_url") or input_url,
        source_check.get("error_type") or "",
    )
    source_content_type = str(source_check.get("content_type") or "").lower()
    if not source_check.get("ok") or "text/html" in source_content_type:
        error_type = source_check.get("error_type") or ("html_response" if "text/html" in source_content_type else "source_unreachable")
        logging.warning(
            "PULSE_MUX_SOURCE_UNREACHABLE trace_id=%s media_id=%s mux_asset_create_status=blocked mux_asset_create_error_type=%s source_status=%s source_content_type=%s",
            trace_id,
            media_id,
            error_type,
            source_check.get("status") or 0,
            source_check.get("content_type") or "",
        )
        return {
            "ok": False,
            "status": "source_unreachable",
            "status_code": source_check.get("status") or 0,
            "error_type": error_type,
            "source": source_check,
            "message": "Mux cannot download the video source URL. Configure MUX_SOURCE_BASE_URL or R2_MUX_SOURCE_BASE_URL to a public R2 source.",
        }
    payload = {
        "input": input_url,
        "playback_policy": ["public"],
        "mp4_support": "standard",
    }
    request = Request(
        "https://api.mux.com/video/v1/assets",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": auth_header,
            "Content-Type": "application/json",
            "User-Agent": "CoinPilotX-MuxMedia/1.0",
        },
        method="POST",
    )
    try:
        with urlopen(request, timeout=float(os.getenv("MUX_ASSET_CREATE_TIMEOUT_SECONDS", "8"))) as response:
            body = response.read().decode("utf-8", "replace")
            data = json.loads(body or "{}")
            response_status = int(getattr(response, "status", 0) or 0)
    except HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:
            body = ""
        error_type = "unauthorized" if exc.code in {401, 403} else "mux_api_http_error"
        logging.warning(
            "PULSE_MUX_ASSET_CREATE_FAILED trace_id=%s media_id=%s mux_asset_create_status=failed mux_asset_create_status_code=%s mux_asset_create_error_type=%s response_body=%s",
            trace_id,
            media_id,
            exc.code,
            error_type,
            body[:500],
        )
        logging.warning("MUX_ASSET_CREATE_RESPONSE trace_id=%s media_id=%s status=failed status_code=%s error_type=%s", trace_id, media_id, exc.code, error_type)
        return {"ok": False, "status": "failed", "status_code": exc.code, "error_type": error_type, "error": body[:500], "message": "Mux rejected the upload credentials." if exc.code in {401, 403} else "Mux asset creation failed."}
    except URLError as exc:
        logging.warning(
            "PULSE_MUX_ASSET_CREATE_FAILED trace_id=%s media_id=%s mux_asset_create_status=failed mux_asset_create_error_type=network_error error=%s",
            trace_id,
            media_id,
            str(exc)[:300],
        )
        logging.warning("MUX_ASSET_CREATE_RESPONSE trace_id=%s media_id=%s status=failed status_code=0 error_type=network_error", trace_id, media_id)
        return {"ok": False, "status": "failed", "status_code": 0, "error_type": "network_error", "error": str(exc)[:500], "message": "Mux could not be reached from the server."}
    except Exception as exc:
        logging.warning(
            "PULSE_MUX_ASSET_CREATE_FAILED trace_id=%s media_id=%s mux_asset_create_status=failed mux_asset_create_error_type=unexpected_error error=%s",
            trace_id,
            media_id,
            str(exc)[:300],
        )
        logging.warning("MUX_ASSET_CREATE_RESPONSE trace_id=%s media_id=%s status=failed status_code=0 error_type=unexpected_error", trace_id, media_id)
        return {"ok": False, "status": "failed", "status_code": 0, "error_type": "unexpected_error", "error": str(exc)[:500], "message": "Mux asset creation failed."}
    asset = data.get("data") or {}
    playback_ids = asset.get("playback_ids") or []
    playback_id = ""
    for item in playback_ids:
        if item.get("policy") == "public" or not playback_id:
            playback_id = item.get("id") or ""
    result = {
        "ok": bool(asset.get("id") and playback_id),
        "asset_id": asset.get("id") or "",
        "playback_id": playback_id,
        "status": asset.get("status") or "created",
        "status_code": response_status,
        "error_type": "" if asset.get("id") and playback_id else "missing_playback_id",
    }
    if result["ok"]:
        logging.info(
            "PULSE_MUX_ASSET_CREATED trace_id=%s media_id=%s mux_asset_id=%s mux_playback_id=%s mux_status=%s mux_asset_create_status_code=%s",
            trace_id,
            media_id,
            result["asset_id"],
            result["playback_id"],
            result["status"],
            response_status,
        )
        logging.info(
            "MUX_ASSET_CREATE_RESPONSE trace_id=%s media_id=%s status=%s status_code=%s mux_asset_id=%s mux_playback_id=%s",
            trace_id,
            media_id,
            result["status"],
            response_status,
            result["asset_id"],
            result["playback_id"],
        )
    else:
        logging.warning(
            "PULSE_MUX_ASSET_CREATE_INCOMPLETE trace_id=%s media_id=%s mux_asset_create_status=incomplete mux_asset_create_status_code=%s mux_asset_create_error_type=%s mux_asset_id_present=%s mux_playback_id_present=%s",
            trace_id,
            media_id,
            response_status,
            result["error_type"],
            bool(result["asset_id"]),
            bool(result["playback_id"]),
        )
        logging.warning("MUX_ASSET_CREATE_RESPONSE trace_id=%s media_id=%s status=incomplete status_code=%s error_type=%s", trace_id, media_id, response_status, result["error_type"])
    return result


def _public_url_for_path(path):
    resolved = Path(path).resolve()
    static_root = Path("static").resolve()
    try:
        relative = resolved.relative_to(static_root)
        return "/static/" + str(relative).replace(os.sep, "/")
    except ValueError:
        pass
    try:
        relative = resolved.relative_to(UPLOAD_ROOT.resolve())
        return "/uploads/" + str(relative).replace(os.sep, "/")
    except ValueError:
        pass
    return "/" + str(Path(path)).replace(os.sep, "/").lstrip("/")


def normalize_url(url):
    value = str(url or "").strip().replace("\\", "/")
    if not value:
        return ""
    if value.startswith(("http://", "https://", "data:", "blob:")):
        return value
    for marker in ("/static/uploads/", "static/uploads/"):
        if marker in value:
            found = value[value.index(marker):]
            return found if found.startswith("/") else "/" + found
    for marker in ("/uploads/", "uploads/"):
        if marker in value:
            found = value[value.index(marker):]
            return found if found.startswith("/") else "/" + found
    if value.startswith("/static/") or value.startswith("/uploads/"):
        return value
    if value.startswith("static/") or value.startswith("uploads/"):
        return "/" + value
    try:
        path_value = Path(value).expanduser()
        if path_value.is_absolute():
            resolved = path_value.resolve()
            try:
                rel = resolved.relative_to(STATIC_ROOT)
                return "/static/" + str(rel).replace(os.sep, "/")
            except ValueError:
                pass
            try:
                rel = resolved.relative_to(Path(os.getenv("MEDIA_UPLOAD_DIR", "static/uploads")).resolve())
                return "/uploads/" + str(rel).replace(os.sep, "/")
            except ValueError:
                pass
    except Exception:
        pass
    if "/" not in value and "." in value:
        return "/static/uploads/" + value
    return value if value.startswith("/") else "/" + value


def cdn_url_for_key(storage_key):
    """Return the canonical CDN URL for an object-storage key."""
    key = str(storage_key or "").strip().replace("\\", "/").lstrip("/")
    if not key:
        return ""
    base = os.getenv("R2_PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not base:
        return ""
    return f"{base}/{key}"


def mux_source_url_for_key(storage_key, fallback_url=""):
    """Return a public media source URL that Mux can download without app auth/cookies."""
    key = str(storage_key or "").strip().replace("\\", "/").lstrip("/")
    for env_key in (
        "MUX_SOURCE_BASE_URL",
        "R2_MUX_SOURCE_BASE_URL",
        "R2_DIRECT_PUBLIC_BASE_URL",
        "R2_PUBLIC_DEV_URL",
        "R2_R2DEV_BASE_URL",
    ):
        base = str(os.getenv(env_key) or "").strip().rstrip("/")
        if base and key:
            return f"{base}/{key}"
    return normalize_url(fallback_url)


def inspect_mux_source_url(source_url, *, timeout=4.0):
    """Fetch public source headers before handing a URL to Mux."""
    value = normalize_url(source_url)
    result = {
        "ok": False,
        "status": 0,
        "content_type": "",
        "content_length": "",
        "final_url": value,
        "redirects": 0,
        "error_type": "",
    }
    if not value.startswith("https://"):
        result["error_type"] = "not_https"
        return result
    opener = None
    try:
        import urllib.request

        class _RedirectTracker(urllib.request.HTTPRedirectHandler):
            redirects = 0

            def redirect_request(self, req, fp, code, msg, headers, newurl):
                self.redirects += 1
                return super().redirect_request(req, fp, code, msg, headers, newurl)

        tracker = _RedirectTracker()
        opener = urllib.request.build_opener(tracker)
        request = Request(value, method="HEAD", headers={"User-Agent": "Mux Video Ingest/1.0"})
        with opener.open(request, timeout=float(timeout or 4.0)) as response:
            result.update({
                "ok": 200 <= int(getattr(response, "status", 0) or 0) < 400,
                "status": int(getattr(response, "status", 0) or 0),
                "content_type": response.headers.get("content-type", ""),
                "content_length": response.headers.get("content-length", ""),
                "final_url": response.geturl() or value,
                "redirects": tracker.redirects,
            })
            return result
    except HTTPError as exc:
        if exc.code in {405, 501}:
            try:
                request = Request(value, headers={"User-Agent": "Mux Video Ingest/1.0", "Range": "bytes=0-0"})
                opener = opener or urlopen
                if hasattr(opener, "open"):
                    response_ctx = opener.open(request, timeout=float(timeout or 4.0))
                else:
                    response_ctx = opener(request, timeout=float(timeout or 4.0))
                with response_ctx as response:
                    status = int(getattr(response, "status", 0) or 0)
                    result.update({
                        "ok": status in {200, 206},
                        "status": status,
                        "content_type": response.headers.get("content-type", ""),
                        "content_length": response.headers.get("content-length", ""),
                        "final_url": response.geturl() or value,
                    })
                    return result
            except Exception as fallback_exc:
                result["error_type"] = fallback_exc.__class__.__name__
                return result
        result.update({
            "status": int(exc.code or 0),
            "content_type": exc.headers.get("content-type", "") if exc.headers else "",
            "content_length": exc.headers.get("content-length", "") if exc.headers else "",
            "error_type": "cloudflare_challenge" if (exc.headers and exc.headers.get("cf-mitigated") == "challenge") else "http_error",
        })
        return result
    except Exception as exc:
        result["error_type"] = exc.__class__.__name__
        return result


def stream_url_for_media(media_id, media_type="", storage_provider="", storage_key=""):
    """Use first-party streaming for durable video/audio when edge security blocks raw CDN files."""
    try:
        media_id = int(media_id or 0)
    except Exception:
        media_id = 0
    kind = str(media_type or "").lower()
    provider = str(storage_provider or "").lower()
    if media_id and kind in {"video", "audio"} and provider in {"r2", "s3"} and storage_key:
        return f"/api/pulse/media/{media_id}/stream"
    return ""


def _cdn_url_from_r2_private_url(value):
    """Map a private R2/S3 endpoint URL to the public CDN URL when possible."""
    try:
        parsed = urlparse(str(value or "").strip())
    except Exception:
        return ""
    host = (parsed.netloc or "").lower()
    if "r2.cloudflarestorage.com" not in host:
        return ""
    key = (parsed.path or "").lstrip("/")
    bucket = os.getenv("R2_BUCKET", "").strip().strip("/")
    if bucket and key.startswith(bucket + "/"):
        key = key[len(bucket) + 1 :]
    return cdn_url_for_key(key)


def validate_remote_url(url, timeout=2.5):
    """Fast HEAD validation for audits and admin diagnostics, not hot rendering."""
    value = normalize_url(url)
    if not value.startswith(("http://", "https://")):
        return _url_available(value), "local"
    try:
        req = Request(value, method="HEAD", headers={"User-Agent": "CoinPilotX-MediaAudit/1.0"})
        with urlopen(req, timeout=float(timeout or 2.5)) as resp:
            return 200 <= int(getattr(resp, "status", 200)) < 400, str(getattr(resp, "status", "ok"))
    except Exception as exc:
        return False, exc.__class__.__name__


def local_path_for_url(url):
    value = normalize_url(url)
    if not value or value.startswith(("http://", "https://", "data:", "blob:")):
        return None
    parsed = urlparse(value)
    path = parsed.path or value
    upload_root = Path(os.getenv("MEDIA_UPLOAD_DIR", "static/uploads")).resolve()
    candidates = []
    if path.startswith("/static/"):
        candidates.append(STATIC_ROOT / path[len("/static/"):])
    if path.startswith("/uploads/"):
        candidates.append(upload_root / path[len("/uploads/"):])
        candidates.append(STATIC_ROOT / "uploads" / path[len("/uploads/"):])
    if path.startswith("static/"):
        candidates.append(Path(path).resolve())
    for candidate in candidates:
        try:
            if candidate.exists():
                return str(candidate)
        except Exception:
            continue
    return str(candidates[0]) if candidates else None


def _url_available(url, provider=""):
    value = normalize_url(url)
    if not value:
        return False
    if value.startswith(("data:", "blob:")):
        return True
    if value.startswith(("http://", "https://")):
        return True
    path = local_path_for_url(value)
    return bool(path and os.path.exists(path))


def _orientation(width, height, ratio):
    try:
        ratio = float(ratio or 0) or (float(width) / float(height) if width and height else 0)
    except Exception:
        ratio = 0
    if not ratio:
        return "unknown"
    if abs(ratio - 1) < 0.08:
        return "square"
    return "landscape" if ratio > 1 else "portrait"


def resolve_media(media=None, *, url="", thumbnail_url="", poster_url="", media_type="", check_remote=False):
    item = dict(media or {})
    storage_key = item.get("storage_key") or item.get("object_key") or item.get("stored_filename") or ""
    mux_playback_id = item.get("mux_playback_id") or item.get("muxPlaybackId") or item.get("playback_id") or ""
    mux_status = str(item.get("mux_status") or "").strip().lower()
    mux_urls = mux_playback_urls(mux_playback_id)
    canonical_cdn_url = cdn_url_for_key(storage_key)
    source = url or item.get("cdn_url") or item.get("public_url") or item.get("media_url") or item.get("valid_url") or canonical_cdn_url or ""
    thumb = thumbnail_url or item.get("thumbnail_url") or item.get("medium_url") or item.get("small_url") or mux_urls["thumbnail_url"] or source
    poster = poster_url or item.get("poster_url") or thumb
    private_source = _cdn_url_from_r2_private_url(source)
    if private_source:
        source = private_source
    source = normalize_url(source)
    if source.startswith("/static/uploads/") and canonical_cdn_url and media_storage.provider() in {"r2", "s3"}:
        source = canonical_cdn_url
    private_thumb = _cdn_url_from_r2_private_url(thumb)
    if private_thumb:
        thumb = private_thumb
    thumb = normalize_url(thumb)
    if thumb.startswith("/static/uploads/") and canonical_cdn_url and media_storage.provider() in {"r2", "s3"}:
        thumb = canonical_cdn_url
    private_poster = _cdn_url_from_r2_private_url(poster)
    if private_poster:
        poster = private_poster
    poster = normalize_url(poster)
    if poster.startswith("/static/uploads/") and canonical_cdn_url and media_storage.provider() in {"r2", "s3"}:
        poster = canonical_cdn_url
    width = int(float(item.get("width") or 0) or 0)
    height = int(float(item.get("height") or 0) or 0)
    ratio = item.get("aspect_ratio")
    try:
        ratio = round(float(ratio or 0), 4)
    except Exception:
        ratio = 0
    if not ratio and width and height:
        ratio = round(float(width) / float(height), 4)
    kind = (media_type or item.get("media_type") or "").lower()
    if not kind:
        lowered = source.lower()
        if any(lowered.endswith(ext) for ext in (".mp4", ".webm", ".mov", ".m4v")):
            kind = "video"
        elif any(lowered.endswith(ext) for ext in (".mp3", ".m4a", ".wav", ".ogg")):
            kind = "audio"
        else:
            kind = "image"
    if kind == "video":
        if _is_video_url(thumb):
            thumb = ""
        if _is_video_url(poster):
            poster = ""
        if not poster and _is_image_url(item.get("thumbnail_url") or ""):
            poster = normalize_url(item.get("thumbnail_url") or "")
    if thumb and not _is_image_url(thumb):
        thumb = poster or source
    if mux_urls["thumbnail_url"] and not poster:
        poster = mux_urls["thumbnail_url"]
    try:
        duration = float(item.get("duration") or item.get("duration_seconds") or 0)
    except Exception:
        duration = 0
    has_audio = item.get("has_audio")
    if has_audio is None:
        has_audio = item.get("audio_tracks")
    if has_audio is None:
        has_audio = item.get("audio_track_id")
    try:
        has_audio = None if has_audio is None or has_audio == "" else bool(int(has_audio))
    except Exception:
        has_audio = bool(has_audio)
    provider = item.get("storage_provider") or item.get("provider") or ("r2" if canonical_cdn_url else "remote" if source.startswith(("http://", "https://")) else media_storage.provider())
    first_party_stream = stream_url_for_media(item.get("id"), kind, provider, storage_key)
    saved_playback_url = normalize_url(item.get("playback_url") or "")
    available = item.get("is_available")
    if available is None:
        if check_remote and source.startswith(("http://", "https://")):
            available, _ = validate_remote_url(source)
        else:
            available = _url_available(source, provider=provider)
    else:
        available = bool(int(available)) if str(available).isdigit() else bool(available)
    srcset = item.get("srcset") or ""
    variants = {
        "thumbnail": normalize_url(item.get("thumbnail_url") or thumb),
        "small": normalize_url(item.get("small_url") or thumb),
        "medium": normalize_url(item.get("medium_url") or source),
        "large": normalize_url(item.get("large_url") or source),
        "original": source,
    }
    if not srcset and source and kind in {"image", "gif"}:
        srcset = ", ".join(
            f"{v} {w}w" for v, w in [
                (variants["thumbnail"], 320),
                (variants["medium"], 960),
                (variants["large"], 1440),
                (variants["original"], 2048),
            ] if v
        )
    poster_value = (poster or thumb or source)
    if kind == "video" and _is_video_url(poster_value):
        poster_value = ""
    mux_playback_url = mux_urls["hls_url"] if kind == "video" else ""
    mux_processing = bool(kind == "video" and mux_playback_url and mux_status and mux_status not in {"ready", "asset_ready", "available"})
    return {
        "id": item.get("id"),
        "valid_url": source if available else "",
        "cdn_url": item.get("cdn_url") or canonical_cdn_url,
        "media_url": source,
        "playback_url": mux_playback_url or saved_playback_url or first_party_stream or source,
        "thumbnail_url": thumb or source,
        "poster_url": poster_value,
        "mux_playback_id": mux_playback_id,
        "mux_asset_id": item.get("mux_asset_id") or "",
        "mux_status": mux_status or item.get("mux_status") or "",
        "processing_status": item.get("processing_status") or ("mux_processing" if mux_processing else "ready"),
        "mux_hls_url": mux_urls["hls_url"],
        "mux_thumbnail_url": mux_urls["thumbnail_url"],
        "mux_processing": mux_processing,
        "fallback_url": FALLBACK_URL,
        "media_type": kind,
        "mime_type": item.get("mime_type") or mimetypes.guess_type(source)[0] or "",
        "playback_mime_type": "application/vnd.apple.mpegurl" if mux_playback_url else (item.get("playback_mime_type") or ("video/mp4" if first_party_stream and kind == "video" else "")),
        "file_size_bytes": item.get("file_size_bytes") or item.get("file_size") or 0,
        "duration": duration,
        "has_audio": has_audio,
        "width": width,
        "height": height,
        "aspect_ratio": ratio,
        "orientation": _orientation(width, height, ratio),
        "is_available": bool(available),
        "storage_provider": provider,
        "storage_key": storage_key,
        "bucket": item.get("bucket") or "",
        "object_key": item.get("object_key") or storage_key,
        "verification_status": item.get("verification_status") or ("verified" if available else "failed" if source else "missing"),
        "processing_status": item.get("processing_status") or ("mux_processing" if mux_processing else "ready" if available else "failed"),
        "trace_id": item.get("trace_id") or "",
        "error_message": item.get("error_message") or item.get("availability_error") or "",
        "created_at": item.get("created_at") or "",
        "hydration_state": "ready" if available else ("restoring" if source else "missing"),
        "content_type_verified": bool(item.get("mime_type") or mimetypes.guess_type(source)[0]),
        "srcset": srcset,
        "sizes": item.get("sizes") or "(max-width: 760px) 100vw, (max-width: 1400px) 760px, 900px",
        "variants": variants,
        "diagnostics": {
            "source": source,
            "cdn_url": item.get("cdn_url") or canonical_cdn_url,
            "thumbnail": thumb,
            "provider": provider,
            "mux_playback_id": mux_playback_id,
            "mux_status": mux_status or "",
            "local_path": local_path_for_url(source) if source and not source.startswith(("http://", "https://")) else "",
        },
    }


def _image_header_ok(ext, header):
    return _media_header_ok(ext, header)


def _looks_like_text(header):
    if b"\x00" in header:
        return False
    try:
        header.decode("utf-8")
        return True
    except UnicodeDecodeError:
        return False


def _media_header_ok(ext, header):
    header = header or b""
    if ext in {"jpg", "jpeg"}:
        return header.startswith(b"\xff\xd8\xff")
    if ext == "png":
        return header.startswith(b"\x89PNG\r\n\x1a\n")
    if ext == "gif":
        return header.startswith((b"GIF87a", b"GIF89a"))
    if ext == "webp":
        return header[:4] == b"RIFF" and header[8:12] == b"WEBP"
    if ext in {"mp4", "mov", "m4a"}:
        return b"ftyp" in header[:32]
    if ext == "webm":
        return header.startswith(b"\x1a\x45\xdf\xa3")
    if ext == "mp3":
        return header.startswith(b"ID3") or (len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xE0) == 0xE0)
    if ext == "wav":
        return header.startswith(b"RIFF") and header[8:12] == b"WAVE"
    if ext == "ogg":
        return header.startswith(b"OggS")
    if ext == "pdf":
        return header.startswith(b"%PDF-")
    if ext == "docx":
        return header.startswith(b"PK\x03\x04")
    if ext == "doc":
        return header.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")
    if ext == "txt":
        return _looks_like_text(header)
    return True


def _public(row):
    if not row:
        return None
    item = dict(row)
    width = item.get("width")
    height = item.get("height")
    aspect_ratio = None
    try:
        if width and height:
            aspect_ratio = round(float(width) / float(height), 4)
    except Exception:
        aspect_ratio = None
    resolved = resolve_media(item)
    return {
        "id": item.get("id"),
        "media_url": resolved["media_url"],
        "valid_url": resolved["valid_url"],
        "thumbnail_url": resolved["thumbnail_url"],
        "poster_url": resolved["poster_url"],
        "fallback_url": resolved["fallback_url"],
        "media_type": resolved["media_type"],
        "mime_type": resolved["mime_type"],
        "file_size_bytes": item.get("file_size_bytes"),
        "width": width,
        "height": height,
        "aspect_ratio": aspect_ratio or resolved["aspect_ratio"],
        "orientation": resolved["orientation"],
        "is_available": resolved["is_available"],
        "storage_provider": resolved["storage_provider"],
        "storage_key": resolved["storage_key"],
        "bucket": resolved["bucket"],
        "object_key": resolved["object_key"],
        "cdn_url": resolved["cdn_url"],
        "playback_url": resolved["playback_url"],
        "playback_storage_key": item.get("playback_storage_key") or "",
        "playback_mime_type": resolved["playback_mime_type"],
        "mux_playback_id": resolved["mux_playback_id"],
        "mux_asset_id": resolved["mux_asset_id"],
        "mux_status": resolved["mux_status"],
        "mux_hls_url": resolved["mux_hls_url"],
        "mux_thumbnail_url": resolved["mux_thumbnail_url"],
        "mux_processing": resolved["mux_processing"],
        "duration": resolved["duration"],
        "has_audio": resolved["has_audio"],
        "created_at": resolved["created_at"],
        "verification_status": resolved["verification_status"],
        "processing_status": resolved["processing_status"],
        "trace_id": resolved["trace_id"],
        "error_message": resolved["error_message"],
        "srcset": resolved["srcset"],
        "sizes": resolved["sizes"],
        "moderation_status": item.get("moderation_status") or "pending",
    }


def _image_dimensions(path):
    try:
        from PIL import Image

        with Image.open(path) as img:
            width, height = img.size
            return int(width or 0), int(height or 0)
    except Exception:
        return None, None


def rate_limited(user_id, media_type):
    conn = user_context.connect()
    cur = conn.cursor()
    cutoff = (datetime.utcnow() - timedelta(minutes=5)).isoformat(timespec="seconds")
    cur.execute("SELECT COUNT(*) AS c FROM chat_media_uploads WHERE uploader_user_id=? AND created_at>=?", (int(user_id), cutoff))
    recent = int((cur.fetchone() or {"c": 0})["c"] or 0)
    if recent >= 10:
        conn.close()
        return True
    if media_type == "video":
        video_cutoff = (datetime.utcnow() - timedelta(minutes=30)).isoformat(timespec="seconds")
        cur.execute("SELECT COUNT(*) AS c FROM chat_media_uploads WHERE uploader_user_id=? AND media_type='video' AND created_at>=?", (int(user_id), video_cutoff))
        videos = int((cur.fetchone() or {"c": 0})["c"] or 0)
        conn.close()
        return videos >= 3
    conn.close()
    return False


def save_upload(user_id, file_storage, context_type="private_chat", context_id=""):
    upload_trace = secrets.token_hex(6)
    if not file_storage or not file_storage.filename:
        return {"ok": False, "message": "Choose a photo, GIF, video, voice note, audio clip, or safe file."}, 400
    original = secure_filename(file_storage.filename)
    ext = original.rsplit(".", 1)[-1].lower() if "." in original else ""
    if ext not in ALLOWED_TYPES:
        return {"ok": False, "message": "That file type is not supported."}, 400
    media_type = _media_type(ext)
    if not media_type:
        return {"ok": False, "message": "That file type is not supported."}, 400
    if rate_limited(user_id, media_type):
        return {"ok": False, "message": "You’re sending media quickly. Try again in a few minutes."}, 429
    file_storage.stream.seek(0, os.SEEK_END)
    size = file_storage.stream.tell()
    file_storage.stream.seek(0)
    if size > _limit_bytes(ext):
        return {"ok": False, "message": "File too large. Please upload a smaller media file."}, 400
    header = file_storage.stream.read(512)
    file_storage.stream.seek(0)
    if size and not _media_header_ok(ext, header):
        return {"ok": False, "message": "This media file could not be verified safely."}, 400
    folder = "pulse_media" if str(context_type or "").startswith("pulse") else "chat_media"
    logging.info(
        "PULSE_MEDIA_UPLOAD_START trace_id=%s user_id=%s context_type=%s context_id=%s filename=%s media_type=%s size=%s provider=%s",
        upload_trace,
        int(user_id),
        context_type,
        context_id,
        original,
        media_type,
        size,
        media_storage.provider(),
    )
    storage = media_storage.save_public_file(file_storage, folder=folder)
    logging.info(
        "PULSE_MEDIA_UPLOAD_STORAGE_RESULT trace_id=%s user_id=%s context_type=%s storage_provider=%s durable_uploaded=%s storage_key=%s public_url=%s upload_error=%s",
        upload_trace,
        int(user_id),
        context_type,
        storage.get("provider"),
        bool(storage.get("durable_uploaded")),
        storage.get("storage_key"),
        storage.get("media_url"),
        storage.get("upload_error") or "",
    )
    if media_storage.provider() in {"r2", "s3"} and not storage.get("durable_uploaded"):
        logging.error(
            "PULSE_MEDIA_UPLOAD_DURABLE_REQUIRED_FAILED trace_id=%s user_id=%s context_type=%s storage_key=%s upload_error=%s",
            upload_trace,
            int(user_id),
            context_type,
            storage.get("storage_key"),
            storage.get("upload_error") or "durable upload did not complete",
        )
        return {"ok": False, "message": "Upload could not be saved to durable media storage. Please retry."}, 502
    path = Path(storage.get("local_path") or "")
    stored = storage.get("storage_key") or f"{datetime.utcnow().strftime('%Y%m%d')}_{secrets.token_urlsafe(16)}.{ext}"
    mime = file_storage.mimetype or mimetypes.guess_type(original)[0] or "application/octet-stream"
    width = height = None
    if media_type in {"image", "gif"}:
        width, height = _image_dimensions(path)
    url = storage.get("media_url") or _public_url_for_path(path)
    cdn_url = storage.get("media_url") if str(storage.get("media_url") or "").startswith("https://") else cdn_url_for_key(storage.get("storage_key") or stored)
    verification_status = "verified" if storage.get("durable_uploaded") or media_storage.provider() == "local" else "failed"
    processing_status = "ready" if verification_status == "verified" else "failed"
    availability_error = "" if verification_status == "verified" else (storage.get("upload_error") or "durable_upload_unverified")
    thumbnail_url = url if media_type != "video" else ""
    poster_url = thumbnail_url if media_type != "video" else ""
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        INSERT INTO chat_media_uploads
        (uploader_user_id, context_type, context_id, original_filename, stored_filename, media_url, thumbnail_url,
         media_type, mime_type, file_size_bytes, width, height, moderation_status, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'approved', ?)
        """,
        (
            int(user_id),
            context_type,
            str(context_id or ""),
            original,
            stored,
            url,
            thumbnail_url,
            media_type,
            storage.get("mime_type") or mime,
            int(storage.get("file_size") or size),
            width,
            height,
            _now(),
        ),
    )
    media_id = int(cur.lastrowid)
    try:
        cur.execute(
            """
            UPDATE chat_media_uploads
            SET storage_provider=?, storage_key=?, bucket=?, object_key=?, cdn_url=?, public_url=?, private_url='',
                poster_url=?, small_url=?, medium_url=?, large_url=?,
                is_available=?, processing_status=?, verification_status=?, availability_checked_at=?,
                availability_error=?, trace_id=?, error_message=?, updated_at=?
            WHERE id=?
            """,
            (
                storage.get("provider") or media_storage.provider(),
                storage.get("storage_key") or stored,
                os.getenv("R2_BUCKET", "") if (storage.get("provider") or media_storage.provider()) in {"r2", "s3"} else "",
                storage.get("storage_key") or stored,
                cdn_url or url,
                url,
                poster_url,
                thumbnail_url,
                url,
                url,
                1 if verification_status == "verified" else 0,
                processing_status,
                verification_status,
                _now(),
                availability_error,
                upload_trace,
                availability_error,
                _now(),
                media_id,
            ),
        )
    except Exception:
        pass
    mux_result = {}
    mux_configured = bool(mux_diagnostics().get("configured"))
    if media_type == "video" and mux_configured:
        mux_input_url = mux_source_url_for_key(storage.get("storage_key") or stored, cdn_url or url)
        mux = create_mux_asset_from_url(mux_input_url, trace_id=upload_trace, media_id=media_id)
        mux_result = mux
        if mux.get("ok"):
            mux_urls = mux_playback_urls(mux.get("playback_id") or "")
            try:
                cur.execute(
                    """
                    UPDATE chat_media_uploads
                    SET mux_asset_id=?, mux_playback_id=?, mux_status=?,
                        playback_url=?, playback_mime_type=?,
                        processing_status=CASE WHEN ? IN ('ready','asset_ready','available') THEN 'ready' ELSE 'mux_processing' END,
                        updated_at=?
                    WHERE id=?
                    """,
                    (
                        mux.get("asset_id") or "",
                        mux.get("playback_id") or "",
                        mux.get("status") or "created",
                        mux_urls.get("hls_url") or "",
                        "application/vnd.apple.mpegurl",
                        mux.get("status") or "created",
                        _now(),
                        media_id,
                    ),
                )
            except Exception as exc:
                logging.warning("PULSE_MUX_ASSET_STORE_FAILED trace_id=%s media_id=%s error=%s", upload_trace, media_id, str(exc)[:300])
        else:
            try:
                cur.execute(
                    """
                    UPDATE chat_media_uploads
                    SET mux_status=?, processing_status='mux_failed', error_message=?, updated_at=?
                    WHERE id=?
                    """,
                    (
                        mux.get("status") or "failed",
                        (mux.get("message") or mux.get("error_type") or "Mux asset creation failed.")[:1000],
                        _now(),
                        media_id,
                    ),
                )
            except Exception:
                pass
            conn.commit()
            cur.execute("SELECT * FROM chat_media_uploads WHERE id=?", (media_id,))
            failed_row = cur.fetchone()
            conn.close()
            failed_media = _public(failed_row)
            logging.error(
                "PULSE_MUX_ASSET_REQUIRED_FAILED trace_id=%s media_id=%s mux_asset_create_attempt=%s mux_asset_create_status=%s mux_asset_create_error_type=%s mux_asset_create_status_code=%s",
                upload_trace,
                media_id,
                True,
                mux.get("status") or "failed",
                mux.get("error_type") or "unknown",
                mux.get("status_code") or 0,
            )
            return {
                "ok": False,
                "message": mux.get("message") or "Video upload reached storage but Mux could not create the playback asset. Please retry.",
                "error": "mux_asset_create_failed",
                "trace_id": upload_trace,
                "media": failed_media,
                "mux": {
                    "token_id_present": bool(mux_diagnostics().get("token_id_configured")),
                    "token_secret_present": bool(mux_diagnostics().get("token_secret_configured")),
                    "asset_create_attempt": True,
                    "asset_create_status": mux.get("status") or "failed",
                    "asset_create_status_code": mux.get("status_code") or 0,
                    "asset_create_error_type": mux.get("error_type") or "unknown",
                },
            }, 502
    conn.commit()
    cur.execute("SELECT * FROM chat_media_uploads WHERE id=?", (media_id,))
    row = cur.fetchone()
    conn.close()
    public_media = _public(row)
    if media_type == "video":
        public_media["mux_asset_create_attempt"] = bool(mux_configured)
        public_media["mux_asset_create_status"] = mux_result.get("status") or ("not_configured" if not mux_configured else "")
        public_media["mux_asset_create_error_type"] = mux_result.get("error_type") or ""
    logging.info(
        "PULSE_MEDIA_UPLOAD_COMPLETE trace_id=%s user_id=%s media_id=%s storage_provider=%s media_url=%s processing_status=%s verification_status=%s object_key=%s cdn_url=%s mux_asset_create_attempt=%s mux_asset_create_status=%s mux_asset_create_error_type=%s",
        upload_trace,
        int(user_id),
        media_id,
        public_media.get("storage_provider"),
        public_media.get("media_url"),
        public_media.get("processing_status") or "ready",
        public_media.get("verification_status") or "",
        public_media.get("object_key") or public_media.get("storage_key") or "",
        public_media.get("cdn_url") or "",
        bool(mux_configured) if media_type == "video" else False,
        mux_result.get("status") or "",
        mux_result.get("error_type") or "",
    )
    return {"ok": True, "media": public_media}, 200


def attach_media_to_message(user_id, message_id, media_ids, context_type="private_chat", context_id=""):
    ids = [int(x) for x in (media_ids or []) if str(x).isdigit()]
    if not ids:
        return []
    conn = user_context.connect()
    cur = conn.cursor()
    attached = []
    for media_id in ids[:4]:
        cur.execute("SELECT * FROM chat_media_uploads WHERE id=? AND uploader_user_id=? AND message_id IS NULL LIMIT 1", (media_id, int(user_id)))
        row = cur.fetchone()
        if not row:
            continue
        cur.execute(
            "UPDATE chat_media_uploads SET message_id=?, context_type=?, context_id=? WHERE id=?",
            (int(message_id), context_type, str(context_id or ""), media_id),
        )
        attached.append(_public(row))
    conn.commit()
    conn.close()
    return attached


def media_for_messages(message_ids):
    ids = [int(x) for x in (message_ids or []) if int(x or 0)]
    if not ids:
        return {}
    conn = user_context.connect()
    cur = conn.cursor()
    placeholders = ",".join(["?"] * len(ids))
    cur.execute(f"SELECT * FROM chat_media_uploads WHERE message_id IN ({placeholders}) AND moderation_status!='blocked' ORDER BY id ASC", ids)
    out = {}
    for row in cur.fetchall():
        item = _public(row)
        out.setdefault(int(row["message_id"]), []).append(item)
    conn.close()
    return out


def report_media(user_id, media_id, reason=""):
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute("UPDATE chat_media_uploads SET moderation_status='pending', moderation_reason=? WHERE id=?", (str(reason or "reported")[:500], int(media_id)))
    conn.commit()
    conn.close()
    return {"ok": True, "message": "Media reported for review."}


def migrate_local_media_row(row, *, force=False):
    """Upload a legacy local media row to durable object storage when possible."""
    item = dict(row or {})
    media_id = int(item.get("id") or 0)
    if not media_id:
        return {"ok": False, "media_id": 0, "status": "invalid"}
    current_provider = str(item.get("storage_provider") or "").lower()
    if current_provider in {"r2", "s3"} and not force:
        return {"ok": True, "media_id": media_id, "status": "already_durable"}
    storage_status = media_storage.storage_status()
    if storage_status.get("provider") not in {"r2", "s3"} or not storage_status.get("configured"):
        return {"ok": False, "media_id": media_id, "status": "r2_not_configured", "required": storage_status.get("required") or {}}
    source_url = item.get("media_url") or item.get("public_url") or item.get("thumbnail_url") or ""
    local_path = local_path_for_url(source_url)
    if not local_path or not os.path.exists(local_path):
        return {"ok": False, "media_id": media_id, "status": "local_file_missing", "source_url": source_url}
    storage_key = str(item.get("storage_key") or item.get("stored_filename") or "").strip().lstrip("/")
    if not storage_key:
        extension = Path(local_path).suffix.lower() or mimetypes.guess_extension(item.get("mime_type") or "") or ".bin"
        storage_key = f"pulse_media/backfill/{datetime.utcnow().strftime('%Y/%m/%d')}/media-{media_id}-{secrets.token_hex(5)}{extension}"
    mime_type = item.get("mime_type") or mimetypes.guess_type(local_path)[0] or "application/octet-stream"
    uploaded, error = media_storage._upload_to_object_storage(Path(local_path), storage_key, mime_type)
    if not uploaded:
        return {"ok": False, "media_id": media_id, "status": "upload_failed", "error": error}
    public_url = cdn_url_for_key(storage_key) or media_storage.public_media_url(storage_key)
    conn = user_context.connect()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE chat_media_uploads
        SET storage_provider=?, storage_key=?, public_url=?, media_url=?, thumbnail_url=COALESCE(NULLIF(thumbnail_url,''), ?),
            is_available=1, availability_error='', processing_status='ready'
        WHERE id=?
        """,
        (media_storage.provider(), storage_key, public_url, public_url, public_url, media_id),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "media_id": media_id, "status": "migrated", "storage_key": storage_key, "public_url": public_url}


def repair_media_row(row):
    """Normalize one media row so renderers receive either a good URL or a fallback state."""
    item = dict(row or {})
    media_id = int(item.get("id") or 0)
    resolved = resolve_media(item, check_remote=False)
    updates = {}
    if resolved.get("cdn_url") and media_storage.provider() in {"r2", "s3"}:
        updates["media_url"] = resolved["cdn_url"]
        updates["public_url"] = resolved["cdn_url"]
        updates["storage_provider"] = media_storage.provider()
    available = bool(resolved.get("is_available"))
    updates["is_available"] = 1 if available else 0
    updates["availability_error"] = "" if available else "missing_or_unreachable"
    if not media_id:
        return {"ok": False, "status": "invalid"}
    if updates:
        conn = user_context.connect()
        cur = conn.cursor()
        fields = ", ".join([f"{key}=?" for key in updates])
        cur.execute(f"UPDATE chat_media_uploads SET {fields} WHERE id=?", [*updates.values(), media_id])
        conn.commit()
        conn.close()
    return {"ok": True, "media_id": media_id, "status": "available" if available else "marked_unavailable", "media_url": updates.get("media_url") or resolved.get("media_url") or ""}
