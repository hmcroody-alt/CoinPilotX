"""Revert all four ranking-signal defects at once and prove the tests notice.

Unlike the later harnesses this one has a single "mutant": it restores the whole
pre-fix state of `engine.py` and `ranking.py` together — unwindowed impression and
click counts, trending computed from a lifetime counter, and the interest profile
read once behind three keys. Reverting them individually was tried and is not
informative: the four defects overlap, so a partial revert leaves the suite red for
reasons that do not isolate cleanly.

Originally this edited the working tree and restored it in a `finally`. It now
mutates a throwaway copy instead, for the reason recorded in
`prove_commerce_discovery_value_tiers.py`: a harness killed between the write and
the restore would leave a mutant in the real source, which is precisely the state
this tool exists to detect.
"""
import pathlib, shutil, subprocess, sys, tempfile

# Derived, never hardcoded — see the note in
# `prove_commerce_discovery_reachability.py`. `REPO` is only read.
REPO = pathlib.Path(__file__).resolve().parents[2]
PY_BIN = sys.executable
TESTS = [
    "tests/commerce_discovery/test_signals_tell_the_truth.py",
    "tests/commerce_discovery/test_ranking_and_explain.py",
]


def sandbox(tmp):
    """A throwaway copy of the repo. Only the copy is ever mutated."""
    base = pathlib.Path(tmp) / "repo"
    base.mkdir()
    for path in ("services", "tests", "conftest.py", "pytest.ini", "setup.cfg", "tox.ini"):
        src = REPO / path
        if not src.exists():
            continue
        if src.is_dir():
            shutil.copytree(src, base / path,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(src, base / path)
    return base


def revert(ROOT):
    eng = ROOT / "services/commerce_discovery/engine.py"
    s = eng.read_text()
    # defect 1: no window on impressions / clicks
    a = ('"SELECT listing_id, COUNT(*) AS impressions, "\n'
         '            "SUM(CASE WHEN event_at>? THEN 1 ELSE 0 END) AS recent_impressions "')
    assert a in s, "impression window anchor"
    s = s.replace(a, '"SELECT listing_id, COUNT(*) AS impressions "')
    b = ('"SELECT listing_id, COUNT(*) AS clicks, "\n'
         '            "SUM(CASE WHEN event_at>? THEN 1 ELSE 0 END) AS recent_clicks "')
    assert b in s, "click window anchor"
    s = s.replace(b, '"SELECT listing_id, COUNT(*) AS clicks "')
    s = s.replace('[trend_start] + ids,', 'ids,')
    s = s.replace('entry["recent_impressions"] = int(row.get("recent_impressions") or 0)', 'pass')
    s = s.replace('entry["recent_clicks"] = int(row.get("recent_clicks") or 0)', 'pass')
    # defect 3+4: one read behind three keys
    start = s.index('    profile: dict[str, Any] = {"topics"')
    end = s.index('    return profile\n', start) + len('    return profile\n')
    old = '''    profile: dict[str, Any] = {"topics": (), "viewed_categories": (), "followed_sellers": frozenset()}
    viewer = int(user_id or 0)
    try:
        cur.execute(
            "SELECT DISTINCT l.category, l.seller_user_id FROM marketplace_saved_products s "
            "JOIN marketplace_listings l ON l.id=s.listing_id WHERE s.user_id=? LIMIT 20",
            (viewer,),
        )
        rows = _rows(cur)
        cats = tuple(str(r.get("category") or "") for r in rows if r.get("category"))
        profile["topics"] = cats
        profile["viewed_categories"] = cats
        profile["followed_sellers"] = frozenset(
            int(r["seller_user_id"]) for r in rows if r.get("seller_user_id")
        )
    except Exception:
        pass
    return profile
'''
    s = s[:start] + old + s[end:]
    eng.write_text(s)

    rk = ROOT / "services/commerce_discovery/ranking.py"
    t = rk.read_text()
    # defect 2: trending on a lifetime counter
    c = 'if stats and float(stats.get("recent_clicks") or 0) >= config.trend_min_clicks():'
    assert c in t, "trending anchor"
    t = t.replace(c, 'if stats and float(stats.get("clicks") or 0) >= 10:')
    # conversion window switch removed
    d = '''    recent_impressions = float(stats.get("recent_impressions") or 0)
    if recent_impressions >= config.conversion_window_min_impressions():
        impressions = recent_impressions
        clicks = float(stats.get("recent_clicks") or 0)
'''
    assert d in t, "conversion window anchor"
    t = t.replace(d, "")
    rk.write_text(t)

def pytest_in(base):
    return subprocess.run([PY_BIN, "-m", "pytest", *TESTS, "-q", "--no-header",
                           "-p", "no:cacheprovider"],
                          cwd=base, capture_output=True, text=True)


def main():
    with tempfile.TemporaryDirectory() as tmp:
        base = sandbox(tmp)

        # A red baseline makes the result below meaningless: tests that already fail
        # cannot testify about a mutant.
        baseline = pytest_in(base)
        if baseline.returncode != 0:
            print("BASELINE IS RED — nothing below means anything")
            print(baseline.stdout[-3000:])
            return 1
        print("baseline: green\n")

        revert(base)
        r = pytest_in(base)
        killed = sorted({l.split("::")[-1] for l in r.stdout.splitlines()
                         if l.startswith("FAILED")})
        print(r.stdout[-4500:])
        print(f"\nthe four defects, reverted together: killed by {len(killed)} tests")
        for name in killed:
            print(f"      killed by: {name}")
        if not killed:
            print("\nSURVIVED — the suite does not actually describe these fixes")
            return 1
        return 0


if __name__ == "__main__":
    sys.exit(main())
