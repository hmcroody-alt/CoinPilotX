"""Mobile UX audit heuristics for production polish."""

from __future__ import annotations


def mobile_route_check(route="", html="") -> dict:
    html = html or ""
    warnings = []
    if "viewport" not in html:
        warnings.append("missing viewport")
    if "overflow-x:hidden" not in html and "overflow-x: hidden" not in html:
        warnings.append("no explicit horizontal overflow guard")
    if "mobile-bottom-nav" in html and "env(safe-area-inset-bottom)" not in html:
        warnings.append("bottom nav missing safe-area padding")
    if "<button" in html and "min-height" not in html:
        warnings.append("buttons may be too small for touch")
    return {
        "route": route,
        "mobile_score": max(0, 100 - len(warnings) * 12),
        "warnings": warnings,
        "status": "WARN" if warnings else "PASS",
    }
