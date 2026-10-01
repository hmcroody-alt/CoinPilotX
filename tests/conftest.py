"""Shared pytest fixtures for the backend suite."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
