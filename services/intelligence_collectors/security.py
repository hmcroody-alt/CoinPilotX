"""Security Pulse collector for defensive vulnerability intelligence."""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

from .base import BaseCollector, CollectorResult, IntelligenceCandidate, compact, network_error_message, source_status, utc_now_iso


def _parse_date(value: str) -> datetime | None:
    try:
        return datetime.strptime(str(value or "")[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except Exception:
        return None


class SecurityPulseCollector(BaseCollector):
    stream = "security_pulse"
    collector_key = "security_pulse_sources"

    def run(self, limit: int = 20) -> CollectorResult:
        started = time.perf_counter()
        statuses: list[dict[str, Any]] = []
        candidates: list[IntelligenceCandidate] = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=21)
        try:
            data, cached, duration = self.fetch_json(
                "https://www.cisa.gov/sites/default/files/feeds/known_exploited_vulnerabilities.json",
                cache_key="cisa_kev_catalog",
                ttl_seconds=900,
            )
            vulns = data.get("vulnerabilities") if isinstance(data, dict) else []
            vulns = vulns if isinstance(vulns, list) else []
            statuses.append(source_status("cisa", "success_cached" if cached else "success", duration_ms=duration, candidates=len(vulns)))
            for row in vulns[-80:]:
                if not isinstance(row, dict):
                    continue
                added = _parse_date(str(row.get("dateAdded") or ""))
                if added and added < cutoff:
                    continue
                cve = compact(row.get("cveID"), 40)
                vendor = compact(row.get("vendorProject"), 100)
                product = compact(row.get("product"), 120)
                vuln_name = compact(row.get("vulnerabilityName"), 220)
                action = compact(row.get("requiredAction"), 280)
                due = compact(row.get("dueDate"), 40)
                severity = "urgent" if "known exploited" in vuln_name.lower() or added else "high"
                candidates.append(IntelligenceCandidate(
                    stream=self.stream,
                    source="cisa",
                    source_keys=["cisa"],
                    source_url="https://www.cisa.gov/known-exploited-vulnerabilities-catalog",
                    source_confidence=0.94,
                    title=f"{cve} is in CISA's exploited vulnerability catalog",
                    summary=f"CISA lists {vendor} {product}: {vuln_name}. Required action: {action or 'review vendor guidance.'}",
                    why_it_matters="Known exploited vulnerabilities can affect real users and organizations. This Pulse is defensive guidance only.",
                    expected_impact=f"Patch urgency is elevated for affected systems. Federal due date: {due or 'not listed'}.",
                    category="known_exploited_vulnerability",
                    region="global",
                    severity=severity,
                    confidence=0.9,
                    freshness_score=0.88 if added else 0.72,
                    impact_score=0.86,
                    dedupe_key=f"security:cisa_kev:{cve}",
                    event_time=added.isoformat().replace("+00:00", "Z") if added else utc_now_iso(),
                    evidence=[{"source": "cisa", "cve": cve, "vendor": vendor, "product": product, "date_added": row.get("dateAdded"), "due_date": due}],
                    metadata={"defensive_only": True, "cve": cve, "vendor": vendor, "product": product},
                ))
                if len(candidates) >= int(limit or 20):
                    break
        except Exception as exc:
            statuses.append(source_status("cisa", "failed", reason=network_error_message(exc)))

        return CollectorResult(
            stream=self.stream,
            collector_key=self.collector_key,
            status="success" if any(item["status"].startswith("success") for item in statuses) else "failed",
            candidates=candidates[: int(limit or 20)],
            source_statuses=statuses,
            duration_ms=int((time.perf_counter() - started) * 1000),
            message="" if candidates else "No recent CISA exploited vulnerability exceeded filters.",
        )
