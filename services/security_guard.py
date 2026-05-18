"""Request safety guardrails for high-traffic interactive surfaces."""

from __future__ import annotations

import html
import re
import time
from collections import defaultdict


BUCKETS = defaultdict(list)
BLOCKED_EXTENSIONS = {".exe", ".js", ".mjs", ".sh", ".bat", ".cmd", ".php", ".svg", ".html", ".htm"}
SCRIPT_RE = re.compile(r"<\s*script|javascript:|onerror\s*=|onload\s*=", re.I)


def sanitize_text(value, limit=2000):
    value = str(value or "")[:limit]
    return html.escape(value, quote=True)


def suspicious_text(value):
    return bool(SCRIPT_RE.search(str(value or "")))


def file_extension_allowed(filename):
    lowered = str(filename or "").lower()
    return not any(lowered.endswith(ext) for ext in BLOCKED_EXTENSIONS)


def rate_limited(key, limit=60, window_seconds=60):
    now = time.time()
    bucket = [stamp for stamp in BUCKETS.get(key, []) if now - stamp < window_seconds]
    if len(bucket) >= int(limit):
        BUCKETS[key] = bucket
        return True
    bucket.append(now)
    BUCKETS[key] = bucket
    return False


def request_limit_for(path, method):
    if method not in {"POST", "PUT", "PATCH"}:
        return None
    if path.startswith("/api/media/upload"):
        return 10, 300
    if path.startswith("/api/arena/roast"):
        return 45, 60
    if path.startswith("/api/chat/") or path.startswith("/api/players/"):
        return 80, 60
    if path.startswith("/api/sms/"):
        return 8, 600
    if path.startswith("/api/telegram/"):
        return 12, 300
    if path.startswith("/api/alerts") or path.startswith("/api/auto-signals"):
        return 60, 60
    return None
