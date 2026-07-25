"""Executable contract tests for server-authoritative PulseSoc region preferences."""

import os
import sys
import tempfile

os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(
    tempfile.mkdtemp(prefix="pulse_region_preferences_"), "test.db"
)
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services import db  # noqa: E402
from services import pulse_region_preferences as region  # noqa: E402


def test_defaults_are_automatic():
    value = region.get_preferences(7)
    assert value["preferred_currency"] == ""
    assert all(value["automatic"].values())


def test_update_is_partial_and_server_authoritative():
    first = region.update_preferences(7, {
        "locale": "fr-CA",
        "timezone": "America/Toronto",
        "currency": "cad",
        "date_format": "ymd",
    })
    assert first["preferred_currency"] == "CAD"
    assert sorted(first["changed_fields"]) == ["currency", "date_format", "locale", "time_zone"]
    second = region.update_preferences(7, {"currency": "USD"})
    assert second["preferred_locale"] == "fr-CA"
    assert second["preferred_timezone"] == "America/Toronto"
    assert second["preferred_currency"] == "USD"


def test_automatic_clears_manual_overrides():
    region.update_preferences(9, {
        "locale": "ar",
        "timezone": "Asia/Dubai",
        "currency": "AED",
        "date_format": "dmy",
    })
    value = region.update_preferences(9, {
        "locale": "auto",
        "timezone": "device",
        "currency": "system",
        "date_format": "auto",
    })
    assert all(value["automatic"].values())


def test_invalid_values_fail_closed():
    cases = (
        ({"locale": "not_a_locale_%%%"}, "invalid_locale"),
        ({"timezone": "Mars/Phobos"}, "invalid_timezone"),
        ({"currency": "US"}, "invalid_currency"),
        ({"date_format": "guess"}, "invalid_date_format"),
        ({"admin": True}, "unsupported_preference"),
    )
    for payload, code in cases:
        try:
            region.update_preferences(11, payload)
        except region.RegionPreferenceError as exc:
            assert exc.code == code, (code, exc.code)
        else:
            raise AssertionError(f"expected {code}")


def test_updates_are_audited_without_storing_device_data():
    region.update_preferences(12, {"currency": "EUR", "date_format": "dmy"})
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT changed_fields FROM pulse_region_preference_events WHERE user_id=?",
            (12,),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1
    assert rows[0]["changed_fields"] == "currency,date_format"


if __name__ == "__main__":
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]
    passed = 0
    for test in tests:
        test()
        passed += 1
        print(f"PASS  {test.__name__}")
    print(f"\n{passed}/{len(tests)} tests passed")
