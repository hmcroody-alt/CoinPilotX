"""Shared pytest fixtures for the backend suite."""

import os
import sys
import tempfile

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

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
#: Patched as a module **attribute** below *as well as* through `DATABASE_URL`, and both
#: are needed. The attribute is what survives `patch.dict(os.environ, ..., clear=True)`,
#: which several suites drive their subject under; the variable is what closes the other
#: leg of `connect()`, described under the pin that follows.
_FALLBACK_DB_DIR = tempfile.TemporaryDirectory(prefix="pulsesoc-test-fallback-")
_FALLBACK_DB_PATH = os.path.join(_FALLBACK_DB_DIR.name, "coinpilotx.db")


def _would_write_inside_the_repository(url):
    """Whether `connect()` given `url` would open a file in this checkout.

    Absent and empty both count, because they select the relative `LOCAL_SQLITE_FILE`
    fallback. So does any relative sqlite path: it resolves against the working
    directory, which is normally the repository root and is in any case not a place a
    test chose. An absolute path is judged on where it actually lands, so a temp file
    passes; `:memory:` and a PostgreSQL DSN are left alone as deliberate choices.
    """
    value = (url or "").strip()
    if not value:
        return True
    if value.startswith("sqlite:///"):
        target = value[len("sqlite:///"):]
    elif value.startswith("file:"):
        target = value[len("file:"):].split("?", 1)[0]
    else:
        return False
    if not target or target.startswith(":"):
        return False
    if not os.path.isabs(target):
        return True
    root = os.path.realpath(REPO_ROOT)
    resolved = os.path.realpath(target)
    return resolved == root or resolved.startswith(root + os.sep)


#: Close the *first* leg of `connect()`, which the fixture below cannot reach.
#:
#: `connect()` consults `DATABASE_URL` before it consults `LOCAL_SQLITE_FILE`. Patching
#: the constant alone rests on the assumption that a variable unset at session start
#: stays unset, and it does not: `bot` runs `_load_local_environment()` at module scope,
#: which reads `.env.local`, finds `DATABASE_URL` absent, and sets it to the *relative*
#: `sqlite:///coinpilotx.db`. From that moment every `connect()` in the process opens the
#: developer's own database and the fixture's protection is gone, silently — importing
#: `bot` also ran `init_db()` over there, so every table the test wants exists. 77 test
#: files import `bot` directly and more do it lazily from inside a service, which is how
#: this was found: `services/pulsedrop/hydration._price_minor` imports it to parse a
#: price, four rows into the real file.
#:
#: Presence is the whole mechanism: `_load_local_env_file` assigns only keys *absent* from
#: `os.environ`, so a key that is already there can never be captured. That prevents the
#: capture instead of repairing it, which is the difference that matters — an autouse
#: function-scoped fixture re-pinning a mutated variable would still leave the test that
#: triggered the lazy `import bot` writing to the wrong file, because the import happens
#: during that test and the repair only lands before the next one. The value is
#: `_FALLBACK_DB_PATH`, the same file the constant names, so the two legs converge: a suite
#: that clears the environment, or sets the variable empty — a dozen do, via
#: `setdefault("DATABASE_URL", "")` — lands exactly where it landed before.
#:
#: Import time is required, not incidental: ~250 test modules pin their own database at
#: *module* scope, which runs during collection — after this file is imported but before
#: any session fixture. Setting the variable in a fixture would clobber those. Setting it
#: here lets them override it, which is the correct order, and it also covers a module that
#: connects while being collected, before any fixture has run at all. The cost is that
#: `services.db` computes `ENGINE_URL` and `DATABASE_URL_LOADED` from it a few lines below;
#: both move off the relative path, which is the point, and nothing asserts on either.
#: `ENGINE_NAME` stays `"sqlite"`.
#:
#: Conditional, because a `DATABASE_URL` that already names a database elsewhere is a
#: choice — a throwaway PostgreSQL, or `:memory:` — and silently redirecting it to a temp
#: file would mean a Postgres-only dialect crash could not be reproduced locally.
#:
#: Not done by setting `COINPILOTX_DISABLE_LOCAL_ENV`, which is `bot`'s own switch for
#: skipping `.env.local` and looks like the root-cause fix. It is too broad. That file also
#: carries `ENV`, `FLASK_ENV`, `SESSION_COOKIE_SECURE`, three signing keys and the three
#: `UNDX_AGENT_*` flags, and `tests/protection/test_signing_key_separation.py` exists
#: precisely because a key in `.env.local` can displace a derived one — switching the
#: loader off would change that suite's subject rather than fix a database path.
#:
#: Deliberately never restored. Restoring would reintroduce the absence this exists to
#: remove, and the process it applies to is the test process.
if _would_write_inside_the_repository(os.environ.get("DATABASE_URL")):
    os.environ["DATABASE_URL"] = f"sqlite:///{_FALLBACK_DB_PATH}"

from services import db as platform_db  # noqa: E402
from services import schema_guard  # noqa: E402


@pytest.fixture(autouse=True, scope="session")
def _never_write_to_the_developers_database():
    """Redirect the unconfigured-SQLite fallback away from the repo for the whole run.

    One file per pytest *process*, not per test, deliberately: that preserves the old
    behaviour exactly — a single shared database that persists across the run — minus
    the part where the file was the developer's. Per-test files would be stronger
    isolation but would also change semantics for any suite that sets a database up in
    `setUpClass` and reads it back in individual tests, and this fixture's job is to
    move the file, not to fix hermeticity.

    It covers only the second leg of `connect()`. The first — `DATABASE_URL` — is pinned
    at import time above, because a fixture runs too late to stop ~250 test modules from
    pinning their own database during collection. The SQLAlchemy engine, which resolves
    its URL once at import of `services.db`, is therefore covered by that pin and not by
    this fixture; it would still be bound to the relative path if this were the only
    guard.
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
