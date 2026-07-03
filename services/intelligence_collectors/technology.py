"""Technology Pulse collector for trusted official update feeds."""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from .base import BaseCollector, CollectorResult, IntelligenceCandidate, compact, network_error_message, source_status, utc_now_iso


FEEDS = [
    ("openai_updates", "OpenAI", "https://openai.com/news/rss.xml"),
    ("apple_newsroom", "Apple Newsroom", "https://www.apple.com/newsroom/rss-feed.rss"),
]
KEYWORDS = {"release", "launch", "update", "model", "developer", "security", "ai", "iphone", "mac", "research"}


def _entry_text(entry: ET.Element, names: tuple[str, ...]) -> str:
    for name in names:
        node = entry.find(name)
        if node is not None and node.text:
            return compact(node.text, 1000)
    for child in entry:
        if child.tag.split("}")[-1] in names and child.text:
            return compact(child.text, 1000)
    return ""


def _entry_date(value: str) -> str:
    try:
        parsed = parsedate_to_datetime(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    except Exception:
        return utc_now_iso()


class TechnologyPulseCollector(BaseCollector):
    stream = "technology_pulse"
    collector_key = "technology_pulse_sources"

    def run(self, limit: int = 20) -> CollectorResult:
        started = time.perf_counter()
        statuses: list[dict[str, Any]] = []
        candidates: list[IntelligenceCandidate] = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=10)
        for source_key, source_name, url in FEEDS:
            try:
                text, cached, duration = self.fetch_text(url, cache_key=f"rss:{source_key}", ttl_seconds=900)
                root = ET.fromstring(text)
                entries = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
                statuses.append(source_status(source_key, "success_cached" if cached else "success", duration_ms=duration, candidates=len(entries)))
                for entry in entries[:10]:
                    title = _entry_text(entry, ("title",))
                    summary = _entry_text(entry, ("description", "summary", "content"))
                    link = _entry_text(entry, ("link",))
                    if not link:
                        link_node = entry.find("{http://www.w3.org/2005/Atom}link")
                        link = compact(link_node.attrib.get("href"), 400) if link_node is not None else url
                    pub = _entry_date(_entry_text(entry, ("pubDate", "updated", "published")))
                    try:
                        pub_dt = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                    except Exception:
                        pub_dt = datetime.now(timezone.utc)
                    if pub_dt < cutoff:
                        continue
                    haystack = f"{title} {summary}".lower()
                    if not any(keyword in haystack for keyword in KEYWORDS):
                        continue
                    candidates.append(IntelligenceCandidate(
                        stream=self.stream,
                        source=source_key,
                        source_keys=[source_key],
                        source_url=link or url,
                        source_confidence=0.84 if source_key == "apple_newsroom" else 0.86,
                        title=title,
                        summary=summary or f"{source_name} published a technology update.",
                        why_it_matters="Official technology updates can affect creators, developers, device users, and PulseSoc feature planning.",
                        expected_impact="User and developer activity may increase as the update rolls out.",
                        category="official_technology_update",
                        region="global",
                        severity="normal",
                        confidence=0.82,
                        freshness_score=0.84,
                        impact_score=0.68,
                        dedupe_key=f"technology:{source_key}:{title.lower()}",
                        event_time=pub,
                        evidence=[{"source": source_key, "url": link or url, "published_at": pub}],
                    ))
                    if len(candidates) >= int(limit or 20):
                        break
            except Exception as exc:
                statuses.append(source_status(source_key, "failed", reason=network_error_message(exc)))

        return CollectorResult(
            stream=self.stream,
            collector_key=self.collector_key,
            status="success" if any(item["status"].startswith("success") for item in statuses) else "failed",
            candidates=candidates[: int(limit or 20)],
            source_statuses=statuses,
            duration_ms=int((time.perf_counter() - started) * 1000),
            message="" if candidates else "No trusted technology update exceeded filters.",
        )
