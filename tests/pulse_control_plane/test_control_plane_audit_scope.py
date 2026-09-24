"""What ``scripts/control_plane_audit.py`` is allowed to call dead.

The audit's output is read as *"nothing reads this, you may delete it"*, and it
is the input to deleting real Railway variables. Its two errors are not
symmetric:

* A **false DEAD** deletes a live kill switch. That is an outage, caused by the
  tool, on the word of the tool.
* A **false alive** leaves a stale variable in place for a human to check by
  hand. That is a chore.

So every rule in the script is tuned to over-collect readers, and this file pins
the two specific ways that tuning has already been observed to fail. Both were
found by running the audit rather than by reading it, and neither would have
been caught by a test of ``classify_env_gate`` — the classifier was right both
times and was handed a wrong reader count.

These tests read the real repository. That is the point: a fixture tree would
pin the regex and not the scope, and scope is what broke.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from services.pulse_control_plane import env_gates  # noqa: E402


def _load_audit():
    """Import the audit by path — ``scripts/`` is not a package.

    Deliberately not made one. Adding ``scripts/__init__.py`` to make this
    import prettier would put ~200 one-off scripts on the import path of
    everything that runs from the repo root.
    """
    path = REPO / "scripts" / "control_plane_audit.py"
    spec = importlib.util.spec_from_file_location("control_plane_audit", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


audit = _load_audit()


@pytest.fixture(scope="module")
def scanned_files():
    return audit.source_files()


@pytest.fixture(scope="module")
def reader_counts(scanned_files):
    """Reader counts for every name this file has an opinion about.

    One scan for the whole module. ``readers_for`` reads each file once, so a
    per-test fixture would re-read the tree for every assertion below.
    """
    names = set(_COMM_V2_GATES) | {_RENAMED_AWAY} | {g.name for g in env_gates.DEAD_GATES}
    return {k: len(v) for k, v in audit.readers_for(names, scanned_files).items()}


#: Gates of ``pulse_communications_v2/`` — a **registered route pack**
#: (``bot.py:1389``), not a dead directory. ``PULSE_COMM_V2_SSE_ENABLED`` gates a
#: live SSE route at ``pulse_communications_v2/routes.py:899``.
_COMM_V2_GATES = (
    "PULSE_COMM_V2_SSE_ENABLED",
    "PULSE_COMMUNICATIONS_V2_ENABLED",
    "COMM_V2_TWILIO_NOTIFICATIONS_ENABLED",
)

#: Set in production, read by nothing, and mentioned exactly once in the tree —
#: in a rename map that exists to record its death.
_RENAMED_AWAY = "UNDX_METRICS_ENABLED"


class TestAuditScopeReachesEveryReader:
    """The scan must cover packages nobody thought to enumerate."""

    def test_scope_is_not_limited_to_services_and_root(self, scanned_files):
        """The original scope was ``bot.py`` + ``services/`` + root ``*.py``.

        A gate read only from a top-level package outside that set contributed
        zero readers and was reported DEAD. Pinning that the scan reaches at
        least one package beyond the old set is what stops a future refactor
        from quietly restoring the old scope.
        """
        tops = {
            f.relative_to(REPO).parts[0]
            for f in scanned_files
            if len(f.relative_to(REPO).parts) > 1
        }
        assert "pulse_communications_v2" in tops, (
            "a registered route pack is outside the scan scope; "
            "its gates will be reported DEAD"
        )
        assert tops - {"services", "scripts"}, "scope looks like the old enumeration"

    @pytest.mark.parametrize("gate", _COMM_V2_GATES)
    def test_live_route_pack_gates_are_not_dead(self, gate, reader_counts):
        """Each of these gates a route pack that production actually registers.

        Reported DEAD before the scope fix. The audit was saved from advising
        their deletion only by the accident that none of them is currently set
        in Railway, so the environment comparison never reached them — which is
        luck, not a safeguard, and is why this is pinned per gate rather than as
        a single assertion over the group.
        """
        assert reader_counts[gate] > 0, f"{gate} gates a live route and is called dead"

    def test_non_reader_dirs_are_excluded_for_stated_reasons(self):
        """Tests and the two JS apps are not production readers.

        Pinned because the fix for the scope bug was to *widen* the scan, and
        the obvious over-correction is to widen it to everything. A test that
        sets a variable would then make every gate look alive.
        """
        assert set(audit.NON_READER_DIRS) == {"tests", "mobile", "mobile-native"}
        assert "scripts" not in audit.NON_READER_DIRS, (
            "deleting a variable only a script reads still breaks that script"
        )


class TestAnInventoryMayNotVouchForItself:
    """A source that *names* variables is not a source that *reads* them."""

    def test_the_audit_excludes_its_own_packages_prose(self, scanned_files):
        """Everything in the package except the one module that reads env."""
        scanned = {str(f.relative_to(REPO)) for f in scanned_files}
        prose = {
            p
            for p in scanned
            if p.startswith("services/pulse_control_plane/")
            and p not in audit.SELF_READERS
        }
        assert not prose, f"inventory modules are being counted as readers: {prose}"
        assert "scripts/control_plane_audit.py" not in scanned
        assert "scripts/undx_railway_variable_audit.py" not in scanned

    def test_the_packages_one_real_reader_is_still_scanned(self, scanned_files):
        """Excluding the package wholesale put the arming switch on the dead list.

        ``runtime.py`` reads ``PULSE_CONTROL_PLANE_CONSULTATION`` — the wave-1
        switch, set on the production service. Excluding the whole package left
        it with zero readers, so the control plane's own audit was advising that
        the control plane be disarmed.

        It did no harm only because the name does not match ``GATE_NAME`` and so
        was never classified. Pinned here because that is luck rather than a
        safeguard, and the next gate-shaped variable ``runtime.py`` reads would
        not be so lucky.
        """
        scanned = {str(f.relative_to(REPO)) for f in scanned_files}
        assert "services/pulse_control_plane/runtime.py" in scanned

    def test_the_arming_switch_is_not_reported_dead(self, scanned_files):
        found = audit.readers_for({"PULSE_CONTROL_PLANE_CONSULTATION"}, scanned_files)
        assert found["PULSE_CONTROL_PLANE_CONSULTATION"], (
            "the live wave-1 arming switch has no reader; "
            "this audit would advise deleting it"
        )

    def test_the_reader_exception_stays_as_narrow_as_its_justification(self):
        """The exception is argued from one fact, so it may cover one file.

        ``runtime.py`` is both the only module of the package on a request path
        and the only one that reads the environment. That coincidence is what
        justifies re-admitting it. A second entry here would need its own
        argument, and adding one silently would re-open the self-vouching hole
        that ``SELF`` exists to close.
        """
        assert audit.SELF_READERS == ("services/pulse_control_plane/runtime.py",)
        env_readers = [
            path
            for path in (REPO / "services" / "pulse_control_plane").glob("*.py")
            if "os.getenv" in path.read_text() or "os.environ" in path.read_text()
        ]
        assert [p.name for p in env_readers] == ["runtime.py"], (
            "another module of the package now reads the environment; "
            "SELF_READERS is out of step with the package"
        )

    def test_a_rename_map_does_not_resurrect_the_name_it_retires(self, reader_counts):
        """``UNDX_METRICS_ENABLED`` is dead, and its only mention says so.

        It appears once in the tree, in the ``EQUIVALENTS`` map of
        ``scripts/undx_railway_variable_audit.py``, which maps old name → new
        name. That mention is the strongest available evidence the variable is
        dead; a substring search reads it as proof the variable is alive.

        This is the failure the scope fix introduced — widening the scan to
        ``scripts/`` pulled a second variable inventory into reader detection —
        so it is pinned next to the scope tests rather than apart from them.
        """
        assert reader_counts[_RENAMED_AWAY] == 0, (
            f"{_RENAMED_AWAY} is being vouched for by the record of its own removal"
        )

    def test_the_successor_name_is_the_one_with_a_reader(self, scanned_files):
        """Deleting the old name is only safe because the new one means the same.

        A rename map raises a question a mention count cannot answer. Asserted
        here so that "the old name has no readers" cannot be satisfied by *both*
        names being dead — which would mean the behaviour had been switched off
        rather than renamed.
        """
        successor = audit.readers_for({"UNDX_BRAIN_METRICS_ENABLED"}, scanned_files)
        assert successor["UNDX_BRAIN_METRICS_ENABLED"], (
            "neither name is read; this is a removal, not a rename"
        )


class TestTheAuditAgreesWithTheHandWrittenInventory:
    """Two independently derived lists, required to match.

    ``env_gates.DEAD_GATES`` was compiled by hand against live Railway. The
    audit derives its list by scanning. They are built by different methods from
    different evidence, so agreement is worth something and disagreement is
    worth stopping for.
    """

    def test_every_inventoried_dead_gate_still_has_no_reader(self, reader_counts):
        """The condition that makes deleting all fourteen safe.

        This is the assertion that goes red if somebody wires one of them up
        later. The inventory would then be stale in the dangerous direction —
        recorded as safe to delete, while a live reader depends on it — and the
        window between that commit and the deletion is exactly when nobody is
        looking.
        """
        resurrected = {
            gate.name: reader_counts[gate.name]
            for gate in env_gates.DEAD_GATES
            if reader_counts[gate.name] > 0
        }
        assert not resurrected, (
            "inventoried as dead but now read by something: "
            f"{resurrected} — do not delete these from Railway"
        )

    def test_env_example_declares_no_gate_that_nothing_reads(self, scanned_files):
        """The operator contract may not promise a switch that does not exist.

        `.env.example` is what an operator copies and what somebody reaches for
        under pressure. A gate listed there and read by nothing is a switch that
        will be set, and believed, on the day it matters.

        The CI job runs the audit against this file for the same reason. Pinned
        here as well because a workflow step is a line of YAML anybody can
        delete, while this fails in the suite, next to the argument for it.
        """
        declared = audit.load_env(str(REPO / ".env.example"))
        gates = {k for k in declared if audit.GATE_NAME.search(k)}
        hits = audit.readers_for(gates, scanned_files)
        undead = sorted(name for name in gates if not hits[name])
        assert not undead, (
            ".env.example declares gates nothing reads: "
            + ", ".join(undead)
            + " — remove the declaration, or wire the gate up"
        )

    def test_the_contract_check_is_not_vacuous(self, scanned_files):
        """A gate that cannot go red is decoration.

        The assertion above passes on an empty set, which is exactly what it
        would do if ``GATE_NAME`` stopped matching or ``load_env`` returned
        nothing. This pins that the file really does declare gates, so the check
        above is being asked a question with content.
        """
        declared = audit.load_env(str(REPO / ".env.example"))
        gates = {k for k in declared if audit.GATE_NAME.search(k)}
        assert len(gates) > 50, f"only {len(gates)} gates parsed out of .env.example"
        hits = audit.readers_for({"TRANSLATION_ENABLED"}, scanned_files)
        assert hits["TRANSLATION_ENABLED"], "reader detection itself went blind"

    def test_the_inventory_is_still_fourteen_gates(self):
        """Pinned as a number so growth is a decision, not a drive-by.

        Each row carries a production value and a disposition that somebody
        established by hand. Appending a fifteenth without that work would make
        the list look equally authoritative while being half-evidenced.
        """
        assert len(env_gates.DEAD_GATES) == 14
        assert len({g.name for g in env_gates.DEAD_GATES}) == 14, "duplicate name"
