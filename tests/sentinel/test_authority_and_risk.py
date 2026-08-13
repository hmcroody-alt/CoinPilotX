"""Stages 4-6: identity caps, multidimensional authority fail-closed, budgets."""

import pytest

from services.sentinel import authority as auth
from services.sentinel.authority import AuthorityGrant, AuthorityLevel
from services.sentinel.identity import Actor, TrustTier
from services.sentinel.risk import (BudgetTracker, RiskBudget,
                                    UnboundedBudgetError)

HUMAN = Actor("owner.roody", "human", TrustTier.OWNER)
SERVICE = Actor("svc.worker", "service", TrustTier.OPERATIONAL)
MODEL = Actor("undx.test", "model", TrustTier.ADVISORY)


class TestIdentity:
    def test_model_trust_tier_is_capped(self):
        with pytest.raises(ValueError):
            Actor("undx.rogue", "model", TrustTier.OPERATIONAL)

    def test_service_cannot_be_owner(self):
        with pytest.raises(ValueError):
            Actor("svc.rogue", "service", TrustTier.OWNER)

    def test_unknown_actor_resolves_untrusted(self):
        from services.sentinel import identity
        assert identity.get("never-registered").trust_tier == TrustTier.UNTRUSTED


class TestAuthority:
    def test_unknown_dimension_fails_closed(self):
        grant = AuthorityGrant(SERVICE.actor_id, {"OPERATIONAL": AuthorityLevel.ACT_REVERSIBLE})
        decision = auth.check(SERVICE, grant, "GALACTIC", AuthorityLevel.READ)
        assert not decision.allowed and "SC15" in decision.rule_ids

    def test_model_cannot_act_even_with_grant(self):
        grant = AuthorityGrant(MODEL.actor_id, {"SECURITY": AuthorityLevel.ACT_REVERSIBLE})
        decision = auth.check(MODEL, grant, "SECURITY", AuthorityLevel.ACT_REVERSIBLE)
        assert not decision.allowed and "SC2" in decision.rule_ids

    def test_financial_sensitive_requires_human_approval(self):
        grant = AuthorityGrant(HUMAN.actor_id, {"FINANCIAL": AuthorityLevel.ACT_SENSITIVE})
        denied = auth.check(HUMAN, grant, "FINANCIAL", AuthorityLevel.ACT_SENSITIVE)
        assert not denied.allowed and "SC6" in denied.rule_ids
        approved = auth.check(HUMAN, grant, "FINANCIAL", AuthorityLevel.ACT_SENSITIVE,
                              human_approved=True)
        assert approved.allowed

    def test_owner_only_never_satisfiable_by_service(self):
        grant = AuthorityGrant(SERVICE.actor_id, {"SECURITY": AuthorityLevel.OWNER_ONLY})
        decision = auth.check(SERVICE, grant, "SECURITY", AuthorityLevel.OWNER_ONLY,
                              human_approved=True)
        assert not decision.allowed

    def test_missing_dimension_means_read_only(self):
        grant = AuthorityGrant(SERVICE.actor_id, {})
        assert auth.check(SERVICE, grant, "PRIVACY", AuthorityLevel.READ).allowed
        assert not auth.check(SERVICE, grant, "PRIVACY", AuthorityLevel.ACT_REVERSIBLE).allowed

    def test_check_all_denies_empty_requirements(self):
        grant = AuthorityGrant(SERVICE.actor_id, {})
        assert not auth.check_all(SERVICE, grant, {}).allowed

    def test_decisions_cite_policy_version(self):
        grant = AuthorityGrant(SERVICE.actor_id, {})
        decision = auth.check(SERVICE, grant, "OPERATIONAL", AuthorityLevel.READ)
        assert decision.policy_version == "SENTINEL_CONSTITUTION_V1"


class TestRiskBudgets:
    def test_unbounded_budget_rejected(self):
        for bad in (0, -1, 10**9):
            with pytest.raises(UnboundedBudgetError):
                RiskBudget(actions_per_hour=bad, max_affected_entities=5)
        with pytest.raises(UnboundedBudgetError):
            RiskBudget(actions_per_hour=5, max_affected_entities=0)

    def test_tracker_enforces_action_limit(self):
        tracker = BudgetTracker(RiskBudget(actions_per_hour=2, max_affected_entities=10))
        assert tracker.try_spend(("a",))
        assert tracker.try_spend(("b",))
        assert not tracker.try_spend(("c",))

    def test_tracker_enforces_entity_limit(self):
        tracker = BudgetTracker(RiskBudget(actions_per_hour=10, max_affected_entities=2))
        assert tracker.try_spend(("e1", "e2"))
        assert not tracker.try_spend(("e3",))

    def test_window_rolls_after_an_hour(self):
        tracker = BudgetTracker(RiskBudget(actions_per_hour=1, max_affected_entities=5))
        assert tracker.try_spend(("a",), now=1000.0)
        assert not tracker.try_spend(("b",), now=1001.0)
        assert tracker.try_spend(("b",), now=1000.0 + 3601)
