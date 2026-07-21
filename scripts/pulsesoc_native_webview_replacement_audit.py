#!/usr/bin/env python3
"""Audit whether native PulseSoc can replace the WebView client without web exits.

The check is intentionally static and conservative: a route or file that still
opens a web URL, declares a safe-web fallback, or mounts a WebView is treated as
a blocker for a "native only" App Store update.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
NATIVE_SRC = ROOT / "mobile-native" / "src"
SCREENS_DIR = NATIVE_SRC / "screens"
REPORT_PATH = ROOT / "reports" / "pulsesoc_native_webview_replacement_readiness.json"


CRITICAL_SURFACES = [
    ("Authentication", ["LoginScreen.tsx", "SignupScreen.tsx", "AccountRecoveryScreen.tsx"], ["Login", "Signup", "AccountRecovery"]),
    ("Home feed", ["HomeScreen.tsx"], ["Home"]),
    ("Search / Discover", ["SearchScreen.tsx"], ["Search"]),
    ("Messenger inbox", ["MessengerScreen.tsx"], ["Messenger"]),
    ("Conversation", ["ChatScreen.tsx"], ["Chat"]),
    ("Calls", ["CallScreen.tsx"], ["Call"]),
    ("Groups / Rooms", ["GroupsScreen.tsx"], ["Groups", "GroupDetail"]),
    ("Reels", ["ReelsScreen.tsx"], ["Reels", "ReelDetail"]),
    ("Status", ["StatusScreen.tsx"], ["Status", "StatusDetail"]),
    ("Live", ["LiveScreen.tsx"], ["Live", "LiveDetail"]),
    ("Camera Studio", ["CameraStudioScreen.tsx"], ["CameraStudio"]),
    ("Music / Pulse Radio", ["MusicScreen.tsx"], ["Music"]),
    ("Activity / Notifications", ["ActivityInboxScreen.tsx", "NotificationCenterScreen.tsx", "NotificationPreferencesScreen.tsx"], ["ActivityInbox", "NotificationCenter", "NotificationPreferences"]),
    ("Profile", ["ProfileScreen.tsx", "ProfileEditScreen.tsx"], ["Profile", "ProfileDetail", "ProfileEdit"]),
    ("Marketplace", ["MarketplaceScreen.tsx", "SellerListingComposerScreen.tsx"], ["Marketplace", "MarketplaceDetail", "MarketplaceCreateGateway"]),
    ("Seller / Store", ["SellerStoreScreen.tsx"], ["SellerStore", "MerchantApply", "MerchantDashboard", "MerchantProfile"]),
    ("Buyer Orders", ["BuyerOrdersScreen.tsx"], ["BuyerOrders", "BuyerOrderDetail", "BuyerPurchases", "BuyerOrdersDashboard"]),
    ("Premium / Billing", ["PremiumScreen.tsx"], ["Premium"]),
    ("Creator Studio", ["CreatorStudioScreen.tsx", "ContentPlannerScreen.tsx", "GrowthCenterScreen.tsx"], ["CreatorStudio", "ContentPlanner", "PostScheduler", "DraftStudio", "GrowthCenter"]),
    ("Courses / Learning", ["CoursesLearningScreen.tsx"], ["Courses", "CourseDetail", "LearningLessonDetail"]),
    ("Events", ["EventsScreen.tsx"], ["Events", "EventDetail", "LiveScheduleGateway", "LiveEventCreateGateway"]),
    ("Dashboard", ["UserDashboardScreen.tsx", "DashboardModuleDetailScreen.tsx", "DashboardLegacyModuleScreen.tsx", "DashboardActionAliasScreen.tsx"], ["UserDashboard", "DashboardModuleDetail", "DashboardLegacyModule"]),
    ("Intelligence / UNDX", ["PulseAiScreen.tsx", "IntelligenceCenterScreen.tsx", "AlertManagementScreen.tsx"], ["PulseAI", "IntelligenceCenter", "AlertManagement"]),
    ("Account / Settings", ["AccountCenterScreen.tsx", "SettingsScreen.tsx"], ["AccountCenter", "Settings", "AccountSettings", "AccountSecurity", "AccountPrivacy", "AccountDevices"]),
    ("Trust / Safety", ["SafetyHubScreen.tsx", "TrustSafetyScreen.tsx", "AccountHealthAppealsScreen.tsx", "VerificationCenterScreen.tsx"], ["SafetyHub", "TrustSafety", "AccountHealth", "VerificationCenter"]),
    ("Saved", ["SavedScreen.tsx"], ["Saved"]),
]


BLOCKER_PATTERNS = {
    "mounted_WebView": re.compile(r"react-native-webview|<WebView\b|import\s+.*\bWebView\b"),
    "webview_copy": re.compile(r"\bWebView\b"),
    "Linking.openURL": re.compile(r"\bLinking\.openURL\b|\bopenURL\("),
    "safe_web_fallback": re.compile(r"safe_web_fallback"),
    "fallback_status": re.compile(r"status:\s*[\"']fallback[\"']"),
    "web_fallback_copy": re.compile(r"web fallback|Open web fallback|safe fallback", re.IGNORECASE),
    "webPath": re.compile(r"\bwebPath\b"),
}


@dataclass
class Finding:
    file: str
    line: int
    kind: str
    text: str


def rel(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="ignore") if path.exists() else ""


def native_code_files() -> list[Path]:
    return [
        path
        for path in NATIVE_SRC.rglob("*")
        if path.suffix in {".ts", ".tsx", ".js", ".jsx"}
        and "node_modules" not in path.parts
    ]


def scan_blockers() -> list[Finding]:
    findings: list[Finding] = []
    for path in native_code_files():
        for index, line in enumerate(read(path).splitlines(), start=1):
            for kind, pattern in BLOCKER_PATTERNS.items():
                if pattern.search(line):
                    findings.append(Finding(rel(path), index, kind, line.strip()))
    return findings


def route_names() -> set[str]:
    app_nav = read(NATIVE_SRC / "navigation" / "AppNavigator.tsx")
    types = read(NATIVE_SRC / "navigation" / "types.ts")
    names = set(re.findall(r'<(?:Stack|Tabs)\.Screen\s+name="([^"]+)"', app_nav))
    names.update(re.findall(r"^\s*([A-Za-z0-9_]+):", types, flags=re.MULTILINE))
    return names


def production_route_mentions() -> dict[str, int]:
    roots = [
        ROOT / "templates",
        ROOT / "static" / "js",
        ROOT / "pulse_communications_v2",
        ROOT / "services",
    ]
    patterns = [
        "/pulse/messages",
        "/pulse/reels",
        "/pulse/videos",
        "/pulse/live",
        "/pulse/marketplace",
        "/pulse/seller",
        "/pulse/orders",
        "/pulse/premium",
        "/pulse/creator",
        "/pulse/search",
        "/pulse/status",
        "/pulse/camera",
        "/pulse/music",
        "/pulse/groups",
        "/pulse/rooms",
        "/pulse/settings",
        "/pulse/profile",
        "/pulse/notifications",
        "/pulse/activity",
        "/pulse/dashboard",
        "/pulse/ai",
        "/pulse/intelligence",
        "/pulse/safety",
        "/pulse/verification",
        "/pulse/account",
        "/pulse/support",
        "/pulse/courses",
        "/pulse/events",
        "/dashboard/",
    ]
    counts = {pattern: 0 for pattern in patterns}
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob("*"):
            if path.suffix not in {".html", ".js", ".py"} or "uploads" in path.parts or "__pycache__" in path.parts:
                continue
            text = read(path)
            for pattern in patterns:
                counts[pattern] += text.count(pattern)
    return {key: value for key, value in counts.items() if value}


def surface_matrix(routes: set[str], blockers: list[Finding]) -> list[dict[str, object]]:
    screen_files = {path.name for path in SCREENS_DIR.glob("*.tsx")}
    blocker_text_by_file: dict[str, list[Finding]] = {}
    for finding in blockers:
        blocker_text_by_file.setdefault(Path(finding.file).name, []).append(finding)

    rows = []
    for surface, files, route_list in CRITICAL_SURFACES:
        existing_files = [name for name in files if name in screen_files]
        missing_files = [name for name in files if name not in screen_files]
        route_hits = [name for name in route_list if name in routes]
        missing_routes = [name for name in route_list if name not in routes]
        surface_blockers = [asdict(item) for file_name in files for item in blocker_text_by_file.get(file_name, [])]
        rows.append(
            {
                "surface": surface,
                "native_files_present": existing_files,
                "native_files_missing": missing_files,
                "native_routes_present": route_hits,
                "native_routes_missing": missing_routes,
                "native_shell_or_web_exit_findings": surface_blockers,
                "status": "blocked_by_web_exit" if surface_blockers else ("missing_route_or_file" if missing_files or missing_routes else "native_static_coverage"),
            }
        )
    return rows


def main() -> int:
    blockers = scan_blockers()
    routes = route_names()
    matrix = surface_matrix(routes, blockers)
    by_kind: dict[str, int] = {}
    for finding in blockers:
        by_kind[finding.kind] = by_kind.get(finding.kind, 0) + 1

    hard_blocker_kinds = {"mounted_WebView", "Linking.openURL", "safe_web_fallback", "fallback_status", "web_fallback_copy"}
    hard_blockers = [finding for finding in blockers if finding.kind in hard_blocker_kinds]
    native_static_complete = sum(1 for row in matrix if row["status"] == "native_static_coverage")
    blocked_surfaces = [row for row in matrix if row["status"] != "native_static_coverage"]

    payload = {
        "audit": "pulsesoc_native_webview_replacement",
        "native_route_count": len(routes),
        "critical_surface_count": len(matrix),
        "critical_surfaces_with_static_native_coverage": native_static_complete,
        "critical_surfaces_blocked_or_incomplete": len(blocked_surfaces),
        "blocker_counts_by_kind": by_kind,
        "hard_blocker_count": len(hard_blockers),
        "production_route_mentions": production_route_mentions(),
        "surface_matrix": matrix,
        "hard_blockers": [asdict(finding) for finding in hard_blockers],
        "release_readiness": "FAIL" if hard_blockers or blocked_surfaces else "PASS",
        "reason": "Native-only replacement requires zero WebView/openURL/safe-web-fallback paths and complete static coverage for critical surfaces.",
    }
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    print(f"PulseSoc native WebView replacement audit: {payload['release_readiness']}")
    print(f"Native routes discovered: {payload['native_route_count']}")
    print(f"Critical surfaces: {native_static_complete}/{len(matrix)} static native coverage")
    print(f"Hard web-exit blockers: {len(hard_blockers)}")
    print(f"Blocker counts: {json.dumps(by_kind, sort_keys=True)}")
    if hard_blockers:
        print("Top hard blockers:")
        for finding in hard_blockers[:25]:
            print(f"- {finding.file}:{finding.line} [{finding.kind}] {finding.text[:160]}")
    print(f"Report: {rel(REPORT_PATH)}")
    return 1 if payload["release_readiness"] == "FAIL" else 0


if __name__ == "__main__":
    raise SystemExit(main())
