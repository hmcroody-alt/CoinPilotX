"""Ad delivery must create its own tables, not inherit them from a URL prefix.

`services/business_os/schema_bootstrap.py` exists precisely so a fresh
production database does not 500 with `UndefinedTable`. But it is driven from a
`before_request` hook in `services/business_os_web.py` that returns early
unless the request path starts with `/api/business-os` or `/business-os`.

Delivery is not entered that way. The Pulse feed asks for ads through
`GET /api/pulse/ads/placements`, which never matches that prefix, so in a
worker where nobody had loaded a Business OS page the advertising tables were
never created. The first statement `request_placement` executes is the
per-viewer rate limit, which SELECTs from `business_os_ad_delivery_instances`:

    UndefinedTable: relation "business_os_ad_delivery_instances" does not exist

Observed in production. The caller swallows it, so there is no user-visible
error -- the ad request just degrades to no-fill and the server-authoritative
frequency cap silently stops capping, which is the failure you least want to be
silent.

This test builds the exact condition: a virgin database with the advertising
flag on and `ensure_schema()` deliberately NEVER called, then requests a
placement. Before the fix this raised "no such table". After it, delivery
bootstraps its own schema and returns an honest no-fill.

    python tests/business_os/test_ad_delivery_schema_bootstrap.py
"""

import os
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_ad_bootstrap_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_ADVERTISING"] = "on"

import sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.advertising import delivery as deliv  # noqa: E402


def _assert(cond, msg):
    if not cond:
        raise AssertionError(msg)


def _table_exists(name: str) -> bool:
    conn = db.connect()
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,))
        return cur.fetchone() is not None
    finally:
        conn.close()


def test_delivery_bootstraps_its_own_schema():
    # Precondition: nothing has created the advertising tables. This is the
    # state of a worker that has only ever served /api/pulse/* traffic.
    _assert(not _table_exists("business_os_ad_delivery_instances"),
            "precondition failed: advertising schema already exists, so this "
            "test would pass even with the bug present")

    result = deliv.request_placement(
        viewer_user_id=4242, placement="feed", request={"request_id": "boot-1"})

    # No campaigns exist, so the honest answer is a no-placement result. What
    # matters is that we got a RESULT rather than an UndefinedTable/OperationalError.
    _assert(isinstance(result, dict), f"expected a dict result, got {type(result)}")
    _assert(not result.get("filled"),
            "a virgin database has no eligible campaigns; a filled placement "
            "would mean the selection layer invented one")

    _assert(_table_exists("business_os_ad_delivery_instances"),
            "delivery did not create its own schema; the frequency cap will "
            "keep failing silently on any non-/business-os entry point")


def test_second_call_is_latched_and_still_works():
    """The once-latch must not turn into a one-shot that breaks later calls."""
    result = deliv.request_placement(
        viewer_user_id=4243, placement="feed", request={"request_id": "boot-2"})
    _assert(isinstance(result, dict), "second placement request did not return a result")
    _assert(not result.get("filled"), "unexpected fill on an empty database")


def test_ensure_runs_on_its_own_connection_so_the_ddl_commits():
    """The bootstrap must let ensure_schema OWN its connection.

    `schema.ensure_schema` commits only `if owned` -- i.e. only when it opened
    the connection itself -- because callers are invited to compose it into a
    larger transaction. Hand it the caller's connection and the CREATE TABLEs
    execute but are never committed. On PostgreSQL DDL is transactional, and the
    ordinary no-placement path returns without committing and then closes, so
    every table is rolled back and the next request starts from nothing again.

    SQLite cannot express this failure: it autocommits DDL, so the behavioural
    test above passes whether or not the commit happens. That is exactly how the
    first version of this fix shipped green and stayed a no-op in production --
    made worse by the latch, which recorded success and stopped any retry.

    So assert the mechanism rather than the outcome: ensure_schema is called
    with no caller connection.
    """
    seen = []
    original = deliv._svc.ensure_schema

    def _spy(conn=None):
        seen.append(conn)
        return original(conn)

    deliv._svc.ensure_schema = _spy
    deliv._SCHEMA_READY = False
    try:
        deliv.request_placement(
            viewer_user_id=4244, placement="feed", request={"request_id": "boot-3"})
    finally:
        deliv._svc.ensure_schema = original
        deliv._SCHEMA_READY = True

    _assert(seen, "ensure_schema was never called; the bootstrap did not run")
    _assert(seen[0] is None,
            "ensure_schema was handed a caller connection (%r), so it skips its "
            "commit and the DDL is rolled back when that connection closes on "
            "PostgreSQL" % (seen[0],))


if __name__ == "__main__":
    test_delivery_bootstraps_its_own_schema()
    test_second_call_is_latched_and_still_works()
    test_ensure_runs_on_its_own_connection_so_the_ddl_commits()
    print("OK ad delivery schema bootstrap")
