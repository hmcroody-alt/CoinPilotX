"""Shared fixtures for the Sentinel V1 test suite.

Every test runs against an isolated in-memory SQLite connection with the
full Sentinel schema, and with all SENTINEL_* environment switches cleared
so kill-switch defaults (OFF) are what's actually under test.
"""

import os
import sqlite3

import pytest

from services.sentinel import store


@pytest.fixture(autouse=True)
def _clean_sentinel_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("SENTINEL_"):
            monkeypatch.delenv(key, raising=False)
    yield


@pytest.fixture()
def conn():
    c = sqlite3.connect(":memory:")
    store.ensure_schema(c)
    yield c
    c.close()
