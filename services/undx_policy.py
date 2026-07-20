"""Versioned UNDX policy loading and request-scoped context compilation.

The bootstrap pack is server-owned configuration. This module deliberately
selects small policy fragments for each request instead of serializing the
complete YAML into provider prompts.
"""

from __future__ import annotations

import hashlib
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = ROOT / "backend" / "undx" / "config" / "undx_intelligence_bootstrap.yaml"
V2_CONFIG_PATH = ROOT / "backend" / "undx" / "config" / "undx_intelligence_bootstrap_v2.yaml"
V3_CONFIG_PATH = ROOT / "backend" / "undx" / "config" / "undx_intelligence_bootstrap_v3.yaml"
V4_CONFIG_PATH = ROOT / "backend" / "undx" / "config" / "undx_training_v4_nexus_core.yaml"
CONFIG_ENV = "UNDX_INTELLIGENCE_CONFIG_PATH"
CONFIG_VERSION_ENV = "UNDX_CONFIG_VERSION"
V2_ENABLED_ENV = "UNDX_V2_ENABLED"
V2_HASH_ENV = "UNDX_V2_CONFIG_SHA256"
V4_ACTIONS_ENV = "UNDX_V4_ACTIONS"
V4_KILL_SWITCH_ENV = "UNDX_V4_DISABLE_WRITES"
MAX_POLICY_CHARS = 9000

# Conceptual names map to existing authenticated production routes. The model
# never receives credentials and cannot invent routes outside this registry.
PRODUCTION_TOOL_REGISTRY: dict[str, dict[str, Any]] = {
    "pulsesoc.search": {"method": "GET", "route": "/api/pulse/comm/v2/search", "risk": "low", "confirmation": False},
    "pulsesoc.get_profile": {"method": "GET", "route": "/api/pulse/comm/v2/people/search", "risk": "low", "confirmation": False, "canonical_key": "user_id"},
    "pulsesoc.get_conversation": {"method": "GET", "route": "/api/pulse/comm/v2/conversations/<conversation_ref>/messages", "risk": "medium", "confirmation": False, "canonical_key": "conversation_id"},
    "pulsesoc.draft_message": {"method": None, "route": None, "risk": "low", "confirmation": False, "mode": "model_draft_only"},
    "pulsesoc.send_message": {"method": "POST", "route": "/api/pulse/comm/v2/conversations/<conversation_ref>/messages", "risk": "high", "confirmation": True, "canonical_key": "message_id"},
    "pulsesoc.create_post": {"method": "POST", "route": "/api/pulse/posts", "risk": "high", "confirmation": True, "canonical_key": "post_id"},
    "pulsesoc.create_reel": {"method": "POST", "route": "/api/pulse/reels/create", "risk": "high", "confirmation": True, "canonical_key": "reel_id"},
    "pulsesoc.get_alerts": {"method": "GET", "route": "/api/pulse/intelligence/state", "risk": "medium", "confirmation": False, "canonical_key": "alert_id"},
    "pulsesoc.get_crypto_alert": {"method": "GET", "route": "/api/crypto/alerts", "risk": "medium", "confirmation": False, "canonical_key": "alert_definition_id"},
    "pulsesoc.notification_preferences.read": {"method": "GET", "route": "/api/pulse/notifications/preferences", "risk": "read_only", "confirmation": False, "canonical_key": "user_id"},
    "pulsesoc.notification_preferences.update": {"method": "PATCH", "route": "/api/pulse/notifications/preferences", "risk": "medium", "confirmation": True, "canonical_key": "user_id", "verification_route": "/api/pulse/notifications/preferences"},
    "pulsesoc.media.init": {"method": "POST", "route": "/api/messages/media/init", "risk": "medium", "confirmation": False, "canonical_key": "attachment_id"},
    "pulsesoc.media.upload": {"method": "POST", "route": "/api/messages/media/upload", "risk": "medium", "confirmation": False, "canonical_key": "attachment_id"},
    "pulsesoc.media.complete": {"method": "POST", "route": "/api/messages/media/complete", "risk": "medium", "confirmation": False, "canonical_key": "attachment_id"},
    "web.search": {"method": None, "route": "services.pulse_ai_web_search.search", "risk": "medium", "confirmation": False},
    "calculator.execute": {"method": None, "route": "deterministic_server_calculator", "risk": "low", "confirmation": False},
}

