#!/usr/bin/env python3
"""Audit that UNDX lives only inside Pulse Premium."""

from __future__ import annotations

from pathlib import Path
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402


AUDIT_USER_ID = 1


def require(condition, message, detail=""):
    if not condition:
        raise AssertionError(f"{message}: {detail}")
    print(f"ok - {message}")


def ensure_audit_user():
    conn = bot.db()
    cur = conn.cursor()
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    cur.execute(
        """
        INSERT INTO users (user_id, username, display_name, email, account_status, created_at, updated_at)
        VALUES (?, 'undx-audit', 'UNDX Audit', 'undx-audit@example.com', 'active', ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET account_status='active', updated_at=excluded.updated_at
        """,
        (AUDIT_USER_ID, now, now),
    )
    conn.commit()
    conn.close()


def login_test_user(client):
    with client.session_transaction() as sess:
        sess["account_user_id"] = AUDIT_USER_ID


def page_html(client, path):
    response = client.get(path)
    require(response.status_code == 200, f"{path} returns 200", response.status_code)
    return response.get_data(as_text=True)


def main():
    bot.init_db()
    ensure_audit_user()
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    router_source = (ROOT / "undx_router.py").read_text(encoding="utf-8")
    worker_source = (ROOT / "undx_worker.py").read_text(encoding="utf-8")
    feed_css = (ROOT / "static/css/pulse_desktop_feed.css").read_text(encoding="utf-8")

    require("pulse_undx_core_homepage_html" not in source, "UNDX feed helper removed")
    require("__UNDX_CORE__" not in source, "Pulse feed UNDX placeholder removed")
    require(".replace(\"__UNDX_CORE__\"" not in source, "Pulse feed no longer renders UNDX")
    require("pulse-undx-core" not in feed_css, "feed stylesheet no longer contains UNDX card styles")

    client = bot.webhook_app.test_client()
    login_test_user(client)
    feed_html = page_html(client, "/pulse")
    premium_html = page_html(client, "/pulse/premium")
    undx_html = page_html(client, "/pulse/premium/undx")

    require("data-undx-premium-entry" not in feed_html, "UNDX entry absent from Pulse feed")
    require("/pulse/premium/undx" not in feed_html, "UNDX premium route absent from Pulse feed")
    require("Enter the Unknown Destination" not in feed_html, "UNDX Command Center hero absent from Pulse feed")
    require("Builder Intelligence Console" not in feed_html, "UNDX builder console absent from Pulse feed")
    require("Generate Mission Blueprint" not in feed_html, "UNDX blueprint action absent from Pulse feed")
    require("Mission Memory" not in feed_html, "UNDX mission memory absent from Pulse feed")
    require("Clear Mission Memory" not in feed_html, "UNDX clear memory action absent from Pulse feed")
    require("Replay Mission" not in feed_html, "UNDX replay action absent from Pulse feed")
    require("View Evolution" not in feed_html, "UNDX evolution action absent from Pulse feed")
    require("Mission Evolution" not in feed_html, "UNDX mission evolution absent from Pulse feed")
    require("UNDX Agent Council" not in feed_html, "UNDX agent council absent from Pulse feed")
    require("Run Agent Council" not in feed_html, "UNDX agent council action absent from Pulse feed")
    require("Architect Agent" not in feed_html, "UNDX architect agent absent from Pulse feed")
    require("Security Agent" not in feed_html, "UNDX security agent absent from Pulse feed")
    require("Optimization Agent" not in feed_html, "UNDX optimization agent absent from Pulse feed")
    require("Rapid Response Agent" not in feed_html, "UNDX rapid response agent absent from Pulse feed")
    require("Provider selected" not in feed_html, "UNDX council provider routing absent from Pulse feed")
    require("Council Mode: Multi-Agent" not in feed_html, "UNDX council mode absent from Pulse feed")
    require("Save Council Output to Mission Memory" not in feed_html, "UNDX council save action absent from Pulse feed")
    require("UNDX Chat Interface" not in feed_html, "UNDX chat interface absent from Pulse feed")
    require("Send Directive" not in feed_html, "UNDX chat send action absent from Pulse feed")
    require("Clear Chat History" not in feed_html, "UNDX clear chat action absent from Pulse feed")
    require("Chat Status: Online" not in feed_html, "UNDX chat status absent from Pulse feed")
    require("Intelligence Bridge: OpenAI" not in feed_html, "UNDX OpenAI bridge status absent from Pulse feed")
    require("Mission Protocol: Active" not in feed_html, "UNDX mission protocol absent from Pulse feed")
    require("UNDX Project Creator" not in feed_html, "UNDX project creator absent from Pulse feed")
    require("Create Project From Mission" not in feed_html, "UNDX project action absent from Pulse feed")
    require("Project Registry" not in feed_html, "UNDX project registry absent from Pulse feed")
    require("Project Core: Active" not in feed_html, "UNDX project core status absent from Pulse feed")
    require("Registry Status: Online" not in feed_html, "UNDX registry status absent from Pulse feed")
    require("UNDX Project Workspace" not in feed_html, "UNDX project workspace absent from Pulse feed")
    require("Project Tasks" not in feed_html, "UNDX project tasks absent from Pulse feed")
    require("Project Milestones" not in feed_html, "UNDX project milestones absent from Pulse feed")
    require("Project Memory" not in feed_html, "UNDX project memory absent from Pulse feed")
    require("Generate Build Directive" not in feed_html, "UNDX build directive action absent from Pulse feed")
    require("Send Directive To UNDX Chat" not in feed_html, "UNDX directive chat handoff absent from Pulse feed")
    require("UNDX Repository Connection Planner" not in feed_html, "UNDX repository planner absent from Pulse feed")
    require("Trusted Repository Plans" not in feed_html, "UNDX repository plans absent from Pulse feed")
    require("Repository Access Protocol" not in feed_html, "UNDX repository protocol absent from Pulse feed")
    require("Repository Core: Planning" not in feed_html, "UNDX repository planning status absent from Pulse feed")
    require("File Access: Disabled" not in feed_html, "UNDX file access status absent from Pulse feed")
    require("Execution Access: Disabled" not in feed_html, "UNDX execution access status absent from Pulse feed")
    require("UNDX Task Execution Planner" not in feed_html, "UNDX task execution planner absent from Pulse feed")
    require("Generate Execution Plan" not in feed_html, "UNDX execution action absent from Pulse feed")
    require("Execution Plan Registry" not in feed_html, "UNDX execution registry absent from Pulse feed")
    require("Execution Core: Planning" not in feed_html, "UNDX execution core status absent from Pulse feed")
    require("Command Execution: Disabled" not in feed_html, "UNDX command execution status absent from Pulse feed")
    require("Approval Protocol: Required" not in feed_html, "UNDX approval protocol status absent from Pulse feed")
    require("Generate Codex-Style Directive" not in feed_html, "UNDX Codex directive action absent from Pulse feed")
    require("Copy Codex-Style Directive" not in feed_html, "UNDX Codex copy action absent from Pulse feed")
    require("UNDX Approval Protocol" not in feed_html, "UNDX approval protocol panel absent from Pulse feed")
    require("Safe Action Gates" not in feed_html, "UNDX safe action gates absent from Pulse feed")
    require("Approval History" not in feed_html, "UNDX approval history absent from Pulse feed")
    require("UNDX Safety Constitution" not in feed_html, "UNDX safety constitution absent from Pulse feed")
    require("Approval Core: Active" not in feed_html, "UNDX approval core status absent from Pulse feed")
    require("Trust Protocol: Active" not in feed_html, "UNDX trust protocol status absent from Pulse feed")
    require("Simulate Approval Request" not in feed_html, "UNDX approval simulator absent from Pulse feed")
    require("UNDX Repository Intelligence" not in feed_html, "UNDX repository intelligence absent from Pulse feed")
    require("Generate Repository Intelligence Profile" not in feed_html, "UNDX repository intelligence action absent from Pulse feed")
    require("Repository Intelligence Registry" not in feed_html, "UNDX repository intelligence registry absent from Pulse feed")
    require("Repository Intelligence Boundary" not in feed_html, "UNDX repository intelligence boundary absent from Pulse feed")
    require("Repository Intelligence: Active" not in feed_html, "UNDX repository intelligence status absent from Pulse feed")
    require("Profile Mode: Conceptual" not in feed_html, "UNDX repository profile mode absent from Pulse feed")
    require("UNDX Read-Only Access Blueprint" not in feed_html, "UNDX read-only access blueprint absent from Pulse feed")
    require("Future Access Workflow" not in feed_html, "UNDX future access workflow absent from Pulse feed")
    require("Access Request Simulator" not in feed_html, "UNDX access request simulator absent from Pulse feed")
    require("Secret Protection Layer" not in feed_html, "UNDX secret protection layer absent from Pulse feed")
    require("Future Trust Roadmap" not in feed_html, "UNDX future trust roadmap absent from Pulse feed")
    require("Read Access Core: Blueprint" not in feed_html, "UNDX read access blueprint status absent from Pulse feed")
    require("Secret Access: Disabled" not in feed_html, "UNDX secret access disabled status absent from Pulse feed")
    require("UNDX Read-Only Preview Gateway" not in feed_html, "UNDX read-only preview gateway absent from Pulse feed")
    require("Preview Gateway Workflow" not in feed_html, "UNDX preview gateway workflow absent from Pulse feed")
    require("Preview Manifest Simulator" not in feed_html, "UNDX preview manifest simulator absent from Pulse feed")
    require("Preview Manifest Registry" not in feed_html, "UNDX preview manifest registry absent from Pulse feed")
    require("Protected Resource Detection" not in feed_html, "UNDX protected resource detection absent from Pulse feed")
    require("Future Desktop Connector Vision" not in feed_html, "UNDX desktop connector vision absent from Pulse feed")
    require("Preview Gateway: Active" not in feed_html, "UNDX preview gateway status absent from Pulse feed")
    require("Read Access: Disabled" not in feed_html, "UNDX read access disabled status absent from Pulse feed")
    require("UNDX Core: Build Beyond the Known" not in feed_html, "old UNDX feed headline absent")

    routes = {str(rule) for rule in bot.webhook_app.url_map.iter_rules()}
    require("/api/undx/chat" in routes, "/api/undx/chat route exists")
    require("/api/undx/agent-council" in routes, "/api/undx/agent-council route exists")
    require('logging.info("OpenAI API key configured: %s", "yes" if api_key else "no")' in source, "UNDX logs safe OpenAI key configuration status")
    require("undx_router.route_undx_request" in source, "UNDX chat route uses Intelligence Router")
    require("undx_router.council_agent_provider_plan" in source, "UNDX Agent Council uses Intelligence Router provider plan")
    require("UNDX Intelligence Router" in router_source, "UNDX Intelligence Router module exists")
    for token in [
        "OPENAI_API_KEY",
        "CLAUDE_AI_API",
        "Gemini_AI_API",
        "DEEPSEEK_AI_API",
        "GROQ_AI_API",
        "UNDX_ROUTER_ENABLED",
        "UNDX_MULTI_MODEL_MODE",
        "UNDX_DEFAULT_AI_PROVIDER",
        "fallback_provider",
        "COUNCIL_AGENT_PROVIDER_MAP",
        "council_agent_provider_plan",
    ]:
        require(token in router_source, f"UNDX router contains {token}")
    require("coinpilotx-undx-worker" in worker_source, "UNDX worker targets Railway service name")

    council_response = client.post("/api/undx/agent-council", json={"mission": "Optimize repository analysis with fast research"})
    require(council_response.status_code == 200, "/api/undx/agent-council returns 200", council_response.status_code)
    council_payload = council_response.get_json() or {}
    council_agents = council_payload.get("agents") or []
    require(len(council_agents) == 5, "UNDX Agent Council router returns five agents", len(council_agents))
    for token in ["selected_provider_label", "provider_status", "fallback_status"]:
        require(all(token in agent for agent in council_agents), f"UNDX Agent Council router includes {token}")

    for token in [
        "data-undx-premium-entry",
        "Enter UNDX",
        "Open the Unknown Destination intelligence layer.",
        "UNDX Core",
        "/pulse/premium/undx",
    ]:
        require(token in premium_html, f"Pulse Premium contains {token}")

    for token in [
        "data-undx-core-page",
        "UNDX Core",
        "Unknown Destination X — the premium intelligence layer designed to help CoinPilotXAI build, analyze, secure, and evolve.",
        "Enter the Unknown Destination",
        "Premium Intelligence Layer",
        "Initialize UNDX Core",
        "View Core Modules",
        "Builder Intelligence Console",
        "Describe what you want CoinPilotXAI to build next, and UNDX will prepare the mission blueprint.",
        "Example: Build a wallet risk analyzer, improve the premium dashboard, or create an AI scam detection module...",
        "Generate Mission Blueprint",
        "Console Status: Online",
        "Mode: Builder Intelligence",
        "Access: Premium",
        "Phase: 3",
        "Enter a mission before initializing UNDX.",
        "Mission Name",
        "Objective",
        "Suggested Modules",
        "Build Steps",
        "Security Notes",
        "Next Action",
        "Mission Memory",
        "UNDX remembers generated missions so future build phases can learn from previous objectives, patterns, and system evolution.",
        "Memory Core: Active",
        "Stored Missions",
        "Clear Mission Memory",
        "No missions stored yet. Generate a blueprint to initialize UNDX memory.",
        "undxMissionMemory",
        "Replay Mission",
        "View Evolution",
        "Mission replay loaded into Builder Intelligence Console.",
        "Mission Evolution",
        "Select a stored mission to view its evolution path.",
        "Selected Mission Name",
        "Original Objective",
        "Suggested Next Phase",
        "Recommended Modules",
        "Risk Notes",
        "Expansion Path",
        "UNDX Evolution Score",
        "Use as Next Blueprint",
        "Evolve this mission into the next build phase:",
        "UNDX Agent Council",
        "Five routed intelligence agents evaluate each mission through the UNDX Intelligence Router before CoinPilotXAI enters the next build phase.",
        "Run Agent Council",
        "Architect Agent",
        "Research Agent",
        "Builder Agent",
        "Optimization Agent",
        "Rapid Response Agent",
        "Provider selected",
        "Provider status",
        "Fallback status",
        "Claude",
        "Gemini",
        "OpenAI",
        "DeepSeek",
        "Groq",
        "/api/undx/agent-council",
        "Council Mode: Multi-Agent",
        "Active Agents: <strong>5</strong>",
        "Council Phase: <strong>Phase 6</strong>",
        "Save Council Output to Mission Memory",
        "Enter or select a mission before running the Agent Council.",
        "Mission analyzed",
        "Strategic priority",
        "Build complexity",
        "Recommended next action",
        "Council status: Complete",
        "Council Output",
        "UNDX Chat Interface",
        "Chat with UNDX and issue real mission directives powered by CoinPilotXAI intelligence.",
        "UNDX Core online. OpenAI intelligence bridge active. What mission should CoinPilotXAI evolve next?",
        "Describe a project, feature, improvement, automation, or mission...",
        "Send Directive",
        "Clear Chat History",
        "/api/undx/chat",
        "Chat Status: Online",
        "Intelligence Bridge: OpenAI",
        "Phase: 7",
        "Mission Protocol: Active",
        "undxChatMemory",
        "UNDX OpenAI bridge error:",
        "UNDX is processing the mission directive through the OpenAI intelligence bridge...",
        "Enter a directive before contacting UNDX.",
        "Send To Builder Console",
        "Save To Mission Memory",
        "UNDX Chat Mission",
        "UNDX Project Creator",
        "Convert missions into structured projects managed by UNDX.",
        "Create Project From Mission",
        "Project Source",
        "Current Builder Intelligence Console mission",
        "Selected Mission Memory item",
        "Latest UNDX Chat response",
        "Project Name",
        "Project Type",
        "Project Objective",
        "Priority",
        "Crypto Intelligence",
        "Security",
        "AI Systems",
        "Automation",
        "Research",
        "Product Experience",
        "Infrastructure",
        "Other",
        "Project Registry",
        "UNDX Project Workspace",
        "Project Core: Active",
        "Registry Status: <strong>Online</strong>",
        "Phase: <strong>8</strong>",
        "Projects Created",
        "Active Projects",
        "Completed Projects",
        "Total Tasks",
        "Completed Tasks",
        "Memory Notes",
        "Completed Milestones",
        "Planning",
        "Open Project",
        "UNDX-",
        "undxProjectRegistry",
        "Store Project",
        "Project Summary",
        "Suggested Milestones",
        "Recommended Modules",
        "No projects registered yet. Create a project to activate the UNDX registry.",
        "Select a project to load its UNDX workspace.",
        "Project ID",
        "Project Tasks",
        "Task title",
        "Task status",
        "Todo",
        "In Progress",
        "Done",
        "Add Task",
        "Mark In Progress",
        "Mark Done",
        "Delete Task",
        "Project Milestones",
        "Blueprint Created",
        "Agent Council Review",
        "Build Plan Ready",
        "Implementation Pending",
        "Pending",
        "Complete",
        "Mark Milestone Complete",
        "Project Memory",
        "UNDX stores important notes, decisions, and build context for this project.",
        "Note text",
        "Add Memory Note",
        "Delete Note",
        "Build Directive",
        "Generate Build Directive",
        "Copy Build Directive",
        "Send Directive To UNDX Chat",
        "UNDX Build Directive",
        "Recommended Next Action",
        "UNDX Repository Connection Planner",
        "Prepare trusted codebases for future UNDX analysis, file access, and build execution.",
        "Repository / Folder Name",
        "Repository Type",
        "Local Folder",
        "GitHub Repository",
        "Railway Project",
        "Vercel Project",
        "Planned Path or URL",
        "Access Level",
        "Planning Only",
        "Read-Only Future Access",
        "Read + Suggest Changes",
        "Read + Edit With Approval",
        "Full Build Agent With Approval",
        "Purpose / Notes",
        "Register Repository Plan",
        "undxRepositoryPlans",
        "Trusted Repository Plans",
        "Status: ${plan.status || 'Planned'}",
        "Attach To Project",
        "Delete Plan",
        "Repository Access Protocol",
        "UNDX will only access code folders after explicit permission. Future phases must use read-only previews, file diffs, backups, and approval checkpoints before edits or commands.",
        "Explicit permission required",
        "Read-only before edit access",
        "Show diffs before changes",
        "Backup before destructive changes",
        "Confirm before terminal commands",
        "Never expose secrets",
        "Repository Core: Planning",
        "File Access: Disabled",
        "Execution Access: Disabled",
        "Phase: 10",
        "Linked Repository Plans",
        "UNDX Task Execution Planner",
        "Convert missions, projects, and repository plans into structured execution tasks before UNDX gains build access.",
        "Generate Execution Plan",
        "Execution Plan Sources",
        "Current Builder Intelligence Console text",
        "Latest UNDX Chat response",
        "Selected Mission Memory item",
        "Selected/open Project Workspace",
        "Linked Repository Plan if attached to the selected project",
        "Add a mission, chat directive, project, or repository plan before generating an execution plan.",
        "Execution Plan ID",
        "EXEC-UNDX-",
        "Source Type",
        "Required Context",
        "Task Breakdown",
        "Understand the mission",
        "Review existing project context",
        "Identify files or modules likely affected",
        "Draft implementation plan",
        "Prepare safe build directive",
        "Suggested Files/Folders to Review",
        "Approval Checkpoints",
        "Approve before file reading",
        "Approve before edits",
        "Approve before terminal commands",
        "Approve before Git commits",
        "Approve before deployment",
        "Risk Level",
        "Rollback Strategy",
        "Create Git checkpoint before changes",
        "Show diff before commit",
        "Keep backup of modified files",
        "Provide manual rollback instructions",
        "Execution Plan Registry",
        "undxExecutionPlans",
        "Open Plan",
        "Generate Codex-Style Directive",
        "Copy Codex-Style Directive",
        "Git commands:",
        "Execution Core: Planning",
        "Command Execution: Disabled",
        "Approval Protocol: Required",
        "Phase: 11",
        "Linked Execution Plans",
        "UNDX Approval Protocol",
        "All future repository access, file operations, code changes, terminal commands, Git actions, and deployments require explicit approval.",
        "Safe Action Gates",
        "Repository Read Access",
        "File Edit Access",
        "Terminal Command Access",
        "Git Commit Access",
        "Deployment Access",
        "Secret Management Access",
        "Status: ${gate.status || 'Disabled'}",
        "Permission Level: ${gate.permissionLevel || 'Always Require Approval'}",
        "Last Updated:",
        "Disabled",
        "Request Approval",
        "Allowed With Confirmation",
        "Always Require Approval",
        "Update Gate",
        "undxApprovalProtocol",
        "Approval History",
        "Action Requested",
        "Gate Type",
        "Decision",
        "Timestamp",
        "undxApprovalHistory",
        "Simulate Approval Request",
        "Read repository files",
        "Edit authentication module",
        "Run Git commit",
        "Deploy production update",
        "Risk Level",
        "Affected Area",
        "Approval Required",
        "Approve",
        "Reject",
        "Trust Score",
        "Approval Requests",
        "Approved",
        "Rejected",
        "UNDX Safety Constitution",
        "Always show diffs before edits",
        "Create backups before destructive changes",
        "Require approval before commands",
        "Require approval before deployment",
        "Preserve recoverability",
        "Preserve mission integrity",
        "Approval Core: Active",
        "Trust Protocol: Active",
        "Phase: 12",
        "Approval Status",
        "Required Gates",
        "UNDX Repository Intelligence",
        "Analyze planned codebases conceptually before UNDX receives file access or execution permissions.",
        "Generate Repository Intelligence Profile",
        "Profile Sources",
        "Selected Repository Plan",
        "Selected/open Project Workspace",
        "Linked Execution Plan",
        "Manual Repository Notes",
        "Describe the repo structure, stack, modules, risks, or goals...",
        "Add a repository plan, project, execution plan, or notes before generating a repository intelligence profile.",
        "Repository Intelligence Registry",
        "Repository Intelligence Profile Detail",
        "Profile ID",
        "RIP-UNDX-",
        "Repository / Project Name",
        "Repository Type",
        "Conceptual Stack Guess",
        "Core Purpose",
        "Likely Important Modules",
        "Possible Risks",
        "Suggested Improvements",
        "Recommended Next Inspection",
        "Required Approval Gates",
        "Confidence Level",
        "undxRepositoryIntelligenceProfiles",
        "Status: ${profile.status || 'Intelligence Profile'}",
        "Open Profile",
        "Generate Execution Plan From Profile",
        "Delete Profile",
        "Linked Repository Intelligence Profiles",
        "Repository Intelligence: Active",
        "Profile Mode: Conceptual",
        "Phase: 13",
        "Repository Intelligence Boundary",
        "UNDX is only analyzing provided planning context. It has not accessed repository files, local folders, GitHub content, secrets, terminals, or deployments.",
        "UNDX Read-Only Access Blueprint",
        "Design the future trust architecture for repository and local folder analysis.",
        "Future Access Workflow",
        "Step 1",
        "Repository registered",
        "Step 2",
        "Read-only access requested",
        "Step 3",
        "User approves request",
        "Step 4",
        "UNDX previews files",
        "Step 5",
        "UNDX generates analysis",
        "Step 6",
        "User reviews results",
        "Step 7",
        "Future edit permissions remain separate",
        "Access Request Simulator",
        "Requested Resource",
        "Reason",
        "Expected Outcome",
        "Generate Access Request",
        "Request ID",
        "ACCESS-UNDX-",
        "Approval Required",
        "Estimated Risk",
        "Protected Areas",
        ".env files",
        "API keys",
        "secrets",
        "tokens",
        "credentials",
        "production configuration",
        "Protected",
        "Future Read-Only Preview",
        "CoinPilotXAI",
        "bot.py",
        "static/js/main.js",
        "templates/pulse.html",
        "Preview Only",
        "Future Analysis Capabilities",
        "summarize repositories",
        "explain architecture",
        "identify risks",
        "suggest improvements",
        "map dependencies",
        "create build plans",
        "Future Restrictions",
        "edit files without approval",
        "run commands without approval",
        "commit without approval",
        "deploy without approval",
        "access secrets",
        "Secret Protection Layer",
        "Secrets always hidden.",
        "redact secrets",
        "avoid displaying credentials",
        "block protected values",
        "require additional approval for sensitive resources",
        "Read-Only Access Status",
        "Not Connected",
        "Access Blueprint Status",
        "Read Access Core: Blueprint",
        "Secret Access: Disabled",
        "Phase: 14",
        "Future Trust Roadmap",
        "Phase 14",
        "Blueprint",
        "Phase 15",
        "Read-only previews",
        "Phase 16",
        "Repository analysis",
        "Phase 17",
        "Diff generation",
        "Phase 18",
        "Edit proposals",
        "Phase 19",
        "Approved edits",
        "Phase 20",
        "Controlled execution",
        "undxReadOnlyAccessRequests",
        "UNDX Read-Only Preview Gateway",
        "Prepare the future workflow for safe repository previews and analysis.",
        "Preview Gateway Workflow",
        "Repository Registered",
        "Read Access Requested",
        "Approval Granted",
        "Manifest Generated",
        "File Preview Available",
        "Analysis Generated",
        "User Review",
        "Preview Manifest Simulator",
        "Repository Name",
        "Repository Type",
        "Planned Files",
        "Notes",
        "Generate Preview Manifest",
        "Manifest ID",
        "MANIFEST-UNDX-",
        "Estimated File Count",
        "Preview Status",
        "Protected Resources",
        "Approval Status",
        "README.md",
        "requirements.txt",
        "Protected Resource Detection",
        ".env",
        "credentials.json",
        "Hidden",
        "Future Analysis Output",
        "Architecture Summary",
        "Dependency Map",
        "Risk Assessment",
        "Improvement Opportunities",
        "Suggested Modules",
        "Preview Manifest Registry",
        "Open Manifest",
        "Attach To Project",
        "Delete Manifest",
        "Linked Preview Manifests",
        "Preview Gateway Status",
        "Blueprint Ready",
        "Preview Gateway: Active",
        "Manifest Mode: Simulation",
        "Read Access: Disabled",
        "Phase: 15",
        "Future Desktop Connector Vision",
        "Future desktop connector may:",
        "expose selected folders",
        "generate manifests",
        "provide previews",
        "protect secrets",
        "require approval",
        "UNDX still cannot directly access files.",
        "Gateway Restrictions",
        "open files",
        "execute commands",
        "without future approved phases.",
        "undxPreviewManifests",
        "Core Modules",
        "Builder Intelligence",
        "Security Expansion",
        "Crypto Research Engine",
        "Autonomous Debugging",
        "Product Growth Intelligence",
        "Mission Control Automation",
        "UNDX Mission Panel",
        "Core Status:",
        "Access Level:",
        "Build Phase:",
        "Intelligence Mode:",
        "Future Features Preview",
        "AI Builder Console",
        "Repo Intelligence",
        "Agent Council",
        "Memory Core",
    ]:
        require(token in undx_html, f"UNDX page contains {token}")

    print("undx premium placement audit ok")


if __name__ == "__main__":
    main()
