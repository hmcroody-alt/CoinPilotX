#!/usr/bin/env python3
"""Guard the public company name displayed across PulseSoc."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OFFICIAL_COMPANY = "CoinPlotXAI Inc."
OFFICIAL_SHORT = "CoinPlotXAI"
REQUIRED_FOOTER = "PulseSoc™ • Built by CoinPlotXAI Inc."
OLD_CORE = "Coin" + "PilotXAI"
OLD_TOKENS = (
    OLD_CORE + " Inc.",
    OLD_CORE,
    "Coin" + "Pilot XAI",
    "Coin" + "pilotxai",
    "coin" + "pilotxai",
    "Built by " + OLD_CORE + " Inc.",
)
SKIP_DIRS = {".git", "venv", "node_modules", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
SKIP_PREFIXES = {ROOT / "reports" / "screenshots"}
SAFE_EXTS = {
    ".cfg",
    ".config",
    ".css",
    ".csv",
    ".html",
    ".ini",
    ".js",
    ".json",
    ".md",
    ".plist",
    ".py",
    ".svg",
    ".toml",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}
LOWERCASE_TECHNICAL_ALLOWLIST = (
    "coin" + "pilotxai.app",
    "coin" + "pilotxai@gmail.com",
    "coin" + "pilotxai_session_id",
    "coin" + "pilotxai_last_visit_day",
    "coin" + "pilotxai-inc",
    "coin" + "pilotxai-alert",
    "coin" + "pilotxai-og.png",
    "og-coin" + "pilotxai.png",
    "coin" + "pilotxai-share-card.svg",
    "\"source\": \"coin" + "pilotxai\"",
    "source = \"coin" + "pilotxai\"",
    "source\", \"coin" + "pilotxai\"",
    "source\" or \"coin" + "pilotxai\"",
    "source\") or \"coin" + "pilotxai\"",
    "source', 'coin" + "pilotxai'",
)


def should_scan(path: Path) -> bool:
    if not path.is_file():
        return False
    if any(part in SKIP_DIRS for part in path.parts):
        return False
    if any(path == prefix or prefix in path.parents for prefix in SKIP_PREFIXES):
        return False
    return path.suffix.lower() in SAFE_EXTS


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return ""


def lowercase_old_is_allowed(line: str) -> bool:
    return any(token in line for token in LOWERCASE_TECHNICAL_ALLOWLIST)


def main() -> int:
    failures: list[str] = []
    for path in ROOT.rglob("*"):
        if not should_scan(path):
            continue
        text = read_text(path)
        if not text:
            continue
        rel = path.relative_to(ROOT)
        for token in OLD_TOKENS:
            if token == "coin" + "pilotxai":
                for line_no, line in enumerate(text.splitlines(), start=1):
                    if token in line and not lowercase_old_is_allowed(line):
                        failures.append(f"{rel}:{line_no} contains old lowercase public company token")
                continue
            if token in text:
                failures.append(f"{rel} contains old company token")

    account_html = read_text(ROOT / "templates" / "account.html")
    if REQUIRED_FOOTER not in account_html:
        failures.append("templates/account.html missing exact PulseSoc footer")

    footer_files = [
        ROOT / "templates" / "index.html",
        ROOT / "templates" / "account.html",
        ROOT / "templates" / "privacy.html",
        ROOT / "templates" / "terms.html",
        ROOT / "templates" / "seo_page.html",
        ROOT / "static" / "llms.txt",
    ]
    for path in footer_files:
        text = read_text(path)
        if path.exists() and REQUIRED_FOOTER not in text:
            failures.append(f"{path.relative_to(ROOT)} missing official footer text")

    bot_source = read_text(ROOT / "bot.py")
    if f'"company": "{OFFICIAL_COMPANY}"' not in bot_source:
        failures.append("bot.py missing official Stripe company metadata")
    if "filename=coinplotxai-" not in bot_source:
        failures.append("admin exports must use the corrected visible download filename")

    communication_files = [
        ROOT / "services" / "email_service.py",
        ROOT / "pulse_communications_v2" / "twilio_service.py",
        ROOT / "static" / "sw.js",
        ROOT / "static" / "service-worker.js",
    ]
    for path in communication_files:
        text = read_text(path)
        if path.exists() and OFFICIAL_SHORT not in text and OFFICIAL_COMPANY not in text:
            failures.append(f"{path.relative_to(ROOT)} missing corrected company name in communications copy")

    if failures:
        print("COMPANY_NAME_BRANDING_AUDIT failed")
        for failure in failures[:120]:
            print("-", failure)
        return 1
    print("COMPANY_NAME_BRANDING_AUDIT ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
