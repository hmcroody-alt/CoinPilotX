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
    require("UNDX Core: Build Beyond the Known" not in feed_html, "old UNDX feed headline absent")

    routes = {str(rule) for rule in bot.webhook_app.url_map.iter_rules()}
    require("/api/undx/chat" in routes, "/api/undx/chat route exists")
    require('logging.info("OpenAI API key configured: %s", "yes" if api_key else "no")' in source, "UNDX logs safe OpenAI key configuration status")

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
        "Six specialized intelligence agents evaluate each mission before CoinPilotXAI enters the next build phase.",
        "Run Agent Council",
        "Architect Agent",
        "Builder Agent",
        "Security Agent",
        "Research Agent",
        "Product Agent",
        "Deployment Agent",
        "Council Mode: Multi-Agent",
        "Active Agents: <strong>6</strong>",
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
