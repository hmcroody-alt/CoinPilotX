#!/usr/bin/env python3
"""Run the SHIPPING Reels preload functions and report what they actually did.

This exists because the Reels preload window used to be verified by grepping bot.py
for an identifier. A grep over minified production text cannot tell a working preload
window from a broken one: it passes as long as the string is present, and fails as soon
as the string is renamed. Both outcomes are independent of whether the feature works.

So instead of matching text, this module lifts the real functions out of ``bot.py`` by
brace matching and executes them under Node against ``reels_preload_harness.js`` -- a
minimal DOM that supplies only browser primitives (elements, ``getBoundingClientRect``,
``video.load()``, ``new Image()``) and counts what the production code chooses to do
with them. Nothing about the preload POLICY lives in the harness, so every number the
scenarios report is a decision made by shipping code.

Two callers share this: the protection contract
(``tests/protection/test_media_playback_contract.py``) and the mobile playback audit
(``scripts/pulse_reels_mobile_playback_audit.py``). They share it so that the behavioral
guarantee is stated once and cannot drift between the two.

Load it from outside the package with :func:`load` (below) or, from a sibling module,
``import reels_preload_runner``.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
HARNESS = HERE / "reels_preload_harness.js"

# The production functions that together implement the Reels preload window. They are
# executed, not grepped, so this list is a requirement that they EXIST under these
# names -- a rename is a real interface change and should be seen by the callers.
REEL_FUNCTIONS = (
    "reelCards",
    "warmReelPoster",
    "primaryReelVideo",
    "logReelAudioState",
    "releaseFarReelMedia",
    "preloadNextReel",
)


def bot_source() -> str:
    """Read bot.py fresh, so a caller never verifies a stale copy of production."""
    return (ROOT / "bot.py").read_text(encoding="utf-8")


def extract_js_function(name: str, source: str | None = None) -> str:
    """Return the verbatim source of ``function name(...){...}`` from bot.py.

    Walks the parameter list to its matching ``)`` before looking for the body's
    opening brace, because a default parameter value may itself contain braces
    (``logReelAudioState(card,video,reason,extra={})``), then brace-matches the body.
    Raises rather than returning a partial function: silently extracting half a
    function would make the behavioral checks meaningless -- it would still run, and
    would still produce numbers, but they would not be production's numbers.
    """
    src = bot_source() if source is None else source
    m = re.search(r"function\s+" + re.escape(name) + r"\s*\(", src)
    if not m:
        raise AssertionError(f"missing production function: {name}()")
    i, depth = m.end() - 1, 0
    while i < len(src):
        if src[i] == "(":
            depth += 1
        elif src[i] == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    else:
        raise AssertionError(f"unbalanced parameter list extracting {name}()")
    j = src.index("{", i)
    depth = 0
    while j < len(src):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():j + 1]
        j += 1
    raise AssertionError(f"unbalanced braces extracting {name}()")


def run_reel_scenarios(names: list[str], source: str | None = None) -> dict:
    """Run the real preload functions against the harness and return its verdicts.

    Raises on a missing Node, a nonzero harness exit, or any scenario that threw. A
    verifier that cannot run is a failure to verify, not a pass and not a skip: the
    Reels preload window is release-critical, so an unrunnable check must be loud.
    """
    node = shutil.which("node") or shutil.which("nodejs")
    if not node:
        raise AssertionError(
            "node is required to verify the Reels preload window behaviorally")
    src = bot_source() if source is None else source
    production = "\n".join(extract_js_function(n, src) for n in REEL_FUNCTIONS)
    harness = HARNESS.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="reelwin_") as tmp:
        bundle = Path(tmp) / "bundle.js"
        bundle.write_text(production + "\n" + harness, encoding="utf-8")
        proc = subprocess.run([node, str(bundle), json.dumps(names)],
                              capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise AssertionError(
            f"Reels harness failed (rc={proc.returncode}):\n{proc.stderr[-2000:]}")
    out = json.loads(proc.stdout)
    for name, verdict in out.items():
        if "harness_error" in verdict:
            raise AssertionError(f"scenario {name} raised: {verdict['harness_error']}")
    return out


def load():
    """Import this module by path, for callers that are not siblings of it.

    ``scripts/`` is not a package and ``tests/`` is not importable as one, so an audit
    living outside this directory cannot ``import`` its way here. Loading by path keeps
    the behavioral guarantee in exactly one file instead of copying it into the audit.
    """
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "reels_preload_runner", Path(__file__).resolve())
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module
