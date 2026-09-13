"""Locks for the two things that were entirely absent from this repository.

Both defects in this file share a shape with the dashboard lies fixed elsewhere
in this mission: a system that looks fine because nothing ever asked it a
question it could fail.

1. NO BACKUP EXISTED. There was no `pg_dump`, no `BACKUP_*` variable, no
   scheduled job, and no restore had ever been performed. "We have backups"
   was a belief about Railway's add-on defaults, not a property anyone had
   tested. `scripts/ops/backup_database.py` now takes a dump and *restores it*,
   and these tests keep the restore step from being quietly removed the first
   time it makes CI slow.

2. THE SECRET KEY FELL BACK TO A PER-PROCESS RANDOM VALUE. `COINPILOTX_SECRET_KEY`
   is the root every credential family is derived from. The Procfile runs
   `gunicorn --workers 2`, so with the variable unset each worker invented a
   different root, derived a different key for every purpose, and rejected the
   other's tokens. The symptom is intermittent 401s, which get triaged as a
   mobile-client bug.

   Deriving rather than sharing did not weaken this: the five keys are pure
   functions of the root, so an unstable root is still an unstable everything,
   and the boot guard below is still the thing standing between that and
   production. See `services/signing_keys.py`, and
   `tests/protection/test_signing_key_separation.py` for the split itself.

These parse source rather than importing: importing `bot` boots a 111k-line
Flask monolith, and the secret guard is now a boot-time `raise`, so importing
it here would either crash the test run or require faking a deployment
environment.
"""

import ast
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[2]
BOT = (REPO / "bot.py").read_text(encoding="utf-8")
BACKUP_SCRIPT = REPO / "scripts" / "ops" / "backup_database.py"


# --- 1. The secret key must not silently differ between workers --------------

def test_deployed_boot_refuses_a_per_process_random_secret():
    """A warning is not enough: nobody reads a warning in a passing boot log."""
    guard = re.search(
        r"if COINPILOTX_RANDOM_SECRET_USED and _deployment_environment_enabled\(\)"
        r".*?raise RuntimeError",
        BOT,
        re.DOTALL,
    )
    assert guard, (
        "bot.py no longer refuses to boot in a deployed environment without "
        "FLASK_SECRET_KEY. Without that guard each gunicorn worker mints tokens "
        "the other worker rejects, and the failure surfaces as random 401s."
    )
    assert len(guard.group(0)) < 1500, "guard matched an implausibly large span"


def test_the_random_secret_escape_hatch_must_be_opt_in():
    """The unsafe mode has to be requested by name, never defaulted to."""
    assert '_env_bool(\n    "PULSESOC_ALLOW_EPHEMERAL_SECRET", False\n)' in BOT \
        or '_env_bool("PULSESOC_ALLOW_EPHEMERAL_SECRET", False)' in BOT, (
        "The ephemeral-secret escape hatch must default to False."
    )


def test_secret_key_requirements_are_documented():
    env_example = (REPO / ".env.example").read_text(encoding="utf-8")
    assert "PULSESOC_ALLOW_EPHEMERAL_SECRET" in env_example
    # The template must say the variable is required, or an operator reading a
    # file of ~180 blank keys has no way to know this one stops the boot.
    #
    # This used to scan a fixed 400-character window above the variable, and it
    # broke when the comment block grew -- the word was still there, directly
    # above the variable, just further up. A character budget is the wrong unit:
    # it silently converts "explain this variable more thoroughly" into a test
    # failure, and the obvious fix (raise 400) would keep being wrong at 800.
    # What the test means is "the comment attached to this variable says it is
    # required", so that is what it now reads.
    lines = env_example.splitlines()
    index = next(i for i, line in enumerate(lines) if line.startswith("FLASK_SECRET_KEY="))
    block = []
    while index > 0 and lines[index - 1].startswith("#"):
        index -= 1
        block.append(lines[index])
    assert block, "FLASK_SECRET_KEY has no explanatory comment at all"
    assert any("REQUIRED" in line for line in block), (
        "the comment block directly above FLASK_SECRET_KEY no longer says the "
        "variable is REQUIRED. Boot fails without it, and an operator scanning "
        "~180 blank keys has nothing else to distinguish it from the optional ones."
    )


