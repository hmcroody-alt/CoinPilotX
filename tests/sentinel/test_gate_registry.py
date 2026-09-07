"""Stage 21: the emergency switch must reach every gate, and enforcement must
never arrive switched on.

``killswitches`` opens by promising that ``SENTINEL_EMERGENCY_KILL_SWITCH``
turns "everything off, no exceptions". Until this file, the test that claimed to
check that promise — ``test_emergency_kills_everything`` — called the three
functions defined in ``killswitches.py`` and stopped. It could not fail for a
gate defined in another module, and two such gates existed:
``bootstrap.bootstrap_enabled`` and ``request_bridge.bridge_enabled``, both
introduced by earlier stages of this same mission. Neither was caught by review.
Both were caught by setting the variable and asking the process what it returned.

So these tests are written against the registry rather than against a list of
imports someone maintained by hand, and one of them refuses to let the registry
fall behind the code.

Every assertion here has a partner that proves it can fail. A test that sets the
emergency switch and finds everything off would pass just as happily if
everything were off for the ordinary reason that these gates default off — which
most of them do. That is the exact shape of a test that guards nothing, so the
partner turns them all on first and proves the "off" is caused by the switch.
"""

import ast
import os
import pathlib

import pytest

from services.sentinel import bootstrap, killswitches, rate_limit, request_bridge

#: What each gate needs in the environment to say yes. Written out rather than
#: derived from the registry, so a wrong env name in the registry cannot hide by
#: being wrong in the test too.
ENABLING_ENV = {
    "SENTINEL_INGEST_ENABLED": "1",
    "SENTINEL_REQUEST_BRIDGE_ENABLED": "1",
    "SENTINEL_EXTERNAL_INTEL_ENABLED": "1",
    "SENTINEL_FINANCIAL_DETECTION_ENABLED": "1",
    "SENTINEL_MARKETPLACE_RISK_ENABLED": "1",
    "SENTINEL_PAYOUT_RISK_ENABLED": "1",
    "SENTINEL_REFUND_RISK_ENABLED": "1",
    "SENTINEL_AD_WALLET_RISK_ENABLED": "1",
    "SENTINEL_DISTRIBUTED_LIMITS_MODE": "enforce",
    "SENTINEL_RECEIPT_PARTICIPATION_ENFORCED": "1",
    "SENTINEL_AUTOMATION_ENABLED": "1",
    "SENTINEL_SCHEMA_BOOTSTRAP_ENABLED": "1",
}

#: ``financial_automation_enabled`` is hard-coded to False: Sentinel has no
#: money-movement capability, so there is no switch that could turn it on. It is
#: registered because a reader looking for "can this move money" must find the
#: answer in the same list as everything else, and it is excluded from the
#: "everything can be turned on" partner for the same reason it is safe.
ALWAYS_FALSE = {"financial_automation"}


def _clear(monkeypatch):
    for name in [k for k in os.environ if k.startswith("SENTINEL_")]:
        monkeypatch.delenv(name, raising=False)


def _enable_all(monkeypatch):
    _clear(monkeypatch)
    for name, value in ENABLING_ENV.items():
        monkeypatch.setenv(name, value)


