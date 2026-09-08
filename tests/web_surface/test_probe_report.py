"""The reader every web-surface probe shares.

Seven suites boot the real Flask app in a child process and read a JSON report
out of its stdout. They all used `json.loads(stdout.split(SENTINEL, 1)[1])`,
which assumes nothing is printed after the report. That assumption is false:
`bot` runs background worker threads, and one of them claims the notification
jobs a probe's own fixtures create, logging a `PUSH_TRACE` line per job. When
those lines land after the report, `json.loads` sees `Extra data` and every
test sharing the module-scoped fixture errors at setup — ten at once in
`test_activity_feed.py`, none of them about what they were testing.

The failure is a race, so it is invisible until it isn't. These tests pin the
tolerance so a future edit cannot quietly restore the strict parse, and pin the
strictness that must survive alongside it: a report cut short by a probe that
died mid-write has to keep failing, because `raw_decode` would otherwise be a
licence to accept half a report as a whole one.
"""

import json

import pytest

from tests.probe_report import SENTINEL, parse_report

# `pytest.fail` raises `Failed`, which descends from `BaseException`, not
# `Exception` — `pytest.raises(Exception)` silently fails to catch it.
FAILED = pytest.fail.Exception

BOOT_NOISE = "CoinPilotX web boot complete\n2026-09-08 12:00:00,000 - ready\n"

#: Verbatim shape of what actually broke the suite: worker log lines emitted
#: after the report was written.
PUSH_TRACE = (
    "2026-09-08 12:17:54,621 - PUSH_TRACE stage=os_job_claimed worker=web:pid1 "
    "job_id=1 notification_id=1 user_id=1 channel=email\n"
    "2026-09-08 12:17:54,622 - PUSH_TRACE stage=os_job_claimed worker=web:pid1 "
    "job_id=2 notification_id=1 user_id=1 channel=push\n"
)

REPORT = {"pages": {"/pulse/activity": {"status": 200}}, "resolves": {"/x": True}}


def _stdout(report_text, trailing=""):
    return BOOT_NOISE + SENTINEL + report_text + trailing


# --- the tolerance the seven suites depend on --------------------------------


def test_worker_logs_after_the_report_do_not_break_the_parse():
    assert parse_report(_stdout(json.dumps(REPORT), PUSH_TRACE)) == REPORT


def test_the_report_still_parses_when_nothing_follows_it():
    assert parse_report(_stdout(json.dumps(REPORT))) == REPORT


def test_boot_noise_before_the_sentinel_is_discarded():
    """The whole reason the sentinel exists — importing `bot` prints ~117kB."""
    noisy = "x" * 120000 + "\n" + SENTINEL + json.dumps(REPORT)
    assert parse_report(noisy) == REPORT


def test_a_newline_between_the_sentinel_and_the_report_is_tolerated():
    assert parse_report(_stdout("\n  " + json.dumps(REPORT), PUSH_TRACE)) == REPORT


# --- the strictness that must survive the tolerance --------------------------


def test_a_report_cut_off_mid_write_still_fails():
    """A probe killed partway through leaves valid-looking JSON with no closing
    brace. `raw_decode` must reject it rather than treat the fragment as the
    report — that would turn a dead probe into a passing test with empty data."""
    truncated = json.dumps(REPORT)[:40]
    with pytest.raises(pytest.fail.Exception) as excinfo:
        parse_report(_stdout(truncated))
    assert "not valid JSON" in str(excinfo.value)


def test_a_probe_that_never_reached_the_sentinel_reports_the_boot_failure():
    with pytest.raises(pytest.fail.Exception) as excinfo:
        parse_report("Traceback (most recent call last):\n", "ImportError: boom\n")
    message = str(excinfo.value)
    assert "the app did not boot" in message
    assert "ImportError: boom" in message, (
        "stderr must reach the failure message; the boot error is usually only "
        "there, and without it the report is just 'did not boot'")


def test_a_non_json_payload_after_the_sentinel_fails():
    with pytest.raises(pytest.fail.Exception) as excinfo:
        parse_report(_stdout("Segmentation fault\n"))
    assert "not valid JSON" in str(excinfo.value)


# --- nobody may reintroduce the strict parse ---------------------------------


def test_every_probe_suite_reads_its_report_through_this_helper():
    """The bug was one line copied into seven files. It stays fixed only if the
    eighth suite copies the helper instead of the line."""
    import os
    import re

    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    offenders = []
    users = []
    for folder in ("tests",):
        for root, _dirs, files in os.walk(os.path.join(repo, folder)):
            for name in files:
                if not name.endswith(".py"):
                    continue
                path = os.path.join(root, name)
                source = open(path, encoding="utf-8").read()
                if "<<<REPORT>>>" not in source:
                    continue
                if path.endswith(os.path.join("tests", "probe_report.py")):
                    continue
                if path.endswith(os.path.join("web_surface", "test_probe_report.py")):
                    continue
                users.append(os.path.relpath(path, repo))
                if re.search(r'json\.loads\([^\n]*<<<REPORT>>>', source):
                    offenders.append(os.path.relpath(path, repo))

    assert os.path.join("tests", "web_surface", "test_activity_feed.py") in users, (
        "the walk no longer reaches the suite whose ten setup errors exposed "
        "this bug, so it is looking at the wrong tree. Found: %s" % users)
    assert len(users) >= 6, (
        "only %d probe suites found; this guard would pass even if every one "
        "of them regressed" % len(users))
    assert not offenders, (
        "these suites parse the report themselves and will error on every test "
        "in them whenever a worker thread logs after the report: %s" % offenders)
