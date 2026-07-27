"""Import-time shims so the agent suites can run without the web stack installed.

``services.pulse_ai_service`` reaches ``werkzeug.utils.secure_filename`` through an
unrelated import chain (embed_service -> media_service). Nothing on the agent path
uses it. Rather than skip the transport test — which is precisely the layer the
mission requires evidence for — the one symbol is stubbed here, before any service
import, and only if the real package is genuinely absent.

Importing this module is a no-op on a machine with werkzeug installed.
"""

from __future__ import annotations

import re
import sys
import types


def install() -> None:
    try:  # pragma: no cover - exercised on developer machines, not in CI sandbox
        import werkzeug.utils  # noqa: F401

        return
    except Exception:
        pass
    werkzeug = types.ModuleType("werkzeug")
    utils = types.ModuleType("werkzeug.utils")
    utils.secure_filename = lambda name: re.sub(r"[^A-Za-z0-9_.-]", "_", str(name or ""))
    werkzeug.utils = utils
    sys.modules.setdefault("werkzeug", werkzeug)
    sys.modules.setdefault("werkzeug.utils", utils)


def stub_bot(service) -> None:
    """Point ``pulse_ai_service._open_db`` at the same connector production uses.

    ``_open_db`` acquires its connection through ``bot.db()``, and importing ``bot``
    pulls in the whole Flask application — payment SDKs included. Outside that import
    chain ``bot.db()`` is two lines: ``db_service.connect()`` plus request-context
    instrumentation that does nothing without a request. This shim reproduces it
    exactly, so ``_open_db`` — including its ``ensure_schema`` call — is the real
    function under test rather than a replacement for it.

    Deliberately not replacing ``_open_db`` itself: the connection it hands out, and
    the row factory on it, are part of what the endpoint depends on.
    """
    import sqlite3
    import types

    from services import db as db_service

    shim = types.SimpleNamespace(db=db_service.connect, sqlite3=sqlite3)
    service._bot = lambda: shim


__all__ = ["install", "stub_bot"]