DOMAIN_TERMS = {
    "identity_and_profiles": ("profile", "user", "identity", "account", "block", "privacy"),
    "messenger": ("message", "messenger", "conversation", "chat", "voice note", "send ", "tell "),
    "calls": ("call", "calling", "video pulse", "audio pulse"),
    "posts": ("post", "publish", "feed"),
    "reels": ("reel", "short video"),
    "music": ("music", "track", "artist", "song"),
    "alerts": ("alert", "notification", "briefing"),
    "crypto": ("crypto", "bitcoin", "btc", "ethereum", "eth", "portfolio", "price"),
    "media": ("image", "photo", "video", "audio", "document", "pdf", "attachment", "upload"),
}

HIGH_STAKES_TERMS = ("security", "compromised", "hack", "legal", "medical", "transfer", "trade", "delete", "revoke")
CURRENT_TERMS = ("today", "current", "currently", "latest", "now", "price", "breaking")


class UNDXPolicyError(RuntimeError):
    pass


def _config_path() -> Path:
    configured = os.getenv(CONFIG_ENV, "").strip()
    return Path(configured).expanduser().resolve() if configured else DEFAULT_CONFIG_PATH


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=2)
def _load_cached(path_text: str, mtime_ns: int) -> dict[str, Any]:
    path = Path(path_text)
    try:
        parsed = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise UNDXPolicyError(f"UNDX policy unavailable: {exc.__class__.__name__}") from exc
    if not isinstance(parsed, dict):
        raise UNDXPolicyError("UNDX policy root must be a mapping")
    if str(parsed.get("system_name") or "") != "UNDX":
        raise UNDXPolicyError("UNDX policy canonical system_name is invalid")
    gates = (
        (parsed.get("evaluation_framework") or {}).get("release_gates")
        or (parsed.get("evals_v2") or {}).get("release_gates")
        or (parsed.get("evaluation_v3") or {}).get("gates")
        or (parsed.get("evaluation") or {}).get("release_gates")
    )
    if not parsed.get("schema_version") or not gates:
        raise UNDXPolicyError("UNDX policy version or release gates are missing")
    return parsed


def load_policy() -> dict[str, Any]:
    path = active_config_path()
    try:
        stat = path.stat()
    except OSError as exc:
        raise UNDXPolicyError("UNDX policy file is missing") from exc
    return _load_cached(str(path), stat.st_mtime_ns)


def load_policy_version(version: str) -> dict[str, Any]:
    if str(version).startswith("4"):
        path = V4_CONFIG_PATH
    elif str(version).startswith("3"):
        path = V3_CONFIG_PATH
    elif str(version).startswith("2"):
        path = V2_CONFIG_PATH
    else:
        path = _config_path()
    stat = path.stat()
    return _load_cached(str(path), stat.st_mtime_ns)


def v2_status() -> dict[str, Any]:
    raw = V2_CONFIG_PATH.read_bytes()
    actual_hash = hashlib.sha256(raw).hexdigest()
    expected_hash = os.getenv(V2_HASH_ENV, "").strip().lower()
    requested = _truthy(os.getenv(V2_ENABLED_ENV))
    signature_valid = bool(expected_hash) and expected_hash == actual_hash
    return {
        "requested": requested,
        "enabled": requested and signature_valid,
        "signature_valid": signature_valid,
        "sha256": actual_hash,
        "blocker": "" if not requested or signature_valid else "v2 config hash is missing or invalid",
    }


def active_config_path() -> Path:
    selected = os.getenv(CONFIG_VERSION_ENV, "4.0").strip()
    if selected.startswith("1"):
        return _config_path()
    if selected.startswith("2"):
        return V2_CONFIG_PATH if v2_status()["enabled"] else _config_path()
    if selected.startswith("3"):
        return V3_CONFIG_PATH
    return V4_CONFIG_PATH


