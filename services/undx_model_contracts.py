"""Untrusted model proposals, never an alternative tool authority.

This module does not import an executor, database, HTTP client, or worker. A valid
proposal still needs canonical resolution, ownership, confirmation and readback.
"""
from __future__ import annotations

import ipaddress
import json
import re
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlsplit, urlunsplit


class Role(str, Enum):
    INTENT = "INTENT"
    PLANNER = "PLANNER"
    REASONING = "REASONING"
    RESEARCH = "RESEARCH"
    TOOL_SELECTION = "TOOL_SELECTION"
    CODING = "CODING"
    VISION = "VISION"
    SUMMARIZATION = "SUMMARIZATION"
    CRITIC = "CRITIC"
    VERIFIER = "VERIFIER"
    RECOVERY = "RECOVERY"
    FINAL_RESPONSE = "FINAL_RESPONSE"


class Privacy(str, Enum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    PRIVATE_USER = "PRIVATE_USER"
    PRIVATE_OFFICE = "PRIVATE_OFFICE"
    FINANCIAL = "FINANCIAL"
    SECURITY_SECRET = "SECURITY_SECRET"


class Risk(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ContractError(ValueError):
    """Only fixed reason codes; never embed model data in exceptions."""


@dataclass(frozen=True)
class ModelContext:
    # Created by the authenticated service, NEVER deserialized from client JSON.
    tenant_id: int
    goal_id: str
    role: Role = Role.FINAL_RESPONSE
    privacy: Privacy = Privacy.PRIVATE_USER
    risk: Risk = Risk.LOW
    tier: int = 0
    allowed_capabilities: tuple[str, ...] = ()
    excluded_providers: tuple[str, ...] = ()

    def __post_init__(self):
        if type(self.tenant_id) is not int or self.tenant_id <= 0:
            raise ContractError("tenant_required")
        if not isinstance(self.goal_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", self.goal_id):
            raise ContractError("goal_required")
        if not isinstance(self.role, Role) or not isinstance(self.privacy, Privacy) or not isinstance(self.risk, Risk):
            raise ContractError("invalid_classification")
        if type(self.tier) is not int or not 0 <= self.tier <= 5:
            raise ContractError("invalid_tier")
        if not isinstance(self.allowed_capabilities, tuple) or len(self.allowed_capabilities) > 12:
            raise ContractError("unbounded_tool_scope")
        if any(not isinstance(x, str) for x in self.allowed_capabilities):
            raise ContractError("invalid_tool_scope")


def classify_context(tenant_id: int, goal_id: str, task: str) -> ModelContext:
    """Routing hint only. Cannot downgrade privacy or grant capabilities.

    The conversational prompt contains history/memory, even when its latest
    question is public. Sonar requires a separately scoped public research task.
    Private Office and secret-bearing context have no fabric export path yet.
    """
    text = str(task).lower()
    risk = Risk.HIGH if any(x in text for x in ("security", "cyber", "finance", "payment")) else Risk.LOW
    return ModelContext(tenant_id, goal_id, risk=risk, tier=3 if risk == Risk.HIGH else 0)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ContractError("duplicate_json_key")
        result[key] = value
    return result


def strict_json(raw: str):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 32000:
        raise ContractError("invalid_json_size")
    try:
        return json.loads(raw, object_pairs_hook=_unique_pairs,
                          parse_constant=lambda _: (_ for _ in ()).throw(ContractError("nonfinite_json")))
    except (ValueError, RecursionError, TypeError):
        raise ContractError("invalid_json") from None


PROPOSAL_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "summary": {"type": "string"},
        "proposals": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "properties": {
                "capability_id": {"type": "string"},
                "arguments_json": {"type": "string"},
                "depends_on": {"type": "array", "items": {"type": "integer"}},
            }, "required": ["capability_id", "arguments_json", "depends_on"],
        }},
    }, "required": ["summary", "proposals"],
}


