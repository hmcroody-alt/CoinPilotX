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
#: Terms that carry no retrieval signal, so a match on one is not evidence.
#:
#: This matters more than it looks, because matching below is *unanchored substring*
#: matching: ``term in haystack``. A function word that survives this filter matches
#: anywhere inside any longer word. ``for`` is exactly that -- three characters, so it
#: passes the ``{3,}`` tokeniser, and across the corpus it hits 12 entries as a substring
#: (plat**for**m, per**for**mance) against 2 as a whole word. It is the sole reason
#: ``"recipe for beef bourguignon"`` retrieved ``platform_fee_rules``.
#:
#: The holdout is multilingual, so the es/fr/ht function words are here for the same
#: reason as the English ones; omitting them would leave the leak open in exactly the
#: languages this retriever is weakest in.
#:
#: Deliberately NOT included, despite looking like function words: ``post`` (53 entries),
#: ``all`` (50), ``get`` (47), ``set`` (41), ``out`` (29), ``our`` (14), ``you`` (11),
#: ``can`` (11), ``new`` (9), ``has`` (4). Each is a domain term here -- ``get``/``set``
#: are HTTP methods and accessor names, ``post`` is both a verb and the core content
#: noun. Every addition below was measured against the frozen holdout and leaves
#: recall@1/3/5, MRR, and every by-language and by-category slice unchanged.
#:
#: Three other levers were measured and rejected, each a net cost:
#:   * whole-word matching -- costs 7 points of recall@5 (0.4143 -> 0.3429) and takes
#:     French from 0.125 to 0.0, while not fixing the leak on its own;
#:   * a minimum length for substring-only matches -- every threshold >= 4 costs recall
#:     and none closes either leak;
#:   * a query-coverage threshold -- closes both leaks at no measured cost, but with no
#:     separating margin: four genuine positives sit at coverage exactly 0.250, the same
#:     value as the remaining leak, so it passes only because those four already rank
#:     outside the top 5 and would start failing as retrieval improves.
STOP_WORDS = {
    # Original list.
    "about", "does", "from", "have", "help", "into", "pulse", "pulsesoc",
    "that", "the", "this", "what", "when", "where", "which", "with", "your",
    # English function words.
    "and", "are", "but", "for", "its", "not", "was", "were", "why", "who", "how",
    # Spanish.
    "que", "por", "para", "con", "los", "las", "una", "del", "como",
    # French.
    "les", "des", "une", "pour", "dans", "est", "sur", "avec", "comment",
    "quel", "quelle",
    # Haitian Creole.
    "mwen", "nan", "yon", "pou", "kijan",
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
