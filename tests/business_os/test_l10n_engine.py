"""Localization engine (Stage 6).

Proves the deterministic string-resolution projection: ingest is idempotent on
(source, external_ref); empty locale/key/value are curated; resolution walks the fallback
chain (exact -> explicit fallback -> language base -> org default -> missing); the newest
recorded value wins for a (key, locale); resolutions rank deterministically (missing <
default < base < fallback < exact, then locale asc, then key asc); the per-locale coverage
rollup is correct; recompute is a deterministic idempotent replace (no duplicate rows);
and nothing beyond the four canonical tables is created (nothing renders).

    python tests/business_os/test_l10n_engine.py   # no pytest needed
"""

import os
import sys
import time
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="busos_l10neng_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.localization import schema as sch  # noqa: E402
from services.business_os.localization import engine as eng  # noqa: E402


def setup_module(module=None):
    sch.ensure_schema()


def _cell(out, locale, key):
    for r in out["resolutions"]:
        if r["locale"] == locale and r["string_key"] == key:
            return r
    return None


def test_locale_and_string_dedupe():
    l1 = eng.record_locale("oA", "en", source="feed", external_ref="L1")
    l2 = eng.record_locale("oA", "en", source="feed", external_ref="L1")
    assert l1["recorded"] is True and l2["deduped"] is True, (l1, l2)
    s1 = eng.record_string("oA", "greeting", "en", "Hello", source="feed",
                           external_ref="S1")
    s2 = eng.record_string("oA", "greeting", "en", "Hi", source="feed",
                           external_ref="S1")
    assert s1["recorded"] is True and s2["deduped"] is True, (s1, s2)


def test_bad_input_curated():
    for fn in (lambda: eng.record_locale("oB", ""),
               lambda: eng.record_string("oB", "k", "en", ""),
               lambda: eng.record_string("oB", "", "en", "v"),
               lambda: eng.record_string("oB", "k", "", "v")):
        raised = False
        try:
            fn()
        except eng.LocalizationError:
            raised = True
        assert raised, "invalid input should be rejected"


def test_locale_normalized():
    # en_US and EN-US both canonicalize to en-us so they resolve identically.
    r = eng.record_locale("oNorm", "EN_us")
    assert r["locale"] == "en-us", r


def test_exact_and_default_fallback():
    eng.record_locale("oC", "en", is_default=True)
    eng.record_locale("oC", "fr")
    eng.record_string("oC", "greeting", "en", "Hello")
    eng.record_string("oC", "greeting", "fr", "Bonjour")
    eng.record_string("oC", "farewell", "en", "Bye")  # no fr translation
    out = eng.resolve_org("oC")
    assert _cell(out, "fr", "greeting")["match_type"] == "exact", out
    assert _cell(out, "fr", "greeting")["value"] == "Bonjour", out
    far = _cell(out, "fr", "farewell")
    assert far["match_type"] == "default" and far["value"] == "Bye", far
    assert far["resolved_from"] == "en", far


def test_base_language_fallback():
    eng.record_locale("oD", "en", is_default=True)
    eng.record_locale("oD", "en-US")
    eng.record_string("oD", "greeting", "en", "Hello")
    out = eng.resolve_org("oD")
    cell = _cell(out, "en-us", "greeting")
    assert cell["match_type"] == "base" and cell["value"] == "Hello", cell
    assert cell["resolved_from"] == "en", cell


def test_explicit_fallback_beats_default():
    eng.record_locale("oE", "en", is_default=True)
    eng.record_locale("oE", "fr")
    eng.record_locale("oE", "es", fallback_locale="fr")  # es falls back to fr, not en
    eng.record_string("oE", "greeting", "en", "Hello")
    eng.record_string("oE", "greeting", "fr", "Bonjour")  # no es translation
    out = eng.resolve_org("oE")
    cell = _cell(out, "es", "greeting")
    assert cell["match_type"] == "fallback" and cell["value"] == "Bonjour", cell
    assert cell["resolved_from"] == "fr", cell


