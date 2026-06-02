"""Inactive permission stubs for Pulse Communications 2.0."""

from __future__ import annotations

from .flags import is_enabled


def can_view_conversation(user_id: int, conversation_id: int, context=None) -> bool:
    return bool(is_enabled() and user_id and conversation_id and context)


def can_send_message(user_id: int, conversation_id: int, context=None) -> bool:
    return bool(is_enabled() and user_id and conversation_id and context)


def can_manage_community(user_id: int, community_id: int, context=None) -> bool:
    return bool(is_enabled() and user_id and community_id and context)


def can_moderate_channel(user_id: int, channel_id: int, context=None) -> bool:
    return bool(is_enabled() and user_id and channel_id and context)
