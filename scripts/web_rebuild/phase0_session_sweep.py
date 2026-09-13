#!/usr/bin/env python3
"""Phase 0 retention for ``mobile_security_sessions`` — the shared web+native session store.

The gap analysis asked for a "session TTL sweep: 9,728 of 10,132 rows are
``revoked``/``rotated`` and are never deleted." Three things measured against
production contradict that brief, and this script is what is left after taking
them seriously.

**1. ``rotated`` is not a dead status. It is a second live credential tier.**

``messenger_media_cookie_user_id()`` (``bot.py:91458``) authenticates on::

    WHERE refresh_token_hash=? AND status IN ('active','rotated')
      AND COALESCE(revoked_at,'')='' AND COALESCE(refresh_expires_at,'')>=?

It includes ``rotated`` deliberately — its own docstring explains that shipped
handsets render media carrying only the persistent cookie, and refusing them
would break installed builds. So a rotated row is a working identity, and it has
no time bound beyond ``refresh_expires_at``, which is **ten years out**
(``MOBILE_REFRESH_TOKEN_TTL_SECONDS`` defaults to ``60*60*24*3650``).

Measured in production: **all 1,723 ``rotated`` rows have an empty ``revoked_at``
and a 2036 expiry, so all 1,723 currently authenticate** — 451 of them last
rotated 60–90 days ago. Deleting them logs real users out of message media.

The true dead population is the 8,008 ``revoked`` rows, not 9,731.

**2. Deleting a dead row does not just free space — it disables reuse detection.**

``rotate_mobile_refresh_token()`` (``bot.py:31288``) handles a refresh token that
does not match a live session by looking the hash up again with **no status and
no expiry filter**::

    SELECT * FROM mobile_security_sessions WHERE refresh_token_hash=?
    ORDER BY id DESC LIMIT 1

Finding that row is what triggers family revocation, the ``refresh_token_reuse``
security event, and the user notification. If the row has been deleted the
lookup returns nothing and the function falls through to ``return None, {}`` — a
plain 401. **A replayed stolen token becomes a silent auth failure**, with no
event, no revocation and no alert. The deletion that was supposed to be
housekeeping quietly removes a security control, and nothing fails to announce
it.

**3. The obvious retention key is inert.** Keying retention on
``refresh_expires_at`` looks right and deletes nothing until 2036, because that
is what a ten-year refresh TTL means. Retention here must key on *when the row
stopped being live* — ``revoked_at`` / ``rotated_at`` / ``last_seen_at``.

So the job is not deletion
-------------------------

The thing actually worth fixing is not size. The table is **12 MB**. It is that
**9,731 of 9,731 dead rows still carry a ``user_agent``, and 9,729 still carry an
``ip_hash``** — retained device and network identifiers for sessions that ended,
some of them over two months ago.

This script therefore **tombstones** rather than deletes: it nulls the columns
that are payload (user agent, IP hash, device hash, access-token hash, metadata)
and keeps the columns that are evidence (``refresh_token_hash``,
``session_family_id``, ``user_id``, the timestamps, ``reuse_detected_at``).

That is strictly better than the brief on all three axes:

- **Security:** reuse detection keeps working *forever* instead of being capped
  at whatever horizon a delete job picks.
- **Privacy:** the identifiers leave dead rows on a 30-day clock instead of
  never.
- **Size:** the nulled columns are ~3.1 MB of the 6.2 MB of dead-row payload,
  most of what a delete would have reclaimed.

What the classes are
--------------------

======================  ==========================================  ===========
class                   rows (measured)                             action
======================  ==========================================  ===========
``active``              405                                         never touch
``rotated`` in grace    ``rotated_at`` within the reuse grace        never touch
``rotated`` past grace  1,723 total, still a live credential         tombstone
``revoked``             8,008, dead to every auth path              tombstone
reuse evidence          4,786 carry ``reuse_detected_at``            tombstone, never delete
======================  ==========================================  ===========

**The grace-window carve-out is load-bearing.** ``mobile_refresh_reuse_grace_allowed()``
(``bot.py:31118``) tolerates a benign refresh desync only when the stale row is
``rotated``, within ``PULSESOC_REFRESH_REUSE_GRACE_SECONDS`` (default 180), and
matches on ``device_hash`` **or** ``ip_hash``. Tombstoning nulls exactly those two
columns. Tombstone a row inside that window and the grace check cannot match, the
request falls through to the reuse branch, and a legitimate user is signed out of
every device and sent a "suspicious session activity" alert. This script reads
the same environment variable the app reads and refuses to touch anything newer
than the window plus ``--grace-margin`` seconds.

Deletion is a separate, opt-in flag
-----------------------------------

``--delete`` removes only rows that are *already tombstoned*, are ``revoked``,
carry no reuse evidence, and are older than ``--delete-after-days`` (default
365). It is off by default, and it is the only operation here that costs
something irreversible: reuse detection for those specific tokens.

A note on what this script does not fix
---------------------------------------

47% of the table (4,786 rows) is ``revoked_reason='refresh_token_reuse'``, and
those 4,786 events belong to **nine users** — one of whom accounts for 2,259
across 35 families and 11 devices. Single families were revoked for reuse up to
19 times. Nine users do not commit 4,786 token thefts, and a correctly-detected
family is revoked once and then dead; a family re-triggering nineteen times is a
client that keeps coming back. The dominant bucket is ``platform='web'`` /
``device_label='desktop-web'`` (2,226).

That is a false-positive loop in refresh rotation, it is the largest single
consumer of this table, and **the web rebuild adds exactly the second client leg
that drives it.** It is recorded in the gap analysis. Fixing it changes
authentication behaviour and is deliberately out of scope for a retention job.

Usage::

    # what it would do, touching nothing (default)
    railway run --service Postgres python3 scripts/web_rebuild/phase0_session_sweep.py

    # tombstone
    railway run --service Postgres python3 scripts/web_rebuild/phase0_session_sweep.py --apply

    # tombstone, and delete ancient non-evidence rows too
    railway run --service Postgres python3 scripts/web_rebuild/phase0_session_sweep.py --apply --delete

Works against SQLite too (``DATABASE_URL=sqlite:///...``) so it can be tested
without a Postgres.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime, timedelta, timezone

TABLE = "mobile_security_sessions"


def now_utc() -> datetime:
    """Naive UTC, because that is literally what is in the column.

    ``rotate_mobile_refresh_token()`` writes ``datetime.utcnow().isoformat(
    timespec="seconds")`` (``bot.py:31151``) — no offset, no ``Z``. Comparing an
    offset-aware ISO string (``...+00:00``) against those is a *string* compare
    in this schema, and ``'2026-09-12T05:00:00+00:00' > '2026-09-12T05:00:00'``,
    so an aware cutoff drifts the boundary by a character rather than by an
    hour. Using local time instead would shift every cutoff by the server's
    offset, which on a developer machine is enough to push rows across the grace
    window.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)

