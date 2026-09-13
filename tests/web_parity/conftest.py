"""Give the whole directory one database, chosen before any test module imports.

Two modules here — `test_admin_analytics_escaping.py` and
`test_credential_resolution.py` — need `DATABASE_URL` pointing at a scratch SQLite
file *before* `import bot`, because `bot` resolves its connection details at import
and never looks again. Each module did that for itself:

    os.environ["DATABASE_URL"] = "sqlite:///" + tempfile.mkstemp(suffix=".db")[1]
    import bot

Which is correct in isolation and wrong as a pair. pytest imports both modules into
one process, so the second assignment lands *after* `bot` is already imported and has
no effect on anything. That module's fixtures then populate the database named by its
own variable while the code under test reads the first module's database, which nobody
seeded. The failure surfaces as missing rows and `no such table`, a long way from the
line that caused it, and it moves depending on collection order.

The old workaround was a docstring in each module saying "must run in its own pytest
process". That is not a fix — a directory that cannot be run as a directory is a
directory that stops being run, and these are security tests.

So the choice is made once, here, and both modules now use `setdefault` to cooperate
with it. The file is per-process and shared across the directory, matching what
`tests/conftest.py` does for the unconfigured-SQLite fallback: the same database for
the whole run, which is the behaviour each module already had by itself.

**The assignment below overrides the environment, and that is the point.** The obvious
tidy version — `setdefault` here too, so an operator can point the suite somewhere —
was measured and rejected. With it, `DATABASE_URL=sqlite:////tmp/probe.db python3 -m
pytest tests/web_parity/test_admin_analytics_escaping.py` passed 11/11 while creating
589 tables in that database and leaving six stored-XSS payloads in `analytics_events`.
These modules call `bot.init_db()` and then seed attacker-controlled markup and forged
session rows; they are not safe to aim at a database anyone cares about, and
`DATABASE_URL` is exactly the variable someone has already exported to reach one.
The per-module hard assignment had that protection by accident. Keeping it explicit
here is the only reason the modules below are allowed to use `setdefault` at all: the
value is always already set, so their fallback never actually runs in-suite — it exists
so the file still reads correctly on its own.

`COINPILOTX_INIT_DB_ON_IMPORT` is set for the same reason it was set in the modules —
the tables have to exist before the first fixture runs, and there is no migration step
to build them.
"""

import os
import tempfile

#: Deliberately not a `TemporaryDirectory` whose cleanup runs at interpreter exit.
#: `bot` holds connections to this file for the life of the process, and on some
#: platforms removing it underneath them turns an ordinary teardown into an error
#: after the results have already been printed. The OS temp directory is the right
#: owner of a file this size.
_DB_PATH = tempfile.mkstemp(prefix="pulsesoc-web-parity-", suffix=".db")[1]

os.environ["DATABASE_URL"] = "sqlite:///" + _DB_PATH
os.environ["COINPILOTX_INIT_DB_ON_IMPORT"] = "1"
os.environ.setdefault("FLASK_SECRET_KEY", "web-parity-tests")
