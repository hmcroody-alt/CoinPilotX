"""Stages 1 + 3: constitution integrity and data classification."""

import pytest

from services.sentinel import classification
from services.sentinel.classification import Level
from services.sentinel.constitution import CONSTITUTION_VERSION, RULES, rule


class TestConstitution:
    def test_fifteen_rules_present(self):
        assert len(RULES) == 15
        assert {r.rule_id for r in RULES} == {f"SC{i}" for i in range(1, 16)}

    def test_rules_are_immutable(self):
        with pytest.raises(Exception):
            rule("SC1").text = "weakened"

    def test_unknown_rule_fails_closed(self):
        with pytest.raises(KeyError):
            rule("SC99")

    def test_version_is_stamped(self):
        assert CONSTITUTION_VERSION == "SENTINEL_CONSTITUTION_V1"


class TestClassification:
    def test_secrets_are_highly_restricted_by_name(self):
        for name in ("password", "api_key", "stripe_token", "card_number", "cvv"):
            assert classification.classify_field(name) == Level.HIGHLY_RESTRICTED

    def test_pulse_id_is_sensitive_internal_identifier(self):
        assert classification.classify_field("pulse_id") == Level.SENSITIVE
        assert not classification.external_share_allowed("pulse_id")

    def test_unknown_field_defaults_confidential_not_public(self):
        assert classification.classify_field("mystery_field") == Level.CONFIDENTIAL

    def test_redact_strips_secrets_recursively(self):
        payload = {"user": {"password": "x", "note": "ok"},
                   "items": [{"api_key": "k"}], "email": "a@b.c"}
        out = classification.redact(payload)
        assert out["user"]["password"] == classification.REDACTED
        assert out["user"]["note"] == "ok"
        assert out["items"][0]["api_key"] == classification.REDACTED
        assert out["email"] == classification.REDACTED  # SENSITIVE > CONFIDENTIAL

    def test_external_share_is_minimize_by_default(self):
        # Unknown fields classify CONFIDENTIAL → never shareable.
        assert not classification.external_share_allowed("mystery_field")
        assert not classification.external_share_allowed("email")
        assert not classification.external_share_allowed("secret_key")