class TestTheEmergencySwitchReachesEverything:
    def test_every_registered_gate_is_off_under_the_emergency_switch(self, monkeypatch):
        _enable_all(monkeypatch)
        monkeypatch.setenv("SENTINEL_EMERGENCY_KILL_SWITCH", "1")
        survivors = [name for name, on in killswitches.all_gates().items() if on]
        assert survivors == [], (
            f"these gates ignored the emergency switch: {survivors}. The module "
            "docstring promises 'everything off, no exceptions', so either the gate "
            "must consult killswitches.emergency_killed() or the promise must change."
        )

    def test_the_same_gates_are_on_without_it(self, monkeypatch):
        """Partner to the test above. Without this, 'everything is off' could be
        true merely because these gates default off — which most of them do — and
        the emergency test would be proving nothing at all."""
        _enable_all(monkeypatch)
        off = [name for name, on in killswitches.all_gates().items()
               if not on and name not in ALWAYS_FALSE]
        assert off == [], (
            f"expected every gate to be switchable on, but these stayed off: {off}. "
            "Either ENABLING_ENV names the wrong variable, or a gate has an "
            "additional precondition this test does not know about."
        )

    def test_the_registry_is_not_empty(self, monkeypatch):
        """A third partner. Both tests above iterate the registry, so an empty
        registry satisfies each of them vacuously."""
        assert len(killswitches.GATES) >= 12
        names = [g.name for g in killswitches.GATES]
        assert len(names) == len(set(names)), f"duplicate gate names: {names}"

    @pytest.mark.parametrize("gate_name", ["schema_bootstrap", "request_bridge"])
    def test_the_two_gates_that_used_to_survive_no_longer_do(self, monkeypatch, gate_name):
        """Named regression. These are the two the registry was built to catch;
        losing them again should fail with their own names, not inside a loop."""
        _enable_all(monkeypatch)
        assert killswitches.gate_value(gate_name) is True
        monkeypatch.setenv("SENTINEL_EMERGENCY_KILL_SWITCH", "1")
        assert killswitches.gate_value(gate_name) is False

    def test_an_unknown_gate_name_is_denied_rather_than_allowed(self, monkeypatch):
        """Hard Rule #5 at the registry's own edge. A caller asking about a gate
        that was renamed or removed is a caller whose question has no answer, and
        the answer to an unanswerable authorization question is no. Returning True
        here would turn a deleted gate into a permanently open one.

        Caught by mutation: an earlier version of this file did not test it, and
        flipping the fallback to ``return True`` passed the whole suite.
        """
        _enable_all(monkeypatch)
        assert killswitches.gate_value("no_such_gate") is False
        assert killswitches.gate_value("") is False
        # Partner: the fallback is only meaningful if a real name says yes here.
        assert killswitches.gate_value("ingest") is True

    def test_the_underlying_functions_agree_with_the_registry(self, monkeypatch):
        """The registry resolves by dotted path, so a typo in a module or function
        name would make a gate silently unreachable rather than loudly broken."""
        _enable_all(monkeypatch)
        monkeypatch.setenv("SENTINEL_EMERGENCY_KILL_SWITCH", "1")
        assert bootstrap.bootstrap_enabled() is False
        assert request_bridge.bridge_enabled() is False
        assert rate_limit.enabled() is False
        assert rate_limit.mode() == rate_limit.MODE_OFF


class TestEnforcementNeverArrivesOn:
    """Hard Rule #3 lives here. A gate whose effect a user can feel must require
    someone to decide; if it defaults on, deploying the code is the decision, and
    the shipped client finds out in production."""

    def test_no_gate_a_user_can_feel_defaults_on(self, monkeypatch):
        _clear(monkeypatch)
        on = [g.name for g in killswitches.enforcement_gates()
              if killswitches.gate_value(g.name)]
        assert on == [], (
            f"these enforcement gates default ON: {on}. An enforcement control "
            "that is on before anyone chose it changes production behaviour at "
            "deploy time for a client that cannot be updated."
        )

    def test_the_check_can_see_an_enforcement_gate_that_is_on(self, monkeypatch):
        """Partner. The test above passes trivially if enforcement_gates() returns
        nothing, or if gate_value always says False."""
        _clear(monkeypatch)
        assert len(killswitches.enforcement_gates()) >= 1
        monkeypatch.setenv("SENTINEL_DISTRIBUTED_LIMITS_MODE", "enforce")
        on = [g.name for g in killswitches.enforcement_gates()
              if killswitches.gate_value(g.name)]
        assert "distributed_limits" in on

    def test_the_registry_default_matches_what_the_gate_actually_does(self, monkeypatch):
        """``default_on`` is documentation, and documentation that is never
        compared to behaviour drifts. Comparing it here means the registry cannot
        describe a gate as default-off while it is default-on."""
        _clear(monkeypatch)
        for gate in killswitches.GATES:
            if gate.name in ALWAYS_FALSE:
                continue
            actual = killswitches.gate_value(gate.name)
            assert actual is gate.default_on, (
                f"gate {gate.name!r} is registered as default_on={gate.default_on} "
                f"but returns {actual} with a clean environment"
            )


