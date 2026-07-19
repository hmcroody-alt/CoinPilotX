#!/usr/bin/env python3
"""Audit the canonical UNDX backend identity pipeline.

Legacy database tables and /api/pulse-ai routes are allowed for compatibility.
User-facing assistant identity must be UNDX and native sends must target the
canonical UNDX agent metadata.
"""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def require(condition: bool, message: str, failures: list[str]) -> None:
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    service = read("services/pulse_ai_service.py")
    knowledge = read("services/pulse_ai_knowledge.py")
    provider = read("services/pulse_ai_provider_router.py")
    native = read("mobile-native/src/api/messenger.ts")
    routes = read("pulse_communications_v2/routes.py")

    require('UNDX_DISPLAY_NAME = "UNDX"' in service, "Backend lacks canonical UNDX display name.", failures)
    require('UNDX_AGENT_ID = "undx"' in service, "Backend lacks canonical UNDX agent id.", failures)
    require('UNDX_ASSISTANT_ID = "undx"' in service, "Backend lacks canonical UNDX assistant id.", failures)
    require('UNDX_CONVERSATION_TYPE = "undx_intelligence"' in service, "Backend lacks canonical UNDX conversation type.", failures)
    require("UNDX_IDENTITY_REPLY" in service and "PulseSOC’s AGI-class" in service, "Backend lacks server-side UNDX identity response.", failures)
    require("_enforce_undx_reply_identity" in service, "Backend does not enforce UNDX reply identity after provider output.", failures)
    require("provider boundary prepends and verifies" in service, "Service does not delegate identity enforcement to provider boundary.", failures)
    require('"title": UNDX_DISPLAY_NAME' in service, "Conversation payload title is not UNDX-owned.", failures)
    require('"sender_display_name": "You" if mine else UNDX_DISPLAY_NAME' in service, "Message payload sender identity is not UNDX-owned.", failures)
    require("UNDX_UNAVAILABLE_MESSAGE" in service, "Backend does not use UNDX unavailable fallback.", failures)
    require('"assistant": UNDX_DISPLAY_NAME' in service, "Status endpoint does not expose UNDX assistant identity.", failures)

    forbidden_service_identity = [
        '"title": "Pulse AI"',
        '"display_name": "Pulse AI"',
        '"sender_display_name": "You" if mine else "Pulse AI"',
        '"assistant": "Pulse AI"',
        "Ask Pulse AI something first.",
        "Pulse AI is receiving too many messages.",
        "Pulse AI is temporarily unavailable.",
        "Pulse AI feedback learning is disabled",
        "Thanks. Pulse AI will use",
    ]
    for text in forbidden_service_identity:
        require(text not in service, f"Backend still has user-facing Pulse AI identity: {text}", failures)

    require('ASSISTANT_NAME = "UNDX"' in knowledge, "Prompt builder assistant name is not UNDX.", failures)
    require("You are UNDX" in knowledge, "Core system prompt does not identify as UNDX.", failures)
    require("Never identify yourself as Pulse AI" in knowledge, "Core system prompt lacks legacy identity refusal.", failures)
    require("Pulse AI is temporarily unavailable" not in provider, "Provider fallback still leaks Pulse AI identity.", failures)
    require("UNDX is temporarily unavailable" in provider, "Provider fallback does not use UNDX identity.", failures)
    require("UNDX_IDENTITY_BLOCK" in provider and "Your canonical name is UNDX" in provider, "Provider boundary lacks canonical identity block.", failures)
    require("prepare_undx_model_request" in provider and "assert UNDX_IDENTITY_REQUIRED_PHRASE in final_system_context" in provider, "Provider boundary lacks fail-closed identity invariant.", failures)
    require("identity_configuration_error" in provider, "Provider boundary lacks safe identity configuration error.", failures)
    require("undx_identity_violation" in provider and "UNDX_IDENTITY_RESPONSE_REJECTED" in provider, "Provider boundary lacks response rejection and regeneration.", failures)

    require("/api/pulse-ai/conversation" in routes, "Production conversation route was removed.", failures)
    require("/api/pulse-ai/message" in routes, "Production message route was removed.", failures)
    require("pulse_ai_service.get_conversation" in routes, "Production conversation route no longer reuses service.", failures)
    require("pulse_ai_service.send_message" in routes, "Production message route no longer reuses service.", failures)

    require('PULSE_AI_AGENT_ID = "undx"' in native, "Native request path lacks UNDX agent id constant.", failures)
    require('PULSE_AI_ASSISTANT_ID = "undx"' in native, "Native request path lacks UNDX assistant id constant.", failures)
    require('PULSE_AI_CONVERSATION_TYPE = "undx_intelligence"' in native, "Native request path lacks UNDX conversation type constant.", failures)
    require("agent_id: PULSE_AI_AGENT_ID" in native, "Native send body does not pass UNDX agent id.", failures)
    require("assistant_id: PULSE_AI_ASSISTANT_ID" in native, "Native send body does not pass UNDX assistant id.", failures)
    require("conversation_type: PULSE_AI_CONVERSATION_TYPE" in native, "Native send body does not pass UNDX conversation type.", failures)

    if failures:
        print("PulseSoc UNDX identity backend audit FAILED")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("PulseSoc UNDX identity backend audit passed")
    print("- canonical name: UNDX")
    print("- canonical participant id: -9001001")
    print("- canonical agent id: undx")
    print("- production routes preserved: /api/pulse-ai/conversation, /api/pulse-ai/message")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
