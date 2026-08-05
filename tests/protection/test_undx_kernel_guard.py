"""The UNDX write kernel must not be able to quietly remove its own guards.

`undx_execution_kernel.apply_approved_changes()` writes files into the working
repository after an operator supplies the phrase `APPROVE UNDX WRITE`. It
refuses to write outside the repository root and refuses to touch secrets.

It did not refuse to write to *itself*. `PROTECTED_PATTERNS`, `APPROVAL_PHRASE`
and the containment check all live in `undx_execution_kernel.py`, and that file
matched none of the patterns. One approved change to it - arriving in a batch
labelled as a refactor, alongside a dozen innocuous edits - could empty the
protected list or blank the approval phrase, and every write afterwards would be
unguarded. The same held for `tests/protection/`, `scripts/protection/` and
`.github/workflows/`: rewriting those makes the safety net report green without
anything being fixed.

The fix is escalation rather than prohibition. UNDX improving its own guards is
legitimate; a flat ban would be deleted the first time it was inconvenient. So
those paths need a second, *different* phrase, `APPROVE UNDX GUARD CHANGE`,
which means approving a refactor can never also mean removing the rails.

Unlike the other protection suites, these tests import the module and exercise
it against a temporary repository. The kernel has no Flask dependency, so this
is cheap - and a guard this important deserves to be tested by its behaviour
rather than by grepping for the string that implements it.
"""

import importlib.util
import pathlib
import shutil
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]

# The kernel imports `undx_brain_layer` from the repository root, so the root has
# to be importable regardless of where this file is run from.
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
import undx_execution_kernel as kernel  # noqa: E402

WRITE = kernel.APPROVAL_PHRASE
GUARD = kernel.GUARD_APPROVAL_PHRASE


class _Sandbox:
    """A throwaway repository, so no test can write into the real tree."""

    def __enter__(self):
        self.root = pathlib.Path(tempfile.mkdtemp()).resolve()
        self._saved = (kernel.DEFAULT_REPOSITORY_PATH, kernel.BACKUP_ROOT, kernel.LOG_PATH)
        kernel.DEFAULT_REPOSITORY_PATH = self.root
        kernel.BACKUP_ROOT = self.root / ".undx_backups"
        kernel.LOG_PATH = self.root / "undx_execution_log.jsonl"
        (self.root / "tests" / "protection").mkdir(parents=True)
        (self.root / "scripts" / "protection").mkdir(parents=True)
        (self.root / ".github" / "workflows").mkdir(parents=True)
        (self.root / "services").mkdir()
        (self.root / "undx_execution_kernel.py").write_text("GUARDS = 1\n")
        (self.root / "tests" / "protection" / "test_x.py").write_text("old\n")
        (self.root / "scripts" / "protection" / "run.py").write_text("old\n")
        (self.root / ".github" / "workflows" / "protection.yml").write_text("old\n")
        (self.root / "services" / "ordinary.py").write_text("old\n")
        return self

    def __exit__(self, *exc):
        kernel.DEFAULT_REPOSITORY_PATH, kernel.BACKUP_ROOT, kernel.LOG_PATH = self._saved
        shutil.rmtree(self.root, ignore_errors=True)
        return False

    def apply(self, rel, before="old\n", *, guard="", after="NEW\n"):
        proposal = {
            "proposalId": "p",
            "changes": [{"id": "c1", "path": rel, "before": before, "after": after}],
        }
        return kernel.apply_approved_changes(
            str(self.root), proposal, WRITE, ["c1"], guard_approval=guard
        )


def _refuses(sandbox, rel, before="old\n", *, guard=""):
    try:
        sandbox.apply(rel, before, guard=guard)
    except kernel.KernelError:
        return True
    return False


# --- The escalation itself ---------------------------------------------------

def test_the_two_approval_phrases_are_different():
    """If they were equal the escalation would be invisible.

    Any batch already carrying the write phrase would satisfy the guard check
    too, and the second gate would exist only in the documentation.
    """
    assert WRITE != GUARD
    assert GUARD, "GUARD_APPROVAL_PHRASE must not be empty"


def test_kernel_cannot_rewrite_itself_with_the_write_phrase_alone():
    with _Sandbox() as box:
        assert _refuses(box, "undx_execution_kernel.py", "GUARDS = 1\n"), (
            "UNDX can rewrite its own guard file with an ordinary write approval. "
            "That single change removes every constraint on later writes."
        )
        assert (box.root / "undx_execution_kernel.py").read_text() == "GUARDS = 1\n"


def test_protection_suite_and_ci_cannot_be_rewritten_with_the_write_phrase_alone():
    """These make the safety net green without fixing the thing it guards."""
    with _Sandbox() as box:
        for rel in (
            "tests/protection/test_x.py",
            "scripts/protection/run.py",
            ".github/workflows/protection.yml",
        ):
            assert _refuses(box, rel), f"{rel} was writable without the guard phrase"


