# Database Backup and Restore Runbook

Mission phase 13.

## What was here before

Nothing. No `pg_dump` call anywhere in the repository, no `BACKUP_*` variable in
`.env.example`'s ~180 keys, no scheduled job, no restore procedure, and no
record that a restore had ever been attempted. "We have backups" was a belief
about what Railway's Postgres add-on does by default, held by people who had
never tested it.

That belief may even be true. The problem is that it was never checked, and the
moment you need it is the moment you cannot afford to find out.

## What exists now

`scripts/ops/backup_database.py`. It takes a dump *and restores it*, and reports
those as two separate outcomes.

```
python3 scripts/ops/backup_database.py                    # dump, then restore-verify
python3 scripts/ops/backup_database.py --no-verify        # dump only
python3 scripts/ops/backup_database.py --verify-only PATH # re-check an old dump
python3 scripts/ops/backup_database.py --list             # inventory
```

Exit codes are the interface, and three of them are deliberately distinct:

| Code | Meaning |
|---|---|
| 0 | Dump written and successfully restored |
| 1 | The dump could not be taken |
| 2 | The dump was taken but did **not** restore |
| 3 | The dump was taken; restore was **not attempted** |

Codes 2 and 3 must never be collapsed. Reporting "I could not check" as "I
checked and it was fine" is the same defect this mission removed from `/health`,
where a hard-coded `"ok": True` reported a healthy platform through a total
data-layer outage.

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `DATABASE_URL` | sqlite | Source database |
| `PULSESOC_BACKUP_DIR` | `./backups` | Output directory. Gitignored |
| `PULSESOC_BACKUP_RETENTION` | 14 | Dumps kept before pruning oldest |
| `PULSESOC_BACKUP_VERIFY_URL` | *(unset)* | **Scratch** Postgres for restore proof |

`PULSESOC_BACKUP_VERIFY_URL` needs a real, empty, throwaway database — the
verify step runs `DROP SCHEMA public CASCADE` before restoring. The script
refuses to proceed if that URL equals `DATABASE_URL`, because that particular
typo destroys production. With it unset, Postgres backups exit **3**, not 0: the
dump is taken and honestly labelled unverified.

On Railway, add a second Postgres service and point `PULSESOC_BACKUP_VERIFY_URL`
at it. Without one, the restore has not been proven and the runbook below is
theory.

## Verified result

Run on this repository's SQLite database, 2026-08-05:

```
[backup] wrote pulsesoc-sqlite-20260805T224848Z.sql.gz (13,728,965 bytes)
[backup] VERIFIED restorable (647 tables, 275,655 rows)
```

The failure paths were exercised too, because a verification step that cannot
fail is not a verification step:

| Injected fault | Result |
|---|---|
| Dump truncated to half its length | `VERIFICATION FAILED: EOFError…`, exit 2 |
| Valid gzip of a valid but **empty** database | `VERIFICATION FAILED: restored database contains no tables`, exit 2 |
| Untouched dump | `VERIFIED restorable (647 tables, 275,655 rows)`, exit 0 |

The empty-database row is the one worth dwelling on. That file is a valid gzip
containing a valid SQLite database that passes `PRAGMA integrity_check` — every
structural check says it is fine. Only counting tables catches it, and an empty
result is precisely what a misconfigured `DATABASE_URL` produces. A backup job
pointed at the wrong database will otherwise report success every night.

## Restore procedure

**Postgres.** Confirm the target first; this is destructive and irreversible.

```bash
gunzip -c backups/pulsesoc-postgresql-<stamp>.sql.gz | psql "$TARGET_URL" -v ON_ERROR_STOP=1
```

`ON_ERROR_STOP=1` is not optional. Without it `psql` continues past failed
statements and exits 0, leaving a partial restore that looks like a successful
one.

**SQLite.** Stop the app first — restoring under a live process yields a
database that opens fine and is subtly wrong.

```bash
gunzip -c backups/pulsesoc-sqlite-<stamp>.sql.gz > coinpilotx.db
```

**After any restore**, before sending traffic:

```bash
curl -fsS https://<host>/health/ready
```

`/health/ready` returns 503 with `database_unreachable` or `route_packs_failed`
until the process can actually serve. `/health` deliberately stays 200 — it
answers liveness, and restarting a container over a transient database blip only
lengthens the outage.

## What is still not done

**No off-site copy.** Dumps land on local disk. On Railway that disk is
ephemeral, so a container replacement takes the backups with it. R2 credentials
already exist in the environment (`R2_*` / `AWS_*`, see
`docs/provider_api_purchase_report.md` §2) and uploading there is the obvious
next step, but it is not implemented and should not be assumed.

**No schedule.** Nothing runs this automatically. The `Procfile` has no cron
process and Railway cron is not configured. Until one of those exists, backups
happen when a human remembers, which is the same as not having backups.

**Restore time is unmeasured.** 647 tables restored quickly against local
SQLite. Nobody has timed a full Postgres restore at production size, so the
recovery time objective is currently unknown rather than merely large.

These three are honest gaps, written down rather than closed, because a runbook
that overstates its coverage is worse than one that admits where it stops.
