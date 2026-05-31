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


def read_text_file(path: Path, max_bytes: int = 2_000_000) -> str:
    if protected(path):
        raise ConnectorError("Protected file blocked.")
    if not path.exists() or not path.is_file():
        raise ConnectorError("Requested file does not exist.")
    if path.suffix.lower() not in TEXT_EXTENSIONS:
        raise ConnectorError("Unsupported file type for safe read.")
    if path.stat().st_size > max_bytes:
        raise ConnectorError("File exceeds safe read limit.")
    return path.read_text(encoding="utf-8", errors="replace")


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
        ("Mission", "Clear structure, strong visual hierarchy, and fast path to action."),
        ("Experience", "Responsive sections designed for desktop and mobile visitors."),
        ("Trust", "Readable copy, accessible contrast, and careful spacing."),
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
    .brand {{ font-weight:900; letter-spacing:.08em; text-transform:uppercase; color:var(--primary); }}
    .button {{ display:inline-flex; min-height:44px; align-items:center; justify-content:center; border:1px solid color-mix(in srgb, var(--primary) 42%, transparent); border-radius:12px; padding:12px 16px; color:#061018; background:linear-gradient(135deg,var(--primary),var(--secondary)); font-weight:900; text-decoration:none; }}
    .hero {{ display:grid; grid-template-columns:minmax(0,1.1fr) minmax(280px,.9fr); gap:28px; align-items:center; }}
    h1 {{ font-size:clamp(42px,8vw,86px); line-height:.95; margin:0 0 20px; }}
    p {{ color:#bfd0dd; font-size:1.05rem; line-height:1.7; }}
    .panel {{ border:1px solid rgba(255,255,255,.14); border-radius:22px; background:rgba(255,255,255,.07); padding:24px; box-shadow:0 28px 90px rgba(0,0,0,.32); }}
    .grid {{ display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:16px; margin-top:34px; }}
    article {{ border:1px solid rgba(255,255,255,.12); border-radius:18px; padding:18px; background:rgba(255,255,255,.055); }}
    article span {{ color:var(--secondary); font-weight:900; }}
    @media (max-width: 760px) {{ main {{ padding:34px 0; }} .hero,.grid {{ grid-template-columns:1fr; }} nav {{ align-items:flex-start; flex-direction:column; margin-bottom:38px; }} }}
  </style>
</head>
<body>
  <main>
    <nav><div class="brand">{title}</div><a class="button" href="#start">Start</a></nav>
    <section class="hero" id="start">
      <div>
        <h1>{title}</h1>
        <p>{description}</p>
        <a class="button" href="#details">Explore the build</a>
      </div>
      <aside class="panel">
        <strong>Built by UNDX Execution Kernel</strong>
        <p>This page was generated as an approval-gated repository-aware proposal.</p>
      </aside>
    </section>
    <section class="grid" id="details">
{card_html}
    </section>
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


def choose_target_files(workspace: Path, task: str, scan: dict[str, Any]) -> list[str]:
    requested = extract_requested_paths(task)
    if requested:
        return requested
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
    for collection in ("htmlFiles", "templates", "reactFiles", "vueFiles", "jsFiles", "cssFiles", "pythonFiles"):
        values = scan.get(collection) or []
        if values:
            return [values[0]]
    return ["index.html"]


def generate_proposal(workspace: Path, task: str) -> dict[str, Any]:
    if not (task or "").strip():
        raise ConnectorError("Mission directive is required for proposal generation.")
    scan = scan_workspace(workspace)
    target_files = choose_target_files(workspace, task, scan)
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
    return {
        "ok": True,
        "proposalId": f"DESKPROP-UNDX-{int(datetime.now().timestamp())}",
        "targetFile": target_rel,
        "proposalEngine": "Repository-Aware",
        "proposalEngineVersion": PROPOSAL_ENGINE_VERSION,
        "activeProposalHandler": ACTIVE_PROPOSAL_HANDLER_NAME,
        "repositoryAware": True,
        "repositoryMap": scan.get("repositoryMap", {}),
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
        commands = [cmd for key in ("python_compile", "undx_audit", "site_functional_audit", "performance_audit", "pulse_feed_layout_audit") for cmd in checks[key] if all((workspace / part).exists() for part in cmd[2:] if part.endswith(".py"))]
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
    return response({"ok": True, "relativePath": target.relative_to(workspace).as_posix(), "content": read_text_file(target)})


@app.route("/proposal/generate", methods=["POST", "OPTIONS"])
def proposal_generate():
    payload = request.get_json(silent=True) or {}
    workspace = resolve_workspace(payload.get("workspacePath"))
    proposal = generate_proposal(workspace, str(payload.get("taskDescription") or payload.get("task") or ""))
    log_action("proposal_generate", {"workspace": workspace.as_posix(), "proposalId": proposal["proposalId"], "targetFile": proposal["targetFile"], "proposalEngineVersion": PROPOSAL_ENGINE_VERSION, "activeProposalHandler": ACTIVE_PROPOSAL_HANDLER_NAME})
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
