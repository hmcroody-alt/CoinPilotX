#!/usr/bin/env python3
"""Audit PulseSoc Intelligence alert presentation V2."""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, label: str, failures: list[str]) -> None:
    if not condition:
        failures.append(label)


def assert_clean_copy(failures: list[str]) -> None:
    from services.pulsesoc_intelligence_engine import normalize_intelligence_alert_copy

    samples = [
        (
            "crypto",
            {
                "stream_key": "crypto_pulse",
                "headline": "BTC crossed $61,000",
                "summary": "Bitcoin crossed $61,000 with rising volume.",
                "priority": "high",
                "metadata": {"asset_symbol": "BTC"},
            },
            "BTC BREAKOUT DETECTED",
        ),
        (
            "market",
            {
                "stream_key": "market_pulse",
                "headline": "S&P 500 status card",
                "summary": "Market strength is improving ahead of the close.",
                "priority": "normal",
                "metadata": {"status_card": {"asset": "S&P 500", "status": "Momentum improving", "signal": "Breakout watch"}},
            },
            "S&P 500 MOMENTUM RISING",
        ),
        (
            "security",
            {
                "stream_key": "security_pulse",
                "headline": "Critical iOS update released",
                "summary": "A critical Apple security update is available.",
                "priority": "urgent",
            },
            "SECURITY SIGNAL",
        ),
        (
            "discovery",
            {
                "stream_key": "pulsesoc_discoveries",
                "headline": "PulseSoc recorded a platform update from pulse_app_store_readiness.md",
                "summary": "PulseSoc recorded a platform update from pulse_app_store_readiness.md. https://apps.apple.com/us/app/pulsesoc/id6777591572",
                "priority": "normal",
            },
            "NEW DISCOVERY AVAILABLE",
        ),
    ]

    for label, signal, expected_headline in samples:
        copy = normalize_intelligence_alert_copy(signal)
        joined = " ".join(str(copy.get(key, "")) for key in ("lock_title", "lock_headline", "lock_body", "card_headline", "card_summary"))
        require(copy.get("lock_title") == "PULSESOC ALERT", f"{label} lock title is official PulseSoc label", failures)
        require(copy.get("lock_headline") == expected_headline, f"{label} headline normalized", failures)
        require("http" not in joined and "apps.apple.com" not in joined, f"{label} copy hides raw URLs", failures)
        require(".md" not in joined and ".py" not in joined and "dedupe" not in joined.lower(), f"{label} copy strips internal file/debug language", failures)
        require(len(str(copy.get("lock_body") or "")) <= 170, f"{label} lock body is short", failures)

    unsafe_financial = normalize_intelligence_alert_copy(
        {
            "stream_key": "market_pulse",
            "headline": "Market advice",
            "summary": "Buy now for guaranteed profit.",
            "priority": "high",
        }
    )
    require("not financial advice" in unsafe_financial.get("lock_body", "").lower(), "unsafe financial phrase is replaced with safety wording", failures)

    unsafe_cyber = normalize_intelligence_alert_copy(
        {
            "stream_key": "security_pulse",
            "headline": "Security test",
            "summary": "Exploit code and malware instructions are available.",
            "priority": "urgent",
        }
    )
    require("protection guidance" in unsafe_cyber.get("lock_body", "").lower(), "unsafe cyber phrase is replaced with defensive wording", failures)

    digest = normalize_intelligence_alert_copy({"stream_key": "pulsesoc_discoveries", "priority": "normal"}, "digest")
    require(digest.get("lock_headline") == "DAILY BRIEFING READY", "digest copy uses daily briefing headline", failures)


def main() -> int:
    failures: list[str] = []

    engine = read("services/pulsesoc_intelligence_engine.py")
    notifications = read("services/pulsesoc_notification_system.py")
    service_worker = read("static/service-worker.js")
    js = read("static/js/pulsesoc_intelligence_center.js")
    css = read("static/css/pulsesoc_intelligence_center.css")
    report_path = ROOT / "reports" / "pulsesoc_intelligence_alert_visual_system.md"

    require("normalize_intelligence_alert_copy" in engine, "copy normalization helper exists", failures)
    require('"lock_title": "PULSESOC ALERT"' in engine, "lock-screen title generator uses official label", failures)
    require("_clean_alert_text" in engine and "https?://" in engine, "normalizer strips URLs and internal text", failures)
    require("FINANCIAL_UNSAFE_PHRASES" in engine and "MARKET_INTELLIGENCE_DISCLAIMER" in engine, "financial safety wording exists", failures)
    require("CYBER_UNSAFE_PHRASES" in engine, "cyber safety guard exists", failures)
    require('"headline": alert_copy["lock_headline"]' in engine, "notification metadata includes normalized headline", failures)
    require('"show_on_lock_screen": True' in engine and '"sound_key": sound_key' in engine, "push metadata keeps lock-screen delivery fields", failures)

    require('"subtitle": str(payload.get("headline")' in notifications, "APNs payload can show Intelligence headline separately", failures)
    require('isIntelligence ? "PULSESOC ALERT"' in service_worker, "service worker uses official Intelligence title", failures)
    require("intelligenceHeadline" in service_worker and "showNotification(title, options)" in service_worker, "service worker renders Intelligence headline/body", failures)
    require("/pulse/alerts" in service_worker, "service worker opens Pulse Alerts for Intelligence", failures)

    require("signal-card-v2" in js and "signal-source-label" in js, "in-app alert card renders V2 structure", failures)
    require("card_priority_badge" in js and "card_category" in js, "in-app card shows category and priority", failures)
    require("data-ask-pulse-ai" in js and "pulse_ai=1" in js, "Ask Pulse AI action exists", failures)
    require("data-delete-signal" in js and "Mark read" in js and "Save" in js, "read/save/delete controls exist", failures)
    require("apps.apple.com/us/app/pulsesoc/id6777591572" not in js, "user alert renderer does not hardcode raw App Store URL", failures)

    require(".signal-card-v2" in css and ".signal-icon" in css, "premium alert card CSS exists", failures)
    require("data-signal-accent=\"green\"" in css and "data-signal-accent=\"gold\"" in css, "category accent styling exists", failures)
    require(".signal-source-label" in css and ".signal-priority" in css, "source and priority styling exists", failures)
    require(report_path.exists(), "visual system report exists", failures)

    assert_clean_copy(failures)

    if failures:
        print("PulseSoc Intelligence alert visual system audit failed:")
        for failure in failures:
            print(f" - {failure}")
        return 1
    print("PulseSoc Intelligence alert visual system audit passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
