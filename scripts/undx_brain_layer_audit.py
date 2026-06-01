import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import undx_brain_layer as brain_layer


PLANNING_MISSION = (
    "Prepare Pulse Communications 2.0 Full Replacement Plan. Proposal only. "
    "Do not write files. Analyze legacy direct messages, rooms, groups, "
    "messenger routes, chat APIs, message models, templates, JavaScript, CSS, "
    "permissions, and database dependencies."
)


def expect(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"ok - {message}")


def main():
    root = Path(__file__).resolve().parents[1]
    workspace = root / ".undx_brain_layer_audit_workspace"
    shutil.rmtree(workspace, ignore_errors=True)
    (workspace / "templates").mkdir(parents=True)
    (workspace / "static").mkdir()
    (workspace / "pulse_communications_v2").mkdir()
    try:
        (workspace / "static" / "offline.html").write_text("<html>offline fallback</html>\n", encoding="utf-8")
        (workspace / "templates" / "pulse_messages.html").write_text("direct messages rooms groups conversation chat\n", encoding="utf-8")
        (workspace / "pulse_communications_v2" / "service.py").write_text("messages conversations rooms groups members reactions\n", encoding="utf-8")
        (workspace / "bot.py").write_text("@webhook_app.route('/pulse/messages')\ndef messages():\n    return 'chat room group direct conversation messages'\n", encoding="utf-8")
        (workspace / ".env").write_text("SECRET=value\n", encoding="utf-8")

        scan = {
            "fileCount": 5,
            "workspaceName": workspace.name,
            "pythonFiles": ["bot.py", "pulse_communications_v2/service.py"],
            "templates": ["templates/pulse_messages.html"],
            "htmlFiles": ["static/offline.html"],
            "tree": [
                {"path": "bot.py", "type": "file"},
                {"path": "templates/pulse_messages.html", "type": "file"},
                {"path": "pulse_communications_v2/service.py", "type": "file"},
                {"path": "static/offline.html", "type": "file"},
                {"path": ".env", "type": "file"},
            ],
        }

        classification = brain_layer.parse_mission(PLANNING_MISSION)
        expect(classification["planningOnly"] is True, "Planning phrase sets planning-only")
        expect(classification["diffAllowed"] is False, "Planning-only disables diffs")
        expect(classification["targetSystem"] == "communications", "Communications target system is detected")

        selection = brain_layer.analyze_mission(
            workspace,
            PLANNING_MISSION,
            scan,
            safe_read=lambda path: path.read_text(encoding="utf-8", errors="replace"),
        )
        targets = selection["targetFiles"]
        expect("static/offline.html" not in targets, "Offline fallback is not selected for communications")
        expect(".env" not in targets, "Protected files are not selected")
        expect(any(path.startswith("pulse_communications_v2/") for path in targets), "V2 communications files are selected")
        expect(all(item.get("score", 0) >= brain_layer.MIN_RELEVANCE_SCORE for item in selection["relevanceScores"]), "Selected files meet relevance threshold")
        expect("UNDX Brain Reasoning" in selection["reasoningReport"], "Reasoning report is generated")
        expect("architectureReview" in selection["multiAgentReview"], "Multi-agent review is generated")

        proposal = brain_layer.generate_planning_proposal(PLANNING_MISSION, workspace.name, scan, selection)
        expect(proposal["proposalType"] == "planning-report", "Planning proposal type is planning-report")
        expect(proposal["requiresApproval"] is False, "Planning proposal does not require write approval")
        expect(proposal["diff"] == "" and proposal["changes"] == [], "Planning proposal has empty diff preview")
        expect(PLANNING_MISSION not in proposal["diff"], "Mission text is not injected into generated code")

        unsafe = dict(proposal)
        unsafe["targetFiles"] = ["static/offline.html"]
        try:
            brain_layer.enforce_safety(unsafe, PLANNING_MISSION, classification)
        except brain_layer.BrainSafetyError:
            expect(True, "Safety guard blocks unrelated offline target")
        else:
            raise AssertionError("Safety guard should block unrelated offline target")
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


if __name__ == "__main__":
    main()
