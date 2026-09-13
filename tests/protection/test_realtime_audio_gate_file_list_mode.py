"""``--changed-files-from`` must not report an all-clear it never verified.

Why this file exists
--------------------
``bot.py`` is protected by diff CONTENT, not by path — a bot.py change counts as
protected only when the changed lines mention an audio symbol
(``backend_diff_patterns``). That check lived behind::

    if BACKEND_FILE in files and base and head:

In ``--changed-files-from`` mode ``base`` and ``head`` are both ``None``, so the
condition was never true. The gate then printed "No protected real-time audio
path changed ... Audio validation is not required for this change" and exited 0,
having never looked at bot.py's diff at all.

CI passes ``--base/--head`` and was unaffected. The exposure was local, and it
landed on the worst possible file: ``--changed-files-from`` is the mode a
developer reaches for to scope the gate to their own files in a shared or dirty
checkout, and ``bot.py`` is by far the most-edited protected surface.

A test that only checks path-based hits would have passed against the broken
gate, because path hits never depended on the range. So these tests drive the
one thing that did: content matching with no git range available.

How
---
Each test builds a disposable git repository in a temp directory containing the
REAL gate script and the REAL manifest, plus a synthetic ``bot.py``. The gate
resolves ``ROOT`` from its own location and runs git with ``cwd=ROOT``, so a copy
placed in the sandbox reads the sandbox's manifest and diffs the sandbox's
working tree. Nothing is written inside the repository under test — a previous
incident in this repo involved a sandbox helper writing the real checkout, so
``test_the_real_bot_py_was_never_touched`` pins that directly.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GATE = ROOT / "scripts" / "realtime_audio_change_gate.py"
MANIFEST = ROOT / "config" / "realtime-audio-protected-paths.json"
REAL_BOT = ROOT / "bot.py"

# Recorded at import, asserted at the end. Cheap insurance against a sandbox
# helper ever escaping the temp directory again.
_REAL_BOT_FINGERPRINT = (REAL_BOT.stat().st_size, REAL_BOT.read_bytes()[:4096])

BACKEND_PATTERNS = json.loads(MANIFEST.read_text(encoding="utf-8"))["backend_diff_patterns"]

ALL_CLEAR = "Audio validation is not required for this change"

BOT_BEFORE = """\
import os


def unrelated_helper(value):
    return value.strip()
"""

# Touches `AGORA_` and `can_publish`, two of the manifest's backend patterns.
BOT_AFTER_AUDIO = """\
import os

AGORA_APP_CERTIFICATE = os.environ.get("AGORA_APP_CERTIFICATE")


def build_live_token(user_id, role):
    can_publish = role == "host"
    return AGORA_APP_CERTIFICATE, can_publish


def unrelated_helper(value):
    return value.strip()
"""

# A real bot.py edit with no audio symbol anywhere. Over-triggering is its own
# failure mode: a gate that fires on every backend change is one people route
# around, and bot.py receives most of this repo's backend edits.
BOT_AFTER_NEUTRAL = """\
import os


def unrelated_helper(value):
    return value.strip().lower()


def another_unrelated_helper(rows):
    return [row for row in rows if row]
