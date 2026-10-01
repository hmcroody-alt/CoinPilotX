"""Bounded contextual image enrichment for PulseSoc Insight posts.

The pipeline deliberately reuses ``pulse_jobs``, ``chat_media_uploads`` and
``pulse_posts.media_ids_json``. Provider failures never invalidate the source
post: after bounded retries the post remains a valid text-only Insight.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import os
import re
import socket
import struct
import urllib.error
import urllib.request
import zlib
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from werkzeug.datastructures import FileStorage

from .. import media_service, media_storage
from .content_policy import sanitize_automated_text


PROMPT_VERSION = "insight-image-v1"
JOB_TYPE = "generate_insight_image"
DECISIONS = {"TEXT_ONLY", "IMAGE_RECOMMENDED", "IMAGE_REQUIRED", "IMAGE_SKIPPED"}

# Product rule: PulseSoc Insight posts are text-only. Automated image
# generation is off, and the switch is a module constant rather than an env var
# so it cannot be turned back on by a misconfigured deploy.
#
# Enforced at three separate boundaries -- decide, enqueue, and process --
# because they fail at different times. `decide_image` covers new posts,
# `enqueue_for_post` is a backstop against any caller that supplies its own
# plan, and the `process_job` guard drains jobs that were already sitting in
# `pulse_jobs` when this shipped. Without that last one, every queued job would
# still generate and attach an image after the feature was supposedly disabled.
AUTOMATED_IMAGES_ENABLED = False
VISUAL_CATEGORIES = {
    "sports": "dynamic editorial sports illustration",
    "creator": "modern creator economy editorial illustration",
    "business": "premium business editorial illustration",
    "technology": "clean futuristic technology editorial illustration",
    "education": "clear educational editorial illustration",
    "travel": "warm travel editorial illustration",
    "wellness": "calm wellness editorial illustration",
    "community": "warm social community illustration",
    "marketplace": "trusted marketplace education illustration",
    "safety": "cyber-safety editorial illustration",
}
SENSITIVE_TERMS = re.compile(r"\b(emergency|missing person|active threat|death|killed|victim|moderation action|account ban)\b", re.I)
PRIVATE_TERMS = re.compile(
    r"\b(?:PLS-[A-Z0-9-]+|\d{3}-\d{2}-\d{4}|(?:\d[ -]*?){13,19}|seed phrase|private key|tax id)\b",
    re.I,
)


class ImagePipelineError(RuntimeError):
    """Retryable provider, validation, or storage failure."""


@dataclass(frozen=True)
class VisualIntent:
    topic: str
    category: str
    visual_style: str
    concepts: tuple[str, ...]
    avoid: tuple[str, ...]


def _clean_public(value: Any, limit: int = 180) -> str:
    value = sanitize_automated_text(str(value or ""))
    value = PRIVATE_TERMS.sub("private information", value)
    value = re.sub(r"https?://\S+|@[A-Za-z0-9_.-]+", "", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


def category_for_post(post: dict[str, Any]) -> str:
    haystack = " ".join(
        [str(post.get("space_slug") or ""), str(post.get("topic") or ""), " ".join(post.get("tags") or [])]
    ).lower()
    aliases = {
        "scam": "safety", "cyber": "safety", "security": "safety",
        "sport": "sports", "creator": "creator", "business": "business",
        "tech": "technology", "education": "education", "learn": "education",
        "travel": "travel", "wellness": "wellness", "health": "wellness",
        "community": "community", "marketplace": "marketplace",
    }
    for token, category in aliases.items():
        if token in haystack:
            return category
    return "community"


def decide_image(post: dict[str, Any]) -> str:
    """Return a deterministic, externally observable image decision."""
    if not AUTOMATED_IMAGES_ENABLED:
        return "TEXT_ONLY"
    text = " ".join([str(post.get("title") or ""), str(post.get("body") or ""), str(post.get("topic") or "")]).strip()
    if not text or len(text) < 90 or SENSITIVE_TERMS.search(text):
        return "TEXT_ONLY"
    category = category_for_post(post)
    if category == "safety" and any(word in text.lower() for word in ("warning", "scam", "verify", "protect")):
        return "IMAGE_REQUIRED"
    if category in VISUAL_CATEGORIES:
        return "IMAGE_RECOMMENDED"
    return "IMAGE_SKIPPED"


def build_visual_intent(post: dict[str, Any]) -> VisualIntent:
    category = category_for_post(post)
    topic = _clean_public(post.get("topic") or post.get("title") or category, 90)
    concepts = tuple(dict.fromkeys([category, topic, "pattern recognition"]))
    return VisualIntent(
        topic=topic,
        category=category,
        visual_style=VISUAL_CATEGORIES.get(category, VISUAL_CATEGORIES["community"]),
        concepts=concepts,
        avoid=(
            "real person or public figure likeness", "fake documentary evidence", "fake score or statistic",
            "financial chart or price", "screenshot or account balance", "brand or team logo", "large text",
        ),
    )


def build_image_prompt(post: dict[str, Any], intent: VisualIntent | None = None) -> str:
    intent = intent or build_visual_intent(post)
    summary = _clean_public(post.get("body") or post.get("title"), 220)
    prompt = (
        f"Create a premium {intent.visual_style} for a PulseSoc educational post about {intent.topic}. "
        f"Public context summary: {summary}. Concepts: {', '.join(intent.concepts)}. "
        "Portrait 4:5 composition, clean focal hierarchy, useful contextual imagery, no visible words or captions. "
        f"Avoid: {', '.join(intent.avoid)}. This is an editorial illustration, never news photography or evidence."
    )
    return sanitize_automated_text(PRIVATE_TERMS.sub("private information", prompt))[:1200]


def plan_for_post(post: dict[str, Any]) -> dict[str, Any]:
    decision = decide_image(post)
    intent = build_visual_intent(post) if decision in {"IMAGE_RECOMMENDED", "IMAGE_REQUIRED"} else None
    return {
        "decision": decision,
        "prompt_version": PROMPT_VERSION,
        "visual_intent": asdict(intent) if intent else None,
    }


class OpenAIImageProvider:
    name = "openai"

    def __init__(self, api_key: str | None = None, model: str | None = None):
        self.api_key = (api_key or os.getenv("OPENAI_API_KEY") or "").strip()
        self.model = (model or os.getenv("PULSE_INSIGHT_IMAGE_MODEL") or "gpt-image-1").strip()

    def generate(self, prompt: str) -> dict[str, Any]:
        if not self.api_key:
            raise ImagePipelineError("image_provider_not_configured")
        request = urllib.request.Request(
            "https://api.openai.com/v1/images/generations",
            data=json.dumps({"model": self.model, "prompt": prompt, "size": "1024x1536", "quality": "medium", "output_format": "png"}).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=int(os.getenv("PULSE_INSIGHT_IMAGE_TIMEOUT_SECONDS", "90"))) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (TimeoutError, socket.timeout) as exc:
            raise ImagePipelineError("image_provider_timeout") from exc
        except urllib.error.HTTPError as exc:
            raise ImagePipelineError(f"image_provider_http_{exc.code}") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ImagePipelineError("image_provider_unavailable") from exc
        item = (payload.get("data") or [{}])[0]
        encoded = item.get("b64_json")
        if not encoded:
            raise ImagePipelineError("image_provider_empty_result")
        try:
            content = base64.b64decode(encoded, validate=True)
        except Exception as exc:
            raise ImagePipelineError("image_provider_invalid_base64") from exc
        return {"bytes": content, "provider": self.name, "model": self.model}


def _validated_png_dimensions(content: bytes) -> tuple[int, int]:
    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
        return 0, 0
    offset, width, height, color_type = 8, 0, 0, 0
    idat = bytearray()
    saw_end = False
    while offset + 12 <= len(content):
        length = struct.unpack(">I", content[offset:offset + 4])[0]
        chunk_type = content[offset + 4:offset + 8]
        end = offset + 12 + length
        if length > 16 * 1024 * 1024 or end > len(content):
            return 0, 0
        data = content[offset + 8:offset + 8 + length]
        expected_crc = struct.unpack(">I", content[offset + 8 + length:end])[0]
        if zlib.crc32(chunk_type + data) & 0xFFFFFFFF != expected_crc:
            return 0, 0
        if chunk_type == b"IHDR" and length == 13:
            width, height = struct.unpack(">II", data[:8])
            bit_depth, color_type, compression, filtering, interlace = data[8:13]
            if bit_depth != 8 or compression or filtering or interlace:
                return 0, 0
        elif chunk_type == b"IDAT":
            idat.extend(data)
        elif chunk_type == b"IEND":
            saw_end = True
            break
        offset = end
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}.get(color_type, 0)
    if not saw_end or not width or not height or not channels or not idat:
        return 0, 0
    try:
        expected_size = (width * channels + 1) * height
        decompressor = zlib.decompressobj()
        pixels = decompressor.decompress(bytes(idat), expected_size + 1)
        if not decompressor.eof or decompressor.unconsumed_tail:
            return 0, 0
    except Exception:
        return 0, 0
    if len(pixels) != expected_size:
        return 0, 0
    return width, height


def validate_and_normalize_image(content: bytes) -> tuple[bytes, int, int, str]:
    if not content or len(content) > int(os.getenv("PULSE_INSIGHT_IMAGE_MAX_BYTES", str(12 * 1024 * 1024))):
        raise ImagePipelineError("generated_image_size_invalid")
    if not media_service._media_header_ok("png", content[:512]):
        raise ImagePipelineError("generated_image_header_invalid")
    width, height = _validated_png_dimensions(content)
    if width < 640 or height < 640 or width > 8192 or height > 8192:
        raise ImagePipelineError("generated_image_dimensions_invalid")
    return content, width, height, hashlib.sha256(content).hexdigest()


def _ensure_tables(cur) -> None:
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS pulse_generated_media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_post_id INTEGER NOT NULL,
            media_id INTEGER,
            generation_version TEXT NOT NULL,
            decision TEXT NOT NULL,
            state TEXT NOT NULL,
            provider TEXT,
            model TEXT,
            prompt_version TEXT,
            generation_job_id INTEGER,
            content_hash TEXT,
            failure_reason TEXT,
            generated_by_system INTEGER DEFAULT 1,
            ai_generated_media INTEGER DEFAULT 1,
            generated_at TEXT,
            created_at TEXT,
            updated_at TEXT,
            UNIQUE(source_post_id, generation_version)
        )
        """
    )