def test_missing_surfaces():
    eng.record_locale("oF", "en", is_default=True)
    eng.record_locale("oF", "fr")
    eng.record_locale("oF", "de")
    eng.record_string("oF", "greeting", "fr", "Bonjour")  # only fr has it
    out = eng.resolve_org("oF")
    # en (default) has no greeting -> missing; de has none in its chain -> missing.
    assert _cell(out, "en", "greeting")["match_type"] == "missing", out
    assert _cell(out, "en", "greeting")["value"] is None, out
    assert _cell(out, "de", "greeting")["match_type"] == "missing", out
    assert _cell(out, "fr", "greeting")["match_type"] == "exact", out


def test_newest_value_wins():
    eng.record_locale("oG", "en", is_default=True)
    eng.record_string("oG", "greeting", "en", "Hello")
    time.sleep(0.003)  # guarantee a strictly later created_at for the correction
    eng.record_string("oG", "greeting", "en", "Howdy")
    out = eng.resolve_org("oG")
    assert _cell(out, "en", "greeting")["value"] == "Howdy", out


def test_deterministic_ordering():
    eng.record_locale("oH", "en", is_default=True)
    eng.record_locale("oH", "fr")
    eng.record_locale("oH", "de")
    eng.record_string("oH", "greeting", "fr", "Bonjour")  # only fr
    eng.record_string("oH", "farewell", "en", "Bye")      # only en (default)
    out = eng.resolve_org("oH")
    order = [eng._MATCH_ORDER[r["match_type"]] for r in out["resolutions"]]
    assert order == sorted(order), order  # non-decreasing by match rank
    assert out["resolutions"][0]["match_type"] == "missing", out
    assert out["resolutions"][-1]["match_type"] == "exact", out
    ranks = [r["rank"] for r in out["resolutions"]]
    assert ranks == list(range(1, len(ranks) + 1)), ranks


def test_coverage_rollup():
    eng.record_locale("oI", "en", is_default=True)
    eng.record_locale("oI", "fr")
    eng.record_locale("oI", "de")
    eng.record_string("oI", "greeting", "fr", "Bonjour")
    eng.record_string("oI", "farewell", "en", "Bye")
    out = eng.resolve_org("oI")
    cov = {c["locale"]: c for c in out["coverage"]}
    # fr: greeting exact + farewell default = 2 resolved / 2 -> 100%
    assert cov["fr"]["resolved"] == 2 and cov["fr"]["missing"] == 0, cov["fr"]
    assert cov["fr"]["coverage_pct"] == 100.0, cov["fr"]
    # de: greeting missing, farewell default -> 1 resolved / 2 -> 50%
    assert cov["de"]["missing"] == 1 and cov["de"]["resolved"] == 1, cov["de"]
    assert cov["de"]["coverage_pct"] == 50.0, cov["de"]


def test_recompute_idempotent_replace():
    eng.record_locale("oR", "en", is_default=True)
    eng.record_locale("oR", "fr")
    eng.record_string("oR", "greeting", "en", "Hello")
    eng.record_string("oR", "greeting", "fr", "Bonjour")
    first = eng.resolve_org("oR")
    second = eng.resolve_org("oR")
    assert first["resolutions"] == second["resolutions"], (first, second)
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT locale, string_key, COUNT(*) c FROM business_os_l10n_resolutions "
            "WHERE org_id = ? GROUP BY locale, string_key", ("oR",)).fetchall()
        for r in rows:
            assert dict(r)["c"] == 1, dict(r)
    finally:
        conn.close()


def test_no_side_effects():
    eng.record_locale("oN", "en", is_default=True)
    eng.record_string("oN", "greeting", "en", "Hello")
    eng.resolve_org("oN")
    conn = db.connect()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name LIKE 'business_os_l10n_%'").fetchall()
        names = {r[0] for r in rows}
        assert names == {
            "business_os_l10n_locales",
            "business_os_l10n_strings",
            "business_os_l10n_resolutions",
            "business_os_l10n_audit"}, names
    finally:
        conn.close()


def _run_standalone():
    setup_module()
    tests = [
        test_locale_and_string_dedupe,
        test_bad_input_curated,
        test_locale_normalized,
        test_exact_and_default_fallback,
        test_base_language_fallback,
        test_explicit_fallback_beats_default,
        test_missing_surfaces,
        test_newest_value_wins,
        test_deterministic_ordering,
        test_coverage_rollup,
        test_recompute_idempotent_replace,
        test_no_side_effects,
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
