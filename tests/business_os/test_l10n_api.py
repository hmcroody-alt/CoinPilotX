"""Localization controller contract (Stage 6).

Proves the framework-agnostic contract: DARK (404) when the flag is off; missing
payload/fields -> 400 with curated codes; recording a locale + string; resolutions report
is computed-on-read with a coverage rollup; locales/strings reports; resolve runs. Curated
codes only, never a raw exception.

    python tests/business_os/test_l10n_api.py   # no pytest needed
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_l10napi_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB
os.environ["BUSINESS_OS_LOCALIZATION"] = "on"

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services.business_os.localization import schema as sch  # noqa: E402
from services.business_os.localization import api  # noqa: E402


def setup_module(module=None):
    sch.ensure_schema()


# --- (a) dark when disabled -------------------------------------------------
def test_dark_when_disabled():
    os.environ["BUSINESS_OS_LOCALIZATION"] = "0"
    try:
        assert api.record_locale({})[0] == 404
        assert api.record_string({})[0] == 404
        assert api.resolutions_report("o")[0] == 404
        assert api.locales_report("o")[0] == 404
        assert api.strings_report("o")[0] == 404
        assert api.run_resolve("o")[0] == 404
    finally:
        os.environ["BUSINESS_OS_LOCALIZATION"] = "on"


# --- (b) validation ---------------------------------------------------------
def test_locale_missing_fields():
    st, body = api.record_locale({"org_id": "o"})
    assert st == 400 and body["code"] == "missing_fields", body


def test_locale_bad_curated():
    st, body = api.record_locale({"org_id": "o", "locale": "   "})
    assert st == 400 and body["code"] == "invalid_locale", body


def test_string_missing_fields():
    st, body = api.record_string({"org_id": "o", "string_key": "k", "locale": "en"})
    assert st == 400 and body["code"] == "missing_fields", body


def test_string_bad_curated():
    st, body = api.record_string({"org_id": "o", "string_key": "k", "locale": "  ",
                                  "value": "v"})
    assert st == 400 and body["code"] == "invalid_string", body


def test_resolutions_missing_org():
    st, body = api.resolutions_report("")
    assert st == 400 and body["code"] == "missing_fields", body


# --- (c) record + compute-on-read resolutions -------------------------------
def test_resolutions_computed_on_read():
    api.record_locale({"org_id": "O1", "locale": "en", "is_default": True})
    api.record_locale({"org_id": "O1", "locale": "fr"})
    api.record_string({"org_id": "O1", "string_key": "greeting", "locale": "en",
                       "value": "Hello"})
    api.record_string({"org_id": "O1", "string_key": "greeting", "locale": "fr",
                       "value": "Bonjour"})
    api.record_string({"org_id": "O1", "string_key": "farewell", "locale": "en",
                       "value": "Bye"})  # fr will fall back to default en
    st, body = api.resolutions_report("O1")
    assert st == 200, body
    res = body["result"]["resolutions"]
    by = {(d["locale"], d["string_key"]): d for d in res}
    assert by[("fr", "greeting")]["match_type"] == "exact", by
    assert by[("fr", "farewell")]["match_type"] == "default", by
    assert by[("fr", "farewell")]["value"] == "Bye", by
    assert isinstance(body["result"]["coverage"], list) and body["result"]["coverage"]


# --- (d) locales + strings reports + resolve --------------------------------
def test_locales_and_strings_reports():
    st, body = api.locales_report("O1")
    assert st == 200 and any(l["is_default"] for l in body["result"]["locales"]), body
    st2, b2 = api.strings_report("O1")
    assert st2 == 200 and any(s["string_key"] == "greeting"
                              for s in b2["result"]["strings"]), b2


def test_resolve_runs():
    st, body = api.run_resolve("O1")
    assert st == 200 and "resolutions" in body["result"], body
    st2, b2 = api.run_resolve("")
    assert st2 == 400 and b2["code"] == "missing_fields", b2


def _run_standalone():
    setup_module()
    tests = [
        test_dark_when_disabled,
        test_locale_missing_fields,
        test_locale_bad_curated,
        test_string_missing_fields,
        test_string_bad_curated,
        test_resolutions_missing_org,
        test_resolutions_computed_on_read,
        test_locales_and_strings_reports,
        test_resolve_runs,
    ]
    passed = 0
    for t in tests:
        t()
        print(f"PASS  {t.__name__}")
        passed += 1
    print(f"\n{passed}/{len(tests)} tests passed")
    return passed == len(tests)


if __name__ == "__main__":
    ok = _run_standalone()
    raise SystemExit(0 if ok else 1)
