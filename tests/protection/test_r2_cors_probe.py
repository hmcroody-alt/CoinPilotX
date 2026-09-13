"""Locks for the probe that says whether a browser can upload to R2.

The probe reports a **blocker**: as of 2026-09-13 `pulse-media2` has no CORS
configuration, so every browser upload fails at preflight. Nothing shipped is
affected -- React Native's fetch is not subject to CORS -- which is exactly why
the answer has to come from a probe rather than from any test or user.

The risk these lock is the probe quietly starting to say "fine". It reads live
infrastructure, so CI cannot run it and nobody will notice it going wrong; and
every way it can break -- a header name that stops matching, an assessment that
returns no problems -- makes it *greener*, not louder. A probe that reports a
blocker resolved when it is not would retire this item from the phase plan on
no evidence.

So these drive `assess()` with synthesised preflight responses. No network.

Zero-arg tests, no fixtures: this directory runs files as scripts.
"""

import importlib.util
import pathlib

REPO = pathlib.Path(__file__).resolve().parents[2]
PROBE_PATH = REPO / "scripts" / "ops" / "r2_cors_probe.py"


def _load():
    spec = importlib.util.spec_from_file_location("r2_cors_probe", PROBE_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


PROBE = _load()
ORIGIN = "https://pulsesoc.com"

GOOD = {
    "Access-Control-Allow-Origin": ORIGIN,
    "Access-Control-Allow-Methods": "GET, PUT, HEAD",
    "Access-Control-Expose-Headers": "ETag",
}


def _assess(headers, status=200, body=""):
    problems, _observed = PROBE.assess(status, headers, body, ORIGIN)
    return problems


def test_a_correct_policy_reports_no_problems():
    assert _assess(GOOD) == [], _assess(GOOD)


def test_the_measured_production_response_is_reported_as_a_blocker():
    """The real answer from `pulse-media2` on 2026-09-13, verbatim.

    R2 returns 403 with an empty header set and names the cause in the body.
    If this ever stops being flagged, the probe has started calling an
    unconfigured bucket ready.
    """
    body = ("<?xml version=\"1.0\" encoding=\"UTF-8\"?><Error><Code>Unauthorized"
            "</Code><Message>CORS not configured for this bucket</Message></Error>")
    problems = _assess({}, status=403, body=body)
    assert problems, "an unconfigured bucket was reported as ready"
    assert "CORS not configured for this bucket" in problems[0], (
        "R2 names the cause itself; quoting it beats inferring it")


def test_a_missing_etag_is_a_problem_even_when_everything_else_is_right():
    """The failure this whole item exists for, and the easiest one to miss.

    Allow-Origin matches, PUT is allowed, the preflight returns 200 -- a browser
    uploads happily right up to the 16 MB multipart threshold, and then every
    part is dropped because its ETag was unreadable. A probe that only checked
    the origin would call this green.
    """
    headers = dict(GOOD)
    headers["Access-Control-Expose-Headers"] = "Content-Length"
    problems = _assess(headers)
    assert problems, "a policy that hides ETag was reported as ready"
    assert any("ETag" in p for p in problems), problems


def test_no_expose_headers_at_all_is_a_problem():
    headers = {k: v for k, v in GOOD.items()
               if k != "Access-Control-Expose-Headers"}
    assert _assess(headers), "an absent ExposeHeaders was treated as sufficient"


def test_a_policy_without_put_is_a_problem():
    headers = dict(GOOD)
    headers["Access-Control-Allow-Methods"] = "GET, HEAD"
    problems = _assess(headers)
    assert any("PUT" in p for p in problems), problems


def test_a_mismatched_origin_is_a_problem():
    headers = dict(GOOD)
    headers["Access-Control-Allow-Origin"] = "https://example.com"
    assert _assess(headers), "a policy for someone else's origin was accepted"


def test_a_wildcard_origin_is_flagged_even_though_it_works():
    """It would pass a purely functional check, and it should not pass silently.

    Presigned URLs carry their own authorisation, so `*` is not a data-exposure
    hole by itself. It does let any page on the internet spend an intercepted
    URL, which is a decision someone should make on purpose rather than inherit
    from a probe that called it fine.
    """
    headers = dict(GOOD)
    headers["Access-Control-Allow-Origin"] = "*"
    problems = _assess(headers)
    assert any("*" in p for p in problems), problems


def test_header_matching_is_case_insensitive():
    """HTTP header names and this policy's values both vary in case.

    `ExposeHeaders: ["etag"]` is the same policy as `["ETag"]`, and a probe that
    only matched one spelling would report a correctly configured bucket as
    broken -- a false alarm, which is how a check gets ignored.

    Both spellings are asserted, and that is the point rather than thoroughness
    for its own sake. An earlier version sent only the lowercase one, which
    cannot detect the normalisation being removed at all: with `.lower()` gone,
    lowercase input still matches lowercase constants and this test still
    passes. It looked covered because `GOOD` above happens to be title-case, so
    the other tests caught it incidentally -- a property held by accident
    somewhere else, which the next edit to `GOOD` would quietly drop.
    """
    quiet = {
        "access-control-allow-origin": ORIGIN,
        "access-control-allow-methods": "get, put, head",
        "access-control-expose-headers": "etag",
    }
    assert _assess(quiet) == [], _assess(quiet)

    shouting = {
        "ACCESS-CONTROL-ALLOW-ORIGIN": ORIGIN,
        "ACCESS-CONTROL-ALLOW-METHODS": "GET, PUT, HEAD",
        "ACCESS-CONTROL-EXPOSE-HEADERS": "ETAG",
    }
    assert _assess(shouting) == [], _assess(shouting)


def test_the_recommended_policy_is_the_one_that_fixes_the_problem():
    """The remediation the probe prints has to pass the probe's own check."""
    rule = PROBE.recommended([ORIGIN])["CORSRules"][0]
    headers = {
        "Access-Control-Allow-Origin": rule["AllowedOrigins"][0],
        "Access-Control-Allow-Methods": ", ".join(rule["AllowedMethods"]),
        "Access-Control-Expose-Headers": ", ".join(rule["ExposeHeaders"]),
    }
    assert _assess(headers) == [], (
        "the probe recommends a policy it would itself report as broken")
    assert "*" not in rule["AllowedOrigins"], (
        "the printed remediation must not hand anyone a wildcard origin")


def test_the_probe_does_not_write():
    """Read-only is a property of this script, not an intention in its header."""
    source = PROBE_PATH.read_text(encoding="utf-8")
    for forbidden in ("put_bucket_cors", "delete_bucket_cors", "put_object",
                      "delete_object", "create_multipart_upload"):
        assert forbidden not in source, (
            f"{forbidden} appeared in a probe documented as read-only against "
            f"production object storage")


def test_the_exit_codes_stay_distinct():
    assert PROBE.EXIT_OK == 0
    assert PROBE.EXIT_MISCONFIGURED == 1
    assert PROBE.EXIT_NO_DATA == 3


if __name__ == "__main__":
    import pathlib as _pathlib
    import sys as _sys

    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