def validate_proposal(raw: str, context: ModelContext) -> dict:
    """Validate a bounded DAG using the ONE canonical capability registry.

    This result is not a ToolCall, confirmation, receipt, or executable plan.
    No raw rationale, confidence or chain-of-thought field is accepted.
    """
    from services import undx_capability_registry as registry
    from services.undx_agent_contracts import validate_arguments, AgentError

    data = strict_json(raw)
    if not isinstance(data, dict) or set(data) != {"summary", "proposals"}:
        raise ContractError("invalid_proposal_shape")
    if not isinstance(data["summary"], str) or len(data["summary"]) > 1000:
        raise ContractError("invalid_summary")
    steps = data["proposals"]
    if not isinstance(steps, list) or len(steps) > 6:
        raise ContractError("step_budget")
    validated = []
    for index, step in enumerate(steps):
        if not isinstance(step, dict) or set(step) != {"capability_id", "arguments_json", "depends_on"}:
            raise ContractError("invalid_step")
        cid = step["capability_id"]
        if not isinstance(cid, str) or cid not in context.allowed_capabilities:
            raise ContractError("tool_outside_scope")
        spec = registry.get(cid)
        if spec is None:
            raise ContractError("unknown_tool")
        if context.role in {Role.VERIFIER, Role.CRITIC, Role.RESEARCH, Role.RECOVERY} and spec.is_write:
            raise ContractError("role_cannot_propose_write")
        args = strict_json(step["arguments_json"])
        if not isinstance(args, dict) or set(args) - {f.name for f in spec.fields}:
            raise ContractError("undeclared_arguments")
        # Canonical validation coerces UI strings; models must produce exact types.
        for f in spec.fields:
            if f.name not in args:
                continue
            value = args[f.name]
            kinds = {"int": (int,), "float": (int, float), "bool": (bool,)}
            if type(value) not in kinds.get(f.kind, (str,)):
                raise ContractError("argument_type")
        try:
            args = validate_arguments(spec.fields, args)
        except AgentError:
            raise ContractError("invalid_arguments") from None
        deps = step["depends_on"]
        if (not isinstance(deps, list) or len(deps) > index
                or any(type(d) is not int or not 0 <= d < index for d in deps)
                or len(set(deps)) != len(deps)):
            raise ContractError("cyclic_or_invalid_dependencies")
        validated.append({"capability_id": cid, "arguments": args, "depends_on": deps})
    return {"summary": data["summary"], "proposals": validated, "authority": "UNTRUSTED_PROPOSAL"}


def normalize_sources(items: list) -> list[dict]:
    """Bounded citation metadata, never fetch URLs or elevate external authority.

    No arbitrary provider snippet/instruction fields are passed along. DNS is not
    resolved because these references are not remotely fetched by this module.
    """
    result, seen = [], set()
    for item in items[:20]:
        if not isinstance(item, dict):
            continue
        raw = item.get("url")
        if not isinstance(raw, str) or len(raw) > 2000:
            continue
        try:
            url = urlsplit(raw)
            host = (url.hostname or "").lower()
            if (url.scheme != "https" or not host or url.username or url.password
                    or url.port not in (None, 443) or "\\" in raw
                    or any(ord(c) < 33 for c in raw)
                    or host.endswith((".local", ".internal")) or "." not in host):
                continue
            try:
                ipaddress.ip_address(host)
                continue  # Do not publish raw IP references, even public IPs.
            except ValueError:
                pass
            if host.replace(".", "").isdigit():
                continue
            # Drop query/fragment; sources cannot smuggle signed links or tokens.
            clean_url = urlunsplit(("https", host, url.path or "/", "", ""))
            if clean_url in seen:
                continue
            seen.add(clean_url)
            title = item.get("title", "")
            result.append({"url": clean_url, "title": title[:200] if isinstance(title, str) else "",
                           "authority": "EXTERNAL_UNVERIFIED", "canonical": False})
        except ValueError:
            continue
    return result
