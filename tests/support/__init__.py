"""Shared helpers for the pytest suites.

Deliberately import-light: several suites in this repository point
``DATABASE_URL`` at their own temporary SQLite file at module import time, so
anything in here must be safe to import before that has happened and must never
resolve a connection at import time.
"""
