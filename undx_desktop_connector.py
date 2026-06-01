"""UNDX Desktop Connector.

Run this service on the local Mac to give UNDX controlled access to explicitly
approved workspaces. The connector is intentionally narrow: it blocks protected
paths, refuses path traversal, writes only approved proposal files, and runs only
allowlisted validation/Git commands.
"""

from __future__ import annotations

import difflib
import html
import json
import os
import re
import shutil
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, make_response, request


VERSION = "1.1.0"
PROPOSAL_ENGINE_VERSION = "repository-aware-v2"
ACTIVE_PROPOSAL_HANDLER_NAME = "generate_proposal"
PORT = int(os.getenv("UNDX_DESKTOP_CONNECTOR_PORT", "8765"))
DEFAULT_WORKSPACE = Path("/Users/hmcherie/Desktop/CoinPilotX").expanduser().resolve()
CONNECTOR_ROOT = Path(__file__).resolve().parent
CONFIG_DIR = CONNECTOR_ROOT / ".undx"
WORKSPACE_CONFIG = CONFIG_DIR / "desktop_workspaces.json"
LOG_PATH = CONFIG_DIR / "desktop_connector_log.jsonl"
BACKUP_ROOT = CONFIG_DIR / "desktop_backups"
APPROVAL_WRITE = "APPROVE UNDX WRITE"
APPROVAL_GIT = "APPROVE UNDX GIT"
APPROVAL_PUSH = "APPROVE UNDX PUSH"

ALLOWED_ORIGINS = {
    "https://coinpilotx.app",
    "http://127.0.0.1:5050",
    "http://localhost:5050",
    "http://127.0.0.1:5059",
    "http://localhost:5059",
}

PROTECTED_PATTERNS = (
    ".env",
    ".env.",
    ".git/",
    "credentials",
    "credential",
    "secret",
    "token",
    "private_key",
    "private-key",
    "id_rsa",
    ".pem",
    ".key",
    ".crt",
    ".p12",
    ".sqlite",
    ".sqlite3",
    ".db",
    "database.dump",
    "payment",
    "stripe",
)

SKIP_DIRS = {".git", "venv", ".venv", "__pycache__", ".mypy_cache", ".pytest_cache", ".undx_backups", ".undx"}
TEXT_EXTENSIONS = {".py", ".html", ".css", ".js", ".jsx", ".ts", ".tsx", ".vue", ".json", ".md", ".txt", ".toml", ".yml", ".yaml", ".ini", ".cfg", ".sql"}
NORMAL_READ_LIMIT_BYTES = 250_000
CHUNKED_READ_LIMIT_BYTES = 2_000_000
MAX_FILE_READ_LINES = 500
SEARCH_CONTEXT_LINES = 3
LANGUAGE_EXTENSIONS = {
    ".py": "Python",
    ".js": "JavaScript",
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".jsx": "JavaScript",
    ".html": "HTML",
    ".css": "CSS",
    ".sql": "SQL",
    ".md": "Markdown",
    ".json": "JSON",
    ".vue": "Vue",
}

PLANNING_ONLY_PHRASES = (
    "proposal only",
    "plan only",
    "planning only",
    "architecture",
    "blueprint",
    "scan",
    "analyze",
    "analysis",
    "report",
    "do not write",
    "do not apply",
    "no files yet",
    "without writing",
    "replacement plan",
    "full replacement plan",
)

MISSION_CLASSIFIERS = {
    "bug-fix": ("bug", "fix", "failure", "error", "broken", "regression", "traceback", "exception"),
    "ui-change": ("ui", "ux", "frontend", "layout", "style", "css", "template", "modal", "mobile", "desktop"),
    "database-migration": ("database", "migration", "schema", "table", "column", "sqlite", "postgres"),
    "documentation-report": ("documentation", "docs", "report", "audit", "assessment", "blueprint"),
    "validation-audit": ("validate", "validation", "audit", "test", "checks", "qa"),
    "code-implementation": ("implement", "build", "create", "add", "update", "replace", "refactor"),
}

COMMUNICATION_KEYWORDS = (
    "message",
    "messages",
    "messenger",
    "chat",
    "room",
    "rooms",
    "group",
    "groups",
    "conversation",
    "conversations",
    "direct",
    "inbox",
    "communication",
    "communications",
    "pulse communications",
)

RISKY_FALLBACK_FILES = {"static/offline.html", "templates/offline.html", "offline.html"}

app = Flask(__name__)


class ConnectorError(ValueError):
    pass


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def response(payload: dict[str, Any], status: int = 200):
    resp = make_response(jsonify(payload), status)
    origin = request.headers.get("Origin", "")
    if origin in ALLOWED_ORIGINS or origin.startswith("http://127.0.0.1:") or origin.startswith("http://localhost:"):
        resp.headers["Access-Control-Allow-Origin"] = origin
        resp.headers["Vary"] = "Origin"
        resp.headers["Access-Control-Allow-Credentials"] = "true"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Private-Network"] = "true"
    resp.headers["Cache-Control"] = "no-store, max-age=0"
    return resp


@app.before_request
def handle_options():
    if request.method == "OPTIONS":
        return response({"ok": True})
    return None


def ensure_config() -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    if not WORKSPACE_CONFIG.exists():
        workspaces = []
        if DEFAULT_WORKSPACE.exists() and DEFAULT_WORKSPACE.is_dir():
            workspaces.append({"label": "CoinPilotX", "path": DEFAULT_WORKSPACE.as_posix(), "createdAt": now_iso()})
        WORKSPACE_CONFIG.write_text(json.dumps({"workspaces": workspaces}, indent=2), encoding="utf-8")


def load_workspaces() -> list[dict[str, str]]:
    ensure_config()
    try:
        data = json.loads(WORKSPACE_CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {"workspaces": []}
    return [item for item in data.get("workspaces", []) if item.get("path")]


def save_workspaces(workspaces: list[dict[str, str]]) -> None:
    ensure_config()
    WORKSPACE_CONFIG.write_text(json.dumps({"workspaces": workspaces}, indent=2), encoding="utf-8")


def log_action(action: str, payload: dict[str, Any]) -> None:
    ensure_config()
    record = {"timestamp": now_iso(), "action": action, **payload}
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True) + "\n")


def protected(path: Path) -> bool:
    normalized = path.as_posix().lower()
    return any(pattern in normalized for pattern in PROTECTED_PATTERNS)


