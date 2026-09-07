"""The website must not drift further behind the native app.

``linking.ts`` declares ``https://pulsesoc.com`` as a universal-link prefix, so
every native deep-link path is also a URL the web server is expected to answer.
When a native destination has no Flask rule, a shared link — from the native
share sheet, a push notification, or an email — lands on a 404 for anyone
without the app installed. There is no catch-all rule and no 404 handler, so
the failure is silent from the app's side and total from the visitor's.

34 such paths exist today. This test does not demand they be fixed; it pins
them so the number can only go down. A newly added native screen with a deep
link and no web route fails here, naming the route.

Regenerate the fixtures together after changing routes or navigation:

    python scripts/parity/dump_url_map.py > scripts/parity/url_map_snapshot.json
    python scripts/parity/build_parity_matrix.py \
        --url-map scripts/parity/url_map_snapshot.json > /tmp/matrix.json
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


def test_the_matrix_still_classifies_the_primary_destinations():
    # A matcher bug that silently returned MISSING for everything would make
    # the guard above vacuous, so pin destinations known to be served.
    matrix = _matrix()
    served = {row["native_route"] for row in matrix if row["parity"] == "PARITY"}
    for route in ("/pulse", "/pulse/reels", "/pulse/messages", "/pulse/marketplace"):
        assert route in served, f"{route} should resolve to a web page"