def enqueue_for_post(cur, post_id: int, plan: dict[str, Any]) -> int:
    if not AUTOMATED_IMAGES_ENABLED:
        return 0
    if plan.get("decision") not in {"IMAGE_RECOMMENDED", "IMAGE_REQUIRED"}:
        return 0
    now = datetime.utcnow().isoformat(timespec="seconds")
    cur.execute(
        """INSERT INTO pulse_jobs
        (job_type,target_type,target_id,status,attempts,max_attempts,run_after,created_at,updated_at)
        VALUES (?,?,?,'pending',0,?,?,?,?)""",
        (JOB_TYPE, "post", int(post_id), max(1, int(os.getenv("PULSE_INSIGHT_IMAGE_MAX_ATTEMPTS", "2"))), now, now, now),
    )
    return int(cur.lastrowid)


def _budget_available(cur) -> tuple[bool, str]:
    _ensure_tables(cur)
    now = datetime.utcnow()
    hour = (now - timedelta(hours=1)).isoformat(timespec="seconds")
    day = (now - timedelta(days=1)).isoformat(timespec="seconds")
    cur.execute("SELECT COUNT(*) AS total FROM pulse_generated_media WHERE created_at>=? AND state IN ('processing','attached')", (hour,))
    hourly = int(dict(cur.fetchone()).get("total") or 0)
    cur.execute("SELECT COUNT(*) AS total FROM pulse_generated_media WHERE created_at>=? AND state IN ('processing','attached')", (day,))
    daily = int(dict(cur.fetchone()).get("total") or 0)
    if hourly >= int(os.getenv("PULSE_INSIGHT_IMAGE_HOURLY_CAP", "4")):
        return False, "hourly_cap"
    if daily >= int(os.getenv("PULSE_INSIGHT_IMAGE_DAILY_CAP", "24")):
        return False, "daily_cap"
    circuit_since = (now - timedelta(minutes=int(os.getenv("PULSE_INSIGHT_IMAGE_CIRCUIT_MINUTES", "15")))).isoformat(timespec="seconds")
    cur.execute("SELECT COUNT(*) AS total FROM pulse_generated_media WHERE created_at>=? AND state='failed'", (circuit_since,))
    if int(dict(cur.fetchone()).get("total") or 0) >= int(os.getenv("PULSE_INSIGHT_IMAGE_CIRCUIT_FAILURES", "3")):
        return False, "provider_circuit_open"
    return True, ""