"""


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-c", "user.email=protection@example.invalid",
            "-c", "user.name=Protection Suite",
            # The fixture repo must behave identically for a developer who has
            # global commit signing on; this configures the sandbox only.
            "-c", "commit.gpgsign=false",
            *args,
        ],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )


def _make_sandbox(tmp: str, *, after: str | None, stage: bool = False, commit: bool = False) -> Path:
    """A throwaway git repo holding the real gate, the real manifest, a fake bot.py.

    ``after`` None leaves bot.py identical to its committed state, which is the
    case where the gate has a bot.py in its file list and no diff to inspect.
    """
    repo = Path(tmp) / "sandbox"
    (repo / "scripts").mkdir(parents=True)
    (repo / "config").mkdir(parents=True)
    shutil.copy2(GATE, repo / "scripts" / GATE.name)
    shutil.copy2(MANIFEST, repo / "config" / MANIFEST.name)
    (repo / "bot.py").write_text(BOT_BEFORE, encoding="utf-8")

    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "baseline")

    if after is not None:
        (repo / "bot.py").write_text(after, encoding="utf-8")
        if stage:
            _git(repo, "add", "bot.py")
        if commit:
            _git(repo, "commit", "-q", "-am", "backend change")
    return repo


def _run_gate(repo: Path, changed: list[str], *extra: str):
    listing = repo / "changed.txt"
    listing.write_text("\n".join(changed), encoding="utf-8")
    proc = subprocess.run(
        [
            sys.executable,
            str(repo / "scripts" / GATE.name),
            "--changed-files-from", str(listing),
            "--skip-declaration",
            *extra,
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    return proc


def _run_gate_range(repo: Path, base: str, head: str):
    return subprocess.run(
        [
            sys.executable,
            str(repo / "scripts" / GATE.name),
            "--base", base, "--head", head,
            "--json", "--skip-declaration",
        ],
        cwd=repo,
        capture_output=True,
        text=True,
    )


class FileListModeInspectsBotPyContent(unittest.TestCase):
    """The regression itself: content matching with no git range."""

    def test_audio_content_in_bot_py_is_detected_without_a_git_range(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_sandbox(tmp, after=BOT_AFTER_AUDIO)
            proc = _run_gate(repo, ["bot.py"], "--json")

        self.assertEqual(
            proc.returncode, 0,
            f"gate failed unexpectedly: {proc.stderr}",
        )
        out = json.loads(proc.stdout)
        self.assertTrue(
            out["protected"],
            "bot.py changed with AGORA_/can_publish in the diff and the gate "
            "reported no protected audio change. This is the exact all-clear "
            "the file-list path used to give for every bot.py change.",
        )
        hits = {h["path"]: h["category"] for h in out["hits"]}
        self.assertIn("bot.py", hits, f"bot.py missing from hits: {hits}")
        self.assertIn("backend_token_and_room_policy", hits["bot.py"])
        self.assertIn("AGORA_", hits["bot.py"])
        self.assertIn("can_publish", hits["bot.py"])

    def test_the_all_clear_sentence_is_not_printed_for_an_audio_bot_py_change(self):
        """Pinned on the literal text because that sentence is the harm.

        The failure was not merely a wrong exit code — it was a confident
        English claim that no validation was owed, printed to a developer who
        had just edited live-audio token policy.
        """
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_sandbox(tmp, after=BOT_AFTER_AUDIO)
            proc = _run_gate(repo, ["bot.py"])
        self.assertNotIn(ALL_CLEAR, proc.stdout)
        self.assertIn("PROTECTED REAL-TIME AUDIO PATHS CHANGED", proc.stdout)

    def test_every_backend_pattern_is_matched_in_file_list_mode(self):
        """Each pattern on its own, so one working pattern cannot cover the rest."""
        for pattern in BACKEND_PATTERNS:
            with self.subTest(pattern=pattern):
                after = BOT_BEFORE + f'\n\nPROBE = "{pattern}_probe"\n'
                with tempfile.TemporaryDirectory() as tmp:
                    repo = _make_sandbox(tmp, after=after)
                    proc = _run_gate(repo, ["bot.py"], "--json")
                out = json.loads(proc.stdout)
                self.assertTrue(
                    out["protected"],
                    f"backend pattern {pattern!r} appeared in a bot.py diff and "
                    "the gate did not see it",
                )

    def test_a_staged_bot_py_change_is_inspected_too(self):
        """`git add` must not hide the diff.

        The fallback reads the unstaged diff first; a developer who has staged
        their bot.py edit would otherwise get the old silent all-clear back.
        """
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_sandbox(tmp, after=BOT_AFTER_AUDIO, stage=True)
            proc = _run_gate(repo, ["bot.py"], "--json")
        out = json.loads(proc.stdout)
        self.assertTrue(
            out["protected"],
            "a staged bot.py audio change was invisible to the gate",
        )

    def test_bot_py_is_inspected_even_when_listed_beside_unprotected_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_sandbox(tmp, after=BOT_AFTER_AUDIO)
            proc = _run_gate(
                repo,
                ["services/db.py", "bot.py", "templates/index.html"],
                "--json",
            )
        out = json.loads(proc.stdout)
        self.assertEqual({h["path"] for h in out["hits"]}, {"bot.py"})


class FileListModeRefusesToGuess(unittest.TestCase):
    """No inspectable diff must mean an error, never an all-clear."""

    def test_bot_py_with_no_inspectable_diff_exits_two(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_sandbox(tmp, after=None)
            proc = _run_gate(repo, ["bot.py"])

        self.assertEqual(
            proc.returncode, 2,
            "bot.py was declared changed but had no diff the gate could read; "
            "that is an un-runnable gate (exit 2), not a pass. "
            f"stdout={proc.stdout!r}",
        )
        self.assertNotIn(ALL_CLEAR, proc.stdout)
        self.assertIn("bot.py", proc.stderr)

    def test_the_error_tells_the_developer_how_to_get_an_answer(self):
        """An exit 2 a developer cannot act on becomes an exit 2 they suppress."""
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_sandbox(tmp, after=None)
            proc = _run_gate(repo, ["bot.py"])
        self.assertIn("--base", proc.stderr)
        self.assertIn("--head", proc.stderr)


class FileListModeStillDoesNotOverTrigger(unittest.TestCase):
    """The fix must not turn every backend edit into an audio declaration."""

    def test_a_bot_py_change_with_no_audio_symbol_stays_clear(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_sandbox(tmp, after=BOT_AFTER_NEUTRAL)
            proc = _run_gate(repo, ["bot.py"], "--json")

        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertFalse(
            out["protected"],
            "a bot.py edit with no audio symbol now demands an audio "
            f"declaration; bot.py takes most backend edits: {out['hits']}",
        )

    def test_a_file_list_without_bot_py_needs_no_git_at_all(self):
        """The common case must stay fast and must not start failing.

        Most file-list invocations never mention bot.py. Those must not acquire
        a new git dependency or a new failure mode from this fix.
        """
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_sandbox(tmp, after=None)
            proc = _run_gate(repo, ["services/db.py", "templates/index.html"], "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(json.loads(proc.stdout)["protected"])


class RangeModeIsUnchanged(unittest.TestCase):
    """The ``--base/--head`` path CI depends on must keep its old behaviour."""

    def test_range_mode_still_detects_audio_content_in_bot_py(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_sandbox(tmp, after=BOT_AFTER_AUDIO, commit=True)
            proc = _run_gate_range(repo, "HEAD~1", "HEAD")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertTrue(out["protected"])
        self.assertIn("bot.py", {h["path"] for h in out["hits"]})

    def test_range_mode_still_ignores_a_neutral_bot_py_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_sandbox(tmp, after=BOT_AFTER_NEUTRAL, commit=True)
            proc = _run_gate_range(repo, "HEAD~1", "HEAD")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(json.loads(proc.stdout)["protected"])

    def test_range_mode_is_not_affected_by_a_dirty_working_tree(self):
        """Range mode must answer about the RANGE.

        The fallback reads the working tree, so a gate that let it leak into
        range mode would start reporting a developer's unrelated uncommitted
        bot.py edits as part of the commits under review — and, worse, would
        mask a committed audio change behind a clean working tree.
        """
        with tempfile.TemporaryDirectory() as tmp:
            repo = _make_sandbox(tmp, after=BOT_AFTER_NEUTRAL, commit=True)
            # Uncommitted audio edit sitting on top of a neutral commit.
            (repo / "bot.py").write_text(BOT_AFTER_AUDIO, encoding="utf-8")
            proc = _run_gate_range(repo, "HEAD~1", "HEAD")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertFalse(
            json.loads(proc.stdout)["protected"],
            "range mode reported a working-tree change as part of the range",
        )


class SandboxStaysInTheSandbox(unittest.TestCase):
    def test_the_real_bot_py_was_never_touched(self):
        """This suite fabricates bot.py diffs; it must do so nowhere near the
        real one. A harness in this repo has previously written the real
        checkout while believing it was writing a temp directory."""
        self.assertEqual(
            (REAL_BOT.stat().st_size, REAL_BOT.read_bytes()[:4096]),
            _REAL_BOT_FINGERPRINT,
            "the real bot.py changed while this suite ran",
        )


if __name__ == "__main__":
    unittest.main()
