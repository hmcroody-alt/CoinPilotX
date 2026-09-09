"""Opt-in record of which of our own validators refused a provider response.

This is deliberately not a log sink -- these modules are barred from those, and
that ban is the reason the gap exists. A dozen validators in the CJ adapter all
raise `MALFORMED_PROVIDER_RESPONSE` and the route answers every one with the
same opaque 502, so a provider response we refuse in production-like conditions
gives an operator nothing to act on but the guess-and-loosen loop.

What is stored is a raise site: a source basename and a line number, both
compile-time constants of this repository. No provider body, no message, no
header, no credential, no request, no actor, no tenant -- there is nothing here
for a leak to carry, which is what makes recording it at all defensible.

Off unless `CJ_SUPPLIER_DIAGNOSTIC_ORIGIN` is set, and self-bounding: the table
keeps only the most recent rows, so a deployment left with the flag on cannot
grow an unbounded table out of a repeated failure.
"""
from __future__ import annotations

import re
import time

from services import db

KEEP_ROWS = 200
_ORIGIN = re.compile(r"[A-Za-z0-9_.]{1,64}:[0-9]{1,6}")


def ensure_schema(conn) -> None:
    conn.execute("""CREATE TABLE IF NOT EXISTS business_os_cj_diagnostic_origin (
        observed_at DOUBLE PRECISION NOT NULL,
        code TEXT NOT NULL, status INTEGER NOT NULL, origin TEXT NOT NULL)""")


def record(code, status, origin) -> None:
    """Best-effort. A diagnostic must never become a second failure."""
    try:
        # Re-validated here rather than trusted from the caller: this module is
        # the thing that decides what may be persisted, and "" is a legitimate
        # answer meaning the raise happened outside the supplier package.
        origin = origin if isinstance(origin, str) and (origin == "" or _ORIGIN.fullmatch(origin)) else "?"
        code = code if isinstance(code, str) and re.fullmatch(r"[A-Za-z_]{1,80}", code) else "?"
        status = status if type(status) is int and 400 <= status <= 599 else 0
        conn = db.connect()
        try:
            ensure_schema(conn)
            conn.execute("INSERT INTO business_os_cj_diagnostic_origin "
                         "(observed_at, code, status, origin) VALUES (?, ?, ?, ?)",
                         (time.time(), code, status, origin))
            conn.execute("DELETE FROM business_os_cj_diagnostic_origin WHERE observed_at < "
                         "(SELECT MIN(observed_at) FROM (SELECT observed_at FROM "
                         "business_os_cj_diagnostic_origin ORDER BY observed_at DESC "
                         "LIMIT ?) AS keep)", (KEEP_ROWS,))
            conn.commit()
        finally:
            conn.close()
    except Exception:
        pass