def policy_metadata() -> dict[str, Any]:
    path = active_config_path()
    raw = path.read_bytes()
    policy = load_policy()
    return {
        "schema_version": str(policy["schema_version"]),
        "pack_version": _pack_version(path),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "path": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else path.name,
        "v2": v2_status(),
        "codename": str(policy.get("codename") or policy.get("system_codename") or ""),
        "v4_actions_enabled": _truthy(os.getenv(V4_ACTIONS_ENV)),
        "v4_writes_disabled": _truthy(os.getenv(V4_KILL_SWITCH_ENV)),
    }


def _pack_version(path: Path) -> str:
    head = path.read_text(encoding="utf-8")[:500]
    match = re.search(r"^#\s*Version:\s*([^\s]+)", head, re.MULTILINE)
    return match.group(1) if match else "unknown"


def _domains(message: str) -> list[str]:
    text = " ".join(str(message or "").lower().split())
    return [name for name, terms in DOMAIN_TERMS.items() if any(term in text for term in terms)]


def _risk_mode(message: str) -> str:
    text = str(message or "").lower()
    if any(term in text for term in HIGH_STAKES_TERMS):
        return "high_stakes"
    if len(text) > 1200 or any(term in text for term in ("research", "architecture", "debug", "analyze")):
        return "deep"
    if len(text) < 80 and not _domains(text):
        return "fast"
    return "standard"


def _render_rules(title: str, values: Any, limit: int = 18) -> str:
    if isinstance(values, dict):
        lines = [f"- {key}: {value}" for key, value in list(values.items())[:limit] if not isinstance(value, (dict, list))]
    else:
        lines = [f"- {value}" for value in list(values or [])[:limit]]
    return title + ":\n" + "\n".join(lines)


def compile_context(message: str, *, include_tools: bool = True) -> dict[str, Any]:
    """Compile bounded server policy for one request; never return the full pack."""
    policy = load_policy()
    domains = _domains(message)
    mode = _risk_mode(message)
    is_v2 = str(policy.get("schema_version") or "").startswith("2")
    if str(policy.get("schema_version") or "").startswith("3"):
        return _compile_v3_context(policy, message, include_tools=include_tools)
    if str(policy.get("schema_version") or "").startswith("4"):
        return _compile_v4_context(policy, message, include_tools=include_tools)
    if is_v2:
        return _compile_v2_context(policy, message, include_tools=include_tools)
    identity = policy["identity"]
    sections = [
        "Canonical identity:\n"
        f"- Name: {identity['canonical_name']}\n"
        f"- Role: {identity['product_role']}\n"
        f"- Statement: {identity['identity_statement'].strip()}",
        _render_rules("Epistemic rules", policy["epistemic_policy"]["answer_rules"]),
        _render_rules("Instruction and injection rules", policy["instruction_hierarchy"]["injection_policy"]),
        _render_rules("Tool rules", policy["tool_policy"]["global_rules"]),
        _render_rules("Response style", policy["response_contract"]["default_style"]),
        _render_rules("Security controls", policy["safety_and_security"]["controls"]),
    ]
    domain_map = policy.get("PulseSOC_domain_map") or {}
    for domain in domains:
        spec = domain_map.get(domain)
        if spec:
            sections.append(_render_rules(f"PulseSOC {domain} invariants", spec.get("invariants") or []))
    if "media" in domains:
        sections.append(_render_rules("Multimodal rules", policy["multimodal_policy"]["rules"]))
    humor = policy["personality"]["humor"]
    sections.append(
        "Tone policy:\n"
        f"- Humor: {humor['frequency']}; never for {', '.join(humor['never_use_for'])}.\n"
        "- Stay warm, direct, and candid about uncertainty."
    )
    if mode == "high_stakes":
        sections.append(str(policy["verification_layer"]["high_impact_rule"]).strip())
    if any(term in str(message or "").lower() for term in CURRENT_TERMS):
        sections.append("Freshness requirement: use an authorized current source or say current data could not be verified.")
    tool_names = _select_tools(domains, message) if include_tools else []
    if tool_names:
        sections.append("Authorized tool registry for this request:\n" + "\n".join(
            f"- {name}: {PRODUCTION_TOOL_REGISTRY[name]}" for name in tool_names
        ))
    compiled = "\n\n".join(section for section in sections if section).strip()
    if len(compiled) > MAX_POLICY_CHARS:
        raise UNDXPolicyError("Compiled policy exceeded its server-side size bound")
    return {
        "system_context": compiled,
        "schema_version": str(policy["schema_version"]),
        "pack_version": _pack_version(active_config_path()),
        "domains": domains,
        "reasoning_mode": mode,
        "tool_names": tool_names,
        "requires_confirmation": any(PRODUCTION_TOOL_REGISTRY[name].get("confirmation") for name in tool_names),
        "compiled_chars": len(compiled),
    }