def _post_context(cur, post_id: int) -> dict[str, Any]:
    cur.execute(
        """SELECT p.id,p.title,p.body,p.tags_json,p.media_ids_json,a.space_slug,a.topic,a.metadata_json
           FROM pulse_posts p LEFT JOIN pulse_ai_posts a ON a.pulse_post_id=p.id
           WHERE p.id=? AND p.user_id=0 ORDER BY a.id DESC LIMIT 1""",
        (int(post_id),),
    )
    row = cur.fetchone()
    if not row:
        raise ImagePipelineError("insight_post_not_found")
    item = dict(row)
    try:
        item["tags"] = json.loads(item.get("tags_json") or "[]")
    except Exception:
        item["tags"] = []
    return item


def _persist_media(cur, post_id: int, content: bytes, width: int, height: int) -> int:
    mime_type = "image/png"
    upload = FileStorage(stream=io.BytesIO(content), filename=f"insight-{post_id}-{PROMPT_VERSION}.png", content_type=mime_type)
    storage = media_storage.save_public_file(upload, folder="pulse_media")
    if media_storage.provider() in {"r2", "s3"} and not storage.get("durable_uploaded"):
        raise ImagePipelineError("generated_image_storage_failed")
    now = datetime.utcnow().isoformat(timespec="seconds")
    media_url = (storage.get("media_url") or "").strip()
    # Storage can report success and still hand back no URL. Attaching that row
    # would flip the post to post_type='image' with nothing to render -- the
    # exact blank block this pipeline must never produce. Fail into the bounded
    # retry path instead, and let the post stay a clean text-only Insight.
    if not media_url:
        raise ImagePipelineError("generated_image_storage_url_missing")
    cur.execute(
        """INSERT INTO chat_media_uploads
        (uploader_user_id,context_type,context_id,original_filename,stored_filename,media_url,thumbnail_url,
         poster_url,media_type,mime_type,file_size_bytes,width,height,moderation_status,storage_provider,storage_key,
         object_key,cdn_url,public_url,is_available,processing_status,verification_status,created_at,updated_at)
        VALUES (0,'pulse',?,?,?,?,?,?, 'image',?,?,?,?,'approved',?,?,?,?,?,1,'ready','verified',?,?)""",
        (
            str(post_id), upload.filename, storage.get("storage_key") or upload.filename, media_url, media_url, media_url,
            mime_type, len(content), width, height, storage.get("provider") or media_storage.provider(),
            storage.get("storage_key") or "", storage.get("storage_key") or "", media_url, media_url, now, now,
        ),
    )
    return int(cur.lastrowid)


