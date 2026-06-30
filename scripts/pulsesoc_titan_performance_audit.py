#!/usr/bin/env python3
"""Static PulseSoc performance audit for Project Titan.

This audit intentionally avoids importing bot.py. PulseSoc has a very large
monolithic Flask file, and importing it just to inspect performance structure
can hide cold-start cost inside the audit itself. The script scans source files,
reports measurable hotspots, and verifies safe optimization guardrails.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "reports" / "pulsesoc_titan_performance_audit.json"

IGNORE_DIRS = {
    ".git",
    "__pycache__",
    ".pytest_cache",
    "venv",
    "node_modules",
    "reports",
    "tmp",
}

JS_PATTERNS = {
    "add_event_listener": r"\.addEventListener\s*\(",
    "remove_event_listener": r"\.removeEventListener\s*\(",
    "set_interval": r"\bsetInterval\s*\(",
    "set_timeout": r"\bsetTimeout\s*\(",
    "mutation_observer": r"\bMutationObserver\s*\(",
    "resize_observer": r"\bResizeObserver\s*\(",
    "intersection_observer": r"\bIntersectionObserver\s*\(",
    "query_selector": r"\.querySelector\s*\(",
    "query_selector_all": r"\.querySelectorAll\s*\(",
    "fetch": r"\bfetch\s*\(",
    "inner_html": r"\.innerHTML\b",
}

DB_PATTERNS = {
    "select_star": r"\bSELECT\s+\*",
    "execute_calls": r"\.execute\s*\(",
    "executemany_calls": r"\.executemany\s*\(",
    "commit_calls": r"\.commit\s*\(",
}


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def should_skip(path: Path) -> bool:
    return any(part in IGNORE_DIRS for part in path.parts)


def iter_files(suffixes: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        if should_skip(path):
            continue
        if path.is_file() and path.suffix in suffixes:
            files.append(path)
    return sorted(files)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore")


def count_patterns(text: str, patterns: dict[str, str]) -> dict[str, int]:
    return {name: len(re.findall(pattern, text, flags=re.IGNORECASE)) for name, pattern in patterns.items()}


def file_metric(path: Path, patterns: dict[str, str] | None = None) -> dict[str, object]:
    text = read_text(path)
    metric: dict[str, object] = {
        "file": rel(path),
        "bytes": path.stat().st_size,
        "lines": text.count("\n") + 1,
    }
    if patterns:
        metric.update(count_patterns(text, patterns))
    return metric


def exact_duplicates(files: list[Path]) -> list[dict[str, object]]:
    buckets: dict[str, list[str]] = defaultdict(list)
    for path in files:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        buckets[digest].append(rel(path))
    return [
        {"sha256": digest, "files": paths, "count": len(paths)}
        for digest, paths in buckets.items()
        if len(paths) > 1
    ]


def js_function_duplicates(js_files: list[Path]) -> list[dict[str, object]]:
    name_to_files: dict[str, set[str]] = defaultdict(set)
    pattern = re.compile(
        r"\bfunction\s+([A-Za-z_$][\w$]*)\s*\(|"
        r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(",
        flags=re.MULTILINE,
    )
    for path in js_files:
        text = read_text(path)
        for match in pattern.finditer(text):
            name = match.group(1) or match.group(2)
            if name:
                name_to_files[name].add(rel(path))
    duplicated = [
        {"name": name, "files": sorted(files), "file_count": len(files)}
        for name, files in name_to_files.items()
        if len(files) > 1
    ]
    return sorted(duplicated, key=lambda item: (-item["file_count"], item["name"]))[:100]


def css_selector_duplicates(css_files: list[Path]) -> list[dict[str, object]]:
    selector_to_files: dict[str, Counter[str]] = defaultdict(Counter)
    selector_pattern = re.compile(r"([^{}]+)\{", flags=re.MULTILINE)
    for path in css_files:
        text = re.sub(r"/\*.*?\*/", "", read_text(path), flags=re.DOTALL)
        for raw_selector in selector_pattern.findall(text):
            selector = " ".join(raw_selector.strip().split())
            if not selector or selector.startswith("@"):
                continue
            selector_parts = [part.strip() for part in selector.split(",")]
            if all(part in {"from", "to"} or re.fullmatch(r"\d+(?:\.\d+)?%", part) for part in selector_parts):
                continue
            selector_to_files[selector][rel(path)] += 1
    duplicated = []
    for selector, files in selector_to_files.items():
        total = sum(files.values())
        if total > 1:
            duplicated.append(
                {
                    "selector": selector[:180],
                    "total_occurrences": total,
                    "files": dict(sorted(files.items())),
                }
            )
    return sorted(duplicated, key=lambda item: -item["total_occurrences"])[:100]


def static_asset_summary(js_metrics: list[dict[str, object]], css_metrics: list[dict[str, object]]) -> dict[str, object]:
    return {
        "js": {
            "files": len(js_metrics),
            "bytes": sum(int(item["bytes"]) for item in js_metrics),
            "largest": sorted(js_metrics, key=lambda item: -int(item["bytes"]))[:20],
        },
        "css": {
            "files": len(css_metrics),
            "bytes": sum(int(item["bytes"]) for item in css_metrics),
            "largest": sorted(css_metrics, key=lambda item: -int(item["bytes"]))[:20],
        },
    }


def db_summary(py_files: list[Path]) -> dict[str, object]:
    metrics = [file_metric(path, DB_PATTERNS) for path in py_files]
    totals = Counter()
    for item in metrics:
        for key in DB_PATTERNS:
            totals[key] += int(item[key])
    return {
        "totals": dict(totals),
        "highest_query_pressure": sorted(
            metrics,
            key=lambda item: -(int(item["execute_calls"]) + int(item["select_star"]) * 5),
        )[:20],
    }


def media_summary() -> dict[str, object]:
    source_files = [ROOT / "bot.py"] + iter_files((".html", ".js"))
    totals = Counter()
    for path in source_files:
        if not path.exists():
            continue
        text = read_text(path)
        totals["img_tags"] += len(re.findall(r"<img\b", text, flags=re.IGNORECASE))
        totals["video_tags"] += len(re.findall(r"<video\b", text, flags=re.IGNORECASE))
        totals["lazy_loading"] += len(re.findall(r"loading=['\"]lazy['\"]", text, flags=re.IGNORECASE))
        totals["preload_metadata"] += len(re.findall(r"preload=['\"]metadata['\"]", text, flags=re.IGNORECASE))
        totals["video_pause_calls"] += len(re.findall(r"\.pause\s*\(", text))
        totals["video_play_calls"] += len(re.findall(r"\.play\s*\(", text))
    return dict(totals)


def live_polling_guardrails() -> dict[str, object]:
    files = [
        ROOT / "static/js/pulse_live_studio.js",
        ROOT / "static/js/pulse_live_studio_runtime.js",
    ]
    checks = {}
    for path in files:
        text = read_text(path)
        checks[rel(path)] = {
            "uses_schedule_live_state_polling": "scheduleLiveStatePolling" in text,
            "pauses_hidden_tab_polling": "document.hidden" in text,
            "guards_overlapping_state_fetches": "inFlight" in text,
            "legacy_unconditional_fetch_interval_absent": "setInterval(() => fetchState(root)" not in text,
        }
    return checks


def bot_cache_guardrails() -> dict[str, object]:
    text = read_text(ROOT / "bot.py")
    return {
        "static_cache_header_present": 'request.path.startswith(("/static/", "/icons/"))' in text,
        "static_cache_immutable_present": "max-age=31536000, immutable" in text,
        "service_worker_no_store_present": 'request.path in ("/static/service-worker.js", "/sw.js")' in text,
        "api_private_default_no_store_present": 'request.path.startswith("/api/")' in text and "no-store" in text,
    }


def analytics_guardrails() -> dict[str, object]:
    text = read_text(ROOT / "static/analytics.js")
    return {
        "scroll_depth_uses_raf_gate": "scrollDepthScheduled" in text and "requestAnimationFrame(evaluateScrollDepth)" in text,
        "heartbeat_skips_hidden_tabs": 'document.visibilityState !== "visible"' in text,
        "analytics_fetches_are_non_blocking": ".catch(function ()" in text and "/api/track" in text,
    }


def ranked_findings(
    js_metrics: list[dict[str, object]],
    css_metrics: list[dict[str, object]],
    db: dict[str, object],
    duplicate_js_functions: list[dict[str, object]],
    duplicate_css_selectors: list[dict[str, object]],
) -> list[dict[str, object]]:
    findings: list[dict[str, object]] = []
    bot_size = (ROOT / "bot.py").stat().st_size
    if bot_size > 5_000_000:
        findings.append(
            {
                "rank": 1,
                "severity": "critical",
                "area": "startup/backend",
                "finding": "bot.py remains a monolithic multi-megabyte Flask module.",
                "evidence": {"bytes": bot_size, "file": "bot.py"},
                "recommended_next_step": "Split cold-start-heavy route/template/static builders into import-light modules after route parity tests exist.",
            }
        )
    largest_js = max(js_metrics, key=lambda item: int(item["bytes"])) if js_metrics else None
    if largest_js and int(largest_js["bytes"]) > 120_000:
        findings.append(
            {
                "rank": 2,
                "severity": "high",
                "area": "frontend/js",
                "finding": "A single JS file exceeds the 120KB review threshold.",
                "evidence": largest_js,
                "recommended_next_step": "Split route-specific logic or lazy-load below-the-fold behaviors without changing current UI.",
            }
        )
    highest_listeners = sorted(js_metrics, key=lambda item: -int(item["add_event_listener"]))[:1]
    if highest_listeners and int(highest_listeners[0]["add_event_listener"]) > 50:
        findings.append(
            {
                "rank": 3,
                "severity": "high",
                "area": "frontend/listeners",
                "finding": "One script registers a high number of explicit event listeners.",
                "evidence": highest_listeners[0],
                "recommended_next_step": "Consolidate repeated handlers into delegated listeners where the DOM structure is stable.",
            }
        )
    select_star_total = int(db["totals"].get("select_star", 0))
    if select_star_total:
        findings.append(
            {
                "rank": 4,
                "severity": "high",
                "area": "database/api",
                "finding": "SELECT * usage remains present and can inflate payload and row materialization cost.",
                "evidence": {"select_star_total": select_star_total},
                "recommended_next_step": "Replace hot-path SELECT * calls with explicit projected columns after endpoint snapshots are captured.",
            }
        )
    if duplicate_js_functions:
        findings.append(
            {
                "rank": 5,
                "severity": "medium",
                "area": "frontend/js",
                "finding": "Duplicate JS utility/function names exist across files.",
                "evidence": duplicate_js_functions[:10],
                "recommended_next_step": "Move stable utilities into existing shared modules only after bundle-load boundaries are confirmed.",
            }
        )
    if duplicate_css_selectors:
        findings.append(
            {
                "rank": 6,
                "severity": "medium",
                "area": "frontend/css",
                "finding": "Repeated CSS selectors exist and should be consolidated carefully.",
                "evidence": duplicate_css_selectors[:10],
                "recommended_next_step": "Consolidate duplicated design tokens and route-specific overrides after visual regression checks.",
            }
        )
    return findings


def main() -> int:
    js_files = [path for path in iter_files((".js",)) if "/vendor/" not in rel(path)]
    css_files = iter_files((".css",))
    py_files = [path for path in iter_files((".py",)) if path.name != Path(__file__).name]

    js_metrics = [file_metric(path, JS_PATTERNS) for path in js_files]
    css_metrics = [file_metric(path) for path in css_files]
    duplicate_js_functions = js_function_duplicates(js_files)
    duplicate_css_selectors = css_selector_duplicates(css_files)
    db = db_summary(py_files)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "scope": "PulseSoc Project Titan static performance audit",
        "static_assets": static_asset_summary(js_metrics, css_metrics),
        "javascript_pressure": {
            "totals": dict(sum((Counter({key: int(item[key]) for key in JS_PATTERNS}) for item in js_metrics), Counter())),
            "highest_listener_pressure": sorted(js_metrics, key=lambda item: -int(item["add_event_listener"]))[:20],
            "highest_timer_pressure": sorted(
                js_metrics,
                key=lambda item: -(int(item["set_interval"]) * 3 + int(item["set_timeout"])),
            )[:20],
            "highest_dom_query_pressure": sorted(
                js_metrics,
                key=lambda item: -(int(item["query_selector"]) + int(item["query_selector_all"]) * 2),
            )[:20],
            "duplicate_function_names": duplicate_js_functions,
            "exact_duplicate_js_files": exact_duplicates(js_files),
        },
        "css_pressure": {
            "duplicate_selectors": duplicate_css_selectors,
            "exact_duplicate_css_files": exact_duplicates(css_files),
        },
        "database_pressure": db,
        "media_patterns": media_summary(),
        "guardrails": {
            "analytics": analytics_guardrails(),
            "live_state_polling": live_polling_guardrails(),
            "cache_headers": bot_cache_guardrails(),
        },
    }
    report["ranked_findings"] = ranked_findings(
        js_metrics,
        css_metrics,
        db,
        duplicate_js_functions,
        duplicate_css_selectors,
    )

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    failed_guardrails = []
    for file_name, checks in report["guardrails"]["live_state_polling"].items():
        for check_name, passed in checks.items():
            if not passed:
                failed_guardrails.append(f"{file_name}:{check_name}")
    for check_name, passed in report["guardrails"]["analytics"].items():
        if not passed:
            failed_guardrails.append(f"static/analytics.js:{check_name}")
    if failed_guardrails:
        print("FAILED live polling guardrails:")
        for failure in failed_guardrails:
            print(f" - {failure}")
        return 1

    print(f"Wrote {rel(REPORT)}")
    print("Top findings:")
    for finding in report["ranked_findings"][:10]:
        print(f" - [{finding['severity']}] {finding['area']}: {finding['finding']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
