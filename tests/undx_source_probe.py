"""Ask a source file what it *does*, not what words it contains.

Every migration in `UNDX_PROVIDER_CALLSITE_CENSUS.md` needs the same four questions
answered about the file it touched: does it still name a vendor endpoint, still read a
provider credential, still hardcode a model, still perform its own HTTP. The obvious
way to answer them is `assertNotIn("api.openai.com", source)`, and that way is wrong
in a specific and self-inflicted way.

A migration worth doing is worth explaining, and the explanation names the thing that
was removed. `services/intelligence.py`'s module docstring says the `OPENAI_API_KEY`
gate is gone and says why; `bot.sports_edge_ai_analysis`'s docstring says `OPENAI_MODEL`
was one of four competing defaults. A substring check fires on both. The first draft of
`tests/test_assistant_response_routing.py` failed five times for exactly that reason,
and `tests/test_sports_edge_routing.py` passed only because `bot.py`'s new docstring
happened not to repeat one particular string.

The failure mode is not "a noisy test". It is that the cheapest way to get green is to
delete the paragraph explaining why the rule exists — so a prose-sensitive protection
test actively destroys the documentation it sits next to. `test_undx_canary.py` reached
the same conclusion first, and says so: discussing it in a docstring is fine and
expected.

So: comments are invisible to `ast.parse` already, and docstrings are skipped here
explicitly. What remains is the set of string literals the module actually evaluates —
a URL it would request, a variable name it would look up, a message it would show a
user — plus the calls it actually makes. Those are mechanisms, and a mechanism is what
a protection test should be about.

The one place this is deliberately *not* used is a privacy class name, where
`services/undx_call_domain.py` is held to the stricter substring standard. That module
can explain its rule without ever writing a class name down, so the stronger check
costs it nothing. The asymmetry is the point: use the strictest check the file can
actually satisfy, and no stricter.
"""

from __future__ import annotations

import ast
import pathlib


def _docstring_ids(tree: ast.AST) -> set[int]:
    """Object ids of every docstring constant, at module, class and function level."""
    ids = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(body, list) and body and isinstance(body[0], ast.Expr) \
                and isinstance(body[0].value, ast.Constant) \
                and isinstance(body[0].value.value, str):
            ids.add(id(body[0].value))
    return ids


def parse(path) -> ast.AST:
    return ast.parse(pathlib.Path(path).read_text(encoding="utf-8"))


def function(tree: ast.AST, name: str) -> ast.FunctionDef:
    """The one function by that name, for checks that must not go file-wide.

    `bot.py` legitimately posts to Stripe, Telegram, Brevo and Mux, so a transport check
    there has to be scoped to the migrated function or it would either pass vacuously or
    forbid four working integrations.
    """
    return next(node for node in ast.walk(tree)
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == name)


def string_literals(tree: ast.AST) -> list[str]:
    """Every string the module evaluates, with docstrings excluded.

    Callers should assert this list is non-empty before asserting anything is absent
    from it: "no literals found" and "no forbidden literals found" are the same result
    from a broken walk, and only one of them is good news.
    """
    skip = _docstring_ids(tree)
    return [node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and id(node) not in skip]


def attribute_calls(tree: ast.AST) -> list[str]:
    """Names of every `something.method()` call, as the bare method name."""
    return [node.func.attr for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)]