def _compile_v2_context(policy: dict[str, Any], message: str, *, include_tools: bool) -> dict[str, Any]:
    identity = policy["constitutional_core"]["immutable_identity"]
    mode = _v2_reasoning_mode(message)
    domains = _domains(message)
    sections = [
        "Canonical identity:\n"
        f"- Name: {identity['canonical_name']}\n"
        f"- Role: {identity['public_role']}\n"
        f"- Statement: {identity['identity_sentence'].strip()}",
        _render_rules("Non-negotiable rules", policy["constitutional_core"]["non_negotiable_rules"]),
        _render_rules("Retrieval anti-injection rules", policy["retrieval_v2"]["anti_injection"]),
        _render_rules("Tool write protocol", policy["tool_governance_v2"]["write_protocol"]),
        _render_rules("Self-evaluation before response", policy["self_evaluation"]["phases"]["pre_response"]["checks"]),
        _render_rules("Self-evaluation after draft", policy["self_evaluation"]["phases"]["post_draft"]["checks"]),
    ]
    if mode == "crisis":
        sections.append(_render_rules("Security incident first actions", policy["high_stakes_protocols"]["security_incident"]["first_actions"]))
    if "media" in domains:
        sections.append(_render_rules("Multimodal memory", [policy["multimodal_v2"]["multimodal_memory"]["rule"]]))
    if any(term in str(message or "").lower() for term in CURRENT_TERMS):
        sections.append("Freshness requirement: retrieve a current authoritative source and label inference.")
    tool_names = _select_tools(domains, message) if include_tools else []
    if tool_names:
        sections.append("Authorized existing production tools for this request:\n" + "\n".join(
            f"- {name}: {PRODUCTION_TOOL_REGISTRY[name]}" for name in tool_names
        ))
    compiled = "\n\n".join(sections).strip()
    if len(compiled) > MAX_POLICY_CHARS:
        raise UNDXPolicyError("Compiled v2 policy exceeded its server-side size bound")
    return {
        "system_context": compiled,
        "schema_version": "2.0",
        "pack_version": _pack_version(V2_CONFIG_PATH),
        "domains": domains,
        "reasoning_mode": mode,
        "tool_names": tool_names,
        "requires_confirmation": any(PRODUCTION_TOOL_REGISTRY[name].get("confirmation") for name in tool_names),
        "compiled_chars": len(compiled),
    }


def _v2_reasoning_mode(message: str) -> str:
    text = str(message or "").lower()
    if any(term in text for term in HIGH_STAKES_TERMS):
        return "crisis"
    if any(term in text for term in ("long-term", "strategy", "multi-step", "mission")):
        return "strategic"
    if len(text) > 600 or any(term in text for term in ("research", "architecture", "debug", "analyze")):
        return "deliberate"
    if len(text) < 80 and not _domains(text):
        return "reflex"
    return "rapid"


