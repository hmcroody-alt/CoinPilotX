#!/usr/bin/env python3
"""Prove the sentinel can fail (brief section 27).

A green gate proves nothing until you have watched it go red. This applies a
set of real mutations to real files, re-runs the gate, and records whether the
gate caught each one -- then restores every file from a byte-for-byte backup.

It is deliberately honest about the mutations the *table* contract does not
catch. Several of the required mutations are fail-open logic defects, not
schema defects; the table contract cannot see them, and saying so is more
useful than quietly scoping the list down to what passes.

    python3 scripts/db_contract_mutation_check.py

Safety: every edit is made against a ``.mutbak`` copy taken immediately before,
and restored in a ``finally``. It never touches git. Run it on a clean tree.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import warnings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from db_contract import contract  # noqa: E402

ALLOWLIST = os.path.join(ROOT, "config", "db_contract_allowlist.json")


def gate():
    """Run the contract. Returns (clean, findings)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        report, allow = contract.analyse(ROOT, ALLOWLIST)
    problems = allow.validate()
    return (not report.findings and not problems), report.findings, problems


class Mutation:
    """One or more edits applied together, then rolled back together.

    Several edits can be needed for a single logical mutation: 68 tables in
    this repo are declared in more than one place, so "remove the schema owner"
    means removing *every* owner. A mutation that removes one of three and
    expects a failure is testing the wrong thing -- the table really is still
    owned.
    """

    def __init__(self, name, edits, expect="table-contract", count=1):
        self.name, self.expect, self.count = name, expect, count
        self.edits = [(os.path.join(ROOT, rel), rel, old, new)
                      for rel, old, new in edits]

    def apply(self):
        for path, rel, old, new in self.edits:
            shutil.copy2(path, path + ".mutbak")
            with open(path, encoding="utf-8", errors="surrogateescape") as fh:
                body = fh.read()
            if old not in body:
                raise RuntimeError(f"{self.name}: anchor not found in {rel}")
            body = body.replace(old, new, self.count)
            with open(path, "w", encoding="utf-8", errors="surrogateescape") as fh:
                fh.write(body)

    def restore(self):
        for path, _rel, _old, _new in self.edits:
            if os.path.exists(path + ".mutbak"):
                shutil.move(path + ".mutbak", path)


# ---------------------------------------------------------------------------
# Mutations the table contract is supposed to catch
# ---------------------------------------------------------------------------

ANCHOR = '@webhook_app.route("/api/chat/start", methods=["POST"])'

SITE = 'cur.execute("UPDATE conversations SET last_message_at=?'

MUTATIONS = [
    Mutation(
        "1. a runtime query names a table that does not exist",
        [("bot.py", SITE,
          'cur.execute("SELECT 1 FROM a_table_that_was_never_created WHERE id=?", (1,))\n'
          '        ' + SITE)],
    ),
    Mutation(
        "2. private_chat_blocks is reintroduced (the original defect)",
        [("bot.py", SITE,
          'try:\n'
          '            cur.execute("SELECT 1 FROM private_chat_blocks WHERE blocker_user_id=?", (1,))\n'
          '        except Exception:\n'
          '            pass\n'
          '        ' + SITE)],
    ),
    Mutation(
        "3. every CREATE TABLE for blocked_users is removed (schema owner gone)",
        [("bot.py",
          "CREATE TABLE IF NOT EXISTS blocked_users",
          "CREATE TABLE IF NOT EXISTS blocked_users_RENAMED_BY_MUTATION"),
         ("services/pulse_social_graph_service.py",
          "CREATE TABLE IF NOT EXISTS blocked_users",
          "CREATE TABLE IF NOT EXISTS blocked_users_RENAMED_BY_MUTATION"),
         ("services/pulse_settings_routes.py",
          "CREATE TABLE IF NOT EXISTS blocked_users",
          "CREATE TABLE IF NOT EXISTS blocked_users_RENAMED_BY_MUTATION")],
        count=-1,   # every occurrence in each file
    ),
    Mutation(
        "3b. ONE of three blocked_users declarations is removed (must NOT fail)",
        [("bot.py",
          "CREATE TABLE IF NOT EXISTS blocked_users",
          "CREATE TABLE IF NOT EXISTS blocked_users_RENAMED_BY_MUTATION")],
        expect="no-finding",
    ),
    Mutation(
        "9. the retired column users.message_privacy comes back",
        [("bot.py", SITE,
          'cur.execute("SELECT message_privacy FROM users WHERE id=?", (1,))\n'
          '        ' + SITE)],
    ),
]

