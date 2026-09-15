"""A withdrawn feature must not be one import away from working again.

The Private Office contains three things — Relationship Intelligence, Private
Meetings and Office Security. Eight features were withdrawn, and their routes,
screens and clients are gone. Their *engines* are still in
``services/private_office``, classified in that package's
``RETIRED_ENGINE_MODULES`` as either the only description of tables that still
hold member rows, or the arithmetic that read them.

Keeping them is safe exactly as long as nothing can reach them. That is a
property of the import graph, and an import graph changes by accident: someone
needs a helper, finds ``documents.extract_claims`` already written and typed,
imports it, and a withdrawn feature is back in a live path — not as a screen
anyone asked for, but as a route handler quietly reading a table the product no
longer maintains.

So the property is computed rather than asserted from memory. This walks every
``.py`` file outside the package, collects the submodules they import, and
follows the package's own internal imports transitively. A retired engine
appearing in that closure fails here, naming the module that pulled it in.

The check is deliberately structural, not behavioural. There is no fixture, no
database and no route: the claim is about what the source says, and the source
is the thing that would change.
"""

import ast
import os

import pytest

from services import private_office

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
PACKAGE_DIR = os.path.join(REPO, "services", "private_office")
PACKAGE = "services.private_office"

#: Directories that are not this repository's shipped source. `.claude` holds
#: sibling worktrees — whole second checkouts of this same tree — and walking
#: into one would read another branch's imports as if they were ours.
SKIP_DIRS = {".git", ".claude", "node_modules", "__pycache__", ".venv",
             "ios", "android", "Pods", "build", "dist", ".mypy_cache"}


def submodules() -> set[str]:
    return {name[:-3] for name in os.listdir(PACKAGE_DIR)
            if name.endswith(".py") and name != "__init__.py"}


def _imported_submodules(source: str, path: str, known: set[str]) -> set[str]:
    """Which ``services.private_office`` submodules this file imports.

    Parsed with ``ast`` rather than matched with a regular expression. A string
    or a comment mentioning ``private_office.shield`` is not an import, and this
    test's whole value is that it fails only on the real thing — a guard that
    cries wolf on a docstring gets weakened by the first person it blocks.
    """
    found: set[str] = set()
    tree = ast.parse(source, filename=path)
    inside_package = os.path.dirname(os.path.abspath(path)) == PACKAGE_DIR
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                # `import services.private_office.shield [as x]`
                if alias.name.startswith(PACKAGE + "."):
                    head = alias.name[len(PACKAGE) + 1:].split(".")[0]
                    if head in known:
                        found.add(head)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level and inside_package:
                # `from . import shield` / `from .shield import x`, which only
                # resolve to this package when the file is in it.
                module = module or ""
                if module:
                    head = module.split(".")[0]
                    if head in known:
                        found.add(head)
                else:
                    for alias in node.names:
                        if alias.name in known:
                            found.add(alias.name)
            elif module == PACKAGE:
                # `from services.private_office import shield`
                for alias in node.names:
                    if alias.name in known:
                        found.add(alias.name)
            elif module.startswith(PACKAGE + "."):
                # `from services.private_office.shield import x`
                head = module[len(PACKAGE) + 1:].split(".")[0]
                if head in known:
                    found.add(head)
    return found


def _python_files() -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(REPO):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            if name.endswith(".py"):
                out.append(os.path.join(dirpath, name))
    return out


def _is_product(path: str) -> bool:
    """Product code, as opposed to a test or a one-off script.

    Tests and scripts are excluded on purpose: a test for a retired engine is
    the thing that documents how its surviving rows are shaped, and a script
    that exports them is exactly what a data request would need. Neither puts
    the engine on a path a member's request can travel. A *route pack* does,
    and route packs live under ``services/`` or in ``bot.py``.
    """
    rel = os.path.relpath(path, REPO)
    return not (rel.startswith("tests" + os.sep) or rel.startswith("scripts" + os.sep))