def _compile_v3_context(policy: dict[str, Any], message: str, *, include_tools: bool) -> dict[str, Any]:
    identity = policy["core_constitution"]["identity"]
    mode = _v3_reasoning_mode(message)
    domains = _domains(message)
    sections = [
        "Canonical identity:\n"
        f"- Name: {identity['canonical_name']}\n"
        f"- Role: {identity['canonical_role']}\n"
        f"- Statement: {identity['public_identity_statement'].strip()}",
        _render_rules("Constitutional principles", policy["core_constitution"]["principles"]),
        _render_rules("Constitutional invariants", policy["core_constitution"]["invariants"]),
        _render_rules("Self-model limitations", policy["self_model"]["limitations"]["examples"]),
        _render_rules("Planning rules", policy["hierarchical_planning"]["planning_rules"]),
        _render_rules("Tool invocation pipeline", policy["tool_system_v3"]["invocation_pipeline"]),
        _render_rules("Tool write safety", policy["tool_system_v3"]["write_safety"]),
        _render_rules("Prompt injection response", policy["security_v3"]["prompt_injection_response"]),
    ]
    if mode in {"deliberate", "strategic", "crisis"}:
        sections.append(_render_rules("Causal reasoning rules", policy["causal_reasoning"]["rules"]))
        sections.append(_render_rules("Verification layers", policy["verification_v3"]["verifier_layers"]))
    if mode in {"strategic", "crisis"}:
        sections.append(_render_rules("Autonomy limits", policy["autonomy_v3"]["never_autonomous"]))
    if "media" in domains:
        sections.append(_render_rules("Attachment security", policy["security_v3"]["attachment_security"]))
    if any(term in str(message or "").lower() for term in CURRENT_TERMS):
        sections.append("Freshness requirement: retrieve current authoritative evidence; never answer unstable facts from memory alone.")
    tool_names = _select_tools(domains, message) if include_tools else []
    if tool_names:
        sections.append("Authorized existing production tools for this request:\n" + "\n".join(
            f"- {name}: {PRODUCTION_TOOL_REGISTRY[name]}" for name in tool_names
        ))
    compiled = "\n\n".join(sections).strip()
    if len(compiled) > MAX_POLICY_CHARS:
        raise UNDXPolicyError("Compiled v3 policy exceeded its server-side size bound")
    return {
        "system_context": compiled,
        "schema_version": "3.0",
        "pack_version": _pack_version(V3_CONFIG_PATH),
        "codename": "SOVEREIGN MIND",
        "domains": domains,
        "reasoning_mode": mode,
        "tool_names": tool_names,
        "requires_confirmation": any(PRODUCTION_TOOL_REGISTRY[name].get("confirmation") for name in tool_names),
        "compiled_chars": len(compiled),
    }


def _v3_reasoning_mode(message: str) -> str:
    text = str(message or "").lower()
    if any(term in text for term in HIGH_STAKES_TERMS):
        return "crisis"
    if any(term in text for term in ("long-term", "strategy", "multi-step", "mission", "roadmap")):
        return "strategic"
    if len(text) > 600 or any(term in text for term in ("research", "architecture", "debug", "analyze", "compare")):
        return "deliberate"
    if len(text) < 80 and not _domains(text):
        return "reflex"
    return "rapid"