def test_the_write_phrase_cannot_be_replayed_as_the_guard_phrase():
    with _Sandbox() as box:
        assert _refuses(box, "undx_execution_kernel.py", "GUARDS = 1\n", guard=WRITE)


def test_path_traversal_does_not_launder_a_guard_path():
    """`services/../undx_execution_kernel.py` is the same file.

    The check resolves before matching; a check on the raw string would miss it.
    """
    with _Sandbox() as box:
        assert _refuses(box, "services/../undx_execution_kernel.py", "GUARDS = 1\n")


# --- The escalation must not become a prohibition ----------------------------

def test_ordinary_files_still_apply_with_only_the_write_phrase():
    """A guard that blocks routine work gets removed. Keep the common path cheap."""
    with _Sandbox() as box:
        result = box.apply("services/ordinary.py")
        assert result["ok"] and result["applied"]
        assert (box.root / "services" / "ordinary.py").read_text() == "NEW\n"
        assert result["guardPathsChanged"] == []


def test_guard_changes_are_possible_with_both_phrases():
    with _Sandbox() as box:
        result = box.apply("undx_execution_kernel.py", "GUARDS = 1\n", guard=GUARD)
        assert result["ok"]
        assert (box.root / "undx_execution_kernel.py").read_text() == "NEW\n"
        assert result["guardPathsChanged"] == ["undx_execution_kernel.py"]


def test_guard_changes_are_recorded_in_the_execution_log():
    """"The kernel rewrote its own rules" is the entry most worth finding later."""
    with _Sandbox() as box:
        box.apply("undx_execution_kernel.py", "GUARDS = 1\n", guard=GUARD)
        log = kernel.LOG_PATH.read_text(encoding="utf-8")
        assert "guardPathsChanged" in log and "undx_execution_kernel.py" in log


# --- Batch behaviour ---------------------------------------------------------

def test_a_mixed_batch_is_refused_before_anything_is_written():
    """Half-applying a batch leaves the operator reconciling by hand, mid-incident.

    A batch of one ordinary edit plus one guard edit must write neither.
    """
    with _Sandbox() as box:
        proposal = {
            "proposalId": "p",
            "changes": [
                {"id": "a", "path": "services/ordinary.py", "before": "old\n", "after": "X\n"},
                {"id": "b", "path": "tests/protection/test_x.py", "before": "old\n", "after": "Y\n"},
            ],
        }
        try:
            kernel.apply_approved_changes(str(box.root), proposal, WRITE, ["a", "b"])
            raise AssertionError("mixed batch applied without the guard phrase")
        except kernel.KernelError:
            pass
        assert (box.root / "services" / "ordinary.py").read_text() == "old\n", (
            "The ordinary file was written before the guard file was refused."
        )


def test_unselected_guard_changes_do_not_force_escalation():
    """Only the approved subset counts.

    `approved_change_ids` narrows a proposal. A guard edit the operator did not
    select must not demand a phrase for a batch that will never write it -
    otherwise operators learn to supply the guard phrase reflexively.
    """
    with _Sandbox() as box:
        proposal = {
            "proposalId": "p",
            "changes": [
                {"id": "a", "path": "services/ordinary.py", "before": "old\n", "after": "X\n"},
                {"id": "b", "path": "tests/protection/test_x.py", "before": "old\n", "after": "Y\n"},
            ],
        }
        result = kernel.apply_approved_changes(str(box.root), proposal, WRITE, ["a"])
        assert result["ok"] and result["guardPathsChanged"] == []
        assert (box.root / "tests" / "protection" / "test_x.py").read_text() == "old\n"


# --- Pre-existing guarantees this change must not have weakened --------------

def test_writes_outside_the_repository_are_still_refused():
    with _Sandbox() as box:
        assert _refuses(box, "../escaped.py")


def test_secret_paths_are_still_refused():
    with _Sandbox() as box:
        for rel in ("services/.env", "services/private_key.py", "services/id_rsa"):
            assert _refuses(box, rel), f"{rel} was writable"


def test_the_write_phrase_is_still_required_at_all():
    with _Sandbox() as box:
        try:
            kernel.apply_approved_changes(
                str(box.root),
                {"proposalId": "p", "changes": [
                    {"id": "c1", "path": "services/ordinary.py", "before": "old\n", "after": "X\n"}]},
                "please",
                ["c1"],
            )
            raise AssertionError("kernel wrote without the approval phrase")
        except kernel.KernelError:
            pass


if __name__ == "__main__":
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
    from _runner import run_module_tests

    raise SystemExit(run_module_tests(globals()))
