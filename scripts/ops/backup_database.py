#!/usr/bin/env python3
"""Take a PulseSoc database backup, and prove it can be restored.

Before this script, the repository contained no backup mechanism of any kind:
no `pg_dump` call, no `BACKUP_*` variable, no scheduled job, nothing. The
database was whatever Railway's Postgres add-on happened to be retaining, and
nobody in the codebase had ever asserted that a recovery was possible.

The design point of this script is the *verification*, not the dump. Taking a
dump is four lines of subprocess. The thing that fails in a real incident is
that the dump was truncated, or was written while the disk was full, or was
gzipped over the top of itself, or captured zero rows because the connection
string pointed at an empty scratch database. Every one of those produces a file
of plausible size with a plausible name, and every one of them is discovered at
exactly the wrong moment.

So `--verify` restores the dump into a throwaway database and counts what came
back. A backup that has never been restored is not a backup; it is a file.

Usage
-----
    python3 scripts/ops/backup_database.py                    # dump + verify
    python3 scripts/ops/backup_database.py --no-verify        # dump only
    python3 scripts/ops/backup_database.py --verify-only PATH # check an old dump
    python3 scripts/ops/backup_database.py --list             # what we have

Environment
-----------
    DATABASE_URL                   source database (required for Postgres)
    PULSESOC_BACKUP_DIR            output directory (default ./backups)
    PULSESOC_BACKUP_RETENTION      how many dumps to keep (default 14)
    PULSESOC_BACKUP_VERIFY_URL     scratch Postgres for restore proof; if unset,
                                   Postgres verification is skipped and *says so*

Exit codes are meaningful: 0 success, 1 backup failed, 2 backup succeeded but
verification failed, 3 verification was skipped because it was not configured.
Code 2 and code 3 are deliberately different. "I could not check" must never be
reported as "I checked and it was fine" -- that is the same class of defect as
the hard-coded `"ok": True` this mission removed from /health.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import pathlib
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

EXIT_OK = 0
EXIT_BACKUP_FAILED = 1
EXIT_VERIFY_FAILED = 2
EXIT_VERIFY_SKIPPED = 3


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

def backup_dir() -> pathlib.Path:
    configured = os.getenv("PULSESOC_BACKUP_DIR", "").strip()
    path = pathlib.Path(configured) if configured else REPO_ROOT / "backups"
    path.mkdir(parents=True, exist_ok=True)
    return path


def retention_count() -> int:
    try:
        return max(1, int(os.getenv("PULSESOC_BACKUP_RETENTION", "14")))
    except ValueError:
        return 14


def database_url() -> str:
    return os.getenv("DATABASE_URL", "").strip()


def is_postgres() -> bool:
    return database_url().startswith(("postgres://", "postgresql://"))


def redact(url: str) -> str:
    """Never let a DSN reach a log line. These carry the password inline."""
    return re.sub(r"//([^:/@]+):([^@]+)@", r"//\1:***@", url or "")


def sqlite_path() -> pathlib.Path:
    url = database_url()
    if url.startswith("sqlite:///"):
        raw = url[len("sqlite:///"):]
        candidate = pathlib.Path(raw)
        return candidate if candidate.is_absolute() else REPO_ROOT / raw
    return REPO_ROOT / "coinpilotx.db"


def timestamp() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def log(message: str) -> None:
    print(f"[backup] {message}", flush=True)


# --------------------------------------------------------------------------
# Taking the backup
# --------------------------------------------------------------------------

def backup_postgres(target: pathlib.Path) -> dict:
    """`pg_dump | gzip`, with the exit status actually checked.

    `--no-owner --no-acl` matter: a dump that carries ownership statements will
    not restore into a scratch database owned by a different role, which means
    verification fails for a reason that has nothing to do with the data. That
    is how verification steps get disabled.
    """
    if not shutil.which("pg_dump"):
        raise RuntimeError(
            "pg_dump is not installed. On Railway's nixpacks image add "
            "`postgresql` to the build packages, or run this script from a "
            "machine that has the Postgres client tools."
        )
    command = [
        "pg_dump",
        "--no-owner",
        "--no-acl",
        "--format=plain",
        "--dbname", database_url(),
    ]
    log(f"pg_dump from {redact(database_url())}")
    with gzip.open(target, "wb") as handle:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert process.stdout is not None
        for chunk in iter(lambda: process.stdout.read(1 << 20), b""):
            handle.write(chunk)
        _, stderr = process.communicate()
    if process.returncode != 0:
        target.unlink(missing_ok=True)
        raise RuntimeError(
            f"pg_dump exited {process.returncode}: {redact(stderr.decode('utf-8', 'replace'))[:400]}"
        )
    return {"engine": "postgresql", "source": redact(database_url())}


def backup_sqlite(target: pathlib.Path) -> dict:
    """Use the online backup API, not a file copy.

    Copying a live SQLite file with `cp` can capture a torn page set if a write
    is in flight, and the result opens fine and fails later. `Connection.backup`
    takes a consistent snapshot of a database that is being written to.
    """
    source = sqlite_path()
    if not source.exists():
        raise RuntimeError(f"SQLite database not found at {source}")
    with tempfile.TemporaryDirectory() as scratch:
        snapshot = pathlib.Path(scratch) / "snapshot.db"
        src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        dst = sqlite3.connect(snapshot)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        with open(snapshot, "rb") as raw, gzip.open(target, "wb") as out:
            shutil.copyfileobj(raw, out)
    return {"engine": "sqlite", "source": str(source)}


def take_backup() -> tuple[pathlib.Path, dict]:
    engine = "postgresql" if is_postgres() else "sqlite"
    target = backup_dir() / f"pulsesoc-{engine}-{timestamp()}.sql.gz"
    meta = backup_postgres(target) if is_postgres() else backup_sqlite(target)
    size = target.stat().st_size
    if size == 0:
        target.unlink(missing_ok=True)
        raise RuntimeError("Backup file is empty")
    meta.update({
        "path": str(target),
        "bytes": size,
        "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
    })
    log(f"wrote {target.name} ({size:,} bytes)")
    return target, meta


# --------------------------------------------------------------------------
# Proving the backup restores
# --------------------------------------------------------------------------

def verify_sqlite(dump: pathlib.Path) -> dict:
    """Open the restored snapshot and make it answer questions.

    `PRAGMA integrity_check` is the part that catches a truncated gzip stream or
    a torn snapshot. The table/row counts are the part that catches a backup of
    the wrong -- empty -- database, which integrity_check would happily pass.
    """
    with tempfile.TemporaryDirectory() as scratch:
        restored = pathlib.Path(scratch) / "restored.db"
        with gzip.open(dump, "rb") as raw, open(restored, "wb") as out:
            shutil.copyfileobj(raw, out)
        conn = sqlite3.connect(restored)
        try:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                return {"verified": False, "reason": f"integrity_check: {integrity}"}
            tables = [
                row[0] for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            rows = 0
            for table in tables:
                rows += conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
        finally:
            conn.close()
    if not tables:
        return {"verified": False, "reason": "restored database contains no tables"}
    return {"verified": True, "tables": len(tables), "rows": rows, "method": "sqlite-restore"}


def verify_postgres(dump: pathlib.Path) -> dict:
    """Restore into PULSESOC_BACKUP_VERIFY_URL and count what arrived.

    This needs a real scratch database. There is no honest way to fake it, so
    when the variable is unset this reports skipped rather than inventing a
    reassuring answer.
    """
    scratch_url = os.getenv("PULSESOC_BACKUP_VERIFY_URL", "").strip()
    if not scratch_url:
        return {
            "verified": None,
            "reason": "PULSESOC_BACKUP_VERIFY_URL is not set; no restore was attempted",
        }
    if not shutil.which("psql"):
        return {"verified": None, "reason": "psql is not installed; no restore was attempted"}
    if scratch_url == database_url():
        return {
            "verified": False,
            "reason": "PULSESOC_BACKUP_VERIFY_URL points at the production database; refusing to restore over it",
        }

    log(f"restoring into scratch database {redact(scratch_url)}")
    reset = subprocess.run(
        ["psql", scratch_url, "-v", "ON_ERROR_STOP=1", "-c",
         "DROP SCHEMA IF EXISTS public CASCADE; CREATE SCHEMA public;"],
        capture_output=True,
    )
    if reset.returncode != 0:
        return {"verified": False, "reason": redact(reset.stderr.decode("utf-8", "replace"))[:300]}

    with gzip.open(dump, "rb") as handle:
        restore = subprocess.run(
            ["psql", scratch_url, "-v", "ON_ERROR_STOP=1", "-q"],
            stdin=handle, capture_output=True,
        )
    if restore.returncode != 0:
        return {"verified": False, "reason": redact(restore.stderr.decode("utf-8", "replace"))[:300]}

    counted = subprocess.run(
        ["psql", scratch_url, "-t", "-A", "-c",
         "SELECT count(*) FROM information_schema.tables WHERE table_schema='public'"],
        capture_output=True,
    )
    tables = int((counted.stdout or b"0").decode().strip() or 0)
    if tables == 0:
        return {"verified": False, "reason": "restore produced zero tables"}
    return {"verified": True, "tables": tables, "method": "postgres-restore"}


def verify(dump: pathlib.Path) -> dict:
    """Run the engine-appropriate restore, converting crashes into verdicts.

    A truncated dump raises EOFError out of gzip rather than returning anything,
    and the first version of this script let that traceback escape. The process
    then exited 1 -- the code that means "the backup could not be taken" -- for
    a run in which the backup was taken fine and the *restore* was the thing
    that failed. An operator reading exit codes would have looked in precisely
    the wrong place. Any exception here is a failed verification, code 2.
    """
    try:
        return verify_postgres(dump) if is_postgres() else verify_sqlite(dump)
    except Exception as exc:
        return {"verified": False, "reason": f"{type(exc).__name__}: {str(exc)[:200]}"}


# --------------------------------------------------------------------------
# Retention and the ledger
# --------------------------------------------------------------------------

def prune() -> list[str]:
    dumps = sorted(backup_dir().glob("pulsesoc-*.sql.gz"))
    removed = []
    while len(dumps) > retention_count():
        oldest = dumps.pop(0)
        oldest.unlink(missing_ok=True)
        removed.append(oldest.name)
    if removed:
        log(f"pruned {len(removed)} old dump(s)")
    return removed


def record(entry: dict) -> None:
    """Append to a ledger so "when did a restore last succeed" is answerable."""
    ledger = backup_dir() / "backup_log.jsonl"
    with open(ledger, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, sort_keys=True) + "\n")


def show_list() -> int:
    dumps = sorted(backup_dir().glob("pulsesoc-*.sql.gz"))
    if not dumps:
        print("No backups found in", backup_dir())
        return EXIT_OK
    for path in dumps:
        stat = path.stat()
        when = dt.datetime.fromtimestamp(stat.st_mtime, dt.timezone.utc).isoformat()
        print(f"{path.name}\t{stat.st_size:>12,} bytes\t{when}")
    return EXIT_OK


# --------------------------------------------------------------------------

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-verify", action="store_true", help="take the dump but skip the restore proof")
    parser.add_argument("--verify-only", metavar="PATH", help="verify an existing dump without taking a new one")
    parser.add_argument("--list", action="store_true", help="list existing dumps and exit")
    args = parser.parse_args(argv)

    if args.list:
        return show_list()

    if args.verify_only:
        dump = pathlib.Path(args.verify_only)
        if not dump.exists():
            log(f"no such dump: {dump}")
            return EXIT_BACKUP_FAILED
        result = verify(dump)
    else:
        try:
            dump, meta = take_backup()
        except Exception as exc:
            log(f"FAILED: {exc}")
            record({"at": dt.datetime.now(dt.timezone.utc).isoformat(), "ok": False, "error": str(exc)[:300]})
            return EXIT_BACKUP_FAILED

        if args.no_verify:
            record({**meta, "verified": None, "reason": "--no-verify"})
            prune()
            log("backup taken; NOT verified (--no-verify)")
            return EXIT_VERIFY_SKIPPED
        result = verify(dump)
        result = {**meta, **result}

    record({"at": dt.datetime.now(dt.timezone.utc).isoformat(), **result})
    prune()

    if result.get("verified") is True:
        detail = f"{result.get('tables')} tables"
        if result.get("rows") is not None:
            detail += f", {result['rows']:,} rows"
        log(f"VERIFIED restorable ({detail})")
        return EXIT_OK
    if result.get("verified") is None:
        log(f"NOT VERIFIED: {result.get('reason')}")
        return EXIT_VERIFY_SKIPPED
    log(f"VERIFICATION FAILED: {result.get('reason')}")
    return EXIT_VERIFY_FAILED


if __name__ == "__main__":
    sys.exit(main())
