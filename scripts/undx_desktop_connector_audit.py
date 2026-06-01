import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import undx_desktop_connector as connector


def expect(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def post(client, path, payload):
    return client.post(path, data=json.dumps(payload), content_type="application/json")


def main():
    root = Path(__file__).resolve().parents[1]
    audit_workspace = root / ".undx_connector_audit_workspace"
    shutil.rmtree(audit_workspace, ignore_errors=True)
    (audit_workspace / "templates").mkdir(parents=True)
    (audit_workspace / "pulse_communications_v2").mkdir()
    (audit_workspace / "scripts").mkdir()
    (audit_workspace / "static" / "js").mkdir(parents=True)
    (audit_workspace / "static" / "css").mkdir(parents=True)
    (audit_workspace / "src").mkdir()
    (audit_workspace / "index.html").write_text("<!doctype html><html><head><title>Old</title></head><body><main>Old site</main></body></html>\n", encoding="utf-8")
    (audit_workspace / "static" / "offline.html").write_text("<!doctype html><html><body>Offline fallback</body></html>\n", encoding="utf-8")
    (audit_workspace / "templates" / "pulse_labs.html").write_text("<section class=\"card\">\n  <h2>Pulse Labs</h2>\n</section>\n", encoding="utf-8")
    (audit_workspace / "templates" / "pulse_messages.html").write_text("<section data-pulse-messages>Direct messages, chat rooms, group conversations</section>\n", encoding="utf-8")
    (audit_workspace / "bot.py").write_text("from flask import Flask\nwebhook_app = Flask(__name__)\n@webhook_app.route('/pulse/labs')\ndef labs():\n    return 'ok'\n@webhook_app.route('/pulse/messages')\ndef pulse_messages():\n    return 'direct messages rooms groups chat conversations'\n@webhook_app.route('/api/pulse/conversations/<conversation_id>/messages')\ndef conversation_messages(conversation_id):\n    return 'messages'\n", encoding="utf-8")
    (audit_workspace / "scripts" / "undx_homepage_audit.py").write_text("print('audit ok')\n", encoding="utf-8")
    (audit_workspace / "scripts" / "chat_system_audit.py").write_text("print('chat messages rooms groups audit ok')\n", encoding="utf-8")
    (audit_workspace / "static" / "js" / "main.js").write_text("console.log('ok');\n", encoding="utf-8")
    (audit_workspace / "static" / "js" / "pulse_messages.js").write_text("const pulseMessages = ['direct','chat rooms','groups','conversation'];\n", encoding="utf-8")
    (audit_workspace / "pulse_communications_v2" / "service.py").write_text("def list_conversations():\n    return 'direct room group conversation messages members reactions search'\n", encoding="utf-8")
    (audit_workspace / "pulse_communications_v2" / "routes.py").write_text("COMMUNICATION_ROUTES = ['/api/pulse/comm/v2/conversations', '/api/pulse/comm/v2/conversations/<id>/messages']\n", encoding="utf-8")
    (audit_workspace / "static" / "js" / "large_module.js").write_text("".join(f"console.log('large chunk line {i} search-target');\n" for i in range(1, 9000)), encoding="utf-8")
    (audit_workspace / "static" / "css" / "main.css").write_text("body { color: white; }\n", encoding="utf-8")
    (audit_workspace / "src" / "App.jsx").write_text("export default function App(){ return <main>React</main>; }\n", encoding="utf-8")
    (audit_workspace / "src" / "App.vue").write_text("<template><main>Vue</main></template>\n", encoding="utf-8")
    (audit_workspace / ".env").write_text("OPENAI_API_KEY=secret\n", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=audit_workspace, text=True, capture_output=True, check=False)

    try:
      connector.ensure_config()
      client = connector.app.test_client()

      health = client.get("/health")
      health_json = health.get_json()
      expect(health.status_code == 200 and health_json["ok"], "Desktop connector health is online")
      expect(health_json["proposalEngine"] == "Repository-Aware", "Health reports repository-aware proposal engine")
      expect(health_json.get("engineSource") == "UNDX_BRAIN_LAYER", "Health reports Brain Layer as active engine source")
      expect(health_json.get("brainLayerActive") is True, "Health reports Brain Layer active")
      expect(health_json.get("activeMissionClassifier") == "undx_brain_layer.parse_mission", "Health reports active mission classifier")
      expect(health_json.get("activeFileSelector") == "undx_brain_layer.select_repository_files", "Health reports active file selector")
      expect(health_json["activeProposalHandler"] == "generate_proposal", "Health reports active proposal handler")

      register = post(client, "/workspace/register", {"workspacePath": audit_workspace.as_posix(), "workspaceLabel": "Audit Workspace"})
      expect(register.status_code == 200 and register.get_json()["ok"], "Workspace registration accepts approved local folder")

      scan = post(client, "/repo/scan", {"workspacePath": audit_workspace.as_posix()})
      scan_json = scan.get_json()
      expect(scan.status_code == 200 and scan_json["fileCount"] >= 4, "Repository scan is manual and returns file count")
      expect("templates/pulse_labs.html" in scan_json["templates"], "Repository scan detects templates")
      expect("index.html" in scan_json["htmlFiles"], "Repository scan detects root HTML files")
      expect("static/js/main.js" in scan_json["jsFiles"], "Repository scan detects JavaScript files")
      expect("static/css/main.css" in scan_json["cssFiles"], "Repository scan detects CSS files")
      expect("src/App.jsx" in scan_json["reactFiles"], "Repository scan detects React indicators")
      expect("src/App.vue" in scan_json["vueFiles"], "Repository scan detects Vue files")
      expect("Flask" in scan_json["frameworks"], "Repository scan detects Flask framework")
      expect(scan_json["flaskRoutes"][0]["route"] == "/pulse/labs", "Repository scan detects Flask routes")
      expect(".env" in scan_json["protectedFiles"], "Protected files are detected")

      traversal = post(client, "/file/read", {"workspacePath": audit_workspace.as_posix(), "relativePath": "../bot.py"})
      expect(traversal.status_code == 400, "Path traversal is blocked")

      protected = post(client, "/file/read", {"workspacePath": audit_workspace.as_posix(), "relativePath": ".env"})
      expect(protected.status_code == 400, "Protected files are blocked")

      small_read = post(client, "/file/read", {"workspacePath": audit_workspace.as_posix(), "relativePath": "static/js/main.js"})
      small_json = small_read.get_json()
      expect(small_read.status_code == 200 and small_json["readMode"] == "full" and "console.log" in small_json["content"], "Small file reads normally")
      expect(small_json["fileSizeBytes"] > 0 and small_json["normalReadLimitBytes"] == connector.NORMAL_READ_LIMIT_BYTES, "Small file read reports size and limits")

      large_summary = post(client, "/file/read", {"workspacePath": audit_workspace.as_posix(), "relativePath": "static/js/large_module.js"})
      large_summary_json = large_summary.get_json()
      expect(large_summary.status_code == 200 and large_summary_json["requiresChunkedRead"] is True and large_summary_json["content"] == "", "Large file returns summary instead of full browser payload")
      expect("first" in large_summary_json["availableActions"] and large_summary_json["fileSizeBytes"] > connector.NORMAL_READ_LIMIT_BYTES, "Large file exposes chunk actions and size")

      first_chunk = post(client, "/file/read", {"workspacePath": audit_workspace.as_posix(), "relativePath": "static/js/large_module.js", "mode": "first"})
      first_chunk_json = first_chunk.get_json()
      expect(first_chunk.status_code == 200 and first_chunk_json["lineCount"] == connector.MAX_FILE_READ_LINES, "Large file first chunk is capped at 500 lines")
      expect(first_chunk_json["nextStartLine"] == 501 and "large chunk line 500" in first_chunk_json["content"], "Large file first chunk provides next cursor")

      next_chunk = post(client, "/file/read", {"workspacePath": audit_workspace.as_posix(), "relativePath": "static/js/large_module.js", "mode": "next", "startLine": first_chunk_json["nextStartLine"]})
      next_chunk_json = next_chunk.get_json()
      expect(next_chunk.status_code == 200 and next_chunk_json["startLine"] == 501 and next_chunk_json["lineCount"] == connector.MAX_FILE_READ_LINES, "Large file next chunk reads bounded section")

      range_read = post(client, "/file/read", {"workspacePath": audit_workspace.as_posix(), "relativePath": "static/js/large_module.js", "mode": "range", "startLine": 120, "endLine": 140})
      range_json = range_read.get_json()
      expect(range_read.status_code == 200 and range_json["startLine"] == 120 and range_json["endLine"] == 140, "Line range read returns requested bounded lines")

      search_read = post(client, "/file/read", {"workspacePath": audit_workspace.as_posix(), "relativePath": "static/js/large_module.js", "mode": "search", "query": "line 777"})
      search_json = search_read.get_json()
      expect(search_read.status_code == 200 and search_json["matchCount"] >= 1 and search_json["matches"][0]["line"] == 777, "Large file search returns context around matches")

      proposal = post(client, "/proposal/generate", {"workspacePath": audit_workspace.as_posix(), "taskDescription": "Recreate this website as a premium UNDX analytics landing page"})
      proposal_json = proposal.get_json()
      expect(proposal.status_code == 200 and proposal_json["targetFile"] == "index.html", "Repository-aware proposal targets index.html")
      expect(proposal_json["targetFiles"] == ["index.html"], "Proposal returns targetFiles")
      expect(proposal_json["requiresApproval"] is True, "Proposal requires approval before write")
      expect("--- a/index.html" in proposal_json["diff"], "Proposal returns top-level unified diff")
      expect("Launch UNDX" in proposal_json["changes"][0]["after"], "Landing page proposal includes Launch UNDX button")
      expect("Generated by UNDX" in proposal_json["changes"][0]["after"], "Landing page proposal includes required footer")
      expect(proposal_json["repositoryAware"] is True, "Proposal is marked repository-aware")
      expect(proposal_json["proposalEngine"] == "Repository-Aware", "Proposal reports repository-aware engine")
      expect(proposal_json.get("engineSource") == "UNDX_BRAIN_LAYER", "Implementation proposal reports Brain Layer engine source")
      expect(proposal_json.get("brainLayerActive") is True, "Implementation proposal reports Brain Layer active")
      expect("Built by UNDX Execution Kernel" in proposal_json["changes"][0]["after"], "Landing page proposal includes generated marker")
      expect("--- a/index.html" in proposal_json["changes"][0]["diff"] and "+++ b/index.html" in proposal_json["changes"][0]["diff"], "Unified diff is generated for index.html")

      planning_mission = "Prepare Pulse Communications 2.0 Full Replacement Plan. Proposal only. Do not write files. Analyze legacy direct messages, rooms, groups, messenger routes, chat APIs, message models, templates, JavaScript, CSS, permissions, and database dependencies."
      planning = post(client, "/proposal/generate", {"workspacePath": audit_workspace.as_posix(), "taskDescription": planning_mission})
      planning_json = planning.get_json()
      planning_targets = planning_json.get("targetFiles") or []
      expect(planning.status_code == 200 and planning_json["proposalType"] == "planning-report", "Planning-only mission returns planning report")
      expect(planning_json["missionType"] == "planning-only", "Planning-only mission is classified safely")
      expect(planning_json["targetSystem"] == "communications", "Pulse Communications mission is classified as communications")
      expect(planning_json["diffAllowed"] is False, "Planning-only mission disables diff generation")
      expect(planning_json["requiresApproval"] is False and planning_json["diff"] == "" and planning_json["changes"] == [], "Planning-only mission does not generate diffs or changes")
      expect(planning_json.get("brainLayer", {}).get("enabled") is True, "Planning-only mission is processed by UNDX Brain Layer")
      expect(planning_json.get("engineSource") == "UNDX_BRAIN_LAYER", "Planning proposal reports Brain Layer engine source")
      expect(planning_json.get("brainLayerActive") is True, "Planning proposal reports Brain Layer active")
      expect(planning_json.get("brainLayer", {}).get("version") == "live-autonomous-v1", "Planning proposal reports brain layer version")
      expect("UNDX Brain Reasoning" in planning_json.get("reasoningReport", ""), "Planning proposal includes brain reasoning report")
      expect("architectureReview" in planning_json.get("multiAgentReview", {}), "Planning proposal includes multi-agent review")
      expect("static/offline.html" not in planning_targets, "Pulse Communications planning does not target static/offline.html")
      expect(any(any(keyword in path.lower() for keyword in ("message", "chat", "room", "group", "conversation", "communication")) or path == "bot.py" for path in planning_targets), "Pulse Communications planning targets communications-related files")
      expect(any(path.startswith("pulse_communications_v2/") for path in planning_targets), "Pulse Communications planning learns the v2 communications file set")
      expect(all(path != "static/offline.html" for path in planning_targets), "Offline fallback file is not selected for non-offline mission")
      expect(all(item.get("why") and item.get("score", 0) >= connector.MIN_RELEVANCE_SCORE for item in planning_json.get("targetFileReasons", [])), "Communications file matching returns explanations and scores")
      expect("Repository Communications Map" in planning_json["report"], "Planning report includes communications map")
      expect("Exact Candidate Files And Relevance Scores" in planning_json["report"], "Planning report includes relevance scores")
      expect("First Safe Implementation Patch" in planning_json["report"], "Planning report includes first safe patch section")
      expect(planning_mission not in planning_json.get("diff", ""), "Planning mission text is not pasted into an HTML diff")
      expect(all(planning_mission not in str(change.get("after", "")) for change in planning_json.get("changes", [])), "Planning mission text is not pasted into generated HTML")
      expect(all("Built by UNDX Execution Kernel" not in str(change.get("after", "")) for change in planning_json.get("changes", [])), "Planning mission does not generate legacy HTML")
      expect("static/offline.html" not in planning_json["report"], "Planning report does not include unrelated offline fallback")

      offline_attack = post(client, "/proposal/generate", {"workspacePath": audit_workspace.as_posix(), "taskDescription": f"{planning_mission} static/offline.html"})
      offline_attack_json = offline_attack.get_json()
      expect(offline_attack.status_code == 200 and offline_attack_json["diff"] == "" and "static/offline.html" not in (offline_attack_json.get("targetFiles") or []), "Explicit offline fallback mention is blocked for communications planning")

      no_approval = post(client, "/patch/apply", {"workspacePath": audit_workspace.as_posix(), "approvedPatch": proposal_json, "approvalPhrase": "NO"})
      expect(no_approval.status_code == 400, "Approval phrase required before write")

      applied = post(client, "/patch/apply", {"workspacePath": audit_workspace.as_posix(), "approvedPatch": proposal_json, "approvalPhrase": connector.APPROVAL_WRITE})
      applied_json = applied.get_json()
      expect(applied.status_code == 200 and "index.html" in applied_json["changedFiles"], "Approved patch writes only approved modified file")
      expect(Path(applied_json["backupPath"]).exists(), "Backup created before write")
      applied_index = (audit_workspace / "index.html").read_text(encoding="utf-8")
      expect("Repository-aware landing page proposal" in applied_index, "Approved index.html modification is persisted without raw directive text")
      expect("Recreate this website as a premium UNDX analytics landing page" not in applied_index, "Generated HTML does not paste the raw mission directive")

      new_file = post(client, "/proposal/generate", {"workspacePath": audit_workspace.as_posix(), "taskDescription": "Create file docs/launch-plan.md with launch checklist"})
      new_file_json = new_file.get_json()
      expect(new_file.status_code == 200 and new_file_json["changes"][0]["type"] == "create", "New file proposal is generated")
      new_file_no_approval = post(client, "/patch/apply", {"workspacePath": audit_workspace.as_posix(), "approvedPatch": new_file_json, "approvalPhrase": "NO"})
      expect(new_file_no_approval.status_code == 400 and not (audit_workspace / "docs" / "launch-plan.md").exists(), "New file creation is approval gated")
      new_file_applied = post(client, "/patch/apply", {"workspacePath": audit_workspace.as_posix(), "approvedPatch": new_file_json, "approvalPhrase": connector.APPROVAL_WRITE})
      expect(new_file_applied.status_code == 200 and (audit_workspace / "docs" / "launch-plan.md").exists(), "Approved new file is created")

      validation = post(client, "/validate/run", {"workspacePath": audit_workspace.as_posix(), "validationType": "python_compile"})
      expect(validation.status_code == 200, "Validation allowlist accepts Python compile")
      expect(post(client, "/validate/run", {"workspacePath": audit_workspace.as_posix(), "validationType": "html_validate"}).get_json()["ok"], "HTML validation passes")
      expect(post(client, "/validate/run", {"workspacePath": audit_workspace.as_posix(), "validationType": "css_validate"}).get_json()["ok"], "CSS validation passes")
      expect(post(client, "/validate/run", {"workspacePath": audit_workspace.as_posix(), "validationType": "javascript_parse"}).get_json()["ok"], "JavaScript syntax validation passes")
      expect(post(client, "/validate/run", {"workspacePath": audit_workspace.as_posix(), "validationType": "file_existence"}).get_json()["ok"], "File existence validation passes")

      arbitrary = post(client, "/validate/run", {"workspacePath": audit_workspace.as_posix(), "validationType": "rm -rf /"})
      expect(arbitrary.status_code == 400, "Arbitrary command execution is blocked")

      git_commit = post(client, "/git/commit", {"workspacePath": audit_workspace.as_posix(), "approvalPhrase": "NO", "changedFiles": ["templates/pulse_labs.html"]})
      expect(git_commit.status_code == 400, "Git approval is required")

      git_status = post(client, "/git/status", {"workspacePath": audit_workspace.as_posix()})
      git_json = git_status.get_json()
      expect(git_status.status_code == 200 and ("index.html" in git_json.get("stdout", "") or "docs/launch-plan.md" in git_json.get("stdout", "")), "Git status reports approved local changes")
    finally:
      shutil.rmtree(audit_workspace, ignore_errors=True)


if __name__ == "__main__":
    main()
