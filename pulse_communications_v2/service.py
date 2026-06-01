"""No-op service layer for Pulse Communications 2.0.

The methods here define the backend contract for future work. They intentionally
return inactive responses while the feature flag remains disabled.
"""

from __future__ import annotations

from .flags import PULSE_COMMUNICATIONS_V2_ENABLED
from .schemas import ServiceResult


DISABLED_MESSAGE = "Pulse Communications 2.0 is disabled."


def disabled_result(action: str) -> dict:
    return ServiceResult(
        ok=False,
        status="disabled",
        message=DISABLED_MESSAGE,
        data={"action": action, "enabled": bool(PULSE_COMMUNICATIONS_V2_ENABLED)},
    ).to_dict()


def create_conversation(user_id: int, payload=None) -> dict:
    return disabled_result("create_conversation")


def list_conversations(user_id: int, filters=None) -> dict:
    return disabled_result("list_conversations")


def send_message(user_id: int, conversation_id: int, payload=None) -> dict:
    return disabled_result("send_message")


def list_messages(user_id: int, conversation_id: int, filters=None) -> dict:
    return disabled_result("list_messages")


def create_community(user_id: int, payload=None) -> dict:
    return disabled_result("create_community")


def create_channel(user_id: int, community_id: int, payload=None) -> dict:
    return disabled_result("create_channel")
