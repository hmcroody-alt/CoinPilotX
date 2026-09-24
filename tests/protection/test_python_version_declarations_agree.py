"""The repo must declare ONE Python version, and it must be the one production runs.

Three places name a Python version, and they are read by three different things:

* ``nixpacks.toml``            -- Railway builds the production image from this
* ``.github/workflows/*.yml``  -- every CI job's ``actions/setup-python``
* ``.python-version``          -- what a developer or agent gets locally

Nothing in the repo *reads* ``.python-version``; pyenv and uv do, on a human's
machine. That is exactly why it drifted to 3.13.13 while production ran 3.11 and
nobody noticed for as long as it took someone to hit a semantic difference.

Someone did. The three straddled **3.12**, where slice objects became hashable:

    CompatRow(cols, vals)[:2]
    # 3.11  (CI, prod):  TypeError: unhashable type: 'slice'
    # 3.12+ (local):     KeyError: slice(None, 2, None)

A protection test pinned ``KeyError`` because that is what the local interpreter
raised, passed every local run, and turned the Backend protection suite red.
See issue #29 and ``tests/protection/test_row_shape_engine_parity.py``.

The point of this file is not the version number. It is that a mismatch should
cost one red check at the moment it is introduced, rather than a debugging
session later, on a machine whose interpreter nobody thought to question.

This is a *consistency* gate, deliberately not a *currency* gate: it asserts the
three agree, and takes ``nixpacks.toml`` as the authority because that is the
one that actually ships. Upgrading is a real decision with real testing behind
it, and this file must not be what forces it -- it should pass on the day
someone moves all three forward together, and fail on the day they move one.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# python311, python312, python3, python3Full, ...
_NIX_PYTHON = re.compile(r"\bpython(\d)(\d+)\b")
_SETUP_PY = re.compile(r"""python-version:\s*["']?([0-9]+(?:\.[0-9]+)*)["']?""")


def _production_version() -> str:
    """The version Railway actually builds, as ``major.minor``."""
    text = (ROOT / "nixpacks.toml").read_text(encoding="utf-8")
    matches = _NIX_PYTHON.findall(text)
    assert matches, (
        "nixpacks.toml no longer names a pythonNN package. This gate takes it "
        "as the authority on what production runs; if the build moved to a "
        "different mechanism, point this function at the new one rather than "
        "deleting the check."
    )
    assert len(set(matches)) == 1, (
        f"nixpacks.toml names more than one Python: {sorted(set(matches))}"
    )
    major, minor = matches[0]
    return f"{major}.{minor}"


def _workflow_versions():
    """Every ``python-version:`` pin in CI, as (path, value) pairs."""
    found = []
    for path in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        for value in _SETUP_PY.findall(path.read_text(encoding="utf-8")):
            found.append((path.relative_to(ROOT).as_posix(), value))
    return found


def test_the_declarations_are_all_present():
    """A missing file must fail loudly, not vacuously pass.

    Every assertion below is of the form "these agree". Delete one of the
    inputs and they all agree trivially, which is the failure mode this whole
    directory exists to prevent.
    """
    assert (ROOT / "nixpacks.toml").is_file()
    assert (ROOT / ".python-version").is_file()
    workflows = _workflow_versions()
    assert len(workflows) >= 5, (
        f"only {len(workflows)} python-version pins found across CI; this gate "
        "was written when there were 7. If jobs legitimately stopped pinning "
        "Python, lower this floor deliberately -- do not let it decay to 0."
    )


def test_ci_pins_the_version_production_runs():
    production = _production_version()
    mismatched = [
        (path, value)
        for path, value in _workflow_versions()
        if not (value == production or value.startswith(production + "."))
    ]
    assert not mismatched, (
        f"production (nixpacks.toml) runs Python {production}, but CI pins "
        f"something else: {mismatched}. CI is the only place a version "
        "difference can be caught before a deploy, so it must match the "
        "deploy."
    )


def test_the_local_declaration_matches_production():
    """``.python-version`` is what a fresh local setup resolves to.

    It is the declaration nothing in this repo reads, which is precisely why it
    is the one that drifts. It had said 3.13.13 while production ran 3.11.
    """
    production = _production_version()
    declared = (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    assert declared, ".python-version is empty"
    assert declared == production or declared.startswith(production + "."), (
        f".python-version says {declared!r} but production runs Python "
        f"{production}. A local interpreter on the other side of a language "
        "change makes local test runs unreliable in a way that looks like "
        "flakiness -- see issue #29, where a 3.14 venv against a 3.11 "
        "production hid a real defect behind a green local suite. Either "
        "align this file, or move nixpacks.toml and CI forward with it."
    )


def test_the_gate_can_actually_fail():
    """Pin the comparison itself, so a refactor cannot make it vacuous."""
    production = "3.11"

    def agrees(value):
        return value == production or value.startswith(production + ".")

    # Exact, and more-specific-but-compatible, both pass.
    assert agrees("3.11")
    assert agrees("3.11.9")
    # The version this repo actually had, and its neighbours, must not.
    assert not agrees("3.13.13")
    assert not agrees("3.12")
    assert not agrees("3.14.0")
    # A prefix that is not a version boundary must not sneak through:
    # "3.1" is not "3.11", and str.startswith would say otherwise without
    # the "." this comparison appends.
    assert not agrees("3.1")


if __name__ == "__main__":
    import pathlib as _pathlib
    import sys as _sys

    _sys.path.insert(0, str(_pathlib.Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
