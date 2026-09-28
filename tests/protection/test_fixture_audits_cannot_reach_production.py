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

Three rules this suite pins, all of which were learned the hard way:

1. **The guard must run above the import that binds the DSN, not at the top of
   `main()`.** For the 83 scripts that import bot, that import is the moment:
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

3. **`import bot` is not the only route to `DATABASE_URL`.** `from services import
   db` resolves `ENGINE_URL` and builds the engine at import time (services/db.py
   lines 41 and 118), and so does any `services.*` module that transitively
   imports it. On PostgreSQL that engine is built once, from that value, and
   `connect()` hands back its pool - so a script that points `DATABASE_URL` at a
   sqlite file *after* such an import has isolated nothing and will write to
   whatever DSN was ambient. `scripts/undx_read_qa_run.py` has precisely that
   ordering: it imports `services.undx_agent_policy` and only then assigns
   sqlite. Detection therefore follows the services import graph rather than the
   name `bot`. (A route through some other root package - `pulse_communications_v2`,
   say - remains undetected. No script currently reaches the database only that
   way; one that did would need adding here.)

A script that isolates itself is not required to carry the guard. Two proofs
count, and either must take effect before the import that latches the DSN:
assigning `DATABASE_URL` to a sqlite file, and refusing outright any DSN whose
host is not loopback - the shape `scripts/verify_cart_variant_schema_on_postgres.py`
uses, which is as final as the guard and predates it.
"""

import ast
import functools
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
SERVICES = ROOT / "services"

IDENTITY_TABLES = {"users", "admin_users"}
WRITE_VERBS = ("INSERT", "UPDATE", "DELETE", "REPLACE")

# Matches `scripts/local_database_guard.py`. Kept as a literal set rather than
# imported from it, because this list is what a hand-rolled refusal is *checked
# against* - importing the guard's own copy would make the two agree by
# construction and stop testing anything.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1", "[::1]"})

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
    # One-time production cleanup for a single named broken group ("bigboss").
    # Deleting real rows is the entire point of the script.
    "scripts/remove_bigboss_group.py",
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


def _mutates_real_rows(tree):
    """True when a script writes identity rows, or deletes rows from any table.

    Two criteria rather than one. Identity writes are the headline hazard, but
    `pulse_music_picker_audit.py` fabricated a post and cleaned up with
    `DELETE FROM pulse_posts WHERE id=?`, and `failed_login_security_audit.py`
    cleaned up with `DELETE FROM auth_events WHERE email_domain=?` - neither
    touches users, and both destroy real rows if the id or domain matches.

    UPDATE against a non-identity table is deliberately *not* a criterion. The
    scripts that only do that are prod repair tools (`pulse_media_repair.py`,
    `cleanup_room_join_messages.py`), and a criterion that swept them in would
    need an allowlist longer than the rule it enforces.
    """
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in {"execute", "executemany"}:
            continue
        sql = _literal_sql(node)
        if not sql:
            continue
        normalised = " ".join(sql.split())
        verb = normalised.split()[0].upper() if normalised.split() else ""
        if verb in {"DELETE", "DROP", "TRUNCATE"}:
            return True
        if _target_table(normalised) in IDENTITY_TABLES:
            return True
    return False


def _imported_services_modules(node, package=""):
    """The `services.*` modules a source imports, named relative to `services/`.

    `from services.x import y` records both `x` and `x.y`, because `y` may be a
    submodule or an attribute and only the real module list can tell them apart.
    `package` resolves relative imports and only matters inside `services/`.
    """
    found = set()

    def record(dotted):
        parts = [part for part in dotted.split(".") if part]
        if not parts:
            found.add("")
        for index in range(1, len(parts) + 1):
            found.add(".".join(parts[:index]))

    for inner in ast.walk(node):
        if isinstance(inner, ast.Import):
            for alias in inner.names:
                if alias.name == "services":
                    found.add("")
                elif alias.name.startswith("services."):
                    record(alias.name[len("services."):])
        elif not isinstance(inner, ast.ImportFrom):
            continue
        elif inner.level:
            base = package.split(".") if package else []
            if inner.level > 1:
                base = base[: max(0, len(base) - (inner.level - 1))]
            prefix = base + ((inner.module or "").split("."))
            record(".".join(prefix))
            for alias in inner.names:
                record(".".join([*prefix, alias.name]))
        elif inner.module == "services":
            for alias in inner.names:
                record(alias.name)
        elif (inner.module or "").startswith("services."):
            stem = inner.module[len("services."):]
            record(stem)
            for alias in inner.names:
                record(f"{stem}.{alias.name}")
    return found


@functools.lru_cache(maxsize=1)
def _services_modules_reaching_the_database():
    """Every module under `services/` whose imports reach `services/db.py`.

    Importing any of them latches the DSN, so for this suite's purposes they are
    all `services.db`. Computed rather than listed because it is most of the
    directory - a checked-in list would be stale within the week.
    """
    trees = {}
    packages = {}
    for path in SERVICES.rglob("*.py"):
        parts = list(path.relative_to(SERVICES).with_suffix("").parts)
        is_package = bool(parts) and parts[-1] == "__init__"
        if is_package:
            parts.pop()
        name = ".".join(parts)
        try:
            trees[name] = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        packages[name] = name if is_package else name.rpartition(".")[0]

    edges = {
        name: _imported_services_modules(tree, package=packages[name])
        for name, tree in trees.items()
    }
    reaching = {"db"}
    growing = True
    while growing:
        growing = False
        for name, imports in edges.items():
            if name not in reaching and imports & reaching:
                reaching.add(name)
                growing = True
    return frozenset(reaching)


def _enclosing_functions(tree):
    """Map each node to the function whose body holds it, or None at module scope."""
    enclosing = {}

    def descend(node, current):
        for child in ast.iter_child_nodes(node):
            enclosing[child] = current
            inside = child if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else current
            descend(child, inside)

    descend(tree, None)
    return enclosing


def _database_binding_events(tree):
    """(line, enclosing function, label) for every import that latches the DSN.

    `import bot` runs init_db() and connects outright. Importing a services
    module that reaches services/db.py resolves `ENGINE_URL` and builds the
    engine, which on PostgreSQL is the only time the DSN is read - so both make
    a later `DATABASE_URL` assignment ineffective.
    """
    reaching = _services_modules_reaching_the_database()
    scopes = _enclosing_functions(tree)
    events = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if isinstance(node, ast.Import):
            imports_bot = any(
                alias.name == "bot" or alias.name.startswith("bot.") for alias in node.names
            )
        else:
            imports_bot = node.module == "bot" or (node.module or "").startswith("bot.")
        if imports_bot:
            events.append((node.lineno, scopes.get(node), "bot"))
            continue
        reached = sorted(_imported_services_modules(node) & reaching)
        if reached:
            events.append((node.lineno, scopes.get(node), f"services.{reached[0]}"))
    return events


def _guard_call_line(tree):
    lines = [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "require_local_database"
    ]
    return min(lines) if lines else None


def _loopback_refusal_functions(tree):
    """Functions here that exit unless the database host is loopback.

    Maps name -> whether the body reads DATABASE_URL itself, because the other
    spelling takes the URL as an argument and is only a proof if the call site
    passes DATABASE_URL to it.

    Deliberately narrow: the body has to name the loopback hosts (literally, or
    through a module constant listing them), pull a host out of the URL, and
    exit. A script that establishes locality some other way should call
    `require_local_database` rather than hope this pattern-matches it.
    """
    listing_loopback = set()
    for node in tree.body:
        if isinstance(node, ast.Assign):
            rendered = ast.unparse(node.value)
            if any(host in rendered for host in LOOPBACK_HOSTS):
                listing_loopback.update(
                    target.id for target in node.targets if isinstance(target, ast.Name)
                )

    refusals = {}
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        source = ast.unparse(node)
        used = {inner.id for inner in ast.walk(node) if isinstance(inner, ast.Name)}
        names_loopback = any(host in source for host in LOOPBACK_HOSTS) or bool(
            used & listing_loopback
        )
        reads_a_host = "hostname" in source or "urlparse" in source
        refuses = any(
            isinstance(inner, ast.Raise)
            or (isinstance(inner, ast.Call) and "exit" in ast.unparse(inner.func).lower())
            for inner in ast.walk(node)
        )
        if names_loopback and reads_a_host and refuses:
            refusals[node.name] = "DATABASE_URL" in source
    return refusals


def _isolation_proofs(tree):
    """(line, enclosing function) for each thing making a remote DSN impossible.

    Two shapes count. Pointing `DATABASE_URL` at sqlite redirects the script
    outright. Refusing any DSN whose host is not loopback is equally final, and
    it is the only shape available to a script that needs a real PostgreSQL to
    exercise a PostgreSQL-only code path - `verify_cart_variant_schema_on_postgres.py`
    drops and recreates a table on a throwaway container and cannot redirect
    itself to sqlite without testing nothing.
    """
    scopes = _enclosing_functions(tree)
    refusals = _loopback_refusal_functions(tree)
    proofs = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value == "DATABASE_URL"
                    and "sqlite" in ast.unparse(node.value).lower()
                ):
                    proofs.append((node.lineno, scopes.get(node)))
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr == "setdefault":
                if (
                    len(node.args) == 2
                    and isinstance(node.args[0], ast.Constant)
                    and node.args[0].value == "DATABASE_URL"
                    and "sqlite" in ast.unparse(node.args[1]).lower()
                ):
                    proofs.append((node.lineno, scopes.get(node)))
            elif isinstance(node.func, ast.Name) and node.func.id in refusals:
                if refusals[node.func.id] or "DATABASE_URL" in ast.unparse(node):
                    proofs.append((node.lineno, scopes.get(node)))
    return proofs


def _binding_precedes_proof(event, proof):
    """True when `event` provably latches the DSN before `proof` takes effect.

    Line order is execution order only within one scope. A module-scope import
    runs before any function body, but an import nested in a *different*
    function than the proof cannot be ordered statically, and assuming it runs
    first would misread `messenger_media_composer_wiring_audit.py`, whose
    seeding helper imports a services module long after `main()` has already
    redirected DATABASE_URL.
    """
    event_line, event_scope, _label = event
    proof_line, proof_scope = proof
    if event_scope is None:
        return proof_scope is not None or event_line < proof_line
    return event_scope is proof_scope and event_line < proof_line


def _isolates_own_database(tree):
    """True when some isolation proof runs before anything has latched the DSN."""
    events = _database_binding_events(tree)
    return any(
        not any(_binding_precedes_proof(event, proof) for event in events)
        for proof in _isolation_proofs(tree)
    )


@functools.lru_cache(maxsize=1)
def _scripts_requiring_the_guard():
    """(relative path, tree, earliest DSN-latching import) for scripts needing a guard."""
    found = []
    for path in sorted(SCRIPTS.rglob("*.py")):
        relative = path.relative_to(ROOT).as_posix()
        if relative in INTENTIONAL_PRODUCTION_TOOLS:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        events = _database_binding_events(tree)
        if not events:
            # Imports neither bot nor any services module that reaches
            # services/db.py, so nothing here can resolve DATABASE_URL.
            continue
        if not _mutates_real_rows(tree):
            continue
        if _isolates_own_database(tree):
            continue
        found.append((relative, tree, min(events, key=lambda event: event[0])))
    return found


def test_the_scan_still_finds_the_scripts_it_is_meant_to_protect():
    """A detector that matches nothing would pass this suite while guarding nothing."""
    relatives = [relative for relative, _, _ in _scripts_requiring_the_guard()]
    assert "scripts/prelaunch_user_restriction_audit.py" in relatives, (
        "the script this suite was written for is no longer detected; the scan "
        "has drifted and every other assertion here is now vacuous"
    )
    assert len(relatives) >= 80, (
        f"only {len(relatives)} scripts detected, which is below the 86 that "
        "reach DATABASE_URL and write identity rows - the detector has broken"
    )


#: name -> (source lines, reaches the database, isolates itself). Synthetic, because
#: the repo's own scripts cannot cover the combinations: nothing in `scripts/`
#: currently pairs a loopback refusal with an identity write, and a detector is only
#: worth having if it separates these five cases from each other.
DETECTOR_CASES = {
    "services.db, nothing else": (
        [
            "from services import db",
            'db.connect().cursor().execute("DELETE FROM users WHERE user_id=20")',
        ],
        True,
        False,
    ),
    "sqlite assigned before the import": (
        [
            "import os",
            'os.environ["DATABASE_URL"] = "sqlite:///throwaway.db"',
            "from services import db",
            'db.connect().cursor().execute("DELETE FROM users WHERE user_id=20")',
        ],
        True,
        True,
    ),
    # The undx_read_qa_run shape. The assignment is real, and useless: on
    # PostgreSQL the engine was built from the old value one line earlier.
    "sqlite assigned after the import": (
        [
            "import os",
            "from services import db",
            'os.environ["DATABASE_URL"] = "sqlite:///throwaway.db"',
            'db.connect().cursor().execute("DELETE FROM users WHERE user_id=20")',
        ],
        True,
        False,
    ),
    # The verify_cart_variant_schema_on_postgres shape.
    "loopback refusal before the import": (
        [
            "import os",
            "import sys",
            "from urllib.parse import urlparse",
            'LOOPBACK = {"127.0.0.1", "localhost", "::1"}',
            "def _guard(url):",
            "    if (urlparse(url).hostname or '') not in LOOPBACK:",
            "        sys.exit('refused')",
            '_guard(os.environ.get("DATABASE_URL", ""))',
            "from services import db",
            'db.connect().cursor().execute("DELETE FROM users WHERE user_id=20")',
        ],
        True,
        True,
    ),
    "its own sqlite file, no app import": (
        [
            "import sqlite3",
            'sqlite3.connect("x.db").execute("DELETE FROM users WHERE user_id=20")',
        ],
        False,
        False,
    ),
}


def test_the_detector_separates_a_real_isolation_proof_from_a_useless_one():
    """A detector that answered the same way to all five would pass every test above."""
    wrong = []
    for name, (lines, reaches, isolated) in DETECTOR_CASES.items():
        tree = ast.parse("\n".join(lines))
        observed = (bool(_database_binding_events(tree)), _isolates_own_database(tree))
        if observed != (reaches, isolated):
            wrong.append(f"{name}: expected {(reaches, isolated)}, read {observed}")
        if not _mutates_real_rows(tree):
            wrong.append(f"{name}: the write itself went unseen, so the case proves nothing")
    assert not wrong, "the detector misreads these:\n  " + "\n  ".join(wrong)


def test_the_graph_resolves_a_services_module_that_never_names_db():
    """The whole point of the graph: `from services import X` where X imports db."""
    indirect = sorted(_services_modules_reaching_the_database() - {"db", ""})
    assert indirect, (
        "the services import graph resolved nothing but services.db itself, so "
        "every script reaching the database through another services module is "
        "invisible to this suite"
    )
    tree = ast.parse(f"from services import {indirect[0]}")
    assert _database_binding_events(tree), (
        f"services.{indirect[0]} reaches services/db.py, so importing it latches "
        "the DSN, but the scan does not count it as a binding import"
    )


def test_the_scan_follows_services_imports_and_not_only_bot():
    """The count above can stay healthy while the services route detects nothing.

    83 of the detected scripts import bot, so `bot`-only detection satisfies
    every other assertion here. This one fails if the services import graph
    stops being followed, which is the half of the rule that `import bot` does
    not already cover.
    """
    reached_without_bot = [
        relative
        for relative, tree, _ in _scripts_requiring_the_guard()
        if not any(label == "bot" for _, _, label in _database_binding_events(tree))
    ]
    assert reached_without_bot, (
        "every detected script imports bot, so the services import graph is "
        "matching nothing. A script reaching DATABASE_URL through "
        "`from services import db` - or any services module that imports it - "
        "must be detected too; that route needs no mention of bot at all"
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


def test_the_guard_runs_before_the_database_is_bound():
    late = []
    for relative, tree, (line, _scope, label) in _scripts_requiring_the_guard():
        guard_line = _guard_call_line(tree)
        if guard_line is not None and guard_line >= line:
            late.append(f"{relative}: guard at line {guard_line}, {label} imported at {line}")
    assert not late, (
        "importing bot runs init_db() against DATABASE_URL, and importing any "
        "services module that reaches services/db.py resolves ENGINE_URL and "
        "builds the engine, so a guard below that import is reading a decision "
        "already taken:\n  " + "\n  ".join(late)
    )


if __name__ == "__main__":
    import pathlib as _pathlib
    import sys as _sys

    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
