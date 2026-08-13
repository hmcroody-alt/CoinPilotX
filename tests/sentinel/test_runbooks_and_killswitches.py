"""Stages 14-16 + 23: runbook governance, verification independence,
kill-switch defaults and precedence."""

import pytest

from services.sentinel import killswitches, runbooks, verification
from services.sentinel.authority import AuthorityLevel
from services.sentinel.risk import RiskBudget
from services.sentinel.runbooks import ForbiddenRunbookError, RunbookSpec


def _spec(name="test_probe", **overrides):
    base = dict(
        name=name, description="read-only probe", domain="OPERATIONAL",
        required_level=AuthorityLevel.ACT_REVERSIBLE,
        budget=RiskBudget(actions_per_hour=5, max_affected_entities=5),
        executor=lambda params: {"probed": True},
        verifier=lambda params, result: result.get("probed") is True)
    base.update(overrides)
    return RunbookSpec(**base)


def _enable_chain(monkeypatch, name, domain="OPERATIONAL"):
    monkeypatch.setenv("SENTINEL_AUTOMATION_ENABLED", "1")
    monkeypatch.setenv(f"SENTINEL_{domain}_AUTOMATION_ENABLED", "1")
    monkeypatch.setenv(f"SENTINEL_RUNBOOK_{name.upper()}_ENABLED", "1")


class TestKillSwitches:
    def test_automation_defaults_off(self):
        assert not killswitches.automation_enabled()
        assert not killswitches.domain_automation_enabled("SECURITY")
        assert not killswitches.runbook_enabled("anything", "SECURITY")

    def test_ingest_defaults_on(self):
        assert killswitches.ingest_enabled()

    def test_emergency_kills_everything(self, monkeypatch):
        _enable_chain(monkeypatch, "test_probe")
        monkeypatch.setenv("SENTINEL_EMERGENCY_KILL_SWITCH", "1")
        assert not killswitches.automation_enabled()
        assert not killswitches.runbook_enabled("test_probe", "OPERATIONAL")
        assert not killswitches.ingest_enabled()

    def test_domain_gate_requires_master(self, monkeypatch):
        monkeypatch.setenv("SENTINEL_SECURITY_AUTOMATION_ENABLED", "1")
        assert not killswitches.domain_automation_enabled("SECURITY")


class TestRunbookRegistry:
    def test_forbidden_names_rejected(self):
        for name in ("arbitrary_shell", "run_arbitrary_sql", "raw_shell_access",
                     "eval_anything", "sudo_helper"):
            with pytest.raises(ForbiddenRunbookError):
                _spec(name=name)

    def test_forbidden_description_rejected(self):
        with pytest.raises(ForbiddenRunbookError):
            _spec(name="innocent_name", description="executes arbitrary SQL for ops")

    def test_owner_only_cannot_be_a_runbook(self):
        with pytest.raises(ForbiddenRunbookError):
            _spec(required_level=AuthorityLevel.OWNER_ONLY)

    def test_no_financially_destructive_runbooks_shipped(self):
        shipped = {spec.name for spec in runbooks.all_runbooks()}
        for banned in ("refund", "payout", "rollback", "transfer", "ban_account"):
            assert not any(banned in name for name in shipped)

    def test_execute_denied_by_default_switches(self, conn):
        runbooks._REGISTRY.pop("gated_probe", None)
        runbooks.register(_spec(name="gated_probe"))
        result = runbooks.execute("gated_probe", "svc.worker", conn=conn)
        assert result["status"] == "DENIED"
        assert "OFF" in result["reason"] or "kill-switch" in result["reason"]

    def test_unknown_runbook_denied(self, conn):
        assert runbooks.execute("ghost", "svc.worker", conn=conn)["status"] == "DENIED"

    def test_budget_exhaustion_denies(self, conn, monkeypatch):
        runbooks._REGISTRY.pop("tiny_budget", None)
        runbooks.register(_spec(
            name="tiny_budget",
            budget=RiskBudget(actions_per_hour=1, max_affected_entities=1)))
        _enable_chain(monkeypatch, "tiny_budget")
        assert runbooks.execute("tiny_budget", "svc.worker", conn=conn)["status"] == "EXECUTED_UNVERIFIED"
        assert runbooks.execute("tiny_budget", "svc.worker", conn=conn)["status"] == "DENIED"


class TestIndependentVerification:
    def test_executor_cannot_self_verify(self, conn, monkeypatch):
        runbooks._REGISTRY.pop("verify_me", None)
        runbooks.register(_spec(name="verify_me"))
        _enable_chain(monkeypatch, "verify_me")
        result = runbooks.execute("verify_me", "svc.worker", conn=conn)
        assert result["status"] == "EXECUTED_UNVERIFIED"
        with pytest.raises(verification.VerificationError):
            verification.verify_execution(result["execution_id"], "svc.worker", conn=conn)

    def test_independent_verifier_completes(self, conn, monkeypatch):
        runbooks._REGISTRY.pop("verify_me2", None)
        runbooks.register(_spec(name="verify_me2"))
        _enable_chain(monkeypatch, "verify_me2")
        result = runbooks.execute("verify_me2", "svc.worker", conn=conn)
        outcome = verification.verify_execution(
            result["execution_id"], "sentinel.verifier", conn=conn)
        assert outcome["status"] == "COMPLETED" and outcome["passed"]

    def test_failed_verification_recorded(self, conn, monkeypatch):
        runbooks._REGISTRY.pop("liar", None)
        runbooks.register(_spec(
            name="liar",
            executor=lambda params: {"probed": False},  # executor claims work it didn't do
            verifier=lambda params, result: result.get("probed") is True))
        _enable_chain(monkeypatch, "liar")
        result = runbooks.execute("liar", "svc.worker", conn=conn)
        outcome = verification.verify_execution(
            result["execution_id"], "sentinel.verifier", conn=conn)
        assert outcome["status"] == "VERIFICATION_FAILED" and not outcome["passed"]
