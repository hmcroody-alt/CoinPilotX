#!/usr/bin/env python3
"""Static audit for the PulseSoc Messenger Conversation Control Center."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "templates" / "pulse_messages_v2.html"
JS = ROOT / "static" / "js" / "pulse_messages_v2.js"
CSS = ROOT / "static" / "css" / "pulse_messages_v2.css"
ROUTES = ROOT / "pulse_communications_v2" / "routes.py"
SERVICE = ROOT / "pulse_communications_v2" / "service.py"
MODELS = ROOT / "pulse_communications_v2" / "models.py"
REPORT = ROOT / "reports" / "conversation_control_center_v3_wiring.md"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def require(checks: list[dict], name: str, passed: bool, detail: str = "") -> None:
    checks.append({"name": name, "passed": bool(passed), "detail": detail})


def main() -> int:
    checks: list[dict] = []
    template = read(TEMPLATE)
    js = read(JS)
    css = read(CSS)
    routes = read(ROUTES)
    service = read(SERVICE)
    models = read(MODELS)

    required_sections = [
        "Conversation",
        "Notifications",
        "Appearance",
        "Privacy",
        "AI Assistant",
        "Media",
        "Security",
        "Productivity",
        "Group Settings",
        "Storage",
        "Accessibility",
        "Danger Zone",
    ]
    required_themes = [
        "dark_galaxy",
        "pulse_green",
        "deep_space",
        "nebula",
        "cyber_night",
        "solar_flame",
        "ocean_signal",
        "royal_purple",
        "haiti_night",
        "creator_gold",
    ]
    required_wallpapers = [
        "deep_space",
        "neon_planet",
        "galaxy_grid",
        "pulse_horizon",
        "alien_city",
        "cosmic_ocean",
        "aurora_signal",
        "dark_nebula",
        "star_tunnel",
        "minimal_black",
    ]

    require(checks, "thread gear opens control center", "control-center-action" in template and "data-open-control-center" in template)
    require(checks, "inbox gear opens control center", 'data-control-center-entry="inbox"' in template and 'aria-label="Open Conversation Control Center"' in template)
    require(checks, "inbox gear is not a legacy filter toggle", "data-toggle-filters" not in template and "data-toggle-filters" not in js)
    require(checks, "inbox conversation chooser exists", "data-control-select-conversation" in js and "renderControlConversationChooser" in js)
    require(checks, "search and gear have separate accessible names", 'aria-label="Search people, rooms, and messages"' in template)
    require(checks, "dialog shell exists", "data-conversation-control-center" in template and 'role="dialog"' in template)
    require(checks, "backdrop exists", "data-conversation-control-backdrop" in template)
    require(checks, "settings js exists", "openConversationControlCenter" in js and "renderControlCenter" in js)
    require(checks, "settings css exists", ".conversation-control-center" in css and ".conversation-control-backdrop" in css)
    require(checks, "desktop slide panel exists", "translateX(110%)" in css and "width: clamp(420px, 34vw, 520px)" in css)
    require(checks, "mobile bottom sheet exists", "translateY(108%)" in css and "height: min(92dvh, 760px)" in css)
    require(checks, "required sections present", all(section in js for section in required_sections), ", ".join(section for section in required_sections if section not in js))
    require(checks, "danger zone present", "Danger Zone" in js and "is-danger" in css)
    require(checks, "no internal architecture name exposed", "LogiNexus" not in template + js + css)
    require(checks, "no visible coming soon labels in control center js", "Coming Soon" not in js and "Requires Setup" not in js and "Unavailable" not in js)
    require(checks, "no unknown control status", '"Unknown"' not in js and '"Unknown"' not in service)
    require(checks, "dead call buttons removed", "Voice calls coming soon" not in template + js and "Video calls coming soon" not in template + js)
    require(checks, "microphone icon used for voice attachment", 'data-attachment-option="voice"><span aria-hidden="true">&#127908;' in template)
    require(checks, "microphone icon used for recorder", 'data-voice-start title="Record voice note" aria-label="Record voice note">&#127908;' in template)
    require(checks, "control center endpoints exist", "control-center" in routes and "conversation_control_center" in routes)
    require(checks, "patch endpoint exists", "@comm_v2_blueprint.patch" in routes and "update_conversation_control_center" in routes)
    require(checks, "detail endpoints exist", "conversation_control_media" in routes and "conversation_control_links" in routes and "conversation_control_pins" in routes and "conversation_control_export" in routes)
    require(checks, "action endpoint exists", "conversation_control_action" in routes and "control-center/action" in routes)
    require(checks, "participant permission checks exist", "_conversation_access(cur, user_id, conversation_ref)" in service)
    require(checks, "settings table exists", "comm_v2_conversation_settings" in models and "UNIQUE(conversation_id, user_id)" in models)
    require(checks, "conversation item table exists", "comm_v2_conversation_items" in models)
    require(checks, "group-only controls are gated", "groupOnly: true" in js and "isControlGroup" in js)
    require(checks, "quick actions wired", all(token in js for token in ['action: "search-chat"', 'action: "mute"', 'action: "pin"', 'action: "archive"']))
    require(checks, "notification toggles persist and affect push", "lock_screen_disabled" in service and "_message_preview_hidden(policy_cur" in service)
    require(checks, "privacy toggles affect behavior", "_read_receipts_allowed(cur, user_id, conversation_id)" in service and "typing_indicator" in service)
    require(checks, "appearance toggles affect UI", "applyControlAppearanceSettings" in js and "control-high-contrast" in css and "data-control-theme" in css)
    require(checks, "all built-in themes exposed", all(theme in js and theme in service for theme in required_themes), ", ".join(theme for theme in required_themes if theme not in js or theme not in service))
    require(checks, "all built-in themes styled", all(f'data-control-theme="{theme}"' in css or theme == "dark_galaxy" for theme in required_themes), ", ".join(theme for theme in required_themes if f'data-control-theme="{theme}"' not in css and theme != "dark_galaxy"))
    require(checks, "all built-in wallpapers exposed", all(wallpaper in js and wallpaper in service for wallpaper in required_wallpapers), ", ".join(wallpaper for wallpaper in required_wallpapers if wallpaper not in js or wallpaper not in service))
    require(checks, "all built-in wallpapers styled", all(f'data-control-wallpaper="{wallpaper}"' in css or wallpaper == "deep_space" for wallpaper in required_wallpapers), ", ".join(wallpaper for wallpaper in required_wallpapers if f'data-control-wallpaper="{wallpaper}"' not in css and wallpaper != "deep_space"))
    require(checks, "theme and wallpaper affect real chat surfaces", all(token in css for token in [".comm-shell .messages", ".comm-shell .message.is-mine", ".comm-shell .thread-head", ".comm-shell .composer", "--control-wallpaper", "--control-sent-bg"]))
    require(checks, "appearance settings persist reload safely", "controlSettingsCacheKey" in js and "localStorage.setItem" in js and "hydrateConversationVisualSettings" in js)
    require(checks, "dropdown changes preview immediately and rollback", "optimisticSettings" in js and "previousSettings" in js and "cacheControlSettings(id, previousSettings)" in js)
    require(checks, "media and storage use real data", "conversation_control_media" in service and "conversation_control_links" in service and "storage_used_bytes" in service)
    require(checks, "danger actions require confirmation", "clear-conversation" in js and "data-control-confirm" in js and "conversation_control_action" in service)
    require(checks, "report file exists", REPORT.exists())

    failed = [check for check in checks if not check["passed"]]
    payload = {"ok": not failed, "checks": checks, "failed": failed}
    print(json.dumps(payload, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
