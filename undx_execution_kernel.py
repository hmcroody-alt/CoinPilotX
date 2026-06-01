"""UNDX execution kernel for controlled local repository work.

The kernel is intentionally conservative: it can scan an approved repository,
propose diffs, write only explicitly approved changes, run allowlisted checks,
and expose Git operations behind an approval phrase. It never reads protected
files and never accepts arbitrary shell commands.
"""

from __future__ import annotations

import difflib
import html
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_REPOSITORY_PATH = Path("/Users/hmcherie/Desktop/CoinPilotX").resolve()
APPROVAL_PHRASE = "APPROVE UNDX WRITE"
LOG_PATH = DEFAULT_REPOSITORY_PATH / "undx_execution_log.jsonl"
BACKUP_ROOT = DEFAULT_REPOSITORY_PATH / ".undx_backups"

PROTECTED_PATTERNS = (
    ".env",
    ".env.",
    ".git/",
    "__pycache__/",
    "venv/",
    ".venv/",
    "credentials",
    "credential",
    "secret",
    "token",
    "private_key",
    "id_rsa",
    ".pem",
    ".key",
    ".sqlite",
    ".sqlite3",
    "coinpilotx.db",
)

TEXT_EXTENSIONS = {
    ".py",
    ".html",
    ".css",
    ".js",
    ".json",
    ".md",
    ".txt",
    ".toml",
    ".yml",
    ".yaml",
    ".ini",
    ".cfg",
    ".sql",
}

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


class KernelError(ValueError):
    """Raised when the kernel refuses an unsafe request."""


