#!/usr/bin/env python3
"""Behavioural guard for LiveKit livestream token grants and the audio V2 flag.

Why this file execs functions out of bot.py instead of importing it: bot.py is a
110k-line Flask monolith whose import has side effects (DB init, network clients,
scheduler). The five functions under test are pure given `os.environ`, so we lift
their source with `ast` and run them in an isolated namespace. That keeps the
test fast and hermetic while still testing the real, shipped code - a copy-paste
of the logic into the test would prove nothing.

What is protected here:

1. A listen-only VIEWER must not receive canPublishData or canUpdateOwnMetadata.
   Clients render a participant's role from its LiveKit metadata, so a viewer
   able to rewrite its own metadata can set role="host" and impersonate the
   broadcaster in every other participant's list.
2. A PUBLISHER (host / guest / cohost) must keep both grants - the web Live
   Studio hard-requires them on the cohost path.
3. The verifier must assert the grants that were actually requested, so a future
   change that re-widens viewer grants fails here rather than shipping.
4. The audio V2 rollout flag must default OFF and must be decided server-side.
"""

from __future__ import annotations

import ast
import base64
import hashlib
import hmac
import json
import os
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BOT_SOURCE = (ROOT / "bot.py").read_text(encoding="utf-8")

LIFTED = (
    "pulse_livekit_config",
    "pulse_livekit_b64url",
    "pulse_livekit_access_token",
    "pulse_livekit_verify_token_claims",
    "pulse_live_audio_v2_env_flag",
    "pulse_live_audio_v2_enabled",
    "pulse_live_audio_v2_fallback_enabled",
    "pulse_live_audio_trace_enabled",
)


def _load_namespace() -> dict:
    tree = ast.parse(BOT_SOURCE)
    wanted = {}
    salt = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in LIFTED:
            wanted[node.name] = node
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "PULSE_LIVE_AUDIO_V2_SALT" for t in node.targets
        ):
            salt = node

    missing = [name for name in LIFTED if name not in wanted]
    if missing:
        raise AssertionError(f"bot.py no longer defines: {', '.join(missing)}")
    if salt is None:
        raise AssertionError("bot.py no longer defines PULSE_LIVE_AUDIO_V2_SALT")

    module = ast.Module(body=[salt] + [wanted[name] for name in LIFTED], type_ignores=[])
    namespace = {
        "os": os,
        "time": time,
        "json": json,
        "base64": base64,
        "hmac": hmac,
        "hashlib": hashlib,
        # Sanitisers used only for the human-readable claims echo.
        "clean_html": lambda value: str(value or ""),
        "pulse_live_safe_debug_payload": lambda payload: payload,
    }
    exec(compile(module, "<bot.py:lifted>", "exec"), namespace)  # noqa: S102 - lifted from the real source
    return namespace


NS = _load_namespace()

TEST_ENV = {
    "LIVEKIT_URL": "wss://livekit.test.invalid",
    "LIVEKIT_API_KEY": "test-api-key",
    "LIVEKIT_API_SECRET": "test-api-secret-not-a-real-credential",
}


def expect(condition: bool, label: str) -> None:
    if not condition:
        raise AssertionError(label)
    print(f"ok - {label}")


class env_patch:
    def __init__(self, **values):
        self.values = values
        self.previous = {}

    def __enter__(self):
        for key, value in self.values.items():
            self.previous[key] = os.environ.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return self

    def __exit__(self, *exc):
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        return False


def decode_grants(token: str) -> dict:
    payload = token.split(".")[1]
    payload += "=" * (-len(payload) % 4)
    claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")).decode("utf-8"))
    return claims.get("video") or {}


def mint(role: str, can_publish: bool, **kwargs) -> str:
    return NS["pulse_livekit_access_token"](
        "pulse-user-42",
        "pulse-live-7",
        can_publish=can_publish,
        name="Test Participant",
        metadata={"live_id": 7, "role": role, "guest_id": 0},
        ttl_seconds=3600,
        **kwargs,
    )


def test_viewer_grants_are_least_privilege() -> None:
    token = mint("viewer", False)
    grants = decode_grants(token)
    expect(grants.get("canPublish") is False, "viewer token cannot publish")
    expect(grants.get("canPublishSources") == [], "viewer token cannot publish microphone or camera sources")
    expect(grants.get("canSubscribe") is True, "viewer token can still subscribe (playback works)")
    expect(grants.get("canPublishData") is False, "viewer token cannot publish data")
    expect(
        grants.get("canUpdateOwnMetadata") is False,
        "SECURITY: viewer token cannot rewrite its own metadata (blocks host impersonation)",
    )


