"""UNDX Live Autonomous Brain Layer.

The brain layer is the shared reasoning front door for UNDX proposal flows. It
classifies missions, identifies target systems, selects repository files,
builds planning reports, attaches multi-agent review, and enforces safety
before the Desktop Connector or Execution Kernel return a proposal.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


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
    "pulse_communications",
    "comm_v2",
)

OFFLINE_SYSTEM_TERMS = ("offline", "pwa", "cache", "service worker", "fallback")
RISKY_FALLBACK_FILES = {"static/offline.html", "templates/offline.html", "offline.html"}
PROTECTED_PATH_TERMS = (
    ".env",
    "secret",
    "secrets",
    "token",
    "tokens",
    "credential",
    "credentials",
    "private_key",
    "private-key",
    ".pem",
    ".key",
    ".git/",
)
MIN_RELEVANCE_SCORE = 8

TARGET_SYSTEM_KEYWORDS = {
    "communications": COMMUNICATION_KEYWORDS,
    "pulse-labs": ("pulse labs", "labs", "experiment", "product lab"),
    "pulse-status": ("status", "story", "stories", "pulse status", "create status"),
    "undx": ("undx", "execution kernel", "desktop connector", "proposal", "repository-aware", "brain layer"),
    "auth-login": ("auth", "login", "logout", "session", "password", "account"),
    "payments": ("payment", "stripe", "checkout", "billing", "subscription", "premium"),
    "admin": ("admin", "command center", "global command", "moderation"),
    "wallet-guardian": ("wallet guardian", "wallet", "scam", "risk", "address", "token approval"),
    "homepage": ("homepage", "home page", "landing page", "index", "website"),
    "offline-pwa": OFFLINE_SYSTEM_TERMS,
}


class BrainSafetyError(ValueError):
    """Raised when the brain layer refuses an unsafe proposal."""


@dataclass(frozen=True)
class BrainConfig:
    min_relevance_score: int = MIN_RELEVANCE_SCORE
    max_targets: int = 12


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().lower()


def is_protected_relative_path(relative_path: str) -> bool:
    lowered = str(relative_path or "").replace("\\", "/").lower()
    return any(term in lowered for term in PROTECTED_PATH_TERMS)


def target_system_for_mission(mission_text: str) -> str:
    normalized = normalize_text(mission_text)
    scores: dict[str, int] = {}
    for system, keywords in TARGET_SYSTEM_KEYWORDS.items():
        score = sum(1 for keyword in keywords if keyword in normalized)
        if score:
            scores[system] = score
    if not scores:
        return "unknown"
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))[0][0]


def requested_action_for_mission(mission_text: str) -> str:
    normalized = normalize_text(mission_text)
    if any(word in normalized for word in ("analyze", "scan", "audit", "assess")):
        return "analyze"
    if any(word in normalized for word in ("plan", "proposal", "architecture", "blueprint")):
        return "plan"
    if any(word in normalized for word in ("fix", "repair", "resolve")):
        return "fix"
    if any(word in normalized for word in ("build", "implement", "create", "add", "update", "replace")):
        return "implement"
    return "unknown"


def parse_mission(mission_text: str) -> dict[str, Any]:
    normalized = normalize_text(mission_text)
    planning_only = any(phrase in normalized for phrase in PLANNING_ONLY_PHRASES)
    matched_types = [
        mission_type
        for mission_type, keywords in MISSION_CLASSIFIERS.items()
        if any(keyword in normalized for keyword in keywords)
    ]
    target_system = target_system_for_mission(mission_text)
    requested_action = requested_action_for_mission(mission_text)
    if planning_only:
        mission_type = "planning-only"
        proposal_type = "planning-report"
        diff_allowed = False
    elif matched_types:
        mission_type = matched_types[0]
        proposal_type = "implementation" if mission_type in {"bug-fix", "ui-change", "database-migration", "code-implementation"} else "report"
        diff_allowed = proposal_type == "implementation"
    else:
        mission_type = "code-implementation"
        proposal_type = "implementation"
        diff_allowed = True
    return {
        "missionType": mission_type,
        "missionCategory": "architecture-plan" if mission_type == "planning-only" else mission_type,
        "proposalType": proposal_type,
        "planningOnly": bool(planning_only),
        "diffAllowed": bool(diff_allowed),
        "targetSystem": target_system,
        "requestedAction": requested_action,
        "requiredApproval": bool(diff_allowed),
        "requiresApproval": bool(diff_allowed),
        "safetyLevel": "report-only" if planning_only else ("approval-gated" if diff_allowed else "review-only"),
        "allowedOutputType": "structured planning report" if planning_only else ("unified diff" if diff_allowed else "repository report"),
        "matchedTypes": matched_types,
    }


def mission_keywords(mission_text: str, target_system: str | None = None) -> list[str]:
    normalized = normalize_text(mission_text)
    keywords = set(re.findall(r"[a-z][a-z0-9_-]{2,}", normalized))
    system = target_system or target_system_for_mission(mission_text)
    if system == "communications" or any(word in keywords for word in ("messenger", "messages", "conversation", "conversations", "chat", "rooms", "groups", "communications")):
        keywords.update(COMMUNICATION_KEYWORDS)
    elif system in TARGET_SYSTEM_KEYWORDS:
        keywords.update(TARGET_SYSTEM_KEYWORDS[system])
    return sorted(keywords)


def scan_file_candidates(scan: dict[str, Any], target_system: str = "unknown") -> list[str]:
    ordered: list[str] = []
    repository_map = scan.get("repositoryMap") or {}
    collections = (
        "pythonFiles",
        "templates",
        "templateFiles",
        "jsFiles",
        "cssFiles",
        "auditScripts",
        "staticAssets",
        "htmlFiles",
        "reactFiles",
        "vueFiles",
        "scripts",
    )
    for collection in collections:
        for value in scan.get(collection) or repository_map.get(collection) or []:
            rel = str(value or "")
            if not rel or rel in ordered:
                continue
            if should_block_target(rel, target_system):
                continue
            ordered.append(rel)
    for item in scan.get("tree") or scan.get("folderTree") or []:
        if str(item.get("type") or "") != "file":
            continue
        rel = str(item.get("path") or "")
        if rel and rel not in ordered and not should_block_target(rel, target_system):
            ordered.append(rel)
    return ordered


def should_block_target(relative_path: str, target_system: str = "unknown") -> bool:
    rel = str(relative_path or "").replace("\\", "/")
    if is_protected_relative_path(rel):
        return True
    if rel in RISKY_FALLBACK_FILES and target_system != "offline-pwa":
        return True
    return False


def score_file(
    workspace: Path | None,
    relative_path: str,
    keywords: list[str],
    target_system: str = "unknown",
    safe_read: Callable[[Path], str] | None = None,
) -> tuple[int, list[str]]:
    rel = str(relative_path or "").replace("\\", "/")
    lowered = rel.lower()
    if should_block_target(rel, target_system):
        return 0, ["blocked: protected or unrelated fallback file"]
    score = 0
    reasons: list[str] = []
    for keyword in keywords:
        if keyword and keyword in lowered:
            score += 10
            reasons.append(f"path contains {keyword}")
    if rel == "bot.py":
        score += 4
        reasons.append("main Flask routes and data helpers live in bot.py")
    if rel.startswith("pulse_communications_v2/"):
        score += 8
        reasons.append("Pulse Communications 2.0 module")
    if rel.startswith("scripts/") and "audit" in lowered:
        score += 3
        reasons.append("audit/validation script")
    if workspace and safe_read:
        path = workspace / rel
        if path.exists() and path.is_file():
            try:
                text = safe_read(path).lower()
            except Exception:
                text = ""
            hits = [keyword for keyword in keywords if keyword and keyword in text]
            if hits:
                score += min(30, len(hits) * 3)
                reasons.append("content references " + ", ".join(hits[:5]))
    return score, reasons[:6]


def select_repository_files(
    workspace: Path | None,
    mission_text: str,
    scan: dict[str, Any],
    file_metadata: dict[str, Any] | None = None,
    knowledge_graph: dict[str, Any] | None = None,
    safe_read: Callable[[Path], str] | None = None,
    config: BrainConfig | None = None,
) -> dict[str, Any]:
    config = config or BrainConfig()
    classification = parse_mission(mission_text)
    target_system = classification["targetSystem"]
    keywords = mission_keywords(mission_text, target_system)
    ranked: list[dict[str, Any]] = []
    for rel in scan_file_candidates(scan, target_system):
        score, reasons = score_file(workspace, rel, keywords, target_system, safe_read=safe_read)
        graph_boost = 0
        if knowledge_graph and rel in (knowledge_graph.get("importantFiles") or []):
            graph_boost = 6
            reasons.append("knowledge graph marks file important")
        score += graph_boost
        if score >= config.min_relevance_score:
            ranked.append({"path": rel, "score": score, "why": reasons or ["matched mission keywords"], "targetSystem": target_system})
    ranked.sort(key=lambda item: (-int(item["score"]), str(item["path"])))
    selected = ranked[: config.max_targets]
    return {
        "classification": classification,
        "targetFiles": [item["path"] for item in selected],
        "relevanceScores": selected,
        "targetFileReasons": selected,
        "relevantFilesFound": len(selected),
        "reasoningReport": build_reasoning_report(classification, selected, file_metadata or {}, knowledge_graph or {}),
        "blockedFiles": [path for path in RISKY_FALLBACK_FILES if should_block_target(path, target_system)],
    }


def build_reasoning_report(classification: dict[str, Any], targets: list[dict[str, Any]], file_metadata: dict[str, Any], knowledge_graph: dict[str, Any]) -> str:
    lines = [
        "UNDX Brain Reasoning",
        f"Mission Type: {classification.get('missionType')}",
        f"Target System: {classification.get('targetSystem')}",
        f"Diff Allowed: {classification.get('diffAllowed')}",
        f"Planning Only: {classification.get('planningOnly')}",
        f"Relevant Files Found: {len(targets)}",
    ]
    for item in targets:
        lines.append(f"- {item['path']} score={item.get('score', 0)} because {'; '.join(item.get('why') or [])}")
    if file_metadata:
        lines.append(f"Metadata inputs: {len(file_metadata)}")
    if knowledge_graph:
        lines.append("Knowledge graph input available.")
    return "\n".join(lines)


def planning_sections(targets: list[dict[str, Any]], scan: dict[str, Any], classification: dict[str, Any]) -> list[tuple[str, list[str]]]:
    target_paths = [item["path"] for item in targets]
    target_lines = [
        f"- {item['path']} — score {item.get('score', 0)} — {'; '.join(item.get('why') or ['selected by repository relevance'])}"
        for item in targets
    ] or ["- No safe target files found. Proposal requires repository analysis."]
    if classification.get("targetSystem") == "communications":
        preserve = [
            "- Existing direct message routes and data",
            "- Existing rooms/groups behavior",
            "- Pulse feed, UNDX, Wallet Guardian, admin, auth, and premium routes",
        ]
        replace = ["- Legacy communications UI/API only after v2 passes audits"]
        create = [
            "- pulse_communications_v2/models.py",
            "- pulse_communications_v2/service.py",
            "- pulse_communications_v2/routes.py",
            "- pulse_communications_v2/permissions.py",
        ]
        map_title = "Repository Communications Map"
    else:
        preserve = ["- Existing production behavior outside the target system", "- Authentication/session boundaries", "- Existing data"]
        replace = ["- Only relevant legacy code after validation"]
        create = ["- New files only when they match the target system and pass approval"]
        map_title = "Repository Target System Map"
    return [
        ("Mission Classification", [f"Mission Type: {classification.get('missionType')}", f"Target System: {classification.get('targetSystem')}", f"Requested Action: {classification.get('requestedAction')}", f"Proposal Type: {classification.get('proposalType')}", f"Diff Allowed: {classification.get('diffAllowed')}", "Diff Generation: disabled for planning-only mission"]),
        (map_title, [f"Files scanned: {scan.get('fileCount', 0)}", "Relevant targets: " + (", ".join(target_paths) or "No safe target files found. Proposal requires repository analysis.")]),
        ("Problems Found", ["- Large planning missions must not become random file rewrites.", "- Fallback HTML rewrites are blocked unless the mission targets offline/PWA.", "- Implementation requires relevant files and approval."]),
        ("Exact Candidate Files And Relevance Scores", target_lines),
        ("Target Files", [f"- {path}" for path in target_paths] or ["- No safe target files found. Proposal requires repository analysis."]),
        ("Files To Preserve", preserve),
        ("Files To Replace", replace),
        ("New V2 Files To Create", create),
        ("Database Migration Strategy", ["- Add new tables with target-specific prefixes only.", "- Backfill through compatibility bridges after reads are verified.", "- Keep destructive changes out of the first patch."]),
        ("First Safe Implementation Patch", ["- Start with a small scaffold or report update.", "- Do not write files for planning-only proposals.", "- Require approval for any implementation diff."]),
        ("Validation Plan", ["- Python compile", "- JavaScript parse", "- UNDX audits", "- Target system audits", "- Site functional audit", "- git diff --check"]),
        ("Rollback Plan", ["- Keep legacy routes active.", "- Keep new system behind explicit routes or flags until validated.", "- Revert only the approved patch files if validation fails."]),
        ("Approval Gate", ["- Planning-only proposals do not require write approval.", "- Execution proposals require explicit human approval before file writes."]),
    ]


def generate_planning_report(mission_text: str, workspace_name: str, scan: dict[str, Any], selection: dict[str, Any]) -> str:
    classification = selection.get("classification") or parse_mission(mission_text)
    lines = [f"# UNDX Planning Report: {mission_text.strip()[:120]}", "", f"Repository: {workspace_name or scan.get('workspaceName') or scan.get('repositoryName') or 'workspace'}", ""]
    for title, values in planning_sections(selection.get("relevanceScores") or [], scan, classification):
        lines.extend([f"## {title}", *values, ""])
    review = multi_agent_review(mission_text, classification, selection.get("relevanceScores") or [])
    lines.extend(["## Multi-Agent Review", *review_to_lines(review), ""])
    return "\n".join(lines).strip() + "\n"


def review_to_lines(review: dict[str, Any]) -> list[str]:
    lines = []
    for key in ("architectureReview", "implementationReview", "testingReview", "securityReview", "documentationReview", "researchReview"):
        item = review.get(key) or {}
        lines.append(f"- {item.get('agent', key)}: {item.get('recommendation')} — {item.get('summary')}")
    lines.append(f"- Combined Recommendation: {review.get('combinedRecommendation')}")
    lines.append(f"- Risk Assessment: {review.get('riskAssessment')}")
    return lines


def multi_agent_review(mission_text: str, classification: dict[str, Any], targets: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    planning = bool(classification.get("planningOnly"))
    target_count = len(targets or [])
    diff_allowed = bool(classification.get("diffAllowed"))
    risk = "low" if planning else ("medium" if target_count and diff_allowed else "high")
    recommendation = "Generate planning report only." if planning else ("Proceed with approval-gated diff." if target_count else "Do not generate diff until relevant files are found.")
    return {
        "agents": ["Architect", "Builder", "Testing", "Security", "Documentation", "Research"],
        "architectureReview": {"agent": "Architect", "recommendation": recommendation, "summary": f"Target system is {classification.get('targetSystem')}; keep changes scoped to relevant files."},
        "implementationReview": {"agent": "Builder", "recommendation": "Use compatibility bridges before replacement.", "summary": f"{target_count} relevant files selected."},
        "testingReview": {"agent": "Testing", "recommendation": "Run focused and site-wide audits.", "summary": "Planning-only diffs must remain empty; execution diffs need regression audits."},
        "securityReview": {"agent": "Security", "recommendation": "Block protected files and raw mission injection.", "summary": "Secrets, offline fallback abuse, and low-relevance targets are blocked."},
        "documentationReview": {"agent": "Documentation", "recommendation": "Include rollback and validation notes.", "summary": "Planning reports include preservation, migration, and approval gates."},
        "researchReview": {"agent": "Research", "recommendation": "Use repository evidence over generic assumptions.", "summary": "Target selection is based on path/content relevance."},
        "combinedRecommendation": recommendation,
        "riskAssessment": risk,
    }


def generate_planning_proposal(mission_text: str, workspace_name: str, scan: dict[str, Any], selection: dict[str, Any]) -> dict[str, Any]:
    classification = selection.get("classification") or parse_mission(mission_text)
    report = generate_planning_report(mission_text, workspace_name, scan, selection)
    proposal = {
        "ok": True,
        "proposalType": "planning-report",
        "missionType": classification.get("missionType"),
        "missionCategory": classification.get("missionCategory"),
        "targetSystem": classification.get("targetSystem"),
        "requestedAction": classification.get("requestedAction"),
        "planningOnly": True,
        "diffAllowed": False,
        "requiredApproval": False,
        "requiresApproval": False,
        "targetFile": "",
        "targetFiles": selection.get("targetFiles") or [],
        "targetFileReasons": selection.get("targetFileReasons") or [],
        "relevanceScores": selection.get("relevanceScores") or [],
        "relevantFilesFound": selection.get("relevantFilesFound") or 0,
        "reasoningReport": selection.get("reasoningReport") or "",
        "report": report,
        "diff": "",
        "changes": [],
        "message": "Planning report generated. No files written.",
        "summary": "Planning-only architecture report generated by UNDX Brain Layer.",
        "diffGenerationSafe": False,
        "diffWarning": "Diff generation disabled because this mission was classified as planning-only.",
        "multiAgentReview": multi_agent_review(mission_text, classification, selection.get("relevanceScores") or []),
        "brainLayer": {"enabled": True, "version": "live-autonomous-v1", "generatedAt": now_iso()},
    }
    return enforce_safety(proposal, mission_text, classification)


def generate_execution_metadata(mission_text: str, selection: dict[str, Any]) -> dict[str, Any]:
    classification = selection.get("classification") or parse_mission(mission_text)
    targets = selection.get("relevanceScores") or []
    return {
        "multiAgentReview": multi_agent_review(mission_text, classification, targets),
        "brainLayer": {
            "enabled": True,
            "version": "live-autonomous-v1",
            "generatedAt": now_iso(),
            "reasoningReport": selection.get("reasoningReport") or "",
        },
        "requiredApproval": bool(classification.get("diffAllowed")),
        "diffGenerationSafe": bool(classification.get("diffAllowed") and targets),
    }


def enforce_safety(proposal: dict[str, Any], mission_text: str, classification: dict[str, Any] | None = None) -> dict[str, Any]:
    classification = classification or parse_mission(mission_text)
    diff = str(proposal.get("diff") or "")
    changes = proposal.get("changes") or []
    target_files = [str(path) for path in proposal.get("targetFiles") or []]
    if classification.get("planningOnly") and (diff.strip() or changes):
        raise BrainSafetyError("Safety guard blocked diff generation for a planning-only mission.")
    if classification.get("targetSystem") != "offline-pwa" and any(path in RISKY_FALLBACK_FILES for path in target_files):
        raise BrainSafetyError("Safety guard blocked static/offline.html for a non-offline mission.")
    if any(is_protected_relative_path(path) for path in target_files):
        raise BrainSafetyError("Safety guard blocked protected file target.")
    if diff and mission_text.strip() and mission_text.strip() in diff:
        raise BrainSafetyError("Safety guard blocked raw mission directive text in generated diff.")
    for item in proposal.get("targetFileReasons") or []:
        path = str(item.get("path") or "")
        if should_block_target(path, classification.get("targetSystem") or "unknown"):
            raise BrainSafetyError("Safety guard blocked protected or unrelated target file.")
        if int(item.get("score") or 0) < MIN_RELEVANCE_SCORE and classification.get("targetSystem") not in {"homepage", "offline-pwa"}:
            raise BrainSafetyError("Safety guard blocked low-relevance target file.")
    for change in changes:
        path = str(change.get("path") or "")
        if should_block_target(path, classification.get("targetSystem") or "unknown"):
            raise BrainSafetyError("Safety guard blocked protected or unrelated file rewrite.")
    return proposal


def analyze_mission(
    workspace: Path | None,
    mission_text: str,
    scan: dict[str, Any],
    file_metadata: dict[str, Any] | None = None,
    knowledge_graph: dict[str, Any] | None = None,
    safe_read: Callable[[Path], str] | None = None,
) -> dict[str, Any]:
    selection = select_repository_files(
        workspace,
        mission_text,
        scan,
        file_metadata=file_metadata,
        knowledge_graph=knowledge_graph,
        safe_read=safe_read,
    )
    classification = selection["classification"]
    selection["multiAgentReview"] = multi_agent_review(mission_text, classification, selection.get("relevanceScores") or [])
    selection["brainLayer"] = {"enabled": True, "version": "live-autonomous-v1", "generatedAt": now_iso()}
    return selection
