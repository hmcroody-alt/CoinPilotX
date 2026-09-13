"""The retention job must not be able to disarm reuse detection or sign anyone out.

`phase0_session_sweep.py` clears device and network identifiers off dead rows in
`mobile_security_sessions`. That table is not a log — three separate live code
paths read it, and each one breaks differently if the sweep is wrong:

- `rotate_mobile_refresh_token()` (`bot.py:31288`) detects a replayed refresh
  token by looking its hash up **with no status and no expiry filter**. The row
  being present is the whole mechanism. Delete it and the replay returns a plain
  401 — no security event, no family revocation, no user alert. The control is
  gone and nothing announces it.
- `mobile_refresh_reuse_grace_allowed()` (`bot.py:31118`) forgives a benign
  refresh desync only if the stale `rotated` row still matches on `device_hash`
  **or** `ip_hash`. Those are two of the seven columns this sweep clears. Strip
  one inside the grace window and a legitimate refresh is reclassified as theft:
  the user is signed out of every device and emailed a security warning.
- `messenger_media_cookie_user_id()` (`bot.py:91458`) authenticates on
  `status IN ('active','rotated')`. `rotated` is a live credential tier, not a
  dead one.

None of those failures are loud. A sweep that gets them wrong produces a clean
exit code, a plausible row count, and a security regression that surfaces weeks
later as scattered "why was I logged out" reports. So the tests below assert on
what is left in the table afterwards, not on what the script says it did.

The delete path has one structural property worth naming, because it is the bug
this file was written after finding. `--delete` only removes rows that are
*already* tombstoned, which makes tombstoning and deleting two separate runs: a
row must appear in one invocation's output before a later invocation can remove
it. Running the UPDATE before the DELETE silently collapses that into one pass —
the freshly-tombstoned rows satisfy the delete predicate inside the same
transaction — and the preview under-reports the damage. `test_first_pass_with_
delete_removes_nothing` and `test_preview_exactly_predicts_the_apply` are that
gate.

Run: python3 -m pytest tests/web_parity/test_session_sweep.py
"""

from __future__ import annotations

import re
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "web_rebuild" / "phase0_session_sweep.py"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import importlib.util  # noqa: E402