@pytest.fixture(scope="module")
def graph():
    known = submodules()
    internal: dict[str, set[str]] = {}
    roots: set[str] = set()
    pulled_in_by: dict[str, set[str]] = {name: set() for name in known}

    for path in _python_files():
        with open(path, "r", encoding="utf-8", errors="ignore") as handle:
            source = handle.read()
        try:
            imports = _imported_submodules(source, path, known)
        except SyntaxError:
            # A file this interpreter cannot parse is not a file it can import
            # either, so it cannot be a live path to anything.
            continue
        if os.path.dirname(os.path.abspath(path)) == PACKAGE_DIR:
            name = os.path.basename(path)[:-3]
            if name != "__init__":
                internal[name] = imports - {name}
        elif _is_product(path):
            roots |= imports
            for target in imports:
                pulled_in_by[target].add(os.path.relpath(path, REPO))

    for name, deps in internal.items():
        for target in deps:
            pulled_in_by[target].add(PACKAGE + "." + name)

    reachable: set[str] = set()
    stack = sorted(roots)
    while stack:
        name = stack.pop()
        if name in reachable:
            continue
        reachable.add(name)
        stack.extend(sorted(internal.get(name, ())))

    return {"known": known, "reachable": reachable, "why": pulled_in_by,
            "internal": internal}


def test_the_manifest_names_modules_that_exist(graph):
    """Otherwise the guard below is asserting something about nothing.

    A renamed or deleted engine that stayed in the manifest would be
    unreachable for the trivial reason that it is not there, and the real check
    would pass while covering one module fewer than it claims.
    """
    missing = sorted(set(private_office.RETIRED_ENGINE_MODULES) - graph["known"])
    assert not missing, (
        "RETIRED_ENGINE_MODULES names modules that are not in "
        "services/private_office: %s. If they were deleted, drop them from the "
        "manifest in the same commit." % missing)


def test_no_retired_engine_is_reachable_from_product_code(graph):
    """The claim. Reported per module so a failure names the importer."""
    for name in private_office.RETIRED_ENGINE_MODULES:
        if name not in graph["reachable"]:
            continue
        pytest.fail(
            "services/private_office/%s.py is a retired engine and is reachable "
            "from product code again, imported by: %s.\n\n"
            "Its feature was withdrawn from the Private Office, which contains "
            "only Relationship Intelligence, Private Meetings and Office "
            "Security. If the import is deliberate the feature is being brought "
            "back and belongs in the feature matrix, OFFICE_CHILD_IDS and a "
            "screen — not reached through a helper. If it is not deliberate, "
            "the helper you want should move to a module that is not retired."
            % (name, ", ".join(sorted(graph["why"][name])) or "(unknown)"))


def test_the_live_engines_really_are_reachable(graph):
    """Anti-vacuity, and the only reason to trust the test above.

    If the walker stopped finding imports — a moved package, a changed import
    style, a bug in the visitor — every module would come back unreachable and
    the guard would pass for the worst possible reason. These five are reached
    through different routes on purpose: `office` and `status` through the
    Private Office route pack, `meetings` through the meetings pack, `security`
    through `bot.py` itself, and `field_crypto` only transitively, through a
    module outside this package. A break in any one of those paths shows up
    here rather than as silence.
    """
    for name in ("office", "status", "meetings", "security", "field_crypto"):
        assert name in graph["reachable"], (
            "%s is a live Private Office engine and this test cannot see any "
            "product code importing it. Before treating that as a real finding, "
            "check the import walker: if it has stopped resolving imports, "
            "test_no_retired_engine_is_reachable_from_product_code is passing "
            "vacuously." % name)


def test_a_retired_engine_is_caught_when_something_does_import_it(graph):
    """The mutation, run for real rather than asserted about.

    Everything above is a property of the live tree, which is currently clean —
    so every one of those assertions would also pass if the reachability walk
    returned the empty set for reasons that had nothing to do with retirement.
    This re-runs the same closure with one fabricated edge, a product module
    importing `shield`, and requires that it comes back reachable.
    """
    internal = dict(graph["internal"])
    # `security` is live and reached from bot.py; pretend it grew one import.
    internal["security"] = set(internal.get("security", set())) | {"shield"}

    reachable: set[str] = set()
    stack = ["security"]
    while stack:
        name = stack.pop()
        if name in reachable:
            continue
        reachable.add(name)
        stack.extend(sorted(internal.get(name, ())))

    assert "shield" in reachable, (
        "a product module importing a retired engine did not make it reachable, "
        "so the guard cannot detect the thing it exists to detect")
    assert "shield" not in graph["reachable"], (
        "shield is reachable in the real graph, which makes the mutation above "
        "prove nothing — fix the real finding first")