def process_job(cur, job: dict[str, Any], provider: Any | None = None) -> dict[str, Any]:
    post_id = int(job.get("target_id") or 0)
    job_id = int(job.get("id") or 0)
    attempt = int(job.get("attempts") or 0) + 1
    max_attempts = int(job.get("max_attempts") or 2)
    if not AUTOMATED_IMAGES_ENABLED:
        # Jobs enqueued before automated images were switched off are still
        # sitting in `pulse_jobs`. Retire them here -- ahead of the provider
        # call and ahead of any `pulse_generated_media` bookkeeping -- so the
        # queue drains into text-only instead of attaching images to posts
        # published after the rule changed.
        logging.info("automated_image_disabled_text_only post_id=%s job_id=%s", post_id, job_id)
        return {"ok": True, "decision": "TEXT_ONLY", "text_only": True, "reason": "automated_images_disabled"}
    _ensure_tables(cur)
    cur.execute("SELECT * FROM pulse_generated_media WHERE source_post_id=? AND generation_version=?", (post_id, PROMPT_VERSION))
    existing_row = cur.fetchone()
    existing = dict(existing_row) if existing_row else {}
    if existing.get("state") == "attached" and int(existing.get("media_id") or 0):
        logging.info("automated_image_duplicate_suppressed post_id=%s job_id=%s", post_id, job_id)
        return {"ok": True, "duplicate": True, "media_id": int(existing["media_id"])}

    post = _post_context(cur, post_id)
    plan = plan_for_post(post)
    if plan["decision"] not in {"IMAGE_RECOMMENDED", "IMAGE_REQUIRED"}:
        logging.info("automated_image_fallback_text_only post_id=%s decision=%s", post_id, plan["decision"])
        return {"ok": True, "decision": plan["decision"], "text_only": True}
    available, reason = _budget_available(cur)
    if not available:
        logging.warning("automated_image_fallback_text_only post_id=%s reason=%s", post_id, reason)
        return {"ok": True, "decision": "IMAGE_SKIPPED", "text_only": True, "reason": reason}

    now = datetime.utcnow().isoformat(timespec="seconds")
    cur.execute(
        """INSERT OR IGNORE INTO pulse_generated_media
        (source_post_id,generation_version,decision,state,prompt_version,generation_job_id,created_at,updated_at)
        VALUES (?,?,?,'processing',?,?,?,?)""",
        (post_id, PROMPT_VERSION, plan["decision"], PROMPT_VERSION, job_id, now, now),
    )
    prompt = build_image_prompt(post)
    if "hot take" in prompt.lower() or PRIVATE_TERMS.search(prompt):
        raise ImagePipelineError("generated_prompt_policy_rejected")
    logging.info("automated_image_generation_started post_id=%s job_id=%s prompt_version=%s", post_id, job_id, PROMPT_VERSION)
    try:
        result = (provider or OpenAIImageProvider()).generate(prompt)
        content, width, height, content_hash = validate_and_normalize_image(result.get("bytes") or b"")
        media_id = _persist_media(cur, post_id, content, width, height)
        current_ids = json.loads(post.get("media_ids_json") or "[]")
        normalized_ids = [int(value) for value in current_ids if str(value).isdigit()]
        if media_id not in normalized_ids:
            normalized_ids.append(media_id)
        cur.execute("UPDATE pulse_posts SET media_ids_json=?, post_type='image', updated_at=? WHERE id=?", (json.dumps(normalized_ids), now, post_id))
        cur.execute(
            """UPDATE pulse_generated_media SET media_id=?,state='attached',provider=?,model=?,content_hash=?,
               generated_at=?,failure_reason='',updated_at=? WHERE source_post_id=? AND generation_version=?""",
            (media_id, result.get("provider") or "unknown", result.get("model") or "unknown", content_hash, now, now, post_id, PROMPT_VERSION),
        )
        logging.info("automated_image_generation_success post_id=%s job_id=%s media_id=%s", post_id, job_id, media_id)
        logging.info("automated_image_attached post_id=%s media_id=%s", post_id, media_id)
        return {"ok": True, "decision": plan["decision"], "media_id": media_id}
    except Exception as exc:
        reason = str(exc)[:180] or "image_generation_failed"
        final = attempt >= max_attempts
        cur.execute(
            """UPDATE pulse_generated_media SET state=?,failure_reason=?,updated_at=?
               WHERE source_post_id=? AND generation_version=?""",
            ("failed" if final else "retrying", reason, now, post_id, PROMPT_VERSION),
        )
        logging.warning("automated_image_generation_failure post_id=%s job_id=%s attempt=%s final=%s reason=%s", post_id, job_id, attempt, final, reason)
        if not final:
            raise ImagePipelineError(reason) from exc
        logging.warning("automated_image_fallback_text_only post_id=%s job_id=%s reason=%s", post_id, job_id, reason)
        return {"ok": True, "text_only": True, "reason": reason}
