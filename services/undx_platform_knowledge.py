"""Bounded request-specific access to the source-derived PulseSoc manifest."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any


MANIFEST_PATH = Path(__file__).resolve().parents[1] / "data/pulse_ai/pulsesoc_platform_manifest.json"
MAX_RESULTS = 6
MAX_CONTEXT_CHARS = 3600
STOP_WORDS = {
    "about", "does", "from", "have", "help", "into", "pulse", "pulsesoc",
    "that", "the", "this", "what", "when", "where", "which", "with", "your",
}


@lru_cache(maxsize=2)
def _load_manifest(path: str, mtime_ns: int) -> dict[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def load_manifest() -> dict[str, Any]:
    try:
        stat = MANIFEST_PATH.stat()
    except OSError:
        return {}
    return _load_manifest(str(MANIFEST_PATH), stat.st_mtime_ns)


def _terms(query: str) -> list[str]:
    return [
        term for term in re.findall(r"[a-z0-9]{3,}", str(query or "").lower())
        if term not in STOP_WORDS
    ][:24]


def retrieve(query: str, *, limit: int = MAX_RESULTS, char_limit: int = MAX_CONTEXT_CHARS) -> list[dict[str, Any]]:
    """Return public prompt-ready summaries without source paths or raw schemas."""
    terms = _terms(query)
    if not terms:
        return []
    scored: list[tuple[int, str, dict[str, Any]]] = []
    for item in load_manifest().get("entries") or []:
        if not isinstance(item, dict) or item.get("public") is False:
            continue
        haystack = str(item.get("search_text") or item.get("name") or "").lower()
        exact = sum(4 for term in terms if term == str(item.get("name") or "").lower())
        matches = sum(1 for term in terms if term in haystack)
        if not matches and not exact:
            continue
        scored.append((exact + matches, str(item.get("id") or ""), item))
    scored.sort(key=lambda row: (-row[0], row[1]))

    results: list[dict[str, Any]] = []
    used = 0
    for _, _, item in scored[: max(1, min(int(limit), MAX_RESULTS))]:
        title = f"PulseSoc {str(item.get('kind') or 'capability').replace('_', ' ')}: {item.get('name')}"
        body = " ".join(str(item.get("public_summary") or "").split())[:600]
        if not body or used + len(title) + len(body) > max(300, min(int(char_limit), MAX_CONTEXT_CHARS)):
            continue
        results.append({
            "id": 0,
            "title": title[:160],
            "category": "source_derived_platform_knowledge",
            "body": body,
        })
        used += len(title) + len(body)
    return results
