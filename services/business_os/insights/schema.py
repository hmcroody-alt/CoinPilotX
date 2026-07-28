"""Business OS — Section 7: Insights schema bridge.

This module owns NO table. Insights is a pure read facade that unifies three existing
analytics engines, so "ensuring the schema" simply means idempotently bootstrapping the
three underlying engines' own tables (attribution / recommendations / performance). Each
delegated ``ensure_schema`` is itself idempotent (``CREATE TABLE IF NOT EXISTS``), so this
is a safe no-op against a database where they already exist.
"""

from __future__ import annotations

from services.business_os.attribution import schema as _attr_schema
from services.business_os.recommendations import schema as _rec_schema
from services.business_os.performance import schema as _perf_schema


def ensure_schema(conn=None) -> None:
    # No Insights-owned table; unify the three canonical analytics engines' stores.
    _attr_schema.ensure_schema()
    _rec_schema.ensure_schema()
    _perf_schema.ensure_schema()
