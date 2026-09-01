"""Shared pytest fixtures for the backend suite."""

import datetime as _datetime
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --------------------------------------------------------------------------
# Interpreter-version shim — test environments only
#
# Production pins Python 3.11 via nixpacks, so `from datetime import UTC` in
# services/feature_flag_engine.py and services/reliability_engine.py is correct
# production code. Some development sandboxes still run 3.10, where `UTC` does
# not exist; because bot.py imports feature_flag_engine at module scope, that
# single missing alias makes `import bot` raise and takes the *entire* backend
# suite down at collection — a red suite that says nothing about the code.
#
# `datetime.UTC` is defined in 3.11 as a plain alias of `timezone.utc`, so this
# reproduces it exactly rather than approximating it. The guard is on the
# version, not on absence, so on 3.11 and later this block does nothing at all
# and cannot mask a real defect on the interpreter production runs.
# --------------------------------------------------------------------------
if sys.version_info < (3, 11) and not hasattr(_datetime, "UTC"):
    _datetime.UTC = _datetime.timezone.utc

from services import schema_guard  # noqa: E402


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
