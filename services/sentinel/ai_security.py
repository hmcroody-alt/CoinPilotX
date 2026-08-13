"""Sentinel AI-security boundaries (Stage 19). Defensive only.

Untrusted content (user posts, messages, external signals, web text) is DATA.
It can never carry instructions to Sentinel or UNDX (SC2). This module gives
the platform a single place to (a) wrap untrusted content so downstream code
cannot confuse it with instructions, and (b) heuristically flag likely
prompt-injection attempts for evidence — flagging is intelligence, not
enforcement, and produces no automatic punishment (signal ≠ guilt).

NO FAKE AI: detection is transparent substring/regex heuristics, labeled as
such. There is no model call in this module.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

UNTRUSTED_OPEN = "[UNTRUSTED_CONTENT_BEGIN]"
UNTRUSTED_CLOSE = "[UNTRUSTED_CONTENT_END]"

# Heuristic patterns, case-insensitive. Deliberately conservative; scoring is
# additive and thresholded so a single benign match doesn't flag.
_PATTERNS: tuple[tuple[str, int], ...] = (
    (r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|rules|prompts)", 3),
    (r"disregard\s+(your|the)\s+(instructions|system prompt|rules)", 3),
    (r"you\s+are\s+now\s+(in\s+)?(developer|dan|jailbreak|god)\s*mode", 3),
    (r"reveal\s+(your|the)\s+(system\s+prompt|instructions|secret|api\s*key)", 3),
    (r"pretend\s+(you\s+are|to\s+be)\s+(unrestricted|without\s+rules)", 2),
    (r"\bAPPROVE\s+UNDX\s+WRITE\b", 3),  # approval phrase must never come from content
    (r"as\s+an?\s+(admin|administrator|root|owner)\s*,?\s+i\s+(authorize|approve)", 2),
    (r"system\s*prompt\s*:", 1),
    (r"\{\{.*(system|instruction).*\}\}", 1),
    (r"base64\s*(decode|:)\s*[A-Za-z0-9+/=]{24,}", 1),
)
_COMPILED = tuple((re.compile(p, re.I | re.S), w) for p, w in _PATTERNS)

FLAG_THRESHOLD = 3


@dataclass(frozen=True)
class InjectionScan:
    flagged: bool
    score: int
    matched: tuple[str, ...]
    method: str = "heuristic_regex_v1"  # honest label — not ML, not a model


def scan_for_injection(text: str) -> InjectionScan:
    """Score untrusted text for prompt-injection markers. Pure function."""
    content = str(text or "")
    score = 0
    matched: list[str] = []
    for compiled, weight in _COMPILED:
        if compiled.search(content):
            score += weight
            matched.append(compiled.pattern[:60])
    return InjectionScan(flagged=score >= FLAG_THRESHOLD, score=score,
                         matched=tuple(matched))


def wrap_untrusted(text: str) -> str:
    """Mark content as data before it is shown to any model. The wrapper also
    neutralizes nested wrapper markers so content cannot fake a close."""
    content = str(text or "")
    content = content.replace(UNTRUSTED_OPEN, "[untrusted-open]")
    content = content.replace(UNTRUSTED_CLOSE, "[untrusted-close]")
    return f"{UNTRUSTED_OPEN}\n{content}\n{UNTRUSTED_CLOSE}"


def record_injection_event(subject_type: str, subject_id: str, scan: InjectionScan,
                           source: str, conn=None) -> bool:
    """Persist a flagged scan as a canonical UNDX event (evidence, not
    punishment). Returns False when not flagged — nothing is recorded."""
    if not scan.flagged:
        return False
    from services.sentinel import events
    from services.sentinel.identity import SENTINEL_INGEST
    return events.ingest(events.Event(
        category="UNDX", event_type="injection_detected", severity="medium",
        actor_id=SENTINEL_INGEST.actor_id, source=source,
        subject_type=subject_type, subject_id=subject_id,
        payload={"score": scan.score, "method": scan.method,
                 "matched": list(scan.matched)}), conn=conn)