@dataclass(frozen=True)
class RepositoryPath:
    root: Path

    def rel(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_repository_path(path_value: str | os.PathLike[str] | None = None) -> RepositoryPath:
    requested = Path(path_value or DEFAULT_REPOSITORY_PATH).expanduser().resolve()
    if requested != DEFAULT_REPOSITORY_PATH:
        try:
            requested.relative_to(DEFAULT_REPOSITORY_PATH)
        except ValueError as exc:
            raise KernelError("UNDX can only access the approved CoinPilotX repository path.") from exc
    if not requested.exists() or not requested.is_dir():
        raise KernelError("Repository path does not exist or is not a directory.")
    return RepositoryPath(requested)


def is_protected_path(path: Path) -> bool:
    normalized = path.as_posix().lower()
    return any(pattern in normalized for pattern in PROTECTED_PATTERNS)


def is_text_file(path: Path) -> bool:
    return path.suffix.lower() in TEXT_EXTENSIONS


def safe_read_text(path: Path, max_bytes: int = 6_000_000) -> str:
    if is_protected_path(path):
        raise KernelError(f"Protected file cannot be read: {path.name}")
    if not is_text_file(path):
        raise KernelError(f"Unsupported file type for safe text read: {path.suffix}")
    if path.stat().st_size > max_bytes:
        raise KernelError(f"File is too large for safe preview: {path.name}")
    return path.read_text(encoding="utf-8", errors="replace")


def repository_tree(repo: RepositoryPath, max_entries: int = 360) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    skipped = {".git", "venv", ".venv", "__pycache__", ".undx_backups"}
    for path in sorted(repo.root.rglob("*")):
        rel = repo.rel(path)
        if any(part in skipped for part in Path(rel).parts):
            continue
        entries.append(
            {
                "path": rel,
                "type": "folder" if path.is_dir() else "file",
                "protected": is_protected_path(path),
            }
        )
        if len(entries) >= max_entries:
            break
    return entries


def scan_repository(path_value: str | None = None) -> dict[str, Any]:
    repo = resolve_repository_path(path_value)
    files: list[Path] = []
    protected: list[str] = []
    languages: dict[str, int] = {}
    templates: list[str] = []
    static_assets: list[str] = []
    audit_scripts: list[str] = []
    scripts: list[str] = []
    routes: list[dict[str, str]] = []

    for path in sorted(repo.root.rglob("*")):
        rel = repo.rel(path)
        if any(part in {".git", "venv", ".venv", "__pycache__", ".undx_backups"} for part in Path(rel).parts):
            continue
        if path.is_dir():
            continue
        files.append(path)
        if is_protected_path(path):
            protected.append(rel)
            continue
        language = LANGUAGE_EXTENSIONS.get(path.suffix.lower())
        if language:
            languages[language] = languages.get(language, 0) + 1
        if rel.startswith("templates/") and path.suffix.lower() == ".html":
            templates.append(rel)
        if rel.startswith("static/") and path.suffix.lower() in {".js", ".css"}:
            static_assets.append(rel)
        if rel.startswith("scripts/") and path.suffix.lower() == ".py":
            scripts.append(rel)
            if "audit" in path.name:
                audit_scripts.append(rel)
        if path.suffix.lower() == ".py" and path.stat().st_size <= 6_000_000:
            try:
                text = safe_read_text(path)
            except KernelError:
                continue
            for match in re.finditer(r"@webhook_app\.route\((['\"])(.*?)\1", text):
                routes.append({"route": match.group(2), "file": rel})

    return {
        "ok": True,
        "repositoryPath": repo.root.as_posix(),
        "repositoryName": repo.root.name,
        "fileCount": len(files),
        "languages": languages,
        "routes": routes[:240],
        "templates": templates[:240],
        "staticAssets": static_assets[:240],
        "scripts": scripts[:160],
        "auditScripts": audit_scripts[:120],
        "protectedFiles": protected[:120],
        "tree": repository_tree(repo),
        "lastScan": now_iso(),
        "readOnlySummary": "UNDX scanned repository structure and allowed text metadata only. Protected files were not read.",
    }


def unified_diff(path: str, before: str, after: str) -> str:
    return "".join(
        difflib.unified_diff(
            before.splitlines(True),
            after.splitlines(True),
            fromfile=f"a/{path}",
            tofile=f"b/{path}",
            lineterm="\n",
        )
    )


def change(path: str, before: str, after: str, change_type: str = "modify") -> dict[str, Any]:
    return {
        "id": f"CHANGE-{len(before)}-{len(after)}-{abs(hash(path)) % 100000}",
        "path": path,
        "type": change_type,
        "diff": unified_diff(path, before, after),
        "before": before,
        "after": after,
    }


def replace_once(source: str, old: str, new: str, label: str) -> str:
    if old not in source:
        raise KernelError(f"Could not locate proposal anchor: {label}")
    return source.replace(old, new, 1)


def pulse_labs_template() -> str:
    return """<section class="card pulse-labs-hero">
  <span class="pill">Pulse Labs</span>
  <h2>Pulse Labs</h2>
  <p class="muted">A focused lab space for CoinPilotXAI experiments, navigation ideas, safety prototypes, and product intelligence before they graduate into the wider Pulse ecosystem.</p>
  <div class="grid">
    <article class="card">
      <h3>Navigation Experiments</h3>
      <p class="muted">Plan new Pulse surfaces, creator flows, and premium paths without crowding the feed.</p>
    </article>
    <article class="card">
      <h3>Safety Prototypes</h3>
      <p class="muted">Shape scam defense, wallet safety, and moderation ideas before launch.</p>
    </article>
    <article class="card">
      <h3>Builder Notes</h3>
      <p class="muted">Capture what UNDX should build next for Pulse, Premium, and creator intelligence.</p>
    </article>
  </div>
</section>
<section class="card">
  <h2>Lab Queue</h2>
  <p class="muted">Pulse Labs starts as a navigation destination and will become the staging area for approved CoinPilotXAI product experiments.</p>
  <div class="actions">
    <a class="button primary" href="/pulse/create">Create Pulse</a>
    <a class="button" href="/pulse/premium/undx">Open UNDX</a>
  </div>
</section>
"""


def generic_task_title(directive: str, fallback: str = "UNDX Generated Page") -> str:
    text = re.sub(r"\s+", " ", directive or "").strip()
    text = re.sub(r"^(build|create|generate|make|recreate|replace|add)\s+(a|an|the)?\s*", "", text, flags=re.I)
    text = re.sub(r"\b(landing page|website|page|site|index\.html|html)\b", "", text, flags=re.I).strip(" .:-")
    if not text:
        return fallback
    return " ".join(word.capitalize() if len(word) > 2 else word.upper() for word in text.split()[:8])


def generic_landing_html(directive: str, repository_name: str) -> str:
    title = html.escape(generic_task_title(directive, repository_name or "UNDX Landing Page"), quote=True)
    description = html.escape(re.sub(r"\s+", " ", directive or "A polished generated landing page.").strip(), quote=True)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; font-family: Inter, system-ui, sans-serif; background: #07111f; color: #f7fbff; }}
    main {{ width: min(1120px, calc(100% - 32px)); margin: auto; padding: 72px 0; }}
    h1 {{ font-size: clamp(42px, 8vw, 82px); line-height: .95; margin: 0 0 20px; }}
    p {{ color: #c3d3df; line-height: 1.7; }}
    .hero {{ display: grid; grid-template-columns: minmax(0, 1.1fr) minmax(280px, .9fr); gap: 28px; align-items: center; }}
    .panel {{ border: 1px solid rgba(255,255,255,.16); border-radius: 22px; padding: 24px; background: rgba(255,255,255,.07); }}
    .button {{ display: inline-flex; min-height: 44px; align-items: center; border-radius: 12px; padding: 12px 16px; background: linear-gradient(135deg, #6edff6, #36e58f); color: #061018; font-weight: 900; text-decoration: none; }}
    @media (max-width: 760px) {{ .hero {{ grid-template-columns: 1fr; }} main {{ padding: 36px 0; }} }}
  </style>
</head>
<body>
  <main>
    <section class="hero">
      <div>
        <h1>{title}</h1>
        <p>{description}</p>
        <a class="button" href="#details">Explore</a>
      </div>
      <aside class="panel" id="details">
        <strong>Built by UNDX Execution Kernel</strong>
        <p>Generated as an approval-gated repository-aware code proposal.</p>
      </aside>
    </section>
  </main>
</body>
</html>
"""


def classify_mission(directive: str) -> dict[str, Any]:
    lowered = re.sub(r"\s+", " ", directive or "").strip().lower()
    planning_only = any(phrase in lowered for phrase in PLANNING_ONLY_PHRASES)
    if planning_only:
        mission_type = "planning-only"
        proposal_type = "planning-only"
    elif any(word in lowered for word in ("bug", "fix", "failure", "error", "broken", "regression")):
        mission_type = "bug-fix"
        proposal_type = "implementation"
    elif any(word in lowered for word in ("ui", "ux", "frontend", "layout", "style", "css", "template")):
        mission_type = "ui-change"
        proposal_type = "implementation"
    elif any(word in lowered for word in ("database", "migration", "schema", "table", "column")):
        mission_type = "database-migration"
        proposal_type = "implementation"
    elif any(word in lowered for word in ("audit", "validate", "validation", "test", "checks")):
        mission_type = "validation-audit"
        proposal_type = "report"
    elif any(word in lowered for word in ("docs", "documentation", "report", "blueprint")):
        mission_type = "documentation-report"
        proposal_type = "report"
    else:
        mission_type = "code-implementation"
        proposal_type = "implementation"
    return {
        "missionType": mission_type,
        "missionCategory": "architecture-plan" if mission_type == "planning-only" else mission_type,
        "proposalType": proposal_type,
        "planningOnly": planning_only,
    }


def mission_keywords(directive: str) -> list[str]:
    lowered = (directive or "").lower()
    keywords = set(re.findall(r"[a-z][a-z0-9_-]{2,}", lowered))
    if "pulse communications" in lowered or any(word in keywords for word in ("messenger", "messages", "conversation", "conversations", "chat", "rooms", "groups", "communications")):
        keywords.update(COMMUNICATION_KEYWORDS)
    return sorted(keywords)


def score_repository_file(repo: RepositoryPath, rel: str, keywords: list[str]) -> tuple[int, list[str]]:
    lowered = rel.lower()
    if lowered in RISKY_FALLBACK_FILES:
        return 0, []
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
    path = repo.root / rel
    if path.exists() and path.is_file() and is_text_file(path) and not is_protected_path(path):
        try:
            text = safe_read_text(path, max_bytes=800_000).lower()
        except KernelError:
            text = ""
        hits = [keyword for keyword in keywords if keyword and keyword in text]
        if hits:
            score += min(30, len(hits) * 3)
            reasons.append("content references " + ", ".join(hits[:5]))
    return score, reasons[:6]


def ranked_target_files(repo: RepositoryPath, directive: str, limit: int = 12) -> list[dict[str, Any]]:
    scan = scan_repository(repo.root.as_posix())
    candidates: list[str] = []
    for collection in ("templates", "staticAssets", "scripts"):
        for rel in scan.get(collection) or []:
            if rel not in candidates and rel not in RISKY_FALLBACK_FILES:
                candidates.append(rel)
    for item in scan.get("tree", []):
        rel = str(item.get("path") or "")
        if item.get("type") == "file" and rel not in candidates and rel not in RISKY_FALLBACK_FILES:
            candidates.append(rel)
    ranked: list[dict[str, Any]] = []
    keywords = mission_keywords(directive)
    for rel in candidates:
        score, reasons = score_repository_file(repo, rel, keywords)
        if score > 0:
            ranked.append({"path": rel, "score": score, "why": reasons or ["matched mission keywords"]})
    ranked.sort(key=lambda item: (-int(item["score"]), str(item["path"])))
    return ranked[:limit]


def planning_report(repo: RepositoryPath, directive: str, targets: list[dict[str, Any]], classification: dict[str, Any]) -> str:
    target_lines = [f"- {item['path']}: {'; '.join(item.get('why') or ['selected by repository relevance'])}" for item in targets] or ["- No implementation target selected. Report-only mode is active."]
    target_paths = [item["path"] for item in targets] or ["bot.py", "scripts/messenger_core_audit.py", "scripts/chat_system_audit.py"]
    sections = [
        ("Mission Classification", [f"Mission Type: {classification.get('missionType')}", f"Proposal Type: {classification.get('proposalType')}", "Diff Generation: disabled for planning-only mission"]),
        ("Repository Communications Map", [f"Repository: {repo.root.name}", "Relevant targets: " + ", ".join(target_paths)]),
        ("Problems Found", ["Large planning missions must not be converted into arbitrary HTML rewrites.", "Pulse Communications requires route, model, permission, template, JavaScript, and audit mapping before implementation."]),
        ("Exact Files Involved", target_lines),
        ("Target Files", [f"- {path}" for path in target_paths]),
        ("Files To Preserve", ["- Existing direct message routes and data", "- Existing rooms/groups behavior", "- Pulse feed, UNDX, Wallet Guardian, admin, and auth"]),
        ("Files To Replace", ["- Legacy communications UI/API only after v2 passes audits"]),
        ("New V2 Files To Create", ["- pulse_communications_v2/models.py", "- pulse_communications_v2/service.py", "- pulse_communications_v2/routes.py", "- pulse_communications_v2/permissions.py"]),
        ("Database Migration Strategy", ["Add v2-prefixed tables only.", "Backfill through a bridge after legacy reads are verified."]),
        ("First Safe Implementation Patch", ["Create disabled v2 scaffold/report only after approval.", "No file writes in this planning-only proposal."]),
        ("Validation Plan", ["Python compile", "JavaScript parse", "UNDX audits", "messenger/chat audits", "git diff --check"]),
        ("Rollback Plan", ["Keep legacy routes active.", "Keep v2 behind a false feature flag."]),
        ("Approval Gate", ["Human approval required before any implementation diff."]),
    ]
    lines = [f"# UNDX Planning Report: {directive.strip()[:120]}", ""]
    for title, values in sections:
        lines.extend([f"## {title}", *values, ""])
    return "\n".join(lines).strip() + "\n"


def generic_target_path(repo: RepositoryPath, directive: str) -> str:
    explicit = re.search(r"([\w./-]+\.(?:html|css|js|py|md|txt|json))", directive or "", flags=re.I)
    if explicit:
        return explicit.group(1).strip("./")
    lowered = (directive or "").lower()
    if any(word in lowered for word in ("index", "landing", "website", "recreate", "home page")):
        return "index.html"
    ranked = ranked_target_files(repo, directive, limit=1)
    if ranked:
        return str(ranked[0]["path"])
    raise KernelError("No relevant target file was safe enough for diff generation. Generate a planning report first.")


def propose_generic_repository_change(repo: RepositoryPath, directive: str) -> dict[str, Any]:
    classification = classify_mission(directive)
    ranked = ranked_target_files(repo, directive)
    if classification.get("planningOnly"):
        return {
            "ok": True,
            "proposalId": f"PLAN-UNDX-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
            "directive": directive,
            "proposalType": "planning-only",
            "missionType": classification.get("missionType"),
            "missionCategory": classification.get("missionCategory"),
            "planningOnly": True,
            "targetFiles": [item["path"] for item in ranked],
            "targetFileReasons": [{"path": item["path"], "why": item.get("why", [])} for item in ranked],
            "report": planning_report(repo, directive, ranked, classification),
            "diff": "",
            "changes": [],
            "requiresApproval": False,
            "message": "Planning report generated. No files written.",
            "summary": "Planning-only architecture report generated from repository-aware scan.",
            "generatedAt": now_iso(),
        }
    rel = generic_target_path(repo, directive)
    target = (repo.root / rel).resolve()
    try:
        target.relative_to(repo.root)
    except ValueError as exc:
        raise KernelError(f"Refusing proposal outside repository: {rel}") from exc
    if is_protected_path(target):
        raise KernelError(f"Refusing proposal for protected path: {rel}")
    before = safe_read_text(target) if target.exists() else ""
    suffix = target.suffix.lower()
    if suffix == ".html" or not target.exists():
        after = generic_landing_html(directive, repo.root.name) if suffix in {"", ".html"} else f"Generated by UNDX Execution Kernel\n\nMission:\n{directive.strip()}\n"
    elif suffix == ".css":
        after = before.rstrip() + f"\n\n/* UNDX proposal: {directive.strip()[:180]} */\n"
    elif suffix == ".js":
        after = before.rstrip() + f"\n\n// UNDX proposal: {directive.strip()[:180]}\n"
    else:
        after = before.rstrip() + f"\n\n# UNDX proposal: {directive.strip()[:180]}\n"
    return {
        "ok": True,
        "proposalId": f"PROPOSAL-UNDX-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        "directive": directive,
        "proposalType": "implementation",
        "missionType": classification.get("missionType"),
        "missionCategory": classification.get("missionCategory"),
        "planningOnly": False,
        "targetFiles": [rel],
        "targetFileReasons": [{"path": item["path"], "why": item.get("why", [])} for item in ranked if item["path"] == rel],
        "diff": unified_diff(rel, before, after),
        "requiresApproval": True,
        "message": "Implementation proposal generated. Review diff before approval.",
        "summary": "Repository-aware code proposal generated from the active directive.",
        "approvalPhrase": APPROVAL_PHRASE,
        "repositoryAware": True,
        "changes": [change(rel, before, after, "modify" if target.exists() else "create")],
        "generatedAt": now_iso(),
    }


def propose_pulse_labs(repo: RepositoryPath, directive: str) -> dict[str, Any]:
    bot_path = repo.root / "bot.py"
    audit_path = repo.root / "scripts" / "site_functional_audit.py"
    undx_audit_path = repo.root / "scripts" / "undx_homepage_audit.py"
    template_path = repo.root / "templates" / "pulse_labs.html"
    bot_before = safe_read_text(bot_path)
    bot_after = bot_before
    if '("Labs", "/pulse/labs")' not in bot_after:
        bot_after = replace_once(
            bot_after,
            '        ("Spaces", "/pulse/spaces"),\n        ("Marketplace", "/pulse/marketplace"),',
            '        ("Spaces", "/pulse/spaces"),\n        ("Labs", "/pulse/labs"),\n        ("Marketplace", "/pulse/marketplace"),',
            "desktop top nav",
        )
    if '("Pulse Labs", "/pulse/labs", "△")' not in bot_after:
        bot_after = replace_once(
            bot_after,
            '        ("Marketplace", "/pulse/marketplace", "▣"),\n        ("Teachers", "/pulse/teachers", "T"),',
            '        ("Marketplace", "/pulse/marketplace", "▣"),\n        ("Pulse Labs", "/pulse/labs", "△"),\n        ("Teachers", "/pulse/teachers", "T"),',
            "desktop left rail",
        )
    if '("Pulse Labs", "/pulse/labs"),' not in bot_after:
        bot_after = replace_once(
            bot_after,
            '        ("Marketplace", "/pulse/marketplace"),\n        ("Notifications", "/pulse/notifications"),',
            '        ("Marketplace", "/pulse/marketplace"),\n        ("Pulse Labs", "/pulse/labs"),\n        ("Notifications", "/pulse/notifications"),',
            "Pulse nav items",
        )
    if '"Pulse Labs", "/pulse/labs"), ("Marketplace"' not in bot_after:
        bot_after = replace_once(
            bot_after,
            '        ("Primary", [("Home", "/pulse"), ("Create Status", "/pulse/status"), ("Reels", "/pulse/reels"), ("Spaces", "/pulse/spaces"), ("Marketplace", "/pulse/marketplace"), ("Groups", "/pulse/groups"), ("Teachers", "/pulse/teachers")]),',
            '        ("Primary", [("Home", "/pulse"), ("Create Status", "/pulse/status"), ("Reels", "/pulse/reels"), ("Spaces", "/pulse/spaces"), ("Pulse Labs", "/pulse/labs"), ("Marketplace", "/pulse/marketplace"), ("Groups", "/pulse/groups"), ("Teachers", "/pulse/teachers")]),',
            "mobile drawer nav",
        )
    route_code = '''

@webhook_app.route("/pulse/labs", methods=["GET"])
def pulse_labs_page():
    user = require_account()
    if not user:
        return redirect(url_for("login_page", next=request.path))
    main_html = render_template("pulse_labs.html")
    side_html = (
        "<article class='card'><h2>Lab Signals</h2><p>Prototype navigation, creator systems, and Pulse intelligence without changing the main feed.</p></article>"
        "<article class='card'><h2>UNDX Link</h2><p>Use UNDX to turn lab missions into approved build plans.</p><a class='button primary' href='/pulse/premium/undx'>Open UNDX</a></article>"
    )
    return pulse_social_shell(
        "Pulse Labs",
        "Prototype navigation, safety ideas, creator workflows, and product experiments for the Pulse ecosystem.",
        main_html,
        side_html,
        "",
    )
'''
    if 'def pulse_labs_page()' not in bot_after:
        bot_after = replace_once(
            bot_after,
            '\n\n@webhook_app.route("/api/pulse/search", methods=["GET"])\ndef api_pulse_search():',
            route_code + '\n\n@webhook_app.route("/api/pulse/search", methods=["GET"])\ndef api_pulse_search():',
            "Pulse Labs route",
        )

    audit_before = safe_read_text(audit_path)
    audit_after = audit_before
    if '"/pulse/labs"' not in audit_after:
        audit_after = replace_once(
            audit_after,
            '        "/pulse/friends",\n        "/pulse/messages",',
            '        "/pulse/friends",\n        "/pulse/labs",\n        "/pulse/messages",',
            "site functional audit route",
        )

    undx_audit_before = safe_read_text(undx_audit_path)
    undx_audit_after = undx_audit_before

    template_before = template_path.read_text(encoding="utf-8") if template_path.exists() else ""
    template_after = pulse_labs_template()
    changes = [
        change("bot.py", bot_before, bot_after),
        change("templates/pulse_labs.html", template_before, template_after, "create" if not template_before else "modify"),
        change("scripts/site_functional_audit.py", audit_before, audit_after),
        change("scripts/undx_homepage_audit.py", undx_audit_before, undx_audit_after),
    ]
    return {
        "ok": True,
        "proposalId": f"PROPOSAL-UNDX-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}",
        "directive": directive,
        "summary": "Add a real Pulse Labs page, wire it into Pulse navigation, and extend audits.",
        "approvalPhrase": APPROVAL_PHRASE,
        "changes": changes,
        "generatedAt": now_iso(),
    }


def generate_proposal(path_value: str | None, directive: str) -> dict[str, Any]:
    repo = resolve_repository_path(path_value)
    text = (directive or "").strip()
    if not text:
        raise KernelError("Enter a directive before generating a proposal.")
    lowered = text.lower()
    if "pulse labs" in lowered or ("labs" in lowered and "pulse" in lowered):
        return propose_pulse_labs(repo, text)
    return propose_generic_repository_change(repo, text)


def write_log(event: dict[str, Any]) -> None:
    event = {"timestamp": now_iso(), **event}
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True) + "\n")


def apply_approved_changes(path_value: str | None, proposal: dict[str, Any], approval: str, approved_ids: list[str] | None = None) -> dict[str, Any]:
    if approval != APPROVAL_PHRASE:
        raise KernelError("Exact approval phrase required before UNDX can write files.")
    repo = resolve_repository_path(path_value)
    allowed_ids = set(approved_ids or [])
    applied: list[dict[str, str]] = []
    backup_dir = BACKUP_ROOT / datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    changes = proposal.get("changes") or []
    for item in changes:
        change_id = str(item.get("id") or "")
        if allowed_ids and change_id not in allowed_ids:
            continue
        rel = str(item.get("path") or "").strip()
        target = (repo.root / rel).resolve()
        try:
            target.relative_to(repo.root)
        except ValueError as exc:
            raise KernelError(f"Refusing to write outside repository: {rel}") from exc
        if is_protected_path(target):
            raise KernelError(f"Refusing to write protected path: {rel}")
        before = item.get("before") or ""
        after = item.get("after")
        if after is None:
            raise KernelError(f"Missing approved content for {rel}")
        if target.exists():
            current = safe_read_text(target)
            if current != before:
                raise KernelError(f"Current file changed since proposal was generated: {rel}")
            backup_path = backup_dir / rel
            backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, backup_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(after), encoding="utf-8")
        applied.append({"path": rel, "changeId": change_id})
    write_log({"event": "apply_approved_changes", "proposalId": proposal.get("proposalId"), "applied": applied})
    return {"ok": True, "applied": applied, "backupDirectory": backup_dir.as_posix() if backup_dir.exists() else "", "logPath": LOG_PATH.as_posix()}


SAFE_VALIDATION_COMMANDS = {
    "python_compile": ["venv/bin/python", "-m", "py_compile", "bot.py", "undx_execution_kernel.py", "scripts/site_functional_audit.py", "scripts/undx_homepage_audit.py"],
    "undx_audit": ["venv/bin/python", "scripts/undx_homepage_audit.py"],
    "site_functional_audit": ["venv/bin/python", "scripts/site_functional_audit.py"],
    "performance_audit": ["venv/bin/python", "scripts/performance_audit.py"],
    "pulse_feed_layout_audit": ["venv/bin/python", "scripts/pulse_feed_layout_audit.py"],
}


def run_safe_validation(path_value: str | None, checks: list[str] | None = None) -> dict[str, Any]:
    repo = resolve_repository_path(path_value)
    selected = checks or list(SAFE_VALIDATION_COMMANDS)
    results = []
    for key in selected:
        command = SAFE_VALIDATION_COMMANDS.get(key)
        if not command:
            raise KernelError(f"Validation check is not allowlisted: {key}")
        proc = subprocess.run(command, cwd=repo.root, text=True, capture_output=True, timeout=120, check=False)
        results.append({"check": key, "returnCode": proc.returncode, "stdout": proc.stdout[-8000:], "stderr": proc.stderr[-8000:]})
    write_log({"event": "run_safe_validation", "checks": selected, "results": [{"check": r["check"], "returnCode": r["returnCode"]} for r in results]})
    return {"ok": all(item["returnCode"] == 0 for item in results), "results": results}


def git_gateway(path_value: str | None, action: str, approval: str = "", files: list[str] | None = None, message: str = "") -> dict[str, Any]:
    repo = resolve_repository_path(path_value)
    action = (action or "status").strip()
    if action == "status":
        command = ["git", "status", "--short"]
    elif action == "add":
        if approval != APPROVAL_PHRASE:
            raise KernelError("Exact approval phrase required before git add.")
        safe_files = [str(item) for item in (files or []) if item and not is_protected_path(repo.root / str(item))]
        command = ["git", "add", *safe_files] if safe_files else ["git", "add", "."]
    elif action == "commit":
        if approval != APPROVAL_PHRASE:
            raise KernelError("Exact approval phrase required before git commit.")
        command = ["git", "commit", "-m", message or "Add UNDX execution kernel"]
    elif action == "push":
        if approval != APPROVAL_PHRASE:
            raise KernelError("Exact approval phrase required before git push.")
        command = ["git", "push", "origin", "main"]
    else:
        raise KernelError("Git action is not allowlisted.")
    proc = subprocess.run(command, cwd=repo.root, text=True, capture_output=True, timeout=180, check=False)
    write_log({"event": "git_gateway", "action": action, "returnCode": proc.returncode})
    return {"ok": proc.returncode == 0, "action": action, "returnCode": proc.returncode, "stdout": proc.stdout[-8000:], "stderr": proc.stderr[-8000:]}