#: Nulled by a tombstone. Every one of these is device/network payload or a
#: secret that is useless once the session is dead.
#:
#: ``device_hash`` and ``ip_hash`` are the two the grace check reads, which is
#: why the grace window is carved out above. ``refresh_token_hash``,
#: ``session_family_id`` and ``user_id`` are deliberately NOT here: they are what
#: reuse detection needs, and clearing them is the silent-401 failure this whole
#: file exists to avoid.
TOMBSTONE_COLUMNS = (
    "user_agent",
    "ip_hash",
    "device_hash",
    "access_token_hash",
    "metadata_json",
    "device_label",
    "country",
)

#: Kept forever, on every row, including tombstones.
EVIDENCE_COLUMNS = (
    "id", "user_id", "refresh_token_hash", "session_family_id", "status",
    "created_at", "rotated_at", "revoked_at", "revoked_reason",
    "reuse_detected_at", "last_seen_at", "refresh_expires_at",
)


def grace_seconds() -> int:
    """The app's own constant, read the same way the app reads it (bot.py:140)."""
    return max(30, int(os.getenv("PULSESOC_REFRESH_REUSE_GRACE_SECONDS", "180")))


def connect(url: str):
    """Return (conn, paramstyle, is_postgres). Postgres via psycopg2, else sqlite3."""
    if url.startswith(("postgres://", "postgresql://")):
        try:
            import psycopg2
        except ImportError:  # pragma: no cover
            sys.exit("psycopg2 is required for Postgres. Use the repo's .venv.")
        return psycopg2.connect(url), "%s", True
    import sqlite3
    path = re.sub(r"^sqlite:/+", "/", url) if url.startswith("sqlite:") else url
    return sqlite3.connect(path), "?", False


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def scalar(cur, sql, args=()):
    cur.execute(sql, args)
    row = cur.fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def build_predicates(ph: str, args: argparse.Namespace):
    """The WHERE clauses, with the cutoffs already resolved to ISO strings.

    Timestamps in this table are ISO-8601 *text* in both engines, so string
    comparison is chronological and the same SQL works on Postgres and SQLite.
    Rows with no usable timestamp at all are excluded rather than assumed old —
    a missing date is not evidence that a row is safe to touch.
    """
    now = now_utc()
    # The floor nothing may cross, whatever else is configured.
    grace_cutoff = iso(now - timedelta(seconds=grace_seconds() + args.grace_margin))
    dead_cutoff = iso(now - timedelta(days=args.tombstone_after_days))
    delete_cutoff = iso(now - timedelta(days=args.delete_after_days))

    # "when this row stopped being live".
    #
    # Two columns are deliberately absent. `refresh_expires_at` is a decade out
    # on every row and would make every rule below inert. `created_at` is worse
    # than useless: it is always populated, so including it means no row is ever
    # excluded for want of a date — and it answers the wrong question. A session
    # created 1,000 days ago may have been revoked this morning, and keying on
    # creation would age it out immediately, deleting the reuse evidence for a
    # token that is still circulating.
    #
    # So a `revoked` row with no `revoked_at`, no `rotated_at` and no
    # `last_seen_at` is left alone indefinitely. That is the intended outcome:
    # the row is small, and guessing its age wrongly is unrecoverable. `report()`
    # counts them so the pile cannot grow unnoticed.
    went_dead = (
        "COALESCE(NULLIF(revoked_at,''), NULLIF(rotated_at,''), "
        "NULLIF(last_seen_at,''))"
    )

    still_carries_payload = " OR ".join(
        f"COALESCE({col},'') <> ''" for col in TOMBSTONE_COLUMNS
    )
    # Every comparison here is wrapped in COALESCE, and not for tidiness. This
    # predicate is used under a `NOT`, and in SQL's three-valued logic a bare
    # `revoked_reason = 'refresh_token_reuse'` against a NULL column evaluates to
    # NULL, not false — so `NOT (false OR NULL)` is NULL, and the row silently
    # fails the delete filter. `revoked_reason` is NULL on most production rows,
    # which made `--delete` a permanent no-op that reported success. It failed
    # safe, and it failed silently, which is the combination that survives
    # review.
    has_reuse_evidence = (
        "(COALESCE(reuse_detected_at,'') <> '' "
        "OR COALESCE(revoked_reason,'') = 'refresh_token_reuse')"
    )

    tombstone_where = (
        f"status <> 'active' "
        f"AND {went_dead} IS NOT NULL "
        f"AND {went_dead} < {ph} "      # older than the tombstone horizon
        f"AND {went_dead} < {ph} "      # AND outside the grace window, always
        f"AND ({still_carries_payload})"
    )
    tombstone_args = [dead_cutoff, grace_cutoff]

    delete_where = (
        f"status = 'revoked' "
        f"AND NOT {has_reuse_evidence} "
        f"AND NOT ({still_carries_payload}) "   # tombstoned first, on a previous run
        f"AND {went_dead} IS NOT NULL "
        f"AND {went_dead} < {ph} "
        f"AND {went_dead} < {ph}"
    )
    delete_args = [delete_cutoff, grace_cutoff]

    return {
        "went_dead": went_dead,
        "has_reuse_evidence": has_reuse_evidence,
        "tombstone_where": tombstone_where,
        "tombstone_args": tombstone_args,
        "delete_where": delete_where,
        "delete_args": delete_args,
        "grace_cutoff": grace_cutoff,
    }


