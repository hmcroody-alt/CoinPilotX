#!/usr/bin/env python3
"""Audit PulseSoc Home Status Creator studio UX contracts."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


BOT = ROOT / "bot.py"
CSS = ROOT / "static/css/pulse_status_system.css"
HOME_JS = ROOT / "static/js/pulse_home_core.js"


def expect(ok: bool, label: str, details: str = "") -> None:
    if not ok:
        raise AssertionError(f"{label} failed{': ' + details if details else ''}")
    print(f"PASS: {label}")


def main() -> None:
    source = BOT.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    home_js = HOME_JS.read_text(encoding="utf-8")

    expect("pulse-status-studio-grid" in source, "Home creator uses studio grid markup")
    expect("pulse-status-studio-controls" in source and "pulse-status-studio-preview" in source, "Desktop creator separates controls and preview")
    expect("data-status-mode-button='text'" in source and "data-status-mode-button='media'" in source and "data-status-mode-button='live'" in source, "Creator exposes compact Text Media Live modes")
    expect("data-status-toolset='text'" in source and "data-status-toolset='media'" in source and "data-status-toolset='live'" in source, "Creator tools are progressively disclosed")
    expect("data-status-back" not in source[source.find("def pulse_status_home_creator_html"):source.find("def promotion_card")], "Home creator no longer renders Back button")
    expect(">Cancel<" not in source[source.find("def pulse_status_home_creator_html"):source.find("def promotion_card")], "Home creator no longer renders redundant Cancel button")
    expect("data-status-publish-privacy" in source and "data-status-publish-duration" in source, "Publish area shows compact privacy and duration context")
    expect("Choose a status type to preview it here" not in source[source.find("def pulse_status_home_creator_html"):source.find("def promotion_card")], "Home creator avoids dead placeholder preview copy")
    expect("validateStatusFile" in source and "statusValidateFile" in home_js, "Client validates status media type and size before upload")
    expect("visibility not in {\"public\", \"followers\", \"private\"}" in source, "Server whitelists Status privacy")
    expect("statusRenderLivePreview" in home_js and "renderStatusStarterPreview" in source, "Preview updates from text/privacy/duration changes")
    expect("data-status-live-action='go'" in source and "/pulse/live" in source, "Live actions are wired to Live routes")

    expect(".pulse-status-studio-grid" in css and "grid-template-columns: minmax(320px, .86fr) minmax(360px, 1.14fr)" in css, "Desktop CSS uses two-column studio layout")
    expect("@media (max-width: 900px)" in css and ".pulse-status-studio-grid" in css, "Mobile CSS collapses studio layout")
    expect(".pulse-status-preview-starter" in css and "min-height: 188px" in css, "Mobile preview avoids giant dead space")
    expect(".pulse-status-pill-row select" in css, "Privacy and duration are styled as compact pills")

    bot.init_db()
    client = bot.webhook_app.test_client()
    with client.session_transaction() as session:
        session["account_user_id"] = 950781
    html = client.get("/pulse?create_status=1").get_data(as_text=True)
    expect("pulse-status-studio" in html and "pulse-status-studio-grid" in html, "Runtime Home renders Status studio")
    expect("data-status-toolset='media'" in html and "data-status-live-action='go'" in html, "Runtime Home renders media and live tools")

    print("pulse status creator studio audit ok")


if __name__ == "__main__":
    main()
