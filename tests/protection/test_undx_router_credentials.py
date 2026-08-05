"""Provider credentials must not reach the application log.

`undx_router` fans a request out across OpenAI, Claude, Gemini, DeepSeek and
Groq, holding the keys server-side so they never reach the browser. That part
was right. The logging was not.

The Gemini call passed its key as `?key=<API_KEY>`. `requests` embeds the full
request URL in the string form of its exceptions, and `route_undx_request`
logged `str(exc)` on every failed provider call. So a Gemini outage - the
condition most likely to produce a burst of failures - wrote the API key into
the application log once per request. Nothing was compromised by the key being
in the environment; it was compromised by being in the log, where log shipping,
support bundles and screenshots all reach.

Two independent defences, because either alone is one edit from being undone:

  1. The key travels in the `x-goog-api-key` header, so it is not in the URL and
     cannot appear in exception text, proxy access logs, or a referer.
  2. `_safe_error()` redacts credentials from anything about to be logged,
     both by query-parameter name and by matching known key values.

The second exists because the next provider added to this module will be written
by copying an existing one.
"""

import ast
import inspect
import os
import pathlib
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import undx_router  # noqa: E402


FAKE_GEMINI = "AIzaSyFAKE_SECRET_abcdefghijklmnop"


def _with_fake_keys(fn):
    """Populate keys so value-based redaction has something to match."""
    saved = {k: os.environ.get(k) for k in ("Gemini_AI_API", "OPENAI_API_KEY")}
    os.environ["Gemini_AI_API"] = FAKE_GEMINI
    os.environ["OPENAI_API_KEY"] = "sk-fakeopenaikey1234567890"
    try:
        return fn()
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# --- Defence 1: the key is not in the URL ------------------------------------

def test_gemini_key_is_sent_as_a_header_not_a_query_parameter():
    source = inspect.getsource(undx_router._call_gemini)
    assert "x-goog-api-key" in source, (
        "The Gemini key must travel as a header. As `?key=` it is copied into "
        "proxy access logs and into requests' exception text."
    )
    assert 'params={"key"' not in source and "params={'key'" not in source, (
        "The Gemini call passes its API key as a URL query parameter again."
    )


def test_no_provider_passes_credentials_in_a_query_string():
    """Guard every outbound call, not just the one that was wrong.

    Checked by inspecting the actual `requests.*` call sites rather than by
    grepping the file. A substring search for `?key=` matches the comments that
    explain this defect, which would fail against a correct module and push the
    next person to delete the explanation instead of the problem. This asks the
    precise question: does any request pass a credential-shaped name in `params`?
    """
    tree = ast.parse((REPO / "undx_router.py").read_text(encoding="utf-8"))
    credential_names = {"key", "api_key", "apikey", "access_token", "token", "secret"}
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr in {"post", "get", "put", "request"}):
            continue
        for keyword in node.keywords:
            if keyword.arg != "params" or not isinstance(keyword.value, ast.Dict):
                continue
            for dict_key in keyword.value.keys:
                if isinstance(dict_key, ast.Constant) and str(dict_key.value).lower() in credential_names:
                    offenders.append(f"line {node.lineno}: params={{{dict_key.value!r}: ...}}")
    assert not offenders, (
        "A provider call passes a credential as a URL query parameter: "
        f"{offenders}. Query strings reach proxy access logs and the string "
        "form of requests' exceptions. Send it as a header."
    )


# --- Defence 2: redaction before logging -------------------------------------

def test_failure_logging_is_routed_through_the_redactor():
    source = inspect.getsource(undx_router.route_undx_request)
    assert "error=%s\", provider, exc)" not in source, (
        "A provider failure logs the raw exception again. For any provider "
        "carrying credentials in the URL that writes the key to the log."
    )
    assert source.count("_safe_error(exc)") >= 2, (
        "Both the RequestException and the general failure paths must redact."
    )


def test_redactor_removes_a_key_carried_in_a_url():
    def check():
        message = (
            "503 Server Error for url: https://generativelanguage.googleapis.com/"
            f"v1beta/models/gemini-1.5-flash:generateContent?key={FAKE_GEMINI}"
        )
        cleaned = undx_router._safe_error(Exception(message))
        assert FAKE_GEMINI not in cleaned, f"Key survived redaction: {cleaned}"
        assert "key=***" in cleaned
    _with_fake_keys(check)


def test_redactor_removes_a_key_that_appears_under_an_unexpected_name():
    """Value matching is what catches a parameter name nobody anticipated."""
    def check():
        cleaned = undx_router._safe_error(Exception(f"rejected credential {FAKE_GEMINI}"))
        assert FAKE_GEMINI not in cleaned
    _with_fake_keys(check)


def test_redactor_does_not_destroy_ordinary_error_text():
    """A redactor that eats the diagnosis gets removed by whoever debugs next."""
    def check():
        cleaned = undx_router._safe_error(Exception("Connection reset by peer"))
        assert "Connection reset by peer" in cleaned
    _with_fake_keys(check)


def test_short_values_are_not_treated_as_credentials():
    """An 8-character floor stops an unset-but-empty or trivial key value from
    redacting ordinary words out of every log line."""
    saved = os.environ.get("GROQ_API_KEY")
    os.environ["GROQ_API_KEY"] = "abc"
    try:
        cleaned = undx_router._safe_error(Exception("abc failed to connect"))
        assert "abc failed to connect" in cleaned
    finally:
        if saved is None:
            os.environ.pop("GROQ_API_KEY", None)
        else:
            os.environ["GROQ_API_KEY"] = saved


def test_redacted_output_is_length_capped():
    def check():
        cleaned = undx_router._safe_error(Exception("x" * 5000))
        assert len(cleaned) <= 400
    _with_fake_keys(check)


# --- The router must not report a provider it cannot reach as usable ---------

def test_provider_status_is_derived_from_the_key_being_present():
    source = inspect.getsource(undx_router.provider_status)
    assert "_api_key(provider)" in source, (
        "Provider availability must be computed from configuration, not asserted."
    )


if __name__ == "__main__":
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
