import json
import shutil
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
    (audit_workspace / "scripts").mkdir()
    (audit_workspace / "static" / "js").mkdir(parents=True)
    (audit_workspace / "templates" / "pulse_labs.html").write_text("<section class=\"card\">\n  <h2>Pulse Labs</h2>\n</section>\n", encoding="utf-8")
    (audit_workspace / "bot.py").write_text("from flask import Flask\nwebhook_app = Flask(__name__)\n@webhook_app.route('/pulse/labs')\ndef labs():\n    return 'ok'\n", encoding="utf-8")
    (audit_workspace / "scripts" / "undx_homepage_audit.py").write_text("print('audit ok')\n", encoding="utf-8")
    (audit_workspace / "static" / "js" / "main.js").write_text("console.log('ok');\n", encoding="utf-8")
    (audit_workspace / ".env").write_text("OPENAI_API_KEY=secret\n", encoding="utf-8")

    try:
      connector.ensure_config()
      client = connector.app.test_client()

      health = client.get("/health")
      expect(health.status_code == 200 and health.get_json()["ok"], "Desktop connector health is online")

      register = post(client, "/workspace/register", {"workspacePath": audit_workspace.as_posix(), "workspaceLabel": "Audit Workspace"})
      expect(register.status_code == 200 and register.get_json()["ok"], "Workspace registration accepts approved local folder")

      scan = post(client, "/repo/scan", {"workspacePath": audit_workspace.as_posix()})
      scan_json = scan.get_json()
      expect(scan.status_code == 200 and scan_json["fileCount"] >= 4, "Repository scan is manual and returns file count")
      expect("templates/pulse_labs.html" in scan_json["templates"], "Repository scan detects templates")
      expect(scan_json["flaskRoutes"][0]["route"] == "/pulse/labs", "Repository scan detects Flask routes")
      expect(".env" in scan_json["protectedFiles"], "Protected files are detected")

      traversal = post(client, "/file/read", {"workspacePath": audit_workspace.as_posix(), "relativePath": "../bot.py"})
      expect(traversal.status_code == 400, "Path traversal is blocked")

      protected = post(client, "/file/read", {"workspacePath": audit_workspace.as_posix(), "relativePath": ".env"})
      expect(protected.status_code == 400, "Protected files are blocked")

      proposal = post(client, "/proposal/generate", {"workspacePath": audit_workspace.as_posix(), "taskDescription": "Add text to templates/pulse_labs.html"})
      proposal_json = proposal.get_json()
      expect(proposal.status_code == 200 and proposal_json["targetFile"] == "templates/pulse_labs.html", "Proposal generation targets approved file")
      expect("Built by UNDX Execution Kernel" in proposal_json["changes"][0]["after"], "Proposal includes requested text")

      no_approval = post(client, "/patch/apply", {"workspacePath": audit_workspace.as_posix(), "approvedPatch": proposal_json, "approvalPhrase": "NO"})
      expect(no_approval.status_code == 400, "Approval phrase required before write")

      applied = post(client, "/patch/apply", {"workspacePath": audit_workspace.as_posix(), "approvedPatch": proposal_json, "approvalPhrase": connector.APPROVAL_WRITE})
      applied_json = applied.get_json()
      expect(applied.status_code == 200 and "templates/pulse_labs.html" in applied_json["changedFiles"], "Approved patch writes only approved file")
      expect(Path(applied_json["backupPath"]).exists(), "Backup created before write")

      validation = post(client, "/validate/run", {"workspacePath": audit_workspace.as_posix(), "validationType": "python_compile"})
      expect(validation.status_code == 200, "Validation allowlist accepts Python compile")

      arbitrary = post(client, "/validate/run", {"workspacePath": audit_workspace.as_posix(), "validationType": "rm -rf /"})
      expect(arbitrary.status_code == 400, "Arbitrary command execution is blocked")

      git_commit = post(client, "/git/commit", {"workspacePath": audit_workspace.as_posix(), "approvalPhrase": "NO", "changedFiles": ["templates/pulse_labs.html"]})
      expect(git_commit.status_code == 400, "Git approval is required")
    finally:
      shutil.rmtree(audit_workspace, ignore_errors=True)


if __name__ == "__main__":
    main()