def env_wrappers(tree: ast.AST) -> set[str]:
    """Functions in this tree that are themselves an environment read.

    A function qualifies when it forwards its own first parameter to `os.getenv` or
    `os.environ.get` — `def _env_text(key, default=""): return os.getenv(key, default)`.
    Calling one *is* reading an environment variable, and the name read is the literal
    the caller passed in.

    This exists because :func:`environment_reads` was silently blind to exactly that
    shape. `services/pulse_ai_provider_router.py` reads three variables and every one of
    them goes through a local `_env_text`, so the probe reported zero — which made three
    absence assertions in `tests/test_pulse_ai_provider_reconciliation.py` pass while
    measuring nothing at all. Restoring an `OPENAI_API_KEY` read through the wrapper
    would have been invisible to the test written to forbid it.

    Detected rather than declared, so a second wrapper added later is covered without
    anyone having to remember that a test depends on it.
    """
    wrappers: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        positional = [arg.arg for arg in node.args.posonlyargs + node.args.args]
        if not positional:
            continue
        first = positional[0]
        for call in (n for n in ast.walk(node) if isinstance(n, ast.Call)):
            target = call.func
            if not isinstance(target, ast.Attribute):
                continue
            reads_env = target.attr == "getenv" or (
                target.attr == "get" and isinstance(target.value, ast.Attribute)
                and target.value.attr == "environ")
            if reads_env and call.args and isinstance(call.args[0], ast.Name) \
                    and call.args[0].id == first:
                wrappers.add(node.name)
                break
    return wrappers


def environment_reads(tree: ast.AST) -> list[str]:
    """Names passed to `os.getenv` / `os.environ.get` / `os.environ[...]`.

    Also follows the tree's own environment wrappers — see :func:`env_wrappers` — so a
    module that reads everything through a local helper is not reported as reading
    nothing.

    Returns the names rather than a boolean so a failure message can say *which*
    variable came back, which is the difference between a one-line fix and a hunt.
    """
    names: list[str] = []
    wrappers = env_wrappers(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                and node.func.id in wrappers and node.args \
                and isinstance(node.args[0], ast.Constant):
            names.append(str(node.args[0].value))
            continue
        if isinstance(node, ast.Subscript):
            value = node.value
            if isinstance(value, ast.Attribute) and value.attr == "environ" \
                    and isinstance(node.slice, ast.Constant):
                names.append(str(node.slice.value))
            continue
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if not isinstance(target, ast.Attribute):
            continue
        reads_env = target.attr == "getenv" or (
            target.attr == "get" and isinstance(target.value, ast.Attribute)
            and target.value.attr == "environ")
        if reads_env and node.args and isinstance(node.args[0], ast.Constant):
            names.append(str(node.args[0].value))
    return names


def transport_calls(tree_or_node: ast.AST) -> list[str]:
    """Calls that could reach a vendor over the network.

    Checked by *receiver*, not by verb. The first draft of this check banned the bare
    attribute name `get` and caught four `envelope.get(...)` dict reads — `get` is not a
    transport, it is a word transports happen to use. Naming the receiver is both
    narrower and stricter: `requests.post` is caught whichever verb it uses, and a dict
    is never caught.
    """
    transports = {"requests", "httpx", "urllib", "http", "aiohttp", "session",
                  "openai", "anthropic", "urlopen"}
    found: list[str] = []
    for node in ast.walk(tree_or_node):
        if not isinstance(node, ast.Call):
            continue
        target = node.func
        if isinstance(target, ast.Name) and target.id in {"urlopen", "Request"}:
            found.append(target.id)
            continue
        if not isinstance(target, ast.Attribute):
            continue
        if target.attr in {"urlopen", "Session"}:
            found.append(target.attr)
            continue
        root = target
        while isinstance(root, ast.Attribute):
            root = root.value
        if isinstance(root, ast.Name) and root.id.lower() in transports:
            found.append(f"{root.id}.{target.attr}")
    return found


def imported_names(tree: ast.AST) -> list[str]:
    """Every name an import introduces: module, alias target, and alias name.

    All three, because checking only one of them is how the `undx_call_domain` privacy
    ban survived a mutation for a while: it read `node.module` for `ImportFrom`, which
    is `"services"` for the idiom every module in this repo actually uses, and `None`
    for a relative import.
    """
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names.append(getattr(node, "module", None) or "")
            names.extend(alias.name for alias in node.names)
            names.extend(alias.asname or "" for alias in node.names)
    return names
