#!/usr/bin/env python3
"""
Extract every Flask route from a bot.py snapshot into a machine-readable table.

Part of the PulseSoc web-parity mission (Phase 2, canonical route manifest).
Read-only: parses a snapshot, writes JSON/CSV to reports/. Never edits bot.py.

Usage:
    python3 extract_routes.py <path-to-bot.py> <out-dir>
"""
import ast
import csv
import json
import re
import sys
from collections import Counter

# Decorators that imply an authorization requirement.
AUTH_DECORATORS = {
    "login_required", "require_login", "admin_required", "require_admin",
    "requires_auth", "require_account", "premium_required", "business_required",
    "staff_required", "moderator_required", "engineer_required",
}

# How the handler produces its response -> render strategy.
RENDER_JINJA = "jinja_template"
RENDER_INLINE = "inline_html"
RENDER_JSON = "json_api"
RENDER_REDIRECT = "redirect"
RENDER_OTHER = "other"


def literal(node):
    """Best-effort constant extraction."""
    if isinstance(node, ast.Constant):
        return node.value
    return None


def decorator_name(node):
    """Flatten a decorator expression to a dotted name."""
    if isinstance(node, ast.Call):
        node = node.func
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


def is_route_decorator(node):
    return (
        isinstance(node, ast.Call)
        and decorator_name(node).endswith(".route")
    )


def extract_methods(call):
    for kw in call.keywords or []:
        if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
            vals = [literal(e) for e in kw.value.elts]
            return sorted(v for v in vals if v)
    return ["GET"]


def classify_render(func_node, source_lines):
    """Determine how this handler produces its response."""
    seen = set()
    for sub in ast.walk(func_node):
        if isinstance(sub, ast.Call):
            name = decorator_name(sub)
            base = name.split(".")[-1]
            if base == "render_template":
                tmpl = literal(sub.args[0]) if sub.args else None
                seen.add((RENDER_JINJA, tmpl))
            elif base in ("jsonify",):
                seen.add((RENDER_JSON, None))
            elif base == "redirect":
                seen.add((RENDER_REDIRECT, None))
            elif base.endswith("_html") or base.endswith("_page_html"):
                seen.add((RENDER_INLINE, base))
    # Priority: a page that renders wins over a redirect fallback.
    for kind in (RENDER_JINJA, RENDER_INLINE, RENDER_JSON, RENDER_REDIRECT):
        for k, detail in seen:
            if k == kind:
                return kind, detail
    return RENDER_OTHER, None


def product_area(path):
    """Bucket a route into a product area using the repo's own route families."""
    p = path.strip("/")
    if p.startswith("api/"):
        p = p[4:]
    seg = p.split("/")[0] if p else "root"
    mapping = {
        "": "Root", "root": "Root",
        "pulse": "Pulse (social)",
        "business-os": "Business OS",
        "arena": "Arena",
        "admin": "Operations / Admin",
        "dashboard": "Dashboard",
        "crypto": "Crypto",
        "messages": "Messenger", "chat": "Messenger",
        "account": "Account",
        "mobile": "Mobile bridge",
        "reels": "Reels",
        "alerts": "Alerts",
        "undx": "UNDX",
        "push": "Notifications",
        "payments": "Payments", "billing": "Payments", "checkout": "Payments",
        "marketplace": "Marketplace",
        "live": "Live",
        "education": "Education",
        "settings": "Settings",
    }
    return mapping.get(seg, seg.title() if seg else "Root")


def main(bot_path, out_dir):
    src = open(bot_path, encoding="utf-8", errors="replace").read()
    source_lines = src.splitlines()
    tree = ast.parse(src)

    rows = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        route_decos = [d for d in node.decorator_list if is_route_decorator(d)]
        if not route_decos:
            continue

        other_decos = {
            decorator_name(d).split(".")[-1]
            for d in node.decorator_list
            if not is_route_decorator(d)
        }
        auth = sorted(other_decos & AUTH_DECORATORS)
        render, detail = classify_render(node, source_lines)

        # A handler may carry several @route decorators -> aliases.
        paths = []
        for d in route_decos:
            if d.args:
                val = literal(d.args[0])
                if val:
                    paths.append((val, extract_methods(d), d.lineno))

        for idx, (path, methods, lineno) in enumerate(paths):
            rows.append({
                "path": path,
                "methods": ",".join(methods),
                "handler": node.name,
                "line": lineno,
                "product_area": product_area(path),
                "surface": ("api" if path.startswith("/api/")
                            else "admin" if path.startswith("/admin")
                            else "page"),
                "auth_decorators": ",".join(auth),
                "has_auth_decorator": bool(auth),
                "render": render,
                "render_detail": detail or "",
                "alias_of": paths[0][0] if idx > 0 else "",
                "alias_count": len(paths),
            })

    rows.sort(key=lambda r: r["path"])

    with open(f"{out_dir}/route_table.json", "w") as fh:
        json.dump(rows, fh, indent=2)

    with open(f"{out_dir}/route_table.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)

    # --- summary to stdout ---
    print(f"routes parsed: {len(rows)}")
    print(f"unique paths : {len({r['path'] for r in rows})}")
    print(f"handlers     : {len({r['handler'] for r in rows})}")
    print()
    print("by surface:")
    for k, v in Counter(r["surface"] for r in rows).most_common():
        print(f"  {k:8} {v}")
    print()
    print("by render strategy:")
    for k, v in Counter(r["render"] for r in rows).most_common():
        print(f"  {k:16} {v}")
    print()
    print("page routes by render strategy:")
    for k, v in Counter(r["render"] for r in rows if r["surface"] == "page").most_common():
        print(f"  {k:16} {v}")
    print()
    print("top product areas:")
    for k, v in Counter(r["product_area"] for r in rows).most_common(15):
        print(f"  {k:22} {v}")
    print()
    aliased = [r for r in rows if r["alias_of"]]
    print(f"alias routes (duplicate destinations): {len(aliased)}")
    print()
    pages = [r for r in rows if r["surface"] == "page"]
    noauth = [r for r in pages if not r["has_auth_decorator"]]
    print(f"page routes with NO auth decorator: {len(noauth)}/{len(pages)}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
