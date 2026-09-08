"""Read a JSON report out of a booted app's stdout.

The web-surface suites answer questions only the real Flask app can answer --
does this URL resolve, where does it redirect, what does the page actually
serve -- by booting ``bot`` in a child process and having it print a JSON
report. Importing ``bot`` writes ~117kB of banner and logging to stdout, so the
report is fenced behind a sentinel and the reader takes everything after it.

The fence is not enough on its own. ``bot`` starts background worker threads,
and one of them claims the notification jobs the probe's own fixtures create,
emitting a ``PUSH_TRACE`` line per job. Those lines arrive *after* the report is
written, so ``json.loads(stdout.split(SENTINEL, 1)[1])`` is handed a complete
JSON object followed by log text and raises ``Extra data``. Whether it fires is
a race between the probe finishing and the worker's first poll, so a suite can
pass all day and then error on every test in it -- ten setup errors at once,
none of them about the thing being tested.

Parsing with ``raw_decode`` keeps the useful half of the strictness. It reads
one JSON value and reports where that value ended, so trailing log noise is
ignored, while a report truncated by a probe that died mid-write still fails
loudly rather than being silently accepted as a smaller report.

Every probe suite shares this so the next one written cannot reintroduce the
race by copying an older ``_run_probe``.
"""

import json

import pytest

#: Printed by the probe immediately before its JSON payload.
SENTINEL = "<<<REPORT>>>"


def parse_report(stdout: str, stderr: str = "") -> dict:
    """Return the probe's report, tolerating log output on either side of it."""
    if SENTINEL not in stdout:
        pytest.fail("the app did not boot:\n" + stdout[-4000:] + stderr[-4000:])
    tail = stdout.split(SENTINEL, 1)[1].lstrip()
    try:
        report, _ = json.JSONDecoder().raw_decode(tail)
    except ValueError as exc:
        # Not trailing noise -- raw_decode ignores that. Either the probe died
        # partway through writing, or it printed something that is not JSON.
        pytest.fail("the report after %s is not valid JSON (%s):\n%s"
                    % (SENTINEL, exc, tail[:4000]))
    return report