def test_the_root_secret_still_reaches_mobile_token_signing():
    """Guard the premise. If token signing moves, this whole file's reasoning
    needs revisiting rather than silently continuing to pass.

    It moved. This used to read::

        assert BOT.count("hmac.new(COINPILOTX_SECRET_KEY.encode") >= 2

    and it failed, which is the guard doing its job -- the root no longer signs
    anything directly. It is now the input to five one-way derivations, so this
    file's reasoning survives intact but by one more step: losing the root still
    loses every credential family, because every derived key is a pure function
    of it. That is the property this file actually cares about, and it is what is
    asserted below, rather than the particular call shape that used to express
    it.

    The assertion covers the *mint and verify* sites, not just the derivation:
    checking only that `derive()` is called would pass a bot.py that derived a
    key and then went on signing with the root anyway.
    """
    assert BOT.count("hmac.new(COINPILOTX_MOBILE_ACCESS_KEY.encode") >= 2, (
        "mobile access tokens are no longer signed with the derived mobile key "
        "at both the mint and the verify site."
    )
    assert "COINPILOTX_MOBILE_ACCESS_KEY = _signing_keys.derive(" in BOT, (
        "the mobile signing key is no longer derived from the root secret. If it "
        "became an independently configured variable, losing the root no longer "
        "loses this family and the backup reasoning below needs redoing."
    )
    # The root must not sign anything directly any more. `.encode` is what every
    # signing site spells; the derivation lines pass the root as a plain str.
    assert "hmac.new(COINPILOTX_SECRET_KEY.encode" not in BOT, (
        "something signs with the root secret again. The point of the split is "
        "that no two credential families share a key, and the root sharing with "
        "any one family re-couples it to all five."
    )


# --- 2. Backups must exist, and must be proven restorable --------------------

def test_a_backup_script_exists():
    assert BACKUP_SCRIPT.exists(), (
        "scripts/ops/backup_database.py is gone. This repository previously had "
        "no backup mechanism at all."
    )


def _backup_source():
    return BACKUP_SCRIPT.read_text(encoding="utf-8")


def test_backup_verifies_by_actually_restoring():
    source = _backup_source()
    assert "def verify_sqlite" in source and "def verify_postgres" in source
    # The restore proof is the point of the script; a dump nobody has restored
    # is a file with a reassuring name.
    assert "integrity_check" in source, "SQLite verification must run integrity_check."
    assert "information_schema.tables" in source, (
        "Postgres verification must count restored tables. A restore that "
        "produces an empty schema exits zero from psql."
    )


def test_backup_counts_tables_not_just_file_size():
    """The empty-database case passes every structural check.

    A gzip of a freshly-created SQLite file is a valid gzip of a valid database
    that passes `PRAGMA integrity_check`. Only counting tables catches it, and
    that is exactly the failure a misconfigured DATABASE_URL produces.
    """
    source = _backup_source()
    assert "restored database contains no tables" in source
    assert "restore produced zero tables" in source


def test_unverified_is_reported_differently_from_verified():
    """`verified: None` must never collapse into success.

    This is the same defect class as the hard-coded `"ok": True` removed from
    /health: reporting "I could not check" as "I checked and it was fine".
    """
    source = _backup_source()
    assert "EXIT_VERIFY_SKIPPED = 3" in source
    assert "EXIT_VERIFY_FAILED = 2" in source
    assert 'result.get("verified") is None' in source, (
        "The skipped case must be tested with `is None`, not truthiness - "
        "`if not verified` would merge skipped into failed."
    )


def test_verification_failure_is_a_verdict_not_a_traceback():
    """A truncated dump raises EOFError out of gzip.

    The first version of this script let that escape, so the process exited 1 -
    the code meaning "the backup could not be taken" - for a run where the
    backup was fine and the restore was what failed.
    """
    source = _backup_source()
    tree = ast.parse(source)
    verify = next(
        (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "verify"),
        None,
    )
    assert verify is not None, "backup_database.verify() not found"
    assert any(isinstance(node, ast.Try) for node in ast.walk(verify)), (
        "verify() must convert exceptions into a failed verdict so a corrupt "
        "dump exits 2 rather than crashing with the exit code for a failed dump."
    )


def test_verification_refuses_to_restore_over_the_source_database():
    """`psql $SCRATCH < dump` against production is a data-destroying typo."""
    source = _backup_source()
    assert "scratch_url == database_url()" in source, (
        "verify_postgres() must refuse when the scratch URL is the production "
        "URL. It runs DROP SCHEMA public CASCADE before restoring."
    )
    assert "DROP SCHEMA IF EXISTS public CASCADE" in source


def test_backup_output_never_reaches_git():
    """A dump is a byte-for-byte copy of every user row.

    Committing one publishes the database to anyone with repo access, and git
    history means a later `git rm` does not take it back.
    """
    gitignore = (REPO / ".gitignore").read_text(encoding="utf-8")
    assert "backups/" in gitignore, "The backup directory must be gitignored."
    assert "*.sql.gz" in gitignore, (
        "Dumps written outside the default directory must still be ignored."
    )


def test_connection_strings_are_redacted_in_backup_logs():
    """pg_dump and psql errors embed the DSN, password included."""
    source = _backup_source()
    assert "def redact(" in source
    assert "redact(stderr.decode" in source, (
        "Subprocess stderr must be redacted before it is logged or recorded."
    )
    assert "redact(database_url())" in source


if __name__ == "__main__":
    import pathlib as _pathlib
    import sys as _sys

    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
