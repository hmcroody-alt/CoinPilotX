"""Production hardening heuristics for the admin system audit."""

from __future__ import annotations


def route_status_score(results=None) -> dict:
    results = results or []
    total = len(results)
    failures = [r for r in results if int(r.get("status_code") or 0) >= 500 or r.get("status") == "FAIL"]
    warnings = [r for r in results if r.get("status") == "WARN"]
    score = max(0, 100 - len(failures) * 18 - len(warnings) * 6)
    return {
        "score": score,
        "state": "critical" if failures else "watch" if warnings else "healthy",
        "total": total,
        "failures": len(failures),
        "warnings": len(warnings),
        "recommendations": [
            "Fix raw 500 route failures first.",
            "Replace generic errors with traceable JSON.",
            "Keep user-facing fallbacks mobile friendly.",
        ] if failures or warnings else ["Core audited routes are loading cleanly."],
    }


def detect_fragile_patterns(files=None) -> dict:
    files = files or {}
    issues = []
    for path, text in files.items():
        if "except Exception:" in text and "logging.exception" not in text:
            issues.append({"file": path, "issue": "Broad exception without exception logging"})
        if "Something needs attention" in text:
            issues.append({"file": path, "issue": "Generic user error copy found"})
        if "while True:" in text and "except Exception" in text:
            issues.append({"file": path, "issue": "Potential retry loop around broad exception"})
    return {"issues": issues, "issue_count": len(issues)}