def report(cur, ph: str, p: dict) -> None:
    print("  current population")
    cur.execute(f"SELECT status, COUNT(*) FROM {TABLE} GROUP BY status ORDER BY 2 DESC")
    for status, n in cur.fetchall():
        print(f"    {str(status):<10} {n:>7}")

    live_rotated = scalar(
        cur,
        f"SELECT COUNT(*) FROM {TABLE} WHERE status='rotated' AND COALESCE(revoked_at,'')=''",
    )
    evidence = scalar(cur, f"SELECT COUNT(*) FROM {TABLE} WHERE {p['has_reuse_evidence']}")
    undated = scalar(
        cur,
        f"SELECT COUNT(*) FROM {TABLE} WHERE status <> 'active' AND {p['went_dead']} IS NULL",
    )
    print(f"    {'-' * 28}")
    print(f"    rotated + still a valid credential:  {live_rotated:>7}  (never deleted)")
    print(f"    rows carrying reuse evidence:        {evidence:>7}  (never deleted)")
    print(f"    dead rows with no usable date:       {undated:>7}  (never touched)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="actually write (default: dry run)")
    ap.add_argument("--delete", action="store_true",
                    help="also DELETE ancient tombstoned non-evidence rows (off by default)")
    ap.add_argument("--tombstone-after-days", type=int, default=30,
                    help="clear payload columns on dead rows older than this (default 30)")
    ap.add_argument("--delete-after-days", type=int, default=365,
                    help="with --delete, remove tombstoned revoked rows older than this (default 365)")
    ap.add_argument("--grace-margin", type=int, default=3600,
                    help="extra seconds of safety beyond the reuse grace window (default 3600)")
    ap.add_argument("--database-url", default="")
    args = ap.parse_args()

    url = (args.database_url or os.getenv("DATABASE_PUBLIC_URL")
           or os.getenv("DATABASE_URL") or "")
    if not url:
        return int(bool(sys.stderr.write("No DATABASE_URL / DATABASE_PUBLIC_URL.\n"))) or 2

    conn, ph, is_pg = connect(url)
    if is_pg and not args.apply:
        # Enforced by the server, not by reading the code below and believing it.
        # A dry run is the thing an operator points at production first, and the
        # whole value of it is the guarantee that it cannot write. Without this
        # the guarantee is "I checked that every branch before the `--apply`
        # return is a SELECT", which is true today and is one careless edit from
        # not being true. Postgres will now reject the write instead.
        conn.set_session(readonly=True)
    cur = conn.cursor()
    p = build_predicates(ph, args)

    mode = "APPLY" if args.apply else "DRY RUN — nothing will be written"
    print(f"\n{TABLE} retention  [{mode}]")
    print(f"  engine               : {'postgres' if is_pg else 'sqlite'}")
    print(f"  reuse grace window   : {grace_seconds()}s (+{args.grace_margin}s margin)")
    print(f"  nothing newer than   : {p['grace_cutoff']}  <- hard floor")
    print(f"  tombstone dead rows  : older than {args.tombstone_after_days}d")
    print(f"  delete               : {'ENABLED, >' + str(args.delete_after_days) + 'd' if args.delete else 'disabled'}\n")

    report(cur, ph, p)

    n_tomb = scalar(cur, f"SELECT COUNT(*) FROM {TABLE} WHERE {p['tombstone_where']}",
                    p["tombstone_args"])
    n_del = scalar(cur, f"SELECT COUNT(*) FROM {TABLE} WHERE {p['delete_where']}",
                   p["delete_args"]) if args.delete else 0

    print(f"\n  to tombstone (clear {len(TOMBSTONE_COLUMNS)} payload columns): {n_tomb}")
    if args.delete:
        print(f"  to delete   (already tombstoned, no evidence):  {n_del}")

    # The assertion that matters: whatever we are about to touch, none of it is
    # a live credential inside the grace window.
    unsafe = scalar(
        cur,
        f"SELECT COUNT(*) FROM {TABLE} WHERE ({p['tombstone_where']}) "
        f"AND status='rotated' AND {p['went_dead']} >= {ph}",
        p["tombstone_args"] + [p["grace_cutoff"]],
    )
    if unsafe:
        print(f"\n  REFUSING: {unsafe} row(s) inside the grace window matched. Bug in the predicate.")
        return 1

    if not args.apply:
        print("\n  Dry run. Re-run with --apply to write.\n")
        return 0

    # ORDER IS LOAD-BEARING: delete BEFORE tombstoning, never after.
    #
    # The delete predicate requires `NOT (still_carries_payload)`, which means
    # "this row was tombstoned on an earlier run". That is the two-pass gate: a
    # single invocation must never both strip a row and remove it, so a row is
    # always visible in one run's output before it can vanish in the next. Run
    # the UPDATE first and the gate evaporates, because the rows it just
    # tombstoned satisfy it inside the same transaction. Measured on the fixture
    # with the statements in the other order, the dry run reported `to delete: 0`
    # and the apply deleted 3,222 — the preview lied, in the direction of
    # deleting more than it showed.
    #
    # The row-count guards below are the second half of that property: the
    # preview is not advice, it is a prediction, and a mismatch means the
    # predicate saw a different table than the one counted. Rolling back is the
    # only safe response, because the discrepancy itself is unexplained.
    deleted = 0
    if args.delete:
        cur.execute(f"DELETE FROM {TABLE} WHERE {p['delete_where']}", p["delete_args"])
        deleted = cur.rowcount
        if deleted != n_del:
            conn.rollback()
            print(f"\n  REFUSING: preview said {n_del} to delete, statement matched "
                  f"{deleted}. Rolled back — the dry run must be exact.")
            return 1

    # Empty string, not NULL. `device_hash` is declared NOT NULL (bot.py:31082),
    # so a NULL tombstone aborts the whole UPDATE with an IntegrityError — and
    # because it is one statement, it aborts having written nothing, which is the
    # good version of that bug. '' is also the right shape regardless: every
    # reader in bot.py tests emptiness as `COALESCE(col,'') = ''`, so '' is what
    # "absent" already means in this schema.
    sets = ", ".join(f"{col}=''" for col in TOMBSTONE_COLUMNS)
    cur.execute(f"UPDATE {TABLE} SET {sets} WHERE {p['tombstone_where']}", p["tombstone_args"])
    tombstoned = cur.rowcount
    if tombstoned != n_tomb:
        conn.rollback()
        print(f"\n  REFUSING: preview said {n_tomb} to tombstone, statement matched "
              f"{tombstoned}. Rolled back — the dry run must be exact.")
        return 1
    conn.commit()

    print(f"\n  tombstoned: {tombstoned}")
    if args.delete:
        print(f"  deleted   : {deleted}")
    print("  done. Reuse detection still resolves every remaining hash.\n")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
