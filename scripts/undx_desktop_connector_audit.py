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
    (audit_workspace / "scripts").mkdir()
    (audit_workspace / "static" / "js").mkdir(parents=True)
    (audit_workspace / "static" / "css").mkdir(parents=True)
    (audit_workspace / "src").mkdir()
    (audit_workspace / "index.html").write_text("<!doctype html><html><head><title>Old</title></head><body><main>Old site</main></body></html>\n", encoding="utf-8")
    (audit_workspace / "templates" / "pulse_labs.html").write_text("<section class=\"card\">\n  <h2>Pulse Labs</h2>\n</section>\n", encoding="utf-8")
    (audit_workspace / "bot.py").write_text("from flask import Flask\nwebhook_app = Flask(__name__)\n@webhook_app.route('/pulse/labs')\ndef labs():\n    return 'ok'\n", encoding="utf-8")
    (audit_workspace / "scripts" / "undx_homepage_audit.py").write_text("print('audit ok')\n", encoding="utf-8")
    (audit_workspace / "static" / "js" / "main.js").write_text("console.log('ok');\n", encoding="utf-8")
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

      proposal = post(client, "/proposal/generate", {"workspacePath": audit_workspace.as_posix(), "taskDescription": "Recreate this website as a premium UNDX analytics landing page"})
      proposal_json = proposal.get_json()
      expect(proposal.status_code == 200 and proposal_json["targetFile"] == "index.html", "Repository-aware proposal targets index.html")
      expect(proposal_json["repositoryAware"] is True, "Proposal is marked repository-aware")
      expect(proposal_json["proposalEngine"] == "Repository-Aware", "Proposal reports repository-aware engine")
      expect("Built by UNDX Execution Kernel" in proposal_json["changes"][0]["after"], "Landing page proposal includes generated marker")
      expect("--- a/index.html" in proposal_json["changes"][0]["diff"] and "+++ b/index.html" in proposal_json["changes"][0]["diff"], "Unified diff is generated for index.html")

      no_approval = post(client, "/patch/apply", {"workspacePath": audit_workspace.as_posix(), "approvedPatch": proposal_json, "approvalPhrase": "NO"})
      expect(no_approval.status_code == 400, "Approval phrase required before write")

      applied = post(client, "/patch/apply", {"workspacePath": audit_workspace.as_posix(), "approvedPatch": proposal_json, "approvalPhrase": connector.APPROVAL_WRITE})
      applied_json = applied.get_json()
      expect(applied.status_code == 200 and "index.html" in applied_json["changedFiles"], "Approved patch writes only approved modified file")
      expect(Path(applied_json["backupPath"]).exists(), "Backup created before write")
      expect("premium UNDX analytics" in (audit_workspace / "index.html").read_text(encoding="utf-8"), "Approved index.html modification is persisted")

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
