"""Process-global isolation for the Private Office suite.

Every test module in this directory is written to be runnable on its own —
``python tests/private_office/test_tier_resolver.py`` — and each one sets up
the process it needs at *import* time:

* eight modules point ``DATABASE_URL`` at their own temporary SQLite file;
* two install a stub ``bot`` module into ``sys.modules`` so the route pack can
  be imported without dragging in the 118k-line monolith.

That is the right shape for a standalone script and the wrong shape for a
directory run. pytest imports every module during collection and only then
starts executing, so by the time the first test runs, the *last* module
imported has won both globals for the whole process. The observable result was
six tests that passed individually and failed together — the entitlement route
tests answering ``401`` because the stub they were setting ``_test_user`` on was
no longer the ``bot`` the route pack resolved, and the substrate and
observability suites reading a database another module had claimed.

This fixture makes the collection order irrelevant by restoring, before each
test, the process state that the test's *own* module established at import
time, and putting back whatever was there afterwards. It is deliberately
mechanical: it copies values the modules already declare rather than inventing
a second source of truth for them, so a module that changes its temp path or
its stub keeps working here with no edit.

Nothing here relaxes an assertion. A test that fails after this fixture is a
test that has found something.
"""

import os
import sys

import pytest

# Imported for its cache reset only. The package is import-cheap and pulls in
# neither `bot` nor Flask, which `test_private_observability` proves in a clean
# subprocess.
from services.private_office import schema as _schema


_ABSENT = object()


def _bind(module):
    """Point the process globals at what ``module`` claimed when it imported."""
    tmp_db = getattr(module, "_TMP_DB", None)
    if tmp_db:
        os.environ["DATABASE_URL"] = "sqlite:///" + str(tmp_db)

    stub = getattr(module, "_stub", None)
    if stub is not None:
        sys.modules["bot"] = stub
    else:
        sys.modules.pop("bot", None)


@pytest.fixture(autouse=True, scope="module")
def private_office_module_isolation(request):
    """Bind before ``setup_module`` runs, not after it.

    Four of the entitlement route tests failed even with the per-test fixture
    below, because ``setup_module`` — which creates the entitlement tables and
    seeds users — is itself a module-scoped fixture, and a function-scoped
    fixture cannot run ahead of one. ``setup_module`` was therefore building its
    schema in whichever database the previously finished module had left
    behind, and the tests then looked for ``business_os_ent_grants`` in the
    right file and did not find it.

    Conftest autouse fixtures of the same scope are set up before the ones
    declared in the module, so binding here happens first.
    """
    module = request.module
    saved_url = os.environ.get("DATABASE_URL", _ABSENT)
    saved_bot = sys.modules.get("bot", _ABSENT)

    _bind(module)
    _schema.reset_schema_cache()

    try:
        yield
    finally:
        _schema.reset_schema_cache()
        if saved_bot is _ABSENT:
            sys.modules.pop("bot", None)
        else:
            sys.modules["bot"] = saved_bot
        if saved_url is _ABSENT:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved_url


@pytest.fixture(autouse=True)
def private_office_process_isolation(request):
    """Rebind the process globals a test module claimed at import time.

    Reads three optional module attributes:

    ``_TMP_DB``
        The module's own SQLite file. Re-points ``DATABASE_URL`` at it, because
        ``services.db.connect`` resolves the URL on every call and would
        otherwise hand this test another module's database.

    ``_stub``
        The module's stub ``bot``. Reinstated in ``sys.modules`` so the route
        pack resolves the same object the test is configuring. A module with no
        stub gets ``bot`` *removed* rather than left as somebody else's — an
        accidental stub is worse than none, since it answers instead of raising.
    """
    saved_url = os.environ.get("DATABASE_URL", _ABSENT)
    saved_bot = sys.modules.get("bot", _ABSENT)

    _bind(request.module)

    # `_SCHEMA_READY` is a module-level flag whose whole purpose is to stop the
    # DDL running twice in one process. Pointed at a different database it
    # becomes a claim about a file this test has never opened.
    _schema.reset_schema_cache()

    try:
        yield
    finally:
        _schema.reset_schema_cache()

        if saved_bot is _ABSENT:
            sys.modules.pop("bot", None)
        else:
            sys.modules["bot"] = saved_bot

        if saved_url is _ABSENT:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = saved_url