def _load_sweep():
    spec = importlib.util.spec_from_file_location("phase0_session_sweep", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


sweep = _load_sweep()

#: Mirrors `bot.py:31079` plus the `add_columns_if_missing` block immediately
#: after it. `device_hash TEXT NOT NULL` is reproduced deliberately: it is the
#: constraint that makes a NULL tombstone abort, and a fixture that relaxed it
#: would let a regression through.
SCHEMA = """
CREATE TABLE mobile_security_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    device_hash TEXT NOT NULL,
    device_label TEXT,
    refresh_token_hash TEXT UNIQUE,
    access_token_hash TEXT,
    status TEXT DEFAULT 'active',
    ip_hash TEXT,
    user_agent TEXT,
    created_at TEXT,
    last_seen_at TEXT,
    access_expires_at TEXT,
    refresh_expires_at TEXT,
    rotated_at TEXT,
    revoked_at TEXT,
    metadata_json TEXT,
    session_family_id TEXT,
    platform TEXT,
    country TEXT,
    revoked_reason TEXT,
    reuse_detected_at TEXT,
    last_refresh_at TEXT,
    last_risk_score INTEGER DEFAULT 0,
    last_trace_id TEXT
)
"""

#: Naive UTC, matching what `bot.py:31151` actually writes into these columns.
#: A tz-aware value would serialise with a `+00:00` suffix, and every comparison
#: in this schema is a string comparison.
NOW = sweep.now_utc()


def ago(**kw) -> str:
    return (NOW - timedelta(**kw)).isoformat(timespec="seconds")


#: Every row is named for the rule it exercises. A random 10k-row fixture would
#: have the same coverage on paper and tell you nothing about which rule broke.
#:
#: `refresh_expires_at` is ten years out on every row, including the ancient
#: ones, because that is what production looks like
#: (`MOBILE_REFRESH_TOKEN_TTL_SECONDS = 60*60*24*3650`). Any retention rule that
#: keys on expiry is inert here, which is the point.
FUTURE = (NOW + timedelta(days=3650)).isoformat(timespec="seconds")

ROWS = [
    # name, status, went-dead column, age, reuse evidence?
    dict(name="active_recent", status="active", ts_col="last_seen_at", ts=ago(minutes=5)),
    dict(name="active_ancient", status="active", ts_col="last_seen_at", ts=ago(days=900)),
    # The grace carve-out. `rotated` seconds ago is a live session mid-refresh.
    dict(name="rotated_in_grace", status="rotated", ts_col="rotated_at", ts=ago(seconds=30)),
    dict(name="rotated_edge_of_grace", status="rotated", ts_col="rotated_at", ts=ago(minutes=20)),
    # Old enough to tombstone, but still a credential — never deletable.
    dict(name="rotated_old", status="rotated", ts_col="rotated_at", ts=ago(days=90)),
    # Dead, recent: inside the tombstone horizon, so untouched this run.
    dict(name="revoked_recent", status="revoked", ts_col="revoked_at", ts=ago(days=3)),
    # Dead and old: the ordinary tombstone case.
    dict(name="revoked_old", status="revoked", ts_col="revoked_at", ts=ago(days=120)),
    # Dead, ancient, no evidence: the only class --delete may ever remove.
    dict(name="revoked_ancient", status="revoked", ts_col="revoked_at", ts=ago(days=500)),
    dict(name="revoked_ancient_2", status="revoked", ts_col="revoked_at", ts=ago(days=800)),
    # Ancient but carries reuse evidence. Tombstoned, never deleted — deleting
    # it is what turns a future replay of this token into a silent 401.
    dict(name="reuse_ancient", status="revoked", ts_col="revoked_at", ts=ago(days=500),
         reuse=True),
    dict(name="reuse_by_reason", status="revoked", ts_col="revoked_at", ts=ago(days=600),
         reason="refresh_token_reuse"),
    # No revoked_at/rotated_at at all: falls back through last_seen_at.
    dict(name="revoked_no_revoked_at", status="revoked", ts_col="last_seen_at", ts=ago(days=200)),
    # No usable timestamp anywhere. Must be left alone rather than assumed old.
    dict(name="revoked_undated", status="revoked", ts_col=None, ts=None),
]

PAYLOAD = dict(
    device_hash="dev-hash", device_label="desktop-web", access_token_hash="atk",
    ip_hash="ip-hash", user_agent="Mozilla/5.0 (fixture)", metadata_json='{"a":1}',
    country="US",
)


def seed(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute(SCHEMA)
    for i, spec in enumerate(ROWS):
        cols = dict(PAYLOAD)
        cols.update(
            user_id=1 + (i % 3),
            refresh_token_hash=f"rt-{spec['name']}",
            session_family_id=f"fam-{i % 4}",
            status=spec["status"],
            created_at=ago(days=1000),
            refresh_expires_at=FUTURE,
            platform="web",
        )
        if spec.get("ts_col"):
            cols[spec["ts_col"]] = spec["ts"]
        if spec.get("reuse"):
            cols["reuse_detected_at"] = spec["ts"]
        if spec.get("reason"):
            cols["revoked_reason"] = spec["reason"]
        names = ", ".join(cols)
        marks = ", ".join("?" for _ in cols)
        conn.execute(
            f"INSERT INTO mobile_security_sessions ({names}) VALUES ({marks})",
            list(cols.values()),
        )
    conn.commit()
    conn.close()


def run(path: Path, *flags: str):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--database-url", f"sqlite:///{path}", *flags],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return proc.stdout


def counts(out: str) -> dict:
    """Pull the machine-checkable numbers out of the operator-facing report."""
    def grab(pattern):
        m = re.search(pattern, out, re.M)
        assert m, f"report format changed; {pattern!r} no longer matches:\n{out}"
        return int(m.group(1))
    got = {
        "to_tombstone": grab(r"to tombstone \(clear \d+ payload columns\): (\d+)"),
        "undated": grab(r"dead rows with no usable date:\s+(\d+)"),
    }
    if "--delete" in out or "ENABLED" in out:
        got["to_delete"] = grab(r"to delete\s+\(already tombstoned, no evidence\):\s+(\d+)")
    if "[APPLY]" in out:
        got["tombstoned"] = grab(r"^  tombstoned: (\d+)$")
        if "ENABLED" in out:
            got["deleted"] = grab(r"^  deleted   : (\d+)$")
    return got


def table(path: Path) -> dict:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM mobile_security_sessions ORDER BY id"
    ).fetchall()
    conn.close()
    return {r["refresh_token_hash"].removeprefix("rt-"): dict(r) for r in rows}


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "sessions.db"
    seed(path)
    return path


# --------------------------------------------------------------------------
# The fixture itself has to be right, or every assertion below is vacuous.
# --------------------------------------------------------------------------

def test_the_fixture_looks_like_production(db):
    before = table(db)
    assert len(before) == len(ROWS)
    assert all(r["user_agent"] for r in before.values()), "seeded rows must carry payload"
    assert all(r["refresh_expires_at"] > NOW.isoformat() for r in before.values()), (
        "every row must be unexpired, or the sweep could pass by keying on expiry"
    )


def test_the_script_reads_the_apps_own_grace_constant(monkeypatch):
    """Not a copy of `180`. A copy drifts; this is the same expression."""
    monkeypatch.delenv("PULSESOC_REFRESH_REUSE_GRACE_SECONDS", raising=False)
    assert sweep.grace_seconds() == 180
    monkeypatch.setenv("PULSESOC_REFRESH_REUSE_GRACE_SECONDS", "600")
    assert sweep.grace_seconds() == 600
    monkeypatch.setenv("PULSESOC_REFRESH_REUSE_GRACE_SECONDS", "1")
    assert sweep.grace_seconds() == 30, "bot.py:140 floors this at 30"


def test_evidence_and_tombstone_columns_do_not_overlap():
    """The two lists are the whole safety argument; an overlap voids it."""
    assert not set(sweep.TOMBSTONE_COLUMNS) & set(sweep.EVIDENCE_COLUMNS)
    for needed in ("refresh_token_hash", "session_family_id", "reuse_detected_at"):
        assert needed in sweep.EVIDENCE_COLUMNS


def test_every_column_the_sweep_names_exists_in_the_real_schema():
    """Guards a rename in `bot.py` that would make the UPDATE a no-op or an error.

    A tombstone column that no longer exists fails loudly on SQLite and
    Postgres alike, but an *evidence* column that was renamed would quietly stop
    being protected, and nothing else in this file would notice.
    """
    declared = set(re.findall(r"^\s{4}(\w+) ", SCHEMA, re.M))
    for col in sweep.TOMBSTONE_COLUMNS + sweep.EVIDENCE_COLUMNS:
        assert col in declared, f"{col} is not a column of {sweep.TABLE}"


# --------------------------------------------------------------------------
# Dry run
# --------------------------------------------------------------------------

def test_dry_run_writes_nothing(db):
    before = table(db)
    run(db)
    assert table(db) == before


def test_preview_exactly_predicts_the_apply(db):
    """A preview that under-reports is worse than no preview.

    This is the assertion the ordering bug failed: the dry run said `to delete:
    0` and the apply removed 3,222 rows, because the tombstone UPDATE ran first
    and fed the DELETE rows that did not match when they were counted.
    """
    predicted = counts(run(db, "--delete"))
    actual = counts(run(db, "--apply", "--delete"))
    assert actual["tombstoned"] == predicted["to_tombstone"]
    assert actual["deleted"] == predicted["to_delete"]


# --------------------------------------------------------------------------
# What must never be touched
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name", ["active_recent", "active_ancient"])
def test_active_sessions_are_never_touched(db, name):
    """Age is irrelevant for `active`. A 900-day-old active row is a signed-in user."""
    before = table(db)[name]
    run(db, "--apply", "--delete")
    assert table(db)[name] == before


@pytest.mark.parametrize("name", ["rotated_in_grace", "rotated_edge_of_grace"])
def test_the_reuse_grace_window_is_respected(db, name):
    """`device_hash` and `ip_hash` must survive, or a benign refresh reads as theft.

    `mobile_refresh_reuse_grace_allowed()` needs one of the two to match. Clear
    them and the request falls through to the reuse branch: every device signed
    out, security alert sent, for a user who did nothing wrong.
    """
    run(db, "--apply", "--delete")
    row = table(db)[name]
    assert row["device_hash"] == PAYLOAD["device_hash"]
    assert row["ip_hash"] == PAYLOAD["ip_hash"]


@pytest.mark.parametrize("name", ["rotated_in_grace", "rotated_edge_of_grace"])
def test_the_grace_floor_holds_with_no_retention_horizon(db, name):
    """The test above passes for the wrong reason, so this one exists.

    At the default 30-day horizon, a row rotated 30 seconds ago is protected by
    the horizon; the grace floor is never consulted. Deleting the floor entirely
    leaves that test green — verified by mutation. The floor is a *second*,
    independent bound, and the only way to exercise it is to remove the first.

    `--tombstone-after-days 0` is not hypothetical: it is what an operator
    reaches for when the point of the run is to purge identifiers promptly. That
    is exactly the run where clearing `device_hash` on a session mid-refresh
    converts a routine token rotation into a full account lockout.
    """
    run(db, "--apply", "--tombstone-after-days", "0")
    row = table(db)[name]
    assert row["device_hash"] == PAYLOAD["device_hash"], (
        "the grace floor did not hold once the retention horizon stopped hiding it"
    )
    assert row["ip_hash"] == PAYLOAD["ip_hash"]


def test_a_zero_day_horizon_still_sweeps_everything_outside_the_window(db):
    """The guard above must bound the sweep, not disable it."""
    out = counts(run(db, "--apply", "--tombstone-after-days", "0"))
    assert out["tombstoned"] > 0
    assert table(db)["revoked_recent"]["user_agent"] == ""


def test_a_row_with_no_usable_timestamp_is_left_alone(db):
    """A missing date is not evidence that a row is old.

    `created_at` is deliberately not a fallback, even though it is always
    populated and would make this row eligible. It answers the wrong question: a
    session created 1,000 days ago may have been revoked this morning, and
    ageing it out by creation date would delete the reuse evidence for a token
    that is still in circulation. Leaving the row costs a few hundred bytes;
    guessing its age wrongly is unrecoverable.
    """
    before = table(db)["revoked_undated"]
    assert before["created_at"] < ago(days=900), (
        "the fixture row must be ancient by creation date, or this proves nothing"
    )
    out = counts(run(db, "--apply", "--delete"))
    assert out["undated"] == 1, "the operator report must surface these, not hide them"
    assert table(db)["revoked_undated"] == before


def test_rows_inside_the_tombstone_horizon_are_left_alone(db):
    before = table(db)["revoked_recent"]
    run(db, "--apply")
    assert table(db)["revoked_recent"] == before


def test_the_ten_year_expiry_does_not_shield_dead_rows(db):
    """The retention key is when the row stopped being live, not when it expires.

    Every fixture row expires in 2036. If the predicate keyed on
    `refresh_expires_at` — which reads as the obvious choice — this would
    tombstone nothing at all and the suite would still be green everywhere else.
    """
    out = counts(run(db, "--apply"))
    assert out["tombstoned"] > 0


# --------------------------------------------------------------------------
# Tombstoning
# --------------------------------------------------------------------------

def test_payload_is_cleared_on_dead_rows(db):
    run(db, "--apply")
    row = table(db)["revoked_old"]
    for col in sweep.TOMBSTONE_COLUMNS:
        assert row[col] == "", f"{col} still carries payload"


def test_tombstones_are_empty_strings_not_null(db):
    """`device_hash` is `NOT NULL`, and every reader tests `COALESCE(col,'')=''`.

    A NULL tombstone aborts the UPDATE outright — survivable, because it is one
    statement — but it would also be the wrong shape for every consumer.
    """
    run(db, "--apply")
    conn = sqlite3.connect(db)
    nulls = conn.execute(
        "SELECT COUNT(*) FROM mobile_security_sessions WHERE "
        + " OR ".join(f"{c} IS NULL" for c in sweep.TOMBSTONE_COLUMNS)
    ).fetchone()[0]
    conn.close()
    assert nulls == 0


def test_evidence_survives_every_tombstone(db):
    """The identity of a dead session is kept; only its device fingerprint goes."""
    before = table(db)
    run(db, "--apply")
    after = table(db)
    assert set(after) == set(before)
    for name, row in after.items():
        for col in sweep.EVIDENCE_COLUMNS:
            assert row[col] == before[name][col], f"{name}.{col} changed"


def test_tombstoning_is_idempotent(db):
    run(db, "--apply")
    second = counts(run(db, "--apply"))
    assert second["tombstoned"] == 0, "a tombstoned row must not re-match"


# --------------------------------------------------------------------------
# Deletion — the two-pass gate
# --------------------------------------------------------------------------

def test_first_pass_with_delete_removes_nothing(db):
    """The gate. Nothing may be tombstoned and deleted in the same invocation.

    Every deletable row is still carrying payload on the first run, so it cannot
    match the delete predicate. That guarantees an operator sees a row counted
    as tombstoned before any run can remove it.
    """
    before = set(table(db))
    out = counts(run(db, "--apply", "--delete"))
    assert out["deleted"] == 0
    assert set(table(db)) == before


def test_second_pass_deletes_only_ancient_evidence_free_rows(db):
    run(db, "--apply", "--delete")
    out = counts(run(db, "--apply", "--delete"))
    remaining = set(table(db))
    assert out["deleted"] == 2
    assert not {"revoked_ancient", "revoked_ancient_2"} & remaining
    # Everything else is still there: nothing was swept up as collateral.
    assert len(remaining) == len(ROWS) - 2


def test_delete_is_not_silently_a_no_op(db):
    """`--delete` must be able to delete. Twice it could not, and said nothing.

    The delete predicate runs under a `NOT`, where SQL's three-valued logic is a
    trap: `revoked_reason = 'refresh_token_reuse'` against a NULL column is NULL,
    not false, so `NOT (false OR NULL)` is NULL and the row is quietly excluded.
    `revoked_reason` is NULL on most production rows, so the unwrapped version
    deleted nothing, forever, while exiting 0 and printing a clean report.

    A "nothing was deleted" bug has no symptom. This test is the symptom.
    """
    run(db, "--apply", "--delete")
    assert counts(run(db, "--apply", "--delete"))["deleted"] > 0


@pytest.mark.parametrize("name", ["reuse_ancient", "reuse_by_reason"])
def test_reuse_evidence_is_never_deleted(db, name):
    """Deleting these is the silent-401: a replay stops being detectable at all.

    Both spellings count as evidence — an explicit `reuse_detected_at`, and a
    `revoked_reason` of `refresh_token_reuse` on a row where the timestamp was
    never written.
    """
    for _ in range(3):
        run(db, "--apply", "--delete")
    assert name in table(db)


def test_rotated_rows_are_never_deleted_however_old(db):
    """`rotated` still authenticates `messenger_media_cookie_user_id()`."""
    for _ in range(3):
        run(db, "--apply", "--delete")
    assert "rotated_old" in table(db)


def test_delete_is_off_by_default(db):
    before = set(table(db))
    run(db, "--apply")
    run(db, "--apply")
    assert set(table(db)) == before


def test_every_surviving_row_keeps_its_refresh_hash(db):
    """The reuse lookup is by hash. A row without one is a row that cannot be found."""
    for _ in range(3):
        run(db, "--apply", "--delete")
    for name, row in table(db).items():
        assert row["refresh_token_hash"], f"{name} lost its refresh_token_hash"
        assert row["session_family_id"], f"{name} lost its session_family_id"
