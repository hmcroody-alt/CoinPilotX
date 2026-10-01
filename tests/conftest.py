"""Shared pytest fixtures for the backend suite."""

import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import db as platform_db  # noqa: E402
from services import schema_guard  # noqa: E402

#: Where an unconfigured test writes instead of the developer's own database.
#:
#: `services.db.connect()` falls back to `LOCAL_SQLITE_FILE` — the *relative* string
#: `"coinpilotx.db"` — whenever `DATABASE_URL` is unset, so any test that reaches a raw
#: connection without pinning one writes into the working directory. That was untidy for
#: as long as the tables involved were only ever read by the code that wrote them. It
#: became load-bearing when the embedding budget guard started reading its month-to-date
#: out of `undx_cost_ledger`: accumulated test rows are then spend the guard counts
#: against a developer's real calls, and any test asserting that a call is *allowed*
#: quietly becomes a test of how many times the suite has been run.
#:
#: The damage was invisible because the obvious check does not work — SQLite reuses free
#: pages, so `coinpilotx.db` held exactly 5,971,968 bytes while accumulating a thousand
#: phantom embedding calls and 1.29M phantom tokens. Only `md5 -q coinpilotx.db` before
#: and after a run shows it.
#:
#: Patched as a module **attribute** rather than by setting `DATABASE_URL`, which is the
#: whole reason this works: several suites drive their subject under
#: `patch.dict(os.environ, ..., clear=True)`, and an environment variable set out here
#: does not survive that. A Python global does.
_FALLBACK_DB_DIR = tempfile.TemporaryDirectory(prefix="pulsesoc-test-fallback-")
_FALLBACK_DB_PATH = os.path.join(_FALLBACK_DB_DIR.name, "coinpilotx.db")


@pytest.fixture(autouse=True, scope="session")
def _never_write_to_the_developers_database():
    """Redirect the unconfigured-SQLite fallback away from the repo for the whole run.

    One file per pytest *process*, not per test, deliberately: that preserves the old
    behaviour exactly — a single shared database that persists across the run — minus
    the part where the file was the developer's. Per-test files would be stronger
    isolation but would also change semantics for any suite that sets a database up in
    `setUpClass` and reads it back in individual tests, and this fixture's job is to
    move the file, not to fix hermeticity.

    It does not cover the SQLAlchemy engine, which resolves its URL once at import of
    `services.db` and so is already bound to the relative path by the time this runs.
    Suites that go through a session rather than `connect()` are unaffected; the cost
    ledger, which is what prompted this, uses `connect()`.
    """
    original = platform_db.LOCAL_SQLITE_FILE
    platform_db.LOCAL_SQLITE_FILE = _FALLBACK_DB_PATH
    try:
        yield _FALLBACK_DB_PATH
    finally:
        platform_db.LOCAL_SQLITE_FILE = original


@pytest.fixture(autouse=True)
def _reset_schema_guards():
    """Let each test's fresh database get its schema.

    The guards in services.schema_guard cache "the DDL already ran" for the life
    of the process, which is the point in production but wrong here: most suites
    build a new in-memory or temp-file database per test, and a cached guard
    would hand the second test onwards an empty database.
    """
    schema_guard.reset_all()
    yield
    schema_guard.reset_all()