def resolve_workspace(path_value: str | None) -> Path:
    if not path_value:
        raise ConnectorError("Workspace path is required.")
    workspace = Path(path_value).expanduser().resolve()
    if not workspace.exists() or not workspace.is_dir():
        raise ConnectorError("Repository path does not exist or is not a directory.")
    approved = {Path(item["path"]).expanduser().resolve() for item in load_workspaces()}
    if workspace not in approved:
        raise ConnectorError("Workspace is not registered with the UNDX Desktop Connector.")
    return workspace


def workspace_can_register(path: Path) -> bool:
    home = Path.home().resolve()
    allowed_roots = [home / "Desktop", home / "Documents", CONNECTOR_ROOT]
    return any(path == root or root in path.parents for root in allowed_roots)


def resolve_relative(workspace: Path, relative_path: str) -> Path:
    if not relative_path:
        raise ConnectorError("Relative file path is required.")
    target = (workspace / relative_path).resolve()
    try:
        target.relative_to(workspace)
    except ValueError as exc:
        raise ConnectorError("Path traversal blocked.") from exc
    if protected(target):
        raise ConnectorError("Protected file blocked.")
    return target


def read_text_file(path: Path, max_bytes: int = CHUNKED_READ_LIMIT_BYTES) -> str:
    if protected(path):
        raise ConnectorError("Protected file blocked.")
    if not path.exists() or not path.is_file():
        raise ConnectorError("Requested file does not exist.")
    if path.suffix.lower() not in TEXT_EXTENSIONS:
        raise ConnectorError("Unsupported file type for safe read.")
    if path.stat().st_size > max_bytes:
        raise ConnectorError("File exceeds safe read limit.")
    return path.read_text(encoding="utf-8", errors="replace")


def file_metadata(path: Path, workspace: Path) -> dict[str, Any]:
    if protected(path):
        raise ConnectorError("Protected file blocked.")
    if not path.exists() or not path.is_file():
        raise ConnectorError("Requested file does not exist.")
    if path.suffix.lower() not in TEXT_EXTENSIONS:
        raise ConnectorError("Unsupported file type for safe read.")
    size = path.stat().st_size
    return {
        "relativePath": path.relative_to(workspace).as_posix(),
        "fileSizeBytes": size,
        "normalReadLimitBytes": NORMAL_READ_LIMIT_BYTES,
        "chunkedReadLimitBytes": CHUNKED_READ_LIMIT_BYTES,
        "maxLinesPerChunk": MAX_FILE_READ_LINES,
        "isLarge": size > NORMAL_READ_LIMIT_BYTES,
        "chunkedReadable": size <= CHUNKED_READ_LIMIT_BYTES,
    }


def read_line_window(path: Path, start_line: int = 1, max_lines: int = MAX_FILE_READ_LINES) -> dict[str, Any]:
    start_line = max(1, int(start_line or 1))
    max_lines = max(1, min(MAX_FILE_READ_LINES, int(max_lines or MAX_FILE_READ_LINES)))
    lines: list[str] = []
    current_line = 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for current_line, line in enumerate(handle, start=1):
            if current_line < start_line:
                continue
            if len(lines) >= max_lines:
                break
            lines.append(line)
    end_line = start_line + len(lines) - 1 if lines else max(0, start_line - 1)
    has_more = current_line >= start_line + len(lines)
    return {
        "content": "".join(lines),
        "startLine": start_line,
        "endLine": end_line,
        "nextStartLine": end_line + 1 if has_more and lines else None,
        "hasMore": bool(has_more and lines),
        "lineCount": len(lines),
    }


def read_search_matches(path: Path, query: str, max_matches: int = 12) -> dict[str, Any]:
    query = (query or "").strip()
    if not query:
        raise ConnectorError("Search query is required.")
    lowered_query = query.lower()
    raw_lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    matches: list[dict[str, Any]] = []
    emitted_lines = 0
    for index, line in enumerate(raw_lines, start=1):
        if lowered_query not in line.lower():
            continue
        start = max(1, index - SEARCH_CONTEXT_LINES)
        end = min(len(raw_lines), index + SEARCH_CONTEXT_LINES)
        snippet_lines = raw_lines[start - 1:end]
        if emitted_lines + len(snippet_lines) > MAX_FILE_READ_LINES:
            break
        matches.append({
            "line": index,
            "startLine": start,
            "endLine": end,
            "content": "\n".join(snippet_lines),
        })
        emitted_lines += len(snippet_lines)
        if len(matches) >= max_matches:
            break
    return {"query": query, "matches": matches, "matchCount": len(matches), "lineCount": emitted_lines}


def safe_file_read_response(workspace: Path, target: Path, payload: dict[str, Any]) -> dict[str, Any]:
    meta = file_metadata(target, workspace)
    mode = str(payload.get("mode") or payload.get("readMode") or "auto").strip().lower()
    if not meta["chunkedReadable"]:
        return {
            "ok": True,
            **meta,
            "content": "",
            "requiresChunkedRead": False,
            "message": "File is larger than the 2 MB chunked read safety limit. Use repository search or narrow the target file.",
            "availableActions": [],
        }
    if mode in {"metadata", "summary"}:
        return {"ok": True, **meta, "content": "", "requiresChunkedRead": meta["isLarge"], "availableActions": ["first", "next", "range", "search"]}
    if mode == "auto" and not meta["isLarge"]:
        content = read_text_file(target, max_bytes=NORMAL_READ_LIMIT_BYTES)
        return {"ok": True, **meta, "content": content, "readMode": "full", "requiresChunkedRead": False}
    if mode == "auto":
        return {
            "ok": True,
            **meta,
            "content": "",
            "readMode": "summary",
            "requiresChunkedRead": True,
            "message": "Large file detected. Use first section, next chunk, line range, or search so the browser only receives a small slice.",
            "availableActions": ["first", "next", "range", "search"],
        }
    if mode in {"first", "chunk", "next"}:
        start_line = int(payload.get("startLine") or payload.get("line") or 1)
        window = read_line_window(target, start_line=start_line)
        return {"ok": True, **meta, **window, "readMode": "chunk", "requiresChunkedRead": meta["isLarge"]}
    if mode == "range":
        start_line = int(payload.get("startLine") or 1)
        end_line = int(payload.get("endLine") or start_line + MAX_FILE_READ_LINES - 1)
        max_lines = max(1, min(MAX_FILE_READ_LINES, end_line - start_line + 1))
        window = read_line_window(target, start_line=start_line, max_lines=max_lines)
        return {"ok": True, **meta, **window, "readMode": "range", "requiresChunkedRead": meta["isLarge"]}
    if mode == "search":
        matches = read_search_matches(target, str(payload.get("query") or payload.get("search") or ""))
        return {"ok": True, **meta, **matches, "content": "", "readMode": "search", "requiresChunkedRead": meta["isLarge"]}
    raise ConnectorError("Unsupported file read mode.")