def test_publisher_keeps_both_grants() -> None:
    for role in ("host", "guest", "cohost"):
        grants = decode_grants(mint(role, True))
        expect(grants.get("canPublish") is True, f"{role} token can publish")
        expect(grants.get("canPublishSources") == ["microphone", "camera"], f"{role} token can publish microphone and camera sources")
        expect(grants.get("canPublishData") is True, f"{role} token keeps the data channel")
        expect(
            grants.get("canUpdateOwnMetadata") is True,
            f"{role} token keeps self-metadata (web Live Studio cohost path requires it)",
        )


def test_explicit_overrides_are_honoured() -> None:
    grants = decode_grants(mint("host", True, can_publish_data=False, can_update_own_metadata=False))
    expect(grants.get("canPublish") is True, "explicit override leaves the publish grant alone")
    expect(grants.get("canPublishData") is False, "explicit can_publish_data=False is honoured")
    expect(grants.get("canUpdateOwnMetadata") is False, "explicit can_update_own_metadata=False is honoured")


def test_verifier_accepts_the_grants_it_asked_for() -> None:
    viewer = mint("viewer", False)
    result = NS["pulse_livekit_verify_token_claims"](
        viewer,
        identity="pulse-user-42",
        room_name="pulse-live-7",
        role="viewer",
        require_publish=False,
        expect_publish_data=False,
        expect_update_own_metadata=False,
    )
    expect(result.get("ok") is True, "viewer token verifies against viewer expectations")

    host = mint("host", True)
    result = NS["pulse_livekit_verify_token_claims"](
        host,
        identity="pulse-user-42",
        room_name="pulse-live-7",
        role="host",
        require_publish=True,
        expect_publish_data=True,
        expect_update_own_metadata=True,
        expect_publish_sources=["microphone", "camera"],
    )
    expect(result.get("ok") is True, "host token verifies against host expectations")
    expect(all((result.get("checks") or {}).values()), "every individual host claim check passes")


def test_verifier_rejects_over_wide_viewer_grants() -> None:
    # Simulates a future regression that re-widens viewer grants.
    over_wide = mint("viewer", False, can_publish_data=True, can_update_own_metadata=True)
    result = NS["pulse_livekit_verify_token_claims"](
        over_wide,
        identity="pulse-user-42",
        room_name="pulse-live-7",
        role="viewer",
        require_publish=False,
    )
    expect(result.get("ok") is False, "REGRESSION GUARD: an over-privileged viewer token fails verification")
    failed = [key for key, passed in (result.get("checks") or {}).items() if not passed]
    expect(
        set(failed) == {"publish_data", "update_own_metadata"},
        f"only the widened grants are reported as failing (got {failed})",
    )


def test_verifier_still_catches_tampering() -> None:
    token = mint("viewer", False)
    tampered = token[:-4] + ("aaaa" if not token.endswith("aaaa") else "bbbb")
    result = NS["pulse_livekit_verify_token_claims"](
        tampered, identity="pulse-user-42", room_name="pulse-live-7", role="viewer", require_publish=False
    )
    expect(result.get("ok") is False, "a tampered signature is still rejected")

    wrong_room = NS["pulse_livekit_verify_token_claims"](
        token, identity="pulse-user-42", room_name="pulse-live-999", role="viewer", require_publish=False
    )
    expect(wrong_room.get("ok") is False, "a token for another room is still rejected")


def test_audio_v2_flag_defaults_off() -> None:
    with env_patch(
        LIVESTREAM_AUDIO_V2_ENABLED=None,
        LIVESTREAM_AUDIO_V2_QA_ONLY=None,
        LIVESTREAM_AUDIO_V2_PERCENT=None,
    ):
        expect(NS["pulse_live_audio_v2_enabled"](42) is False, "audio V2 is OFF when the master switch is unset")

    with env_patch(LIVESTREAM_AUDIO_V2_ENABLED="maybe"):
        expect(NS["pulse_live_audio_v2_enabled"](42) is False, "a malformed master switch is treated as OFF")

    with env_patch(LIVESTREAM_AUDIO_V2_ENABLED="false", LIVESTREAM_AUDIO_V2_PERCENT="100"):
        expect(
            NS["pulse_live_audio_v2_enabled"](42) is False,
            "KILL SWITCH: master OFF beats a 100 percent rollout",
        )


