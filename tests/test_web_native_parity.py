"""The website must not drift further behind the native app.

``linking.ts`` declares ``https://pulsesoc.com`` as a universal-link prefix, so
every native deep-link path is also a URL the web server is expected to answer.
When a native destination has no Flask rule, a shared link — from the native
share sheet, a push notification, or an email — lands on a 404 for anyone
without the app installed. There is no catch-all rule and no 404 handler, so
the failure is silent from the app's side and total from the visitor's.

One such path exists today. This test does not demand it be fixed; it pins the
set so it can only shrink. A newly added native screen with a deep link and no
web route fails here, naming the route.

Regenerate the fixture after changing routes or navigation:

    python scripts/parity/dump_url_map.py --out scripts/parity/url_map_snapshot.json

Use ``--out``, not a shell redirect: importing ``bot`` writes ~117kB of boot
banner to stdout, so ``dump_url_map.py > snapshot.json`` produces a file that
begins with log lines and is not valid JSON.
"""

import json
import pathlib
import subprocess
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PARITY = REPO_ROOT / "scripts" / "parity"


def _matrix() -> list[dict]:
    result = subprocess.run(
        [
            sys.executable,
            str(PARITY / "build_parity_matrix.py"),
            "--url-map",
            str(PARITY / "url_map_snapshot.json"),
        ],
        capture_output=True,
        text=True,
        check=True,
        cwd=str(REPO_ROOT),
    )
    return json.loads(result.stdout)["matrix"]


def _baseline() -> set[str]:
    data = json.loads((PARITY / "parity_baseline.json").read_text())
    return set(data["missing_native_routes"])


def test_no_new_native_destination_is_unreachable_on_the_web():
    missing = {row["native_route"] for row in _matrix() if row["parity"] == "MISSING"}
    baseline = _baseline()
    regressions = sorted(missing - baseline)
    assert not regressions, (
        "These native destinations have no web route, so a shared "
        f"https://pulsesoc.com link 404s: {regressions}"
    )


def test_closed_parity_gaps_are_removed_from_the_baseline():
    # Otherwise the baseline silently keeps claiming a gap that was fixed, and
    # the next regression hides inside a stale allowance.
    missing = {row["native_route"] for row in _matrix() if row["parity"] == "MISSING"}
    stale = sorted(_baseline() - missing)
    assert not stale, f"Fixed on web — drop from parity_baseline.json: {stale}"


def test_the_generated_parity_documents_are_not_stale():
    """A stale inventory is worse than no inventory.

    ``docs/parity/*.md`` carry a "do not edit by hand, regenerate" banner, and
    they are what someone reads to answer "what does the web not have yet".
    Nothing forced them to be regenerated, so they fell three snapshot updates
    behind and went on claiming 39 native destinations had no web route. All 39
    existed: `/pulse/dashboard` was serving a 168kB page while the document said
    it was MISSING. A document in that state does not merely fail to help -- it
    sends someone to rebuild a page that is already live.
    """
    result = subprocess.run(
        [sys.executable, str(PARITY / "render_reports.py"), "--check"],
        capture_output=True, text=True, cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        "docs/parity is out of date with the extractors:\n"
        f"{result.stderr.strip() or result.stdout.strip()}"
    )


def test_a_verdict_is_attributed_to_the_rule_that_would_actually_serve_it():
    """The verdict has to come from the handler Werkzeug would run.

    Several rules can match one path, and the matrix reports one verdict, so it
    has to pick the same one the server does -- Werkzeug resolves by specificity,
    not by declaration order. Taking the first rule that matched instead put the
    verdict on a handler that never runs, and it did so in the one direction the
    matrix exists to catch: ``/saved`` scored PARITY on ``/<slug>``, the SEO
    topic-page rule, which answers nine bytes of "Not found" for anything it does
    not recognise. It went on scoring PARITY after a literal ``/saved`` route was
    added, because ``/<slug>`` is declared 38,000 lines earlier.

    So: where the app's destination is itself a registered rule, that rule is
    what answers, and the matrix must say so. Derived from the url_map rather
    than pinned to the eleven rows that were wrong, because the next
    misattribution will be somewhere else.

    Without this the misattribution is only caught by the staleness check above,
    whose remedy -- regenerate the documents -- would bake the wrong verdicts in
    rather than report them.
    """
    rules = set(json.loads((PARITY / "url_map_snapshot.json").read_text()))
    misattributed = []
    for row in _matrix():
        route = row["native_route"]
        # No rows: the rule exists but its handler was not statically reachable
        # (BLUEPRINT_UNVERIFIED), or nothing matched at all (MISSING). Neither is
        # an attribution question.
        if not row["web_rules"] or route not in rules:
            continue
        if route not in row["web_rules"]:
            misattributed.append(
                f"  {route} is itself a registered rule, but the matrix credits "
                f"{row['web_rules']} and reports {row['parity']}"
            )
    assert not misattributed, (
        "these verdicts describe a handler that never runs for the path, because "
        "a more specific rule answers it first:\n" + "\n".join(misattributed)
    )


def test_the_matrix_still_classifies_the_primary_destinations():
    # A matcher bug that silently returned MISSING for everything would make
    # the guard above vacuous, so pin destinations known to be served.
    matrix = _matrix()
    served = {row["native_route"] for row in matrix if row["parity"] == "PARITY"}
    for route in ("/pulse", "/pulse/reels", "/pulse/messages", "/pulse/marketplace"):
        assert route in served, f"{route} should resolve to a web page"