def folder_tree(workspace: Path, max_entries: int = 500) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for path in sorted(workspace.rglob("*")):
        rel = path.relative_to(workspace).as_posix()
        if any(part in SKIP_DIRS for part in Path(rel).parts):
            continue
        entries.append({"path": rel, "type": "folder" if path.is_dir() else "file", "protected": protected(path)})
        if len(entries) >= max_entries:
            break
    return entries


def scan_workspace(workspace: Path) -> dict[str, Any]:
    files: list[Path] = []
    languages: dict[str, int] = {}
    routes: list[dict[str, str]] = []
    html_files: list[str] = []
    css_files: list[str] = []
    js_files: list[str] = []
    python_files: list[str] = []
    templates: list[str] = []
    static_assets: list[str] = []
    react_files: list[str] = []
    vue_files: list[str] = []
    flask_files: list[str] = []
    audit_scripts: list[str] = []
    protected_files: list[str] = []
    frameworks: set[str] = set()
    warnings: list[str] = []
    for path in sorted(workspace.rglob("*")):
        rel = path.relative_to(workspace).as_posix()
        if any(part in SKIP_DIRS for part in Path(rel).parts):
            continue
        if path.is_dir():
            continue
        files.append(path)
        if protected(path):
            protected_files.append(rel)
            continue
        language = LANGUAGE_EXTENSIONS.get(path.suffix.lower())
        if language:
            languages[language] = languages.get(language, 0) + 1
        if path.suffix.lower() == ".html":
            html_files.append(rel)
        if path.suffix.lower() == ".css":
            css_files.append(rel)
        if path.suffix.lower() in {".js", ".jsx", ".ts", ".tsx"}:
            js_files.append(rel)
        if path.suffix.lower() == ".py":
            python_files.append(rel)
        if path.suffix.lower() in {".jsx", ".tsx"} or path.name.lower() in {"package.json", "vite.config.js", "next.config.js"}:
            react_files.append(rel)
        if path.suffix.lower() == ".vue":
            vue_files.append(rel)
        if rel.startswith("templates/") and path.suffix.lower() == ".html":
            templates.append(rel)
        if rel.startswith("static/") and path.suffix.lower() in {".js", ".css"}:
            static_assets.append(rel)
        if rel.startswith("scripts/") and path.suffix.lower() == ".py" and "audit" in path.name:
            audit_scripts.append(rel)
        if path.suffix.lower() == ".py":
            try:
                text = read_text_file(path, max_bytes=4_000_000)
            except ConnectorError as exc:
                warnings.append(f"Skipped {rel}: {exc}")
                continue
            for match in re.finditer(r"@webhook_app\.route\((['\"])(.*?)\1", text):
                routes.append({"route": match.group(2), "file": rel})
            if "Flask(" in text or "@webhook_app.route" in text or "@app.route" in text:
                frameworks.add("Flask")
                flask_files.append(rel)
        elif path.name.lower() == "package.json":
            try:
                text = read_text_file(path)
                if "react" in text.lower():
                    frameworks.add("React")
                if "vue" in text.lower():
                    frameworks.add("Vue")
                if "next" in text.lower():
                    frameworks.add("Next.js")
            except ConnectorError:
                pass
    return {
        "ok": True,
        "workspacePath": workspace.as_posix(),
        "workspaceName": workspace.name,
        "fileCount": len(files),
        "folderTree": folder_tree(workspace),
        "repositoryMap": {
            "htmlFiles": html_files[:240],
            "cssFiles": css_files[:240],
            "jsFiles": js_files[:240],
            "pythonFiles": python_files[:240],
            "templateFiles": templates[:240],
            "reactFiles": react_files[:120],
            "vueFiles": vue_files[:120],
            "flaskFiles": flask_files[:120],
            "frameworks": sorted(frameworks),
        },
        "languages": languages,
        "frameworks": sorted(frameworks),
        "htmlFiles": html_files[:240],
        "cssFiles": css_files[:240],
        "jsFiles": js_files[:240],
        "pythonFiles": python_files[:240],
        "reactFiles": react_files[:120],
        "vueFiles": vue_files[:120],
        "flaskFiles": flask_files[:120],
        "flaskRoutes": routes[:240],
        "templates": templates[:240],
        "staticAssets": static_assets[:240],
        "auditScripts": audit_scripts[:120],
        "protectedFiles": protected_files[:120],
        "warnings": warnings[:80],
        "lastScan": now_iso(),
        "readOnly": True,
    }


def diff_text(path: str, before: str, after: str) -> str:
    return "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile=f"a/{path}", tofile=f"b/{path}", lineterm="\n"))


def task_title(task: str, fallback: str = "UNDX Generated Page") -> str:
    text = re.sub(r"\s+", " ", task or "").strip()
    text = re.sub(r"^(build|create|generate|make|recreate|replace|add)\s+(a|an|the)?\s*", "", text, flags=re.I)
    text = re.sub(r"\b(landing page|website|page|site|index\.html|html)\b", "", text, flags=re.I).strip(" .:-")
    if not text:
        return fallback
    return " ".join(word.capitalize() if len(word) > 2 else word.upper() for word in text.split()[:8])


def escape_page_text(value: str) -> str:
    return html.escape(re.sub(r"\s+", " ", value or "").strip(), quote=True)


def css_palette(task: str) -> tuple[str, str, str]:
    normalized = (task or "").lower()
    if any(word in normalized for word in ("luxury", "premium", "gold")):
        return "#f8d47a", "#7cf7d4", "#090a12"
    if any(word in normalized for word in ("nature", "green", "eco")):
        return "#79f2a6", "#b7f7ff", "#07150e"
    if any(word in normalized for word in ("cyber", "future", "ai", "undx")):
        return "#6edff6", "#36e58f", "#030711"
    return "#6edff6", "#ffd166", "#07111f"