def test_audio_v2_rollout_controls() -> None:
    with env_patch(
        LIVESTREAM_AUDIO_V2_ENABLED="true", LIVESTREAM_AUDIO_V2_QA_ONLY=None, LIVESTREAM_AUDIO_V2_PERCENT="100"
    ):
        expect(NS["pulse_live_audio_v2_enabled"](42) is True, "100 percent rollout enables V2")

    with env_patch(
        LIVESTREAM_AUDIO_V2_ENABLED="true", LIVESTREAM_AUDIO_V2_QA_ONLY=None, LIVESTREAM_AUDIO_V2_PERCENT="0"
    ):
        expect(NS["pulse_live_audio_v2_enabled"](42) is False, "0 percent rollout keeps V2 off")

    with env_patch(
        LIVESTREAM_AUDIO_V2_ENABLED="true", LIVESTREAM_AUDIO_V2_QA_ONLY="true", LIVESTREAM_AUDIO_V2_QA_USER_IDS=None, LIVESTREAM_AUDIO_V2_PERCENT="100"
    ):
        expect(NS["pulse_live_audio_v2_enabled"](42, is_qa=True) is True, "QA-only mode admits QA accounts")
        expect(NS["pulse_live_audio_v2_enabled"](42, is_qa=False) is False, "QA-only mode excludes normal accounts")

    with env_patch(
        LIVESTREAM_AUDIO_V2_ENABLED="true", LIVESTREAM_AUDIO_V2_QA_ONLY="true", LIVESTREAM_AUDIO_V2_QA_USER_IDS="1,34,invalid", LIVESTREAM_AUDIO_V2_PERCENT="0"
    ):
        expect(NS["pulse_live_audio_v2_enabled"](1) is True, "native QA host allowlist receives V2")
        expect(NS["pulse_live_audio_v2_enabled"](34) is True, "native QA viewer allowlist receives V2")
        expect(NS["pulse_live_audio_v2_enabled"](35) is False, "native non-QA account stays on legacy")

    with env_patch(
        LIVESTREAM_AUDIO_V2_ENABLED="true", LIVESTREAM_AUDIO_V2_QA_ONLY=None, LIVESTREAM_AUDIO_V2_PERCENT="50"
    ):
        first = [NS["pulse_live_audio_v2_enabled"](uid) for uid in range(1, 200)]
        second = [NS["pulse_live_audio_v2_enabled"](uid) for uid in range(1, 200)]
        expect(first == second, "percentage bucketing is sticky per user across calls")
        enabled = sum(1 for value in first if value)
        expect(70 <= enabled <= 130, f"a 50 percent rollout splits the population roughly in half (got {enabled}/199)")
        expect(NS["pulse_live_audio_v2_enabled"](0) is False, "an anonymous/unknown user never gets V2")


def test_audio_v2_fallback_defaults_on() -> None:
    with env_patch(LIVESTREAM_AUDIO_V2_FALLBACK_ENABLED=None):
        expect(NS["pulse_live_audio_v2_fallback_enabled"] () is True, "legacy fallback is available by default")
    with env_patch(LIVESTREAM_AUDIO_V2_FALLBACK_ENABLED="false"):
        expect(NS["pulse_live_audio_v2_fallback_enabled"]() is False, "fallback can be switched off explicitly")


def test_audio_trace_requires_master_and_qa_account() -> None:
    with env_patch(LIVESTREAM_AUDIO_TRACE_ENABLED=None, LIVESTREAM_AUDIO_TRACE_USER_IDS="42"):
        expect(NS["pulse_live_audio_trace_enabled"](42) is False, "audio trace defaults OFF")
    with env_patch(LIVESTREAM_AUDIO_TRACE_ENABLED="true", LIVESTREAM_AUDIO_TRACE_USER_IDS=None):
        expect(NS["pulse_live_audio_trace_enabled"](42) is False, "master flag alone cannot trace ordinary users")
        expect(NS["pulse_live_audio_trace_enabled"](42, is_qa=True) is True, "admin QA session may trace")
    with env_patch(LIVESTREAM_AUDIO_TRACE_ENABLED="true", LIVESTREAM_AUDIO_TRACE_USER_IDS="7, 42,invalid"):
        expect(NS["pulse_live_audio_trace_enabled"](42) is True, "explicit QA user allowlist enables trace")
        expect(NS["pulse_live_audio_trace_enabled"](99) is False, "non-QA account remains untraced")


def test_no_secret_material_leaks_into_claims() -> None:
    token = mint("host", True)
    result = NS["pulse_livekit_verify_token_claims"](
        token,
        identity="pulse-user-42",
        room_name="pulse-live-7",
        role="host",
        require_publish=True,
        expect_publish_data=True,
        expect_update_own_metadata=True,
    )
    echoed = json.dumps(result.get("claims") or {}, default=str)
    expect(TEST_ENV["LIVEKIT_API_SECRET"] not in echoed, "the API secret never appears in the echoed claims")
    expect(token not in echoed, "the raw token never appears in the echoed claims")


def main() -> None:
    with env_patch(**TEST_ENV):
        test_viewer_grants_are_least_privilege()
        test_publisher_keeps_both_grants()
        test_explicit_overrides_are_honoured()
        test_verifier_accepts_the_grants_it_asked_for()
        test_verifier_rejects_over_wide_viewer_grants()
        test_verifier_still_catches_tampering()
        test_no_secret_material_leaks_into_claims()
    test_audio_v2_flag_defaults_off()
    test_audio_v2_rollout_controls()
    test_audio_v2_fallback_defaults_on()
    test_audio_trace_requires_master_and_qa_account()
    print("livestream audio token grant contract ok")


if __name__ == "__main__":
    main()
