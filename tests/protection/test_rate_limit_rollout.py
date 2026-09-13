"""Locks for turning the distributed rate limiter on without lying about it.

The shared counter in `services/sentinel/rate_limit.py` was already built, and
already wired into `bot.basic_abuse_guard`. What was missing is the reason it is
still off: nothing could read what `shadow` mode measured. Its output is a
`logger.warning` into Railway logs that age out of the queryable window within
the hour, and `rate_limit.stats()` lives in one gunicorn worker's memory. A
rollout flag whose observations cannot be read is an off switch with extra
steps, and the predictable outcome is that it is either never turned on or
turned on blind.

`scripts/ops/rate_limit_shadow_report.py` is the reader. These tests cover the
two places it could quietly mislead:

1. Reporting "I saw nothing" as "nothing happened" -- the same defect class as
   the hard-coded `"ok": True` removed from /health, and the `verified: None`
   case the backup script has to keep distinct.
2. Restating the limits instead of reading them, which would create a second
   rate-limit policy disagreeing with the deployed one.

Zero-arg tests, no fixtures: this directory runs files as scripts.
"""

import ast
import importlib.util
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
BOT = (REPO / "bot.py").read_text(encoding="utf-8")
SCRIPT = REPO / "scripts" / "ops" / "rate_limit_shadow_report.py"

sys.path.insert(0, str(REPO))

_spec = importlib.util.spec_from_file_location("shadow_report", SCRIPT)
report_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(report_module)


class _Cursor:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *_args, **_kwargs):
        return None

    def fetchall(self):
        return list(self._rows)


class _Conn:
    """Just enough connection to exercise collect() without a database.

    A real Postgres here would make these tests a docker dependency for a
    boundary condition that is pure arithmetic.
    """

    def __init__(self, rows):
        self._rows = rows

    def cursor(self):
        return _Cursor(self._rows)

    def close(self):
        return None


LIMITS = {"/login": (12, 300), "/forgot-password": (6, 300)}


# --- 1. The report must not invent a policy -----------------------------------

def test_limits_are_read_from_bot_not_restated():
    """`ABUSE_GUARD_PROTECTED` is the single deployed source of rate-limit
    policy. A second copy in the reporting script would disagree with it
    eventually, and the report would then describe a rollout nobody shipped."""
    source = SCRIPT.read_text(encoding="utf-8")
    assert "ABUSE_GUARD_PROTECTED" in source
    assert "ast.literal_eval" in source, "the table must be parsed, not copied"
    for literal in ('"/login": (12, 300)', "'/login': (12, 300)"):
        assert literal not in source, "the script restates a deployed limit"


def test_the_parser_actually_finds_the_deployed_table():
    """Guard the premise. If the table is renamed or moved into a function, a
    parser that silently returned {} would report every path as unknown and
    every subject as under its limit -- a clean bill of health from a broken
    reader."""
    limits = report_module.protected_limits()
    assert limits, "ABUSE_GUARD_PROTECTED parsed as empty"
    assert limits["/login"] == (12, 300)
    # Same set the guard enforces, not a subset that happened to parse.
    table = next(
        ast.literal_eval(node.value)
        for node in ast.walk(ast.parse(BOT))
        if isinstance(node, ast.Assign)
        and any(getattr(t, "id", "") == "ABUSE_GUARD_PROTECTED" for t in node.targets)
    )
    assert set(limits) == set(table)


# --- 2. "I saw nothing" must not become "nothing happened" --------------------

def test_an_empty_table_is_not_reported_as_success():
    """Three different situations produce zero rows: the mode is off, every
    check degraded to process memory, or no protected path was hit. Only the
    third is good news, and the report cannot tell them apart -- so it must not
    claim the good one."""
    report = report_module.collect(_Conn([]), LIMITS)
    assert report["rows"] == 0
    rendered = report_module.render(report)
    assert "NO DATA" in rendered

    # All three causes must be named, each by a phrase that only the cause uses.
    # An earlier version of this test asserted `"degraded" in rendered` and a
    # mutation that deleted the degraded cause from the enumeration still passed
    # it -- the word survived in the remediation line that says to check
    # `rate_limit.stats()['degraded']`. A report that tells you how to diagnose a
    # possibility it no longer lists is the failure this test exists to catch.
    for cause in ("SENTINEL_DISTRIBUTED_LIMITS_MODE is off",
                  "degraded to process memory",
                  "no protected path"):
        assert cause in rendered, f"the empty report stops naming a cause: {cause}"

    assert "cannot tell them apart" in rendered, (
        "listing three causes without saying they are indistinguishable invites "
        "the reader to pick the reassuring one")


def test_the_no_data_exit_code_is_distinct_from_both_success_and_error():
    """A scheduled sampler branches on the exit code. Merging no-data into
    success makes a broken counter invisible; merging it into error makes a
    quiet hour page someone."""
    assert report_module.EXIT_OK == 0
    assert report_module.EXIT_NO_DATA == 3
    assert report_module.EXIT_ERROR == 1
    assert len({report_module.EXIT_OK, report_module.EXIT_NO_DATA,
                report_module.EXIT_ERROR}) == 3


# --- 3. The boundary enforcement actually uses --------------------------------

def test_a_subject_exactly_on_the_limit_is_not_counted_as_refused():
    """`check()` refuses on `estimate > limit`, so 12 hits against a limit of 12
    is allowed. Reporting it as refused would overstate the tightening and argue
    against a rollout that is in fact safe."""
    report = report_module.collect(_Conn([("/login", "ip:a", 1000, 12)]), LIMITS)
    assert report["scopes"]["/login"]["would_refuse_subjects"] == 0


