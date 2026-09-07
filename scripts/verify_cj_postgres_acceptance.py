"""Run real staging PostgreSQL acceptance with no credential-bearing traceback."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    url = os.getenv("CJ_ACCEPTANCE_DATABASE_URL", "")
    if not url or os.getenv("CJ_ACCEPTANCE_POSTGRES_APPROVED") != "1":
        print(json.dumps({"result": "NOT_RUN", "reason": "approved_staging_postgresql_required"}))
        return 2
    os.environ["DATABASE_URL"] = url
    os.environ["COINPILOTX_DISABLE_LOCAL_ENV"] = "1"
    output = io.StringIO()
    progress_path = ROOT / "outputs" / "cj-postgres-progress.log"
    progress_path.parent.mkdir(exist_ok=True)
    progress_path.write_text("")
    class Progress:
        def pytest_runtest_logreport(self, report):
            if report.when == "call" or report.failed:
                with progress_path.open("a") as stream:
                    stream.write(json.dumps({"test": report.nodeid, "phase": report.when,
                                             "outcome": report.outcome}) + "\n")
    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(output):
        import pytest
        code = pytest.main(["-q", "-x", "--tb=short", "--no-header", "tests/staging/test_cj_postgres.py"], plugins=[Progress()])
    report = output.getvalue()
    for secret in (url, urlparse(url).password):
        if secret:
            report = report.replace(secret, "[REDACTED]")
    print(report)
    print(json.dumps({"engine": "postgresql", "provider": "synthetic_no_network",
                      "result": "PASS" if code == 0 else "FAIL", "pytest_exit": int(code)}))
    return int(code)


if __name__ == "__main__":
    raise SystemExit(main())