def generated_landing_html(task: str, workspace_name: str) -> str:
    title = escape_page_text(task_title(task, workspace_name or "UNDX Landing Page"))
    primary, secondary, bg = css_palette(task)
    description = escape_page_text(task or "A polished generated landing page.")
    cards = [
        ("Command Visibility", "Monitor launches, product signals, and build readiness from one focused surface."),
        ("Repository Intelligence", "Turn approved codebase context into clear planning, QA, and release direction."),
        ("Approval Gates", "Keep every future write, command, and deployment behind explicit human approval."),
    ]
    card_html = "\n".join(f"      <article><span>{name}</span><p>{body}</p></article>" for name, body in cards)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    :root {{ color-scheme: dark; --primary:{primary}; --secondary:{secondary}; --bg:{bg}; }}
    * {{ box-sizing: border-box; }}
    body {{ margin:0; font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: radial-gradient(circle at top left, color-mix(in srgb, var(--primary) 22%, transparent), transparent 32rem), linear-gradient(145deg, var(--bg), #101827); color:#f7fbff; }}
    main {{ width:min(1120px, calc(100% - 32px)); margin:auto; padding:72px 0; }}
    nav {{ display:flex; justify-content:space-between; align-items:center; gap:16px; margin-bottom:64px; }}
    nav .links {{ display:flex; gap:18px; flex-wrap:wrap; }}
    nav .links a {{ color:#dceaf3; text-decoration:none; font-weight:700; }}
    .brand {{ font-weight:900; letter-spacing:.08em; text-transform:uppercase; color:var(--primary); }}
    .button {{ display:inline-flex; min-height:44px; align-items:center; justify-content:center; border:1px solid color-mix(in srgb, var(--primary) 42%, transparent); border-radius:12px; padding:12px 16px; color:#061018; background:linear-gradient(135deg,var(--primary),var(--secondary)); font-weight:900; text-decoration:none; }}
    .hero {{ display:grid; grid-template-columns:minmax(0,1.1fr) minmax(280px,.9fr); gap:28px; align-items:center; }}
    h1 {{ font-size:clamp(42px,8vw,86px); line-height:.95; margin:0 0 20px; }}
    p {{ color:#bfd0dd; font-size:1.05rem; line-height:1.7; }}
    .panel {{ border:1px solid rgba(255,255,255,.14); border-radius:22px; background:rgba(255,255,255,.07); padding:24px; box-shadow:0 28px 90px rgba(0,0,0,.32); }}
    .grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:16px; margin-top:34px; }}
    .pricing {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:16px; margin-top:24px; }}
    article {{ border:1px solid rgba(255,255,255,.12); border-radius:18px; padding:18px; background:rgba(255,255,255,.055); }}
    article span {{ color:var(--secondary); font-weight:900; }}
    footer {{ margin-top:42px; color:#8ea4b5; }}
    @media (max-width: 760px) {{ main {{ padding:34px 0; }} .hero,.grid,.pricing {{ grid-template-columns:1fr; }} nav {{ align-items:flex-start; flex-direction:column; margin-bottom:38px; }} }}
  </style>
</head>
<body>
  <main>
    <nav>
      <div class="brand">{title}</div>
      <div class="links"><a href="#features">Features</a><a href="#pricing">Pricing</a><a href="#footer">Contact</a></div>
    </nav>
    <section class="hero" id="start">
      <div>
        <h1>{title}</h1>
        <p>{description}</p>
        <a class="button" href="#features">Launch UNDX</a>
      </div>
      <aside class="panel">
        <strong>Built by UNDX Execution Kernel</strong>
        <p>This page was generated as an approval-gated repository-aware proposal.</p>
      </aside>
    </section>
    <section class="grid" id="features">
{card_html}
    </section>
    <section id="pricing" class="pricing">
      <article><span>Starter</span><p>Launch a focused test page with responsive layout and clear calls to action.</p></article>
      <article><span>Pro</span><p>Expand with repository-aware planning, mission context, and approval-ready diffs.</p></article>
      <article><span>Command</span><p>Prepare future build operations with safety gates, validation, and traceable proposals.</p></article>
    </section>
    <footer id="footer">Generated by UNDX</footer>
  </main>
</body>
</html>
"""


def generated_support_file(path: str, task: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix == ".css":
        primary, secondary, bg = css_palette(task)
        return f""":root {{ --undx-primary: {primary}; --undx-secondary: {secondary}; --undx-bg: {bg}; }}
body {{ background: var(--undx-bg); color: #f7fbff; }}
.undx-generated {{ border: 1px solid rgba(255,255,255,.16); border-radius: 16px; padding: 1rem; }}
"""
    if suffix == ".js":
        return "document.documentElement.dataset.undxGenerated = 'true';\nconsole.log('UNDX generated asset loaded');\n"
    if suffix == ".py":
        return '"""UNDX generated planning file."""\n\n\ndef describe():\n    return "Generated by UNDX Execution Kernel"\n'
    if suffix in {".md", ".txt"}:
        title = task_title(task, Path(path).stem or "UNDX Generated File")
        return f"# {title}\n\nGenerated by UNDX Execution Kernel.\n\nMission:\n{task.strip()}\n"
    return f"Generated by UNDX Execution Kernel\n\nMission:\n{task}\n"


def extract_requested_paths(task: str) -> list[str]:
    paths: list[str] = []
    for match in re.finditer(r"([\w./-]+\.(?:html|css|js|jsx|ts|tsx|vue|py|md|txt|json))", task or "", flags=re.I):
        candidate = match.group(1).strip("./")
        if candidate and candidate not in paths:
            paths.append(candidate)
    return paths[:8]


def classify_mission(task: str) -> dict[str, Any]:
    normalized = re.sub(r"\s+", " ", task or "").strip().lower()
    planning = any(phrase in normalized for phrase in PLANNING_ONLY_PHRASES)
    matched_types = [
        mission_type
        for mission_type, keywords in MISSION_CLASSIFIERS.items()
        if any(keyword in normalized for keyword in keywords)
    ]
    if planning:
        mission_type = "planning-only"
        proposal_type = "planning-only"
    elif matched_types:
        mission_type = matched_types[0]
        proposal_type = "implementation" if mission_type in {"bug-fix", "ui-change", "database-migration", "code-implementation"} else "report"
    else:
        mission_type = "code-implementation"
        proposal_type = "implementation"
    return {
        "missionType": mission_type,
        "missionCategory": "architecture-plan" if mission_type == "planning-only" else mission_type,
        "proposalType": proposal_type,
        "planningOnly": proposal_type == "planning-only",
        "matchedTypes": matched_types,
    }


def mission_keywords(task: str) -> list[str]:
    normalized = (task or "").lower()
    keywords = set(re.findall(r"[a-z][a-z0-9_-]{2,}", normalized))
    if "pulse communications" in normalized or any(word in keywords for word in ("messenger", "messages", "conversation", "conversations", "chat", "rooms", "groups", "communications")):
        keywords.update(COMMUNICATION_KEYWORDS)
    return sorted(keywords)


def candidate_scan_files(scan: dict[str, Any]) -> list[str]:
    ordered: list[str] = []
    for collection in ("pythonFiles", "templates", "jsFiles", "cssFiles", "auditScripts", "staticAssets", "htmlFiles", "reactFiles", "vueFiles"):
        for value in scan.get(collection) or scan.get("repositoryMap", {}).get(collection, []) or []:
            if value not in ordered and value not in RISKY_FALLBACK_FILES:
                ordered.append(value)
    return ordered


def score_file_for_mission(workspace: Path, rel: str, keywords: list[str]) -> tuple[int, list[str]]:
    lowered = rel.lower()
    score = 0
    reasons: list[str] = []
    for keyword in keywords:
        if keyword and keyword in lowered:
            score += 10
            reasons.append(f"path contains {keyword}")
    if rel == "bot.py":
        score += 4
        reasons.append("main Flask routes and data helpers live in bot.py")
    if rel.startswith("scripts/") and "audit" in lowered:
        score += 3
        reasons.append("audit/validation script")
    path = workspace / rel
    if path.exists() and path.is_file() and path.suffix.lower() in TEXT_EXTENSIONS and not protected(path):
        try:
            text = read_text_file(path, max_bytes=800_000).lower()
        except ConnectorError:
            text = ""
        content_hits = [keyword for keyword in keywords if keyword and keyword in text]
        if content_hits:
            score += min(30, len(content_hits) * 3)
            reasons.append("content references " + ", ".join(content_hits[:5]))
    return score, reasons[:6]


def ranked_target_files(workspace: Path, task: str, scan: dict[str, Any], limit: int = 12) -> list[dict[str, Any]]:
    keywords = mission_keywords(task)
    ranked: list[dict[str, Any]] = []
    for rel in candidate_scan_files(scan):
        score, reasons = score_file_for_mission(workspace, rel, keywords)
        if score <= 0:
            continue
        ranked.append({"path": rel, "score": score, "why": reasons or ["matched mission keywords"]})
    ranked.sort(key=lambda item: (-int(item["score"]), str(item["path"])))
    return ranked[:limit]


def choose_target_files(workspace: Path, task: str, scan: dict[str, Any], classification: dict[str, Any] | None = None) -> list[str]:
    requested = extract_requested_paths(task)
    if requested:
        return requested
    classification = classification or classify_mission(task)
    normalized = (task or "").lower()
    if "create file" in normalized or "new file" in normalized:
        requested = extract_requested_paths(task)
        if requested:
            return requested
    if "index" in normalized or "landing" in normalized or "website" in normalized or "recreate" in normalized or "home page" in normalized:
        if (workspace / "index.html").exists():
            return ["index.html"]
        return ["index.html"]
    if "pulse labs" in normalized and (workspace / "templates" / "pulse_labs.html").exists():
        return ["templates/pulse_labs.html"]
    ranked = ranked_target_files(workspace, task, scan, limit=8)
    if ranked:
        return [item["path"] for item in ranked[:4]]
    if classification.get("planningOnly"):
        return []
    return []


def communication_report_sections(targets: list[dict[str, Any]], scan: dict[str, Any]) -> dict[str, list[str]]:
    target_paths = [item["path"] for item in targets]
    preserve = [
        "Legacy direct message routes and APIs until v2 is proven",
        "Existing room/group routes, permissions, and database data",
        "Pulse feed, UNDX, Wallet Guardian, admin, auth, and premium routes",
    ]
    replace = [
        "Only legacy communications UI/API surfaces after v2 passes audits",
        "Duplicate frontend message loaders once bridge routes are validated",
    ]
    create = [
        "pulse_communications_v2/__init__.py",
        "pulse_communications_v2/models.py",
        "pulse_communications_v2/service.py",
        "pulse_communications_v2/routes.py",
        "pulse_communications_v2/permissions.py",
        "reports/pulse_communications_2_full_replacement_plan.md",
    ]
    if not target_paths:
        target_paths = ["bot.py", "scripts/messenger_core_audit.py", "scripts/chat_system_audit.py"]
    return {
        "targetFiles": target_paths,
        "filesToPreserve": preserve,
        "filesToReplace": replace,
        "newFilesToCreate": create,
        "auditCandidates": [path for path in (scan.get("auditScripts") or []) if any(keyword in path.lower() for keyword in COMMUNICATION_KEYWORDS)][:12],
    }


def build_planning_report(task: str, workspace: Path, scan: dict[str, Any], targets: list[dict[str, Any]], classification: dict[str, Any]) -> str:
    sections = communication_report_sections(targets, scan)
    repository_map = [
        f"Repository: {workspace.name}",
        f"Files scanned: {scan.get('fileCount', 0)}",
        "Frameworks: " + (", ".join(scan.get("frameworks") or []) or "Unknown"),
        "Relevant targets: " + (", ".join(sections["targetFiles"]) or "None found"),
    ]
    target_lines = [
        f"- {item['path']}: {'; '.join(item.get('why') or ['selected by repository relevance'])}"
        for item in targets
    ] or ["- No safe target files selected for a diff. Report-only mode is active."]
    report_sections = [
        ("Mission Classification", [f"Mission Type: {classification.get('missionType')}", f"Proposal Type: {classification.get('proposalType')}", "Diff Generation: disabled for planning-only mission"]),
        ("Repository Communications Map", repository_map),
        ("Problems Found", ["Legacy direct messages, rooms, groups, chat APIs, and UI loaders need a single replacement map before edits.", "Large rebuild directives are not safe as single-file diffs.", "Fallback HTML rewrites are blocked for planning missions."]),
        ("Exact Files Involved", target_lines),
        ("Target Files", [f"- {path}" for path in sections["targetFiles"]]),
        ("Files To Preserve", [f"- {path}" for path in sections["filesToPreserve"]]),
        ("Files To Replace", [f"- {path}" for path in sections["filesToReplace"]]),
        ("New V2 Files To Create", [f"- {path}" for path in sections["newFilesToCreate"]]),
        ("Database Migration Strategy", ["Add v2-prefixed tables only.", "Backfill through a bridge job after legacy read paths are verified.", "Keep destructive changes behind a separate approval gate."]),
        ("First Safe Implementation Patch", ["Create or update a markdown replacement plan and a disabled v2 route scaffold only.", "Do not route production UI to v2 until audits pass.", "Do not write files from this planning-only proposal."]),
        ("Validation Plan", ["Python compile", "JavaScript parse", "UNDX desktop connector audit", "UNDX homepage audit", "messenger/chat audits", "Pulse route/feed audits", "git diff --check"]),
        ("Rollback Plan", ["Leave legacy routes active.", "Keep v2 behind a false feature flag.", "Revert only v2 scaffold/report files if validation fails."]),
        ("Approval Gate", ["Human approval required before any implementation diff.", "Repository write remains disabled for this proposal."]),
    ]
    lines = [f"# UNDX Planning Report: {task.strip()[:120]}", ""]
    for title, values in report_sections:
        lines.extend([f"## {title}", *values, ""])
    return "\n".join(lines).strip() + "\n"


def generate_proposal(workspace: Path, task: str) -> dict[str, Any]:
    if not (task or "").strip():
        raise ConnectorError("Mission directive is required for proposal generation.")
    scan = scan_workspace(workspace)
    classification = classify_mission(task)
    ranked_targets = ranked_target_files(workspace, task, scan, limit=12)
    if classification.get("planningOnly"):
        report = build_planning_report(task, workspace, scan, ranked_targets, classification)
        target_files = [item["path"] for item in ranked_targets]
        return {
            "ok": True,
            "proposalId": f"DESKPLAN-UNDX-{int(datetime.now().timestamp())}",
            "proposalType": "planning-only",
            "missionType": classification.get("missionType"),
            "missionCategory": classification.get("missionCategory"),
            "planningOnly": True,
            "targetFile": "",
            "targetFiles": target_files,
            "targetFileReasons": [{"path": item["path"], "why": item.get("why", [])} for item in ranked_targets],
            "report": report,
            "diff": "",
            "changes": [],
            "requiresApproval": False,
            "message": "Planning report generated. No files written.",
            "summary": "Planning-only architecture report generated from repository-aware scan.",
            "proposalEngine": "Repository-Aware",
            "proposalEngineVersion": PROPOSAL_ENGINE_VERSION,
            "activeProposalHandler": ACTIVE_PROPOSAL_HANDLER_NAME,
            "repositoryAware": True,
            "repositoryMap": scan.get("repositoryMap", {}),
            "diffGenerationSafe": False,
            "diffWarning": "Diff generation disabled because this mission was classified as planning-only.",
            "riskNotes": ["No files selected for write.", "No fallback HTML target allowed.", "Implementation requires a separate approved mission."],
            "validationPlan": ["Review planning report", "Confirm target files", "Request a separate implementation mission for any patch"],
        }
    target_files = choose_target_files(workspace, task, scan, classification)
    if not target_files:
        report = build_planning_report(task, workspace, scan, ranked_targets, {**classification, "proposalType": "report"})
        return {
            "ok": True,
            "proposalId": f"DESKREPORT-UNDX-{int(datetime.now().timestamp())}",
            "proposalType": "report",
            "missionType": classification.get("missionType"),
            "missionCategory": classification.get("missionCategory"),
            "planningOnly": False,
            "targetFile": "",
            "targetFiles": [],
            "targetFileReasons": [],
            "report": report,
            "diff": "",
            "changes": [],
            "requiresApproval": False,
            "message": "Repository-aware report generated. No relevant implementation target was safe enough for a diff.",
            "summary": "No safe relevant target found for implementation diff.",
            "proposalEngine": "Repository-Aware",
            "proposalEngineVersion": PROPOSAL_ENGINE_VERSION,
            "activeProposalHandler": ACTIVE_PROPOSAL_HANDLER_NAME,
            "repositoryAware": True,
            "repositoryMap": scan.get("repositoryMap", {}),
            "diffGenerationSafe": False,
            "diffWarning": "No relevant target files matched the mission. Fallback HTML rewrites are blocked.",
        }
    changes: list[dict[str, Any]] = []
    for target_rel in target_files:
        target = resolve_relative(workspace, target_rel)
        exists = target.exists()
        before = read_text_file(target) if exists else ""
        suffix = target.suffix.lower()
        normalized = (task or "").lower()
        if suffix == ".html" and (target.name == "index.html" or any(word in normalized for word in ("landing", "website", "recreate", "complete replacement", "full page"))):
            after = generated_landing_html(task, workspace.name)
        elif not exists:
            if suffix == ".html":
                after = generated_landing_html(task, workspace.name)
            else:
                after = generated_support_file(target_rel, task)
        elif suffix == ".html":
            marker = "Built by UNDX Execution Kernel"
            if marker in before and "pulse_labs.html" in target_rel:
                after = before
            elif "pulse_labs.html" in target_rel and "add text" in normalized:
                insertion = f"  <p class=\"muted\">{marker}</p>\n"
                anchor = "</section>\n"
                idx = before.rfind(anchor)
                after = before.rstrip() + "\n" + insertion if idx == -1 else before[:idx] + insertion + before[idx:]
            else:
                after = generated_landing_html(task, workspace.name)
        else:
            addition = f"\n\n/* UNDX proposal: {task.strip()[:180]} */\n" if suffix == ".css" else f"\n\n// UNDX proposal: {task.strip()[:180]}\n" if suffix in {".js", ".jsx", ".ts", ".tsx"} else f"\n\n# UNDX proposal: {task.strip()[:180]}\n"
            after = before if addition.strip() in before else before.rstrip() + addition
        changes.append({"path": target_rel, "before": before, "after": after, "diff": diff_text(target_rel, before, after), "type": "modify" if exists else "create"})
    target_rel = changes[0]["path"]
    combined_diff = "\n\n".join(change.get("diff") or "" for change in changes)
    target_files = [change.get("path") for change in changes if change.get("path")]
    return {
        "ok": True,
        "proposalId": f"DESKPROP-UNDX-{int(datetime.now().timestamp())}",
        "proposalType": "implementation",
        "missionType": classification.get("missionType"),
        "missionCategory": classification.get("missionCategory"),
        "planningOnly": False,
        "targetFile": target_rel,
        "targetFiles": target_files,
        "targetFileReasons": [{"path": item["path"], "why": item.get("why", [])} for item in ranked_targets if item["path"] in target_files],
        "diff": combined_diff,
        "summary": f"Repository-aware proposal for {', '.join(target_files)}.",
        "requiresApproval": True,
        "message": "Implementation proposal generated. Review diff before approval.",
        "proposalEngine": "Repository-Aware",
        "proposalEngineVersion": PROPOSAL_ENGINE_VERSION,
        "activeProposalHandler": ACTIVE_PROPOSAL_HANDLER_NAME,
        "repositoryAware": True,
        "repositoryMap": scan.get("repositoryMap", {}),
        "diffGenerationSafe": True,
        "beforeSnippet": changes[0]["before"][-600:],
        "afterSnippet": changes[0]["after"][-700:],
        "changes": changes,
        "riskNotes": ["Repository-aware proposal generated from approved workspace scan.", "No protected files touched.", "No files are written until approval phrase is supplied.", "Backup required before write."],
        "validationPlan": ["File existence validation", "HTML validation", "CSS validation", "JavaScript syntax validation"],
    }


def apply_patch_payload(workspace: Path, proposal: dict[str, Any], approval: str) -> dict[str, Any]:
    if approval != APPROVAL_WRITE:
        raise ConnectorError("Approval phrase required before write.")
    changed: list[str] = []
    backups: list[str] = []
    backup_dir = BACKUP_ROOT / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_dir.mkdir(parents=True, exist_ok=True)
    for change in proposal.get("changes", []):
        rel = str(change.get("path") or "")
        target = resolve_relative(workspace, rel)
        if protected(target):
            raise ConnectorError("Protected file blocked.")
        change_type = str(change.get("type") or "modify")
        exists = target.exists()
        current = read_text_file(target) if exists else ""
        before = str(change.get("before") or "")
        after = str(change.get("after") or "")
        if not exists and change_type != "create":
            raise ConnectorError(f"Target file does not exist for modify proposal: {rel}")
        if exists and current != before and after not in current:
            raise ConnectorError(f"Current file does not match proposal before-state: {rel}")
        backup_path = backup_dir / rel
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        if exists:
            shutil.copy2(target, backup_path)
            backups.append(backup_path.as_posix())
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(after, encoding="utf-8")
        changed.append(rel)
    log_action("patch_apply", {"workspace": workspace.as_posix(), "changed": changed, "backups": backups})
    return {"ok": True, "changedFiles": changed, "backupPath": backup_dir.as_posix(), "backups": backups}


def run_safe_command(workspace: Path, args: list[str], timeout: int = 90) -> dict[str, Any]:
    result = subprocess.run(args, cwd=workspace, text=True, capture_output=True, timeout=timeout, check=False)
    return {"command": args, "returncode": result.returncode, "stdout": result.stdout[-8000:], "stderr": result.stderr[-8000:], "ok": result.returncode == 0}


def text_files_by_suffix(workspace: Path, suffixes: set[str], max_files: int = 120) -> list[Path]:
    found: list[Path] = []
    for path in sorted(workspace.rglob("*")):
        rel = path.relative_to(workspace).as_posix()
        if len(found) >= max_files:
            break
        if any(part in SKIP_DIRS for part in Path(rel).parts) or path.is_dir() or protected(path):
            continue
        if path.suffix.lower() in suffixes:
            found.append(path)
    return found


def validate_html_files(workspace: Path) -> dict[str, Any]:
    results = []
    for path in text_files_by_suffix(workspace, {".html"}):
        text = read_text_file(path)
        rel = path.relative_to(workspace).as_posix()
        lowered = text.lower()
        is_template = rel.startswith("templates/")
        ok = ("<html" in lowered and "</html>" in lowered and "<body" in lowered and "</body>" in lowered) or (is_template and "<" in text and ">" in text)
        results.append({"file": rel, "ok": ok, "message": "HTML document or template structure present." if ok else "Missing core HTML document tags."})
    return {"ok": all(item["ok"] for item in results), "validationType": "html_validate", "results": results or [{"ok": True, "message": "No HTML files found."}]}


def validate_css_files(workspace: Path) -> dict[str, Any]:
    results = []
    for path in text_files_by_suffix(workspace, {".css"}):
        text = read_text_file(path)
        ok = text.count("{") == text.count("}")
        results.append({"file": path.relative_to(workspace).as_posix(), "ok": ok, "message": "CSS braces balanced." if ok else "CSS braces are not balanced."})
    return {"ok": all(item["ok"] for item in results), "validationType": "css_validate", "results": results or [{"ok": True, "message": "No CSS files found."}]}


def validate_js_files(workspace: Path) -> dict[str, Any]:
    files = text_files_by_suffix(workspace, {".js"}, max_files=80)
    results = []
    node = shutil.which("node")
    for path in files:
        if node:
            results.append(run_safe_command(workspace, [node, "--check", path.relative_to(workspace).as_posix()], timeout=30))
        else:
            text = read_text_file(path)
            ok = text.count("(") == text.count(")") and text.count("{") == text.count("}")
            results.append({"file": path.relative_to(workspace).as_posix(), "ok": ok, "stdout": "Node unavailable; basic bracket check used.", "stderr": "" if ok else "JavaScript brackets are not balanced.", "returncode": 0 if ok else 1})
    return {"ok": all(item["ok"] for item in results), "validationType": "javascript_parse", "results": results or [{"ok": True, "message": "No JavaScript files found."}]}


def validate_file_existence(workspace: Path) -> dict[str, Any]:
    scan = scan_workspace(workspace)
    files = [item for item in scan.get("folderTree", []) if item.get("type") == "file"]
    return {"ok": True, "validationType": "file_existence", "results": [{"ok": True, "fileCount": len(files), "message": "Repository files are visible through approved workspace scan."}]}


def run_validation(workspace: Path, validation_type: str) -> dict[str, Any]:
    checks = {
        "python_compile": [["python3", "-m", "py_compile", "bot.py", "undx_desktop_connector.py", "undx_execution_kernel.py"]],
        "javascript_parse": [],
        "undx_audit": [["python3", "scripts/undx_homepage_audit.py"]],
        "site_functional_audit": [["python3", "scripts/site_functional_audit.py"]],
        "performance_audit": [["python3", "scripts/performance_audit.py"]],
        "pulse_feed_layout_audit": [["python3", "scripts/pulse_feed_layout_audit.py"]],
    }
    requested = validation_type or "python_compile"
    if requested == "html_validate":
        return validate_html_files(workspace)
    if requested == "css_validate":
        return validate_css_files(workspace)
    if requested in {"javascript_parse", "js_syntax"}:
        return validate_js_files(workspace)
    if requested == "file_existence":
        return validate_file_existence(workspace)
    if requested == "safe_all":
        lightweight = [validate_file_existence(workspace), validate_html_files(workspace), validate_css_files(workspace), validate_js_files(workspace)]
        commands = [
            cmd
            for key in ("python_compile", "undx_audit", "site_functional_audit", "performance_audit", "pulse_feed_layout_audit")
            for cmd in checks[key]
            if all((workspace / part).exists() for part in cmd[1:] if part.endswith(".py"))
        ]
        command_results = [run_safe_command(workspace, command) for command in commands]
        all_results = lightweight + [{"ok": all(item["ok"] for item in command_results), "validationType": "allowlisted_commands", "results": command_results}]
        return {"ok": all(item["ok"] for item in all_results), "validationType": requested, "results": all_results}
    elif requested in checks:
        commands = checks[requested]
    else:
        raise ConnectorError("Validation type is not allowlisted.")
    results = [run_safe_command(workspace, command) for command in commands]
    return {"ok": all(item["ok"] for item in results), "validationType": requested, "results": results}


@app.route("/health", methods=["GET", "OPTIONS"])
def health():
    workspaces = load_workspaces()
    return response({
        "ok": True,
        "status": "online",
        "connector": "UNDX Desktop Connector",
        "machineName": socket.gethostname(),
        "allowedWorkspacesCount": len(workspaces),
        "version": VERSION,
        "proposalEngine": "Repository-Aware",
        "proposalEngineVersion": PROPOSAL_ENGINE_VERSION,
        "activeProposalHandler": ACTIVE_PROPOSAL_HANDLER_NAME,
    })


@app.route("/workspace/register", methods=["POST", "OPTIONS"])
def workspace_register():
    payload = request.get_json(silent=True) or {}
    path = Path(str(payload.get("workspacePath") or payload.get("path") or "")).expanduser().resolve()
    label = str(payload.get("workspaceLabel") or payload.get("label") or path.name)
    if not path.exists() or not path.is_dir():
        raise ConnectorError("Workspace path does not exist or is not a directory.")
    if not workspace_can_register(path):
        raise ConnectorError("Workspace must be inside a user-approved local location.")
    workspaces = load_workspaces()
    if not any(Path(item["path"]).expanduser().resolve() == path for item in workspaces):
        workspaces.append({"label": label, "path": path.as_posix(), "createdAt": now_iso()})
        save_workspaces(workspaces)
    log_action("workspace_register", {"workspace": path.as_posix(), "label": label})
    return response({"ok": True, "workspace": {"label": label, "path": path.as_posix()}, "allowedWorkspacesCount": len(load_workspaces())})


@app.route("/repo/scan", methods=["POST", "OPTIONS"])
def repo_scan():
    workspace = resolve_workspace((request.get_json(silent=True) or {}).get("workspacePath"))
    result = scan_workspace(workspace)
    log_action("repo_scan", {"workspace": workspace.as_posix(), "fileCount": result["fileCount"]})
    return response(result)


@app.route("/file/read", methods=["POST", "OPTIONS"])
def file_read():
    payload = request.get_json(silent=True) or {}
    workspace = resolve_workspace(payload.get("workspacePath"))
    target = resolve_relative(workspace, str(payload.get("relativePath") or payload.get("filePath") or ""))
    return response(safe_file_read_response(workspace, target, payload))


@app.route("/proposal/generate", methods=["POST", "OPTIONS"])
def proposal_generate():
    payload = request.get_json(silent=True) or {}
    workspace = resolve_workspace(payload.get("workspacePath"))
    proposal = generate_proposal(workspace, str(payload.get("taskDescription") or payload.get("task") or ""))
    log_action("proposal_generate", {"workspace": workspace.as_posix(), "proposalId": proposal["proposalId"], "targetFile": proposal.get("targetFile", ""), "proposalType": proposal.get("proposalType"), "missionType": proposal.get("missionType"), "proposalEngineVersion": PROPOSAL_ENGINE_VERSION, "activeProposalHandler": ACTIVE_PROPOSAL_HANDLER_NAME})
    return response(proposal)


@app.route("/patch/apply", methods=["POST", "OPTIONS"])
def patch_apply():
    payload = request.get_json(silent=True) or {}
    workspace = resolve_workspace(payload.get("workspacePath"))
    return response(apply_patch_payload(workspace, payload.get("approvedPatch") or payload.get("proposal") or {}, str(payload.get("approvalPhrase") or payload.get("approval") or "")))


@app.route("/validate/run", methods=["POST", "OPTIONS"])
def validate_run():
    payload = request.get_json(silent=True) or {}
    workspace = resolve_workspace(payload.get("workspacePath"))
    return response(run_validation(workspace, str(payload.get("validationType") or "python_compile")))


@app.route("/git/status", methods=["POST", "OPTIONS"])
def git_status():
    workspace = resolve_workspace((request.get_json(silent=True) or {}).get("workspacePath"))
    return response({"ok": True, **run_safe_command(workspace, ["git", "status", "--short"])})


@app.route("/git/commit", methods=["POST", "OPTIONS"])
def git_commit():
    payload = request.get_json(silent=True) or {}
    if str(payload.get("approvalPhrase") or "") != APPROVAL_GIT:
        raise ConnectorError("Git approval phrase required.")
    workspace = resolve_workspace(payload.get("workspacePath"))
    files = [str(item) for item in payload.get("changedFiles") or []]
    if not files:
        raise ConnectorError("Approved changed files are required for git add.")
    for rel in files:
        resolve_relative(workspace, rel)
    message = str(payload.get("message") or "UNDX approved commit")[:180]
    results = [run_safe_command(workspace, ["git", "status", "--short"]), run_safe_command(workspace, ["git", "add", *files]), run_safe_command(workspace, ["git", "commit", "-m", message])]
    return response({"ok": all(item["ok"] for item in results), "results": results})


@app.route("/git/push", methods=["POST", "OPTIONS"])
def git_push():
    payload = request.get_json(silent=True) or {}
    if str(payload.get("approvalPhrase") or "") != APPROVAL_PUSH:
        raise ConnectorError("Push approval phrase required.")
    workspace = resolve_workspace(payload.get("workspacePath"))
    return response({"ok": True, **run_safe_command(workspace, ["git", "push", "origin", "main"], timeout=180)})


@app.errorhandler(ConnectorError)
def connector_error(error: ConnectorError):
    return response({"ok": False, "error": str(error)}, 400)


@app.errorhandler(Exception)
def unexpected_error(error: Exception):
    return response({"ok": False, "error": "UNDX Desktop Connector failed safely.", "detail": str(error)[:300]}, 500)


def main() -> None:
    ensure_config()
    print(f"UNDX Desktop Connector version: {VERSION}", flush=True)
    print(f"UNDX proposal engine version: {PROPOSAL_ENGINE_VERSION}", flush=True)
    print(f"UNDX active proposal handler: {ACTIVE_PROPOSAL_HANDLER_NAME}", flush=True)
    app.run(host="127.0.0.1", port=PORT, debug=False)


if __name__ == "__main__":
    main()