def _compile_v4_context(policy: dict[str, Any], message: str, *, include_tools: bool) -> dict[str, Any]:
    """Compile NEXUS CORE policy fragments without serializing the V4 pack."""
    identity = policy["identity"]
    mode = _v4_reasoning_mode(message)
    domains = _domains(message)
    sections = [
        "Canonical identity:\n"
        f"- Your canonical name is {identity['canonical_name']}.\n"
        f"- Role: {identity['canonical_role']}; {identity['operating_role']}.\n"
        f"- Statement: {identity['identity_statement'].strip()}",
        _render_rules("Operational principles", policy["core_principles"]),
        _render_rules("Context authority", policy["UI_context_awareness"]["rules"]),
        _render_rules("Action orchestration", policy["action_orchestration"]["stages"]),
        _render_rules("Tool governance", policy["tool_governance"]["rules"]),
        _render_rules("Retrieved content boundaries", policy["retrieval_v4"]["anti_injection"]),
        _render_rules("Verification rules", [policy["verification"]["critical_rule"]]),
    ]
    if mode in {"deep", "mission", "high_stakes"}:
        sections.append(_render_rules("Mission stop conditions", policy["hierarchical_planning"]["stop_conditions"]))
    if mode in {"mission", "high_stakes"}:
        sections.append(_render_rules("Never autonomous", policy["delegated_autonomy"]["never_autonomous"]))
    if "media" in domains:
        sections.append(_render_rules("Attachment security", policy["security"]["attachments"]))
    tool_names = _select_tools(domains, message) if include_tools else []
    if tool_names:
        sections.append("Authorized existing production tools for this request:\n" + "\n".join(
            f"- {name}: {PRODUCTION_TOOL_REGISTRY[name]}" for name in tool_names
        ))
    compiled = "\n\n".join(sections).strip()
    if len(compiled) > MAX_POLICY_CHARS:
        raise UNDXPolicyError("Compiled v4 policy exceeded its server-side size bound")
    write_tools = [name for name in tool_names if PRODUCTION_TOOL_REGISTRY[name].get("method") in {"POST", "PATCH", "PUT", "DELETE"}]
    writes_enabled = _truthy(os.getenv(V4_ACTIONS_ENV)) and not _truthy(os.getenv(V4_KILL_SWITCH_ENV))
    return {
        "system_context": compiled,
        "schema_version": "4.0",
        "pack_version": _pack_version(V4_CONFIG_PATH),
        "codename": "NEXUS CORE",
        "domains": domains,
        "reasoning_mode": mode,
        "tool_names": tool_names,
        "write_tool_names": write_tools,
        "writes_enabled": writes_enabled,
        "requires_confirmation": any(PRODUCTION_TOOL_REGISTRY[name].get("confirmation") for name in tool_names),
        "compiled_chars": len(compiled),
    }


def _v4_reasoning_mode(message: str) -> str:
    text = str(message or "").lower()
    if any(term in text for term in HIGH_STAKES_TERMS):
        return "high_stakes"
    if any(term in text for term in ("every monday", "schedule", "multi-step", "mission", "then remind", "across")):
        return "mission"
    if len(text) > 600 or any(term in text for term in ("research", "architecture", "debug", "analyze", "compare")):
        return "deep"
    if len(text) < 80 and not _domains(text):
        return "instant"
    return "standard"


def _select_tools(domains: list[str], message: str) -> list[str]:
    text = str(message or "").lower()
    selected: list[str] = []
    if "identity_and_profiles" in domains:
        selected += ["pulsesoc.search", "pulsesoc.get_profile"]
    if "messenger" in domains:
        selected += ["pulsesoc.get_conversation", "pulsesoc.draft_message"]
        is_draft = any(term in text for term in ("draft", "write a message", "compose"))
        if not is_draft and any(term in text for term in ("send", "message ", "tell ")):
            selected.append("pulsesoc.send_message")
    if "posts" in domains and any(term in text for term in ("post", "publish")):
        selected.append("pulsesoc.create_post")
    if "reels" in domains and any(term in text for term in ("create", "publish", "post")):
        selected.append("pulsesoc.create_reel")
    if "alerts" in domains:
        selected.append("pulsesoc.get_alerts")
        if "notification" in text:
            selected.append("pulsesoc.notification_preferences.read")
            if any(term in text for term in ("turn on", "turn off", "enable", "disable")):
                selected.append("pulsesoc.notification_preferences.update")
    if "crypto" in domains:
        selected.append("pulsesoc.get_crypto_alert")
    if "media" in domains:
        selected += ["pulsesoc.media.init", "pulsesoc.media.upload", "pulsesoc.media.complete"]
    if any(term in text for term in CURRENT_TERMS):
        selected.append("web.search")
    return list(dict.fromkeys(name for name in selected if name in PRODUCTION_TOOL_REGISTRY))