class TestTheRegistryCannotFallBehindTheCode:
    """The defect this stage fixed was not that two gates were wrong. It was that
    nothing noticed. A registry that a new gate can be added beside is the same
    bug with more ceremony, so the code is scanned and compared to the registry."""

    #: Functions that read a Sentinel switch but are not themselves gates. Each
    #: needs a reason, and the reason is checked by hand rather than by pattern:
    #: "it is gated by something registered" is only true if it really calls it.
    EXEMPT = {
        # Parameterised sub-gates. Each requires its registered parent first, so
        # killing the parent kills every instance: domain_automation_enabled
        # returns False unless automation_enabled() does, and runbook_enabled
        # returns False unless domain_automation_enabled() does.
        "killswitches.domain_automation_enabled": "under the registered 'automation' gate",
        "killswitches.runbook_enabled": "under the registered 'automation' gate",
        "external_providers.provider_enabled": "under the registered 'external_intel' gate",
        # Consumers, not gates. evaluate() calls master_enabled/provider_enabled
        # and only reads spec.kill_switch to name it in a human-readable reason;
        # ensure_registered writes provider_enabled()'s answer into a table.
        "enrichment_policy.evaluate": "consumer of the 'external_intel' gate",
        "external_providers.ensure_registered": "records gate state, does not decide it",
        # Returns a mode string rather than a bool. Its boolean wrapper
        # rate_limit.enabled is what the registry points at.
        "rate_limit.mode": "registered through its wrapper rate_limit.enabled",
    }

    @staticmethod
    def _scan():
        """Module-level functions in services/sentinel that consult a Sentinel
        switch, by AST rather than by grep, so a name inside a comment or a
        docstring cannot register as a gate."""
        root = pathlib.Path(__file__).resolve().parents[2] / "services" / "sentinel"
        found = {}
        for path in sorted(root.glob("*.py")):
            tree = ast.parse(path.read_text())
            for node in tree.body:
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for sub in ast.walk(node):
                    literal = (isinstance(sub, ast.Constant)
                               and isinstance(sub.value, str)
                               and sub.value.startswith("SENTINEL_")
                               and sub.value.endswith(("ENABLED", "MODE")))
                    joined = (isinstance(sub, ast.JoinedStr) and any(
                        isinstance(p, ast.Constant) and str(p.value).startswith("SENTINEL_")
                        for p in sub.values))
                    switch_attr = isinstance(sub, ast.Attribute) and sub.attr == "kill_switch"
                    master = isinstance(sub, ast.Name) and sub.id == "MASTER_SWITCH"
                    if literal or joined or switch_attr or master:
                        found[f"{path.stem}.{node.name}"] = path.name
                        break
        return found

    def test_the_scanner_finds_the_gates_we_know_exist(self):
        """Partner, and the load-bearing one. The comparison below passes for a
        scanner that returns nothing, which is exactly what a scanner does after
        someone renames a module or the AST shape shifts under a Python upgrade."""
        found = self._scan()
        for expected in ("killswitches.ingest_enabled",
                         "bootstrap.bootstrap_enabled",
                         "request_bridge.bridge_enabled",
                         "rate_limit.mode",
                         "external_providers.master_enabled"):
            assert expected in found, (
                f"the scanner did not find {expected!r}, so it is not capable of "
                "detecting an unregistered gate either. Fix the scanner before "
                "trusting the test below."
            )

    def test_every_switch_reading_function_is_registered_or_exempt(self):
        registered = {f"{g.module.rsplit('.', 1)[-1]}.{g.func}" for g in killswitches.GATES}
        unaccounted = sorted(
            set(self._scan()) - registered - set(self.EXEMPT))
        assert unaccounted == [], (
            f"these functions read a Sentinel switch but are neither registered in "
            f"killswitches.GATES nor listed as exempt: {unaccounted}. If it decides "
            "whether a capability runs, register it so the emergency switch is "
            "tested against it. If it does not, add it to EXEMPT with the reason."
        )

    def test_every_exemption_names_a_function_that_still_exists(self):
        """An exemption for a deleted function is a hole waiting for someone to
        add a gate with that name."""
        found = self._scan()
        stale = sorted(name for name in self.EXEMPT if name not in found)
        assert stale == [], f"exemptions for functions that no longer read a switch: {stale}"


class TestHealthTellsTheTruthDuringAnEmergencyStop:
    """Hard Rule #4, in the direction that matters. Reporting a stopped control as
    running is worse than reporting nothing, because it is trusted."""

    def test_the_bridge_does_not_report_itself_enabled_while_stopped(self, monkeypatch):
        _enable_all(monkeypatch)
        assert request_bridge.stats()["enabled"] is True
        monkeypatch.setenv("SENTINEL_EMERGENCY_KILL_SWITCH", "1")
        assert request_bridge.stats()["enabled"] is False, (
            "the health surface said the request bridge was enabled while the "
            "emergency switch had it recording nothing"
        )

    def test_the_bridge_really_does_record_nothing_then(self, monkeypatch):
        """Partner: proves the flag above corresponds to behaviour rather than
        merely agreeing with a second copy of the same env read."""
        _enable_all(monkeypatch)
        monkeypatch.setenv("SENTINEL_EMERGENCY_KILL_SWITCH", "1")
        before = request_bridge.stats()
        accepted = request_bridge.emit(_probe_event())
        after = request_bridge.stats()
        assert accepted is False
        assert after["emitted"] == before["emitted"]
        assert after["pending"] == before["pending"]

    def test_switch_state_carries_the_whole_registry(self, monkeypatch):
        _clear(monkeypatch)
        state = killswitches.switch_state()
        assert set(state["gates"]) == {g.name for g in killswitches.GATES}
        assert state["emergency_killed"] is False


def _probe_event():
    from services.sentinel import events
    from services.sentinel.identity import SENTINEL_INGEST
    return events.Event(
        category="SECURITY", event_type="gate_registry_probe", severity="low",
        actor_id=SENTINEL_INGEST.actor_id, source="tests.gate_registry",
        subject_type="test", subject_id="probe", payload={})
