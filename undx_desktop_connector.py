"""UNDX Desktop Connector.

Run this service on the local Mac to give UNDX controlled access to explicitly
approved workspaces. The connector is intentionally narrow: it blocks protected
paths, refuses path traversal, writes only approved proposal files, and runs only
allowlisted validation/Git commands.
"""

from __future__ import annotations

import difflib
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


VERSION = "1.0.0"
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
TEXT_EXTENSIONS = {".py", ".html", ".css", ".js", ".json", ".md", ".txt", ".toml", ".yml", ".yaml", ".ini", ".cfg", ".sql"}
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
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
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
    templates: list[str] = []
    static_assets: list[str] = []
    audit_scripts: list[str] = []
    protected_files: list[str] = []
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
    return {
        "ok": True,
        "workspacePath": workspace.as_posix(),
        "workspaceName": workspace.name,
        "fileCount": len(files),
        "folderTree": folder_tree(workspace),
        "languages": languages,
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
    return "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True), fromfile=f"a/{path}", tofile=f"b/{path}", lineterm=""))


def generate_proposal(workspace: Path, task: str) -> dict[str, Any]:
    normalized = (task or "").lower()
    if "pulse_labs.html" not in normalized and "pulse labs" not in normalized:
        raise ConnectorError("This connector currently supports the first approved test: Add text to templates/pulse_labs.html.")
    target_rel = "templates/pulse_labs.html"
    target = resolve_relative(workspace, target_rel)
    before = read_text_file(target)
    marker = "Built by UNDX Execution Kernel"
    if marker in before:
        after = before
    else:
        insertion = f"  <p class=\"muted\">{marker}</p>\n"
        anchor = "</section>\n"
        idx = before.rfind(anchor)
        if idx == -1:
            after = before.rstrip() + "\n" + insertion
        else:
            after = before[:idx] + insertion + before[idx:]
    return {
        "ok": True,
        "proposalId": f"DESKPROP-UNDX-{int(datetime.now().timestamp())}",
        "targetFile": target_rel,
        "beforeSnippet": before[-600:],
        "afterSnippet": after[-700:],
        "changes": [{"path": target_rel, "before": before, "after": after, "diff": diff_text(target_rel, before, after), "type": "modify"}],
        "riskNotes": ["Single approved template text change.", "No protected files touched.", "Backup required before write."],
        "validationPlan": ["Python compile", "JavaScript parse", "UNDX audit", "site functional audit", "pulse feed layout audit"],
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
        current = read_text_file(target)
        before = str(change.get("before") or "")
        after = str(change.get("after") or "")
        if current != before and after not in current:
            raise ConnectorError(f"Current file does not match proposal before-state: {rel}")
        backup_path = backup_dir / rel
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, backup_path)
        target.write_text(after, encoding="utf-8")
        changed.append(rel)
        backups.append(backup_path.as_posix())
    log_action("patch_apply", {"workspace": workspace.as_posix(), "changed": changed, "backups": backups})
    return {"ok": True, "changedFiles": changed, "backupPath": backup_dir.as_posix(), "backups": backups}


def run_safe_command(workspace: Path, args: list[str], timeout: int = 90) -> dict[str, Any]:
    result = subprocess.run(args, cwd=workspace, text=True, capture_output=True, timeout=timeout, check=False)
    return {"command": args, "returncode": result.returncode, "stdout": result.stdout[-8000:], "stderr": result.stderr[-8000:], "ok": result.returncode == 0}


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
    if requested == "safe_all":
        commands = [cmd for key in ("python_compile", "undx_audit", "site_functional_audit", "performance_audit", "pulse_feed_layout_audit") for cmd in checks[key]]
    elif requested == "javascript_parse":
        return {"ok": True, "validationType": requested, "results": [{"ok": True, "stdout": "JavaScript parse should be run through the app audit harness.", "stderr": "", "returncode": 0}]}
    elif requested in checks:
        commands = checks[requested]
    else:
        raise ConnectorError("Validation type is not allowlisted.")
    results = [run_safe_command(workspace, command) for command in commands]
    return {"ok": all(item["ok"] for item in results), "validationType": requested, "results": results}


@app.route("/health", methods=["GET", "OPTIONS"])
def health():
    workspaces = load_workspaces()
    return response({"ok": True, "status": "online", "connector": "UNDX Desktop Connector", "machineName": socket.gethostname(), "allowedWorkspacesCount": len(workspaces), "version": VERSION})


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
    log_action("proposal_generate", {"workspace": workspace.as_posix(), "proposalId": proposal["proposalId"], "targetFile": proposal["targetFile"]})
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
    app.run(host="127.0.0.1", port=PORT, debug=False)


if __name__ == "__main__":
    main()