def test_a_subject_one_over_the_limit_is_counted():
    report = report_module.collect(_Conn([("/login", "ip:a", 1000, 13)]), LIMITS)
    assert report["scopes"]["/login"]["would_refuse_subjects"] == 1
    assert "WOULD HAVE REFUSED 1 distinct subject" in report_module.render(report)


def test_one_subject_over_in_several_windows_is_still_one_subject():
    """Windows and subjects answer different questions. Counting windows as
    subjects turns one persistent caller into an apparent outage."""
    rows = [("/login", "ip:a", 1000, 20), ("/login", "ip:a", 1300, 20),
            ("/login", "ip:b", 1300, 3)]
    report = report_module.collect(_Conn(rows), LIMITS)
    scope = report["scopes"]["/login"]
    assert scope["would_refuse_windows"] == 2
    assert scope["would_refuse_subjects"] == 1
    assert scope["distinct_subjects"] == 2


def test_a_scope_with_no_known_limit_is_flagged_not_silently_cleared():
    """A path counted by some other caller has limit 0 here. Treating 0 as "no
    limit, therefore fine" would hide it; the report marks it instead."""
    report = report_module.collect(_Conn([("/api/unknown", "ip:a", 1000, 999)]), LIMITS)
    scope = report["scopes"]["/api/unknown"]
    assert scope["known_scope"] is False
    assert scope["would_refuse_subjects"] == 0
    assert "not in ABUSE_GUARD_PROTECTED" in report_module.render(report)
    # None rather than 0: a `0` in the limit column reads as a limit of zero,
    # i.e. refuse everything, which is the opposite of what this row means.
    assert scope["limit"] is None
    row = next(line for line in report_module.render(report).splitlines()
               if line.startswith("/api/unknown"))
    assert row.split()[1] == "-", row


def test_two_long_paths_do_not_render_as_the_same_row():
    """Route families here share long prefixes -- `/api/business-os/...` has 199
    members. Head-truncating the scope column would print two different paths
    identically, and an operator would read one row as covering both."""
    a = "/api/business-os/tenants/settings/notifications/email/digest/alpha"
    b = "/api/business-os/tenants/settings/notifications/email/digest/omega"
    assert a[:44] == b[:44], "premise: these differ only past the column width"
    report = report_module.collect(
        _Conn([(a, "ip:a", 1000, 5), (b, "ip:b", 1000, 5)]), LIMITS)
    rows = [line for line in report_module.render(report).splitlines()
            if line.startswith("/api/business-os")]
    assert len(rows) == 2, rows
    assert rows[0][:44] != rows[1][:44], rows
    assert all("..." in row for row in rows), (
        "a shortened path must say it was shortened")


def test_the_report_carries_its_own_caveats():
    """A report pasted into a ticket loses its docstring. The two facts that
    make these numbers a lower bound have to travel with the numbers."""
    report = report_module.collect(_Conn([("/login", "ip:a", 1000, 13)]), LIMITS)
    text = " ".join(report["caveats"])
    assert "lower bound" in text
    assert "pruned" in text
    assert "lower bound" in report_module.render(report)


# --- 4. The premises this rollout depends on ---------------------------------

def test_the_limiter_still_ships_off_by_default():
    """Nothing in this mission may flip the default. Turning it on is an
    operator decision made against a measurement, not a code change."""
    from services.sentinel import rate_limit
    import os
    previous = os.environ.pop("SENTINEL_DISTRIBUTED_LIMITS_MODE", None)
    try:
        assert rate_limit.mode() == rate_limit.MODE_OFF
        assert rate_limit.enabled() is False
    finally:
        if previous is not None:
            os.environ["SENTINEL_DISTRIBUTED_LIMITS_MODE"] = previous


def test_an_unrecognised_mode_is_off_rather_than_enforce():
    """A typo in a Railway variable must not start refusing production traffic
    on a shipped client that cannot be updated."""
    from services.sentinel import rate_limit
    import os
    previous = os.environ.get("SENTINEL_DISTRIBUTED_LIMITS_MODE")
    os.environ["SENTINEL_DISTRIBUTED_LIMITS_MODE"] = "enfroce"
    try:
        assert rate_limit.mode() == rate_limit.MODE_OFF
    finally:
        if previous is None:
            os.environ.pop("SENTINEL_DISTRIBUTED_LIMITS_MODE", None)
        else:
            os.environ["SENTINEL_DISTRIBUTED_LIMITS_MODE"] = previous


def test_the_guard_still_consults_the_shared_counter():
    """If this call is removed the report keeps working and keeps reporting
    zero, which reads as "the rollout is safe" rather than "nothing is
    counting"."""
    assert "refused = sentinel_rate_refused(request.path, limit, window_seconds)" in BOT


def test_the_counter_subject_is_the_resolved_address_not_a_raw_header():
    """The whole rollout is only as sound as the subject it counts. Sentinel
    keys on `ip:{client_ip_hash()}`, and client_ip_hash() must go through the
    trusted-address resolver -- otherwise a correct distributed count is kept
    per attacker-chosen string."""
    assert 'f"ip:{client_ip_hash()}"' in BOT
    start = BOT.index("def client_ip_hash(")
    body = BOT[start:start + 1800]
    assert "client_address.client_ip(" in body


if __name__ == "__main__":
    import pathlib as _pathlib
    import sys as _sys

    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
