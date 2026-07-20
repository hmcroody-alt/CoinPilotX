#!/usr/bin/env python3
"""Static contract audit for the native Status icon-only action rail."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RAIL = (ROOT / "mobile-native/src/components/StatusActionRail.tsx").read_text()
VIEWER = (ROOT / "mobile-native/src/components/StatusViewerCard.tsx").read_text()
SCREEN = (ROOT / "mobile-native/src/screens/StatusScreen.tsx").read_text()
API = (ROOT / "mobile-native/src/api/status.ts").read_text()


def require(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"PASS {label}")


require("<StatusActionRail" in VIEWER, "viewer mounts the shared Status action rail")
require('label="React"' not in VIEWER and 'label="Reply"' not in VIEWER and 'label="Share"' not in VIEWER, "legacy visible action labels are removed")
require('icon={selectedReaction ? "heart" : "heart-outline"}' in RAIL, "reaction uses outline and filled project heart icons")
require('icon="chatbubble-ellipses-outline"' in RAIL, "reply uses the project message icon")
require('icon="paper-plane-outline"' in RAIL, "share uses the project paper-plane icon")
require('testID="status-action-reaction-count"' in RAIL, "reaction count remains visible")
require("onLongPress={openReactionTray}" in RAIL and "trayOpen ?" in RAIL, "reaction tray mounts only when opened")
require("stopPropagation" in RAIL and "longPressTriggered" in RAIL, "rail owns its gesture responder priority")
require("useLogiNexusReducedMotion" in RAIL and "if (reducedMotion) return" in RAIL, "reduced motion disables bloom and bounce animation")
require('accessibilityLabel="Reply to Status"' in RAIL and 'accessibilityLabel="Share Status"' in RAIL and "React to Status" in RAIL, "icon-only controls retain VoiceOver labels")
require("pendingActions.current.has(actionKey)" in SCREEN, "rapid reaction and share taps are deduplicated")
require("reactionVersions.current.get(status.id)" in SCREEN, "stale reaction responses cannot overwrite newer state")
require("previousCount" in SCREEN and "viewer_reaction: previousReaction" in SCREEN, "failed optimistic reaction rolls back")
require("autoFocus" in SCREEN, "reply composer focuses its input")
for route in ("/api/pulse/status/${statusId}/react", "/api/pulse/status/${statusId}/reply", "/api/pulse/status/${statusId}/share"):
    require(route in API, f"production Status route preserved: {route}")
require("react-native-reanimated" not in RAIL, "no heavy animation dependency was added")

print("PulseSoc native Status futuristic icon actions audit: PASS")
