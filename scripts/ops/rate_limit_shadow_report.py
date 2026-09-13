#!/usr/bin/env python3
"""What would the distributed rate limiter have refused?

`services/sentinel/rate_limit.py` ships default OFF because switching a
*correct* limiter on is not a neutral act. Production has only ever experienced
`bot.basic_abuse_guard`'s per-worker buckets, and the Procfile runs
`gunicorn --workers ${WEB_CONCURRENCY:-4}` with WEB_CONCURRENCY unset, so a
documented "6 per 300 seconds" has in fact permitted up to 24. Correct
enforcement is therefore a ~4x tightening on real users of a shipped iOS client
that cannot be updated. `shadow` mode counts without refusing so the tightening
can be measured before it is imposed.

This is the reader for that measurement. Without it, `shadow` is an off switch
with extra steps: the mode's own output is a `logger.warning` into Railway logs,
which age out of the queryable window within the hour, and `rate_limit.stats()`
lives in one worker's memory and dies with it.

Three things this report is careful about
-----------------------------------------

**`hits` is a lower bound, not the count.** `check()` consults the process-local
bucket first and skips the database entirely when a single worker has already
seen more than the limit -- correct for a limiter, because one worker over the
limit proves the fleet is, but it means the shared counter stops being written
to at exactly the moment traffic gets interesting. Every number below therefore
under-reports a flood. A row at or above its limit is real; the absence of one
is not proof of absence.

**The window is short and the sweep is aggressive.** Rows older than
`PRUNE_RETENTION_WINDOWS` (4) windows are deleted -- twenty minutes for the
300-second windows every protected path uses. That is right for a limiter and
wrong for a rollout measurement, so this script reports only what is currently
visible and says how far back that reaches. Run it on a schedule at least that
often, or you are sampling a fraction of the traffic and calling it a period.

**The limits come from bot.py, not from here.** `ABUSE_GUARD_PROTECTED` is the
single deployed table of paths and limits, and restating the numbers in this
file would create a second rate-limit policy that disagrees with the first
eventually. They are parsed out of the source rather than imported, because
importing `bot` boots a 124k-line Flask monolith.

Usage
-----

    railway run --service Postgres python scripts/ops/rate_limit_shadow_report.py
    ... --json          machine-readable, for a scheduled sampler

Exit codes: 0 rows observed, 3 no rows (see below), 1 the report could not run.

Rollout runbook
---------------

1. Set ``SENTINEL_DISTRIBUTED_LIMITS_MODE=shadow`` in Railway. A variable
   change, not a deploy of new behaviour: shadow counts and refuses nothing.
   The default must stay ``off`` in code -- turning this on is an operator
   decision made against a measurement, and a commit cannot make it.
2. Sample at least every 20 minutes, because that is the retention window.
   Less often and you are reading a fraction of the traffic as though it were
   the period. Exit 3 on the first run after the switch means either the mode
   did not take or nothing was hit; ``rate_limit.stats()['degraded']`` on a
   live worker separates those.
3. Read the ``over-subj`` column. Every one of those is a real caller that is
   not refused today and would be after enforcement. Browsers send no device
   id, so web traffic keys per-IP, and one office NAT is one subject -- decide
   whether a number is abuse or a building before treating it as abuse.
4. Only then consider ``enforce``, and per path rather than all at once.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import pathlib
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[2]

EXIT_OK = 0
EXIT_NO_DATA = 3          # distinct from success: nothing observed != nothing happened
EXIT_ERROR = 1


def protected_limits() -> dict:
    """Parse ABUSE_GUARD_PROTECTED out of bot.py.

    Parsed, not imported, and not copied. A copy would be a second source of
    truth; an import would boot the monolith.
    """
    source = (REPO / "bot.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        if "ABUSE_GUARD_PROTECTED" not in names:
            continue
        table = ast.literal_eval(node.value)
        return {str(path): (int(limit), int(window))
                for path, (limit, window) in table.items()}
    raise RuntimeError(
        "ABUSE_GUARD_PROTECTED not found in bot.py. It is the single source of "
        "rate-limit policy; this report cannot invent one.")


def connect():
    url = (os.getenv("DATABASE_PUBLIC_URL") or os.getenv("DATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError(
            "No DATABASE_URL. Run under `railway run --service Postgres`.")
    import psycopg2  # imported late so --help works without the driver
    return psycopg2.connect(url)


def redact(text: str) -> str:
    """Connection strings appear in psycopg2 errors, password included."""
    import re
    return re.sub(r"://[^:/@\s]+:[^@/\s]+@", "://<redacted>:<redacted>@", str(text))


def fit(text: str, width: int) -> str:
    """Shorten to ``width``, from the middle, and visibly.

    `text[:width]` is the obvious version and it is wrong for paths: this
    platform has route families like `/api/business-os/...` whose members share
    a long prefix and differ only at the tail, so head-truncation renders two
    different scopes as the same row and an operator reads one line as covering
    both. Truncating the middle keeps both ends, and the `...` says a decision
    was made.
    """
    if len(text) <= width:
        return text
    keep = width - 3
    head = (keep + 1) // 2
    return text[:head] + "..." + text[len(text) - (keep - head):]


def collect(conn, limits: dict) -> dict:
    cur = conn.cursor()
    cur.execute(
        "SELECT scope, subject, window_start, hits FROM sentinel_rate_counters "
        "ORDER BY window_start")
    rows = cur.fetchall()

    now = int(time.time())
    per_scope: dict = {}
    oldest = None
    newest = None

    for scope, subject, window_start, hits in rows:
        window_start = int(window_start)
        hits = int(hits)
        oldest = window_start if oldest is None else min(oldest, window_start)
        newest = window_start if newest is None else max(newest, window_start)

        # None, not 0, for a scope with no deployed limit. `0` in a limit column
        # reads as "limit of zero", i.e. refuse everything -- the opposite of
        # what an unrecognised path means. Absent has to look absent. It stays
        # falsy either way, so the refusal test below is unaffected.
        limit, window = limits.get(scope, (None, None))
        entry = per_scope.setdefault(scope, {
            "limit": limit, "window_seconds": window, "known_scope": scope in limits,
            "windows": 0, "subjects": set(), "max_hits": 0,
            "would_refuse_windows": 0, "would_refuse_subjects": set(),
        })
        entry["windows"] += 1
        entry["subjects"].add(subject)
        entry["max_hits"] = max(entry["max_hits"], hits)
        # `>` and not `>=`: check() refuses on `estimate > limit`, so a subject
        # sitting exactly on the limit is allowed. Using `>=` here would report
        # a tightening that enforcement would not actually have applied.
        if limit and hits > limit:
            entry["would_refuse_windows"] += 1
            entry["would_refuse_subjects"].add(subject)

    scopes = {}
    for scope, entry in sorted(per_scope.items()):
        scopes[scope] = {
            "limit": entry["limit"],
            "window_seconds": entry["window_seconds"],
            "known_scope": entry["known_scope"],
            "windows_observed": entry["windows"],
            "distinct_subjects": len(entry["subjects"]),
            "max_hits_in_a_window": entry["max_hits"],
            "would_refuse_windows": entry["would_refuse_windows"],
            "would_refuse_subjects": len(entry["would_refuse_subjects"]),
        }

    return {
        "rows": len(rows),
        "observed_from": oldest,
        "observed_to": newest,
        "observed_span_seconds": (newest - oldest) if (oldest and newest) else 0,
        "sampled_at": now,
        "scopes": scopes,
        # Stated in the output, not just the docstring, so a report pasted into
        # a ticket carries its own caveat.
        "caveats": [
            "hits is a lower bound: the process-local short circuit stops "
            "writing to this table once one worker is over the limit",
            "rows older than 4 windows are pruned, so this is a sample of the "
            "visible window, not of the whole rollout",
        ],
    }


def render(report: dict) -> str:
    lines = []
    if not report["rows"]:
        lines.append("NO DATA: sentinel_rate_counters is empty.")
        lines.append("")
        lines.append("Either SENTINEL_DISTRIBUTED_LIMITS_MODE is off (nothing is")
        lines.append("counting), or every check degraded to process memory (the")
        lines.append("counter could not reach the database), or no protected path")
        lines.append("was hit in the retention window. Those are three different")
        lines.append("situations and this report cannot tell them apart -- check")
        lines.append("rate_limit.stats()['degraded'] on a live worker to separate")
        lines.append("the second from the other two.")
        return "\n".join(lines)

    span = report["observed_span_seconds"]
    lines.append(f"Observed {report['rows']} counter rows across {span}s "
                 f"({span / 60.0:.1f} min) of traffic.")
    lines.append("")
    header = (f"{'path':<44} {'limit':>6} {'peak':>6} {'subjects':>9} "
              f"{'over':>6} {'over-subj':>10}")
    lines.append(header)
    lines.append("-" * len(header))
    for scope, entry in report["scopes"].items():
        marker = "" if entry["known_scope"] else "  (not in ABUSE_GUARD_PROTECTED)"
        limit = entry["limit"] if entry["known_scope"] else "-"
        lines.append(
            f"{fit(scope, 44):<44} {limit!s:>6} {entry['max_hits_in_a_window']:>6} "
            f"{entry['distinct_subjects']:>9} {entry['would_refuse_windows']:>6} "
            f"{entry['would_refuse_subjects']:>10}{marker}")

    total_over = sum(e["would_refuse_subjects"] for e in report["scopes"].values())
    lines.append("")
    if total_over:
        lines.append(f"ENFORCEMENT WOULD HAVE REFUSED {total_over} distinct subject(s).")
        lines.append("Each one is a real caller that is not refused today. Decide "
                     "whether that is abuse or a shared NAT before enforcing.")
    else:
        lines.append("No subject exceeded its limit in the visible window.")
    lines.append("")
    for caveat in report["caveats"]:
        lines.append(f"  caveat: {caveat}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true",
                        help="machine-readable output for a scheduled sampler")
    args = parser.parse_args()

    try:
        limits = protected_limits()
        conn = connect()
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {redact(exc)}", file=sys.stderr)
        return EXIT_ERROR

    try:
        report = collect(conn, limits)
    except Exception as exc:
        print(f"ERROR: {type(exc).__name__}: {redact(exc)}", file=sys.stderr)
        return EXIT_ERROR
    finally:
        try:
            conn.close()
        except Exception:
            pass

    print(json.dumps(report, indent=2, sort_keys=True) if args.json else render(report))
    # "I saw nothing" is not "nothing happened" -- the same distinction the
    # backup script draws between verified and unverified. A scheduled sampler
    # that treated an empty table as a clean bill of health would report a
    # broken counter as a quiet success.
    return EXIT_OK if report["rows"] else EXIT_NO_DATA


if __name__ == "__main__":
    raise SystemExit(main())
