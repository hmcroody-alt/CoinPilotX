"""A script that fabricates identity rows must not be able to run against prod.

`scripts/` holds ~200 fixture-style audits. The shape is always the same: insert
a synthetic user, exercise a surface through the real application, delete the
synthetic user again. That is harmless locally and catastrophic against
production, because the cleanup is written as a literal id.

`scripts/prelaunch_user_restriction_audit.py` ended its run with
`DELETE FROM users WHERE user_id=20`. Twenty is not a reserved id - production
has 39 users, so it is a real account, and there is no backup/restore runbook
for the users table. Nothing about the script signalled the hazard: it reaches
its database through `services/db.py`, which resolves `DATABASE_URL` from the
ambient environment, so the only difference between a local audit and a deleted
customer is which shell you were in. `railway run python3 scripts/...` is a
normal-looking command.

The guard therefore keys on the resolved DSN rather than on the caller's
intent - see `scripts/local_database_guard.py`.

Two placement rules this suite pins, both of which were learned the hard way:

1. **The guard must run above `import bot`, not at the top of `main()`.**
   bot.py ends with `if __name__ != "__main__": initialize_database_for_web_startup()`,
   which calls `init_db()`. Importing bot has therefore already connected to
   `DATABASE_URL` and run the full schema DDL before any function body executes.
   A guard inside `main()` is decorative. (`grep -n '^init_db()' bot.py` finds
   nothing, because the call is nested - which is exactly how the first version
   of this fix shipped wrong.)

2. **Detection is by AST, not by grep.** These scripts carry long lists of SQL
   fragments that they search against bot.py's *source*, so `grep 'DELETE FROM
   users'` is mostly false positives, and it misses the genuinely dangerous
   `INSERT OR REPLACE INTO users`. Only a `.execute()` call whose first argument
   is a literal write statement actually mutates anything.

A script that isolates itself - one that assigns `DATABASE_URL` to a temporary
sqlite file before importing bot - is already unreachable from production and is
not required to carry the guard.
"""

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"

IDENTITY_TABLES = {"users", "admin_users"}
WRITE_VERBS = ("INSERT", "UPDATE", "DELETE", "REPLACE")

# Scripts that are meant to mutate production, and whose writes are safe by
# construction. Guarding these would break a real operator workflow.
INTENTIONAL_PRODUCTION_TOOLS = {
    # Deliberate prod tool: dry-run by default, its only write is
    # `hidden_from_discovery=1` behind an explicit --apply-hide flag, and its
    # docstring records calibration against the 36 real production accounts.
    "scripts/qa_account_classification.py",
    # Operator action: activates a real sponsored ad. Its users write is an
    # idempotent "create the promotions account if absent" and it never deletes.
    "scripts/activate_pulse_radio_ad.py",
}


def _literal_sql(call):
    """The statically-known SQL text of a call's first argument, if any."""
    if not call.args:
        return None
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    if isinstance(first, ast.JoinedStr):
        return "".join(
            part.value if isinstance(part, ast.Constant) and isinstance(part.value, str) else "{}"
            for part in first.values
        )
    return None


def _target_table(sql):
    """The table a write statement targets, or None if it is not a write."""
    words = sql.replace("(", " ").replace("\n", " ").split()
    if not words or words[0].upper() not in WRITE_VERBS:
        return None
    # INSERT [OR IGNORE|OR REPLACE] INTO t / UPDATE t / DELETE FROM t / REPLACE INTO t
    for index, word in enumerate(words[1:], start=1):
        upper = word.upper()
        if upper in {"OR", "IGNORE", "REPLACE", "INTO", "FROM", "ABORT", "ROLLBACK", "FAIL"}:
            continue
        return word.strip('`"[]')
    return None


def _writes_identity_rows(tree):
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in {"execute", "executemany"}:
            continue
        sql = _literal_sql(node)
        if not sql:
            continue
        if _target_table(" ".join(sql.split())) in IDENTITY_TABLES:
            return True
    return False


def _bot_import_line(tree):
    lines = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == "bot" or alias.name.startswith("bot.") for alias in node.names):
                lines.append(node.lineno)
        elif isinstance(node, ast.ImportFrom) and node.module == "bot":
            lines.append(node.lineno)
    return min(lines) if lines else None


def _guard_call_line(tree):
    lines = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "require_local_database"
    ]
    return min(lines) if lines else None


def _isolates_own_database(tree, bot_line):
    """True when the script points DATABASE_URL at sqlite before importing bot."""
    assignments = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value == "DATABASE_URL"
                ):
                    assignments.append((node.lineno, ast.unparse(node.value)))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "setdefault" and len(node.args) == 2:
                key = node.args[0]
                if isinstance(key, ast.Constant) and key.value == "DATABASE_URL":
                    assignments.append((node.lineno, ast.unparse(node.args[1])))
    for line, value in sorted(assignments):
        if "sqlite" in value.lower() and (bot_line is None or line < bot_line):
            return True
    return False


def _scripts_requiring_the_guard():
    """(relative path, tree, bot import line) for every script that needs guarding."""
    found = []
    for path in sorted(SCRIPTS.rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        if relative in INTENTIONAL_PRODUCTION_TOOLS:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        bot_line = _bot_import_line(tree)
        if bot_line is None:
            # No bot import means no route through services/db.py to DATABASE_URL.
            continue
        if not _writes_identity_rows(tree):
            continue
        if _isolates_own_database(tree, bot_line):
            continue
        found.append((relative, tree, bot_line))
    return found


def test_the_scan_still_finds_the_scripts_it_is_meant_to_protect():
    """A detector that matches nothing would pass this suite while guarding nothing."""
    relatives = [relative for relative, _, _ in _scripts_requiring_the_guard()]
    assert "scripts/prelaunch_user_restriction_audit.py" in relatives, (
        "the script this suite was written for is no longer detected; the scan "
        "has drifted and every other assertion here is now vacuous"
    )
    assert len(relatives) >= 50, (
        f"only {len(relatives)} scripts detected, which is far below the ~80 that "
        "reach DATABASE_URL and write identity rows - the detector has broken"
    )


def test_every_identity_writing_script_refuses_a_non_local_database():
    missing = [
        relative
        for relative, tree, _ in _scripts_requiring_the_guard()
        if _guard_call_line(tree) is None
    ]
    assert not missing, (
        "these scripts insert or delete rows in users/admin_users against whatever "
        "DATABASE_URL is set, with no local-database guard:\n  "
        + "\n  ".join(missing)
        + "\n\nAdd, above the bot import:\n"
        "    from scripts.local_database_guard import require_local_database\n"
        '    require_local_database("<script name>")'
    )


def test_the_guard_runs_before_bot_is_imported():
    late = []
    for relative, tree, bot_line in _scripts_requiring_the_guard():
        guard_line = _guard_call_line(tree)
        if guard_line is not None and guard_line >= bot_line:
            late.append(f"{relative}: guard at line {guard_line}, bot imported at {bot_line}")
    assert not late, (
        "importing bot runs init_db() against DATABASE_URL, so a guard below the "
        "import has already let the connection happen:\n  " + "\n  ".join(late)
    )


if __name__ == "__main__":
    import pathlib as _pathlib
    import sys as _sys

    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