CONFIG_MUTATIONS = [
    Mutation(
        "8. UNKNOWN schema is marked safe by an unaccountable allowlist entry",
        [("config/db_contract_allowlist.json", '"tables": [],',
          '"tables": [{"table": "a_table_that_was_never_created"}],')],
        expect="allowlist-accountability",
    ),
    Mutation(
        "8b. a blanket disable switch is added",
        [("config/db_contract_allowlist.json", '"tables": [],',
          '"ignore_everything": true,\n  "tables": [],')],
        expect="no-blanket-disable",
    ),
]


def run_one(mut, checker):
    mut.apply()
    try:
        return checker()
    finally:
        mut.restore()


def main():
    print("Baseline (unmutated):")
    clean, findings, problems = gate()
    print(f"  clean={clean} findings={len(findings)} config_problems={len(problems)}")
    if not clean:
        print("  ABORT: tree is not clean; mutation results would be meaningless.")
        return 2
    print()

    results = []
    for mut in MUTATIONS:
        clean, findings, _ = run_one(mut, gate)
        caught = not clean
        if mut.expect == "no-finding":
            # A control: this mutation SHOULD be survivable. Passing here means
            # the gate stayed green, which is the correct answer.
            caught = clean
        detail = findings[0].render().splitlines()[-1].strip() if findings else ""
        results.append((mut.name, caught, "table contract", detail[:96]))

    for mut in CONFIG_MUTATIONS:
        def check():
            # These are caught by the protection test, not by analyse().
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:randomly",
                 "tests/protection/test_database_contract.py",
                 "-k", "accountable or blanket"],
                cwd=ROOT, capture_output=True, text=True)
            return proc.returncode != 0, proc.stdout.strip().splitlines()[-1:]
        caught, tail = run_one(mut, check)
        results.append((mut.name, caught, "protection test",
                        (tail or [""])[0][:96]))

    print("MUTATION RESULTS")
    print("=" * 100)
    for name, caught, by, detail in results:
        mark = "CAUGHT  " if caught else "MISSED  "
        print(f"{mark} {name}")
        print(f"         by: {by}")
        if detail:
            print(f"         {detail}")
    print()

    missed = [r for r in results if not r[1]]
    print(f"{len(results) - len(missed)}/{len(results)} caught.")
    if missed:
        print("MISSED:")
        for name, *_ in missed:
            print(f"  {name}")
    print()
    print("NOT COVERED BY THIS GATE -- stated rather than quietly dropped:")
    print("  4. wrapping a security query in `except: pass`")
    print("  5. making a block lookup failure return ALLOW")
    print("  7. removing a UNIQUE constraint from a schema")
    print("     These are fail-open *logic* defects, not schema defects. The")
    print("     table named by the query still exists, so the table contract")
    print("     resolves it and says nothing. They are the subject of the")
    print("     fail-open audit, not of this gate.")
    print("  6. breaking a critical route-pack import")
    print("     Already observable: bot.py:1393 records every pack into")
    print("     ROUTE_PACK_STATUS and logs CRITICAL with a traceback;")
    print("     /health/routes returns 503 naming the failed pack.")
    return 1 if missed else 0


if __name__ == "__main__":
    raise SystemExit(main())
