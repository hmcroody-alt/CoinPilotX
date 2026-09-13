"""This directory must never touch a database it did not create.

The modules here call `bot.init_db()` and then write things no real database should
ever contain: stored-XSS payloads through the public ingest endpoint, forged
`mobile_security_sessions` rows, an `admin_users` row with its
`must_change_password` flag cleared. That is fine against a scratch file and
catastrophic against anything else — and `DATABASE_URL` is precisely the variable a
developer has already exported to reach "anything else", whether that is their own
`coinpilotx.db` or, via `railway run`, production.

`conftest.py` therefore *overrides* `DATABASE_URL` rather than defaulting it. This
module is the regression test for that one character of difference, because the bug
it guards against is invisible from inside the suite: with `setdefault`, every test
in this directory still passes. It passes *while writing to the wrong database*. A
green run is not evidence.

Measured, at the moment this was written, with `setdefault` in conftest:
`DATABASE_URL=sqlite:////tmp/probe.db pytest tests/web_parity/test_admin_analytics_escaping.py`
→ 11 passed, and /tmp/probe.db came out holding 589 tables and 6 payload rows.

Run: python3 -m pytest tests/web_parity/test_suite_database_isolation.py
"""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

#: The module used as the canary below. It has to be one that demonstrably *writes*
#: — a read-only module would leave the probe untouched even with the protection
#: removed, and this test would pass for the wrong reason forever. This one creates
#: 589 tables and seeds attacker payloads, which makes it the loudest possible canary.
CANARY = "tests/web_parity/test_admin_analytics_escaping.py"


def test_the_directory_runs_against_its_own_scratch_database():
    """Cheap version: whatever `bot` imported, it came from conftest."""
    url = os.environ.get("DATABASE_URL", "")
    assert url.startswith("sqlite:///"), (
        f"web_parity is pointed at a non-SQLite database ({url!r}). These tests "
        "seed XSS payloads and forged sessions; they must only ever run against a "
        "throwaway file."
    )
    path = url[len("sqlite:///"):]
    assert "pulsesoc-web-parity-" in os.path.basename(path), (
        f"DATABASE_URL ({path!r}) was not the scratch file conftest.py creates. "
        "Something set it first and conftest deferred to it."
    )


def test_an_exported_database_url_is_ignored_not_honoured():
    """The real proof: aim the suite at a database and check it stays untouched.

    This runs a child pytest rather than asserting on an environment variable,
    because the failure being guarded against happens at *import* of `bot` in a
    process whose environment this test cannot retroactively change.
    """
    probe = Path(tempfile.mkdtemp(prefix="pulsesoc-isolation-probe-")) / "probe.db"

    # Create it as a real, empty SQLite database. An absent file would also prove
    # the point, but a present-and-empty one additionally rules out the suite
    # having written and then deleted it, and it is what a developer's actual
    # DATABASE_URL looks like: a file that already exists.
    sqlite3.connect(probe).close()
    before = probe.stat().st_size

    env = dict(os.environ, DATABASE_URL=f"sqlite:///{probe}")
    # Not inherited: this process is already inside the suite, so its own
    # conftest-set value would otherwise be passed straight through and the child
    # would prove nothing.
    result = subprocess.run(
        [sys.executable, "-m", "pytest", CANARY, "-q", "-p", "no:randomly"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=600,
    )

    conn = sqlite3.connect(probe)
    try:
        tables = conn.execute(
            "SELECT count(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]
    finally:
        conn.close()

    assert tables == 0, (
        f"the suite created {tables} tables in a database supplied through "
        f"DATABASE_URL. conftest.py must override that variable, not defer to it "
        f"with setdefault.\n{result.stdout[-2000:]}"
    )
    assert probe.stat().st_size == before, (
        "the externally-supplied database was written to."
    )

    # And the child must actually have run. If it errored at collection it would
    # also leave the probe clean, which would make the assertions above vacuous.
    assert result.returncode == 0, (
        f"the canary module did not pass, so this test proved nothing:\n"
        f"{result.stdout[-3000:]}\n{result.stderr[-2000:]}"
    )
    assert " passed" in result.stdout, result.stdout[-2000:]
