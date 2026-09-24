"""Stage 19/20 — the environment gates that are set and read by nothing.

Re-audited 2026-09-24 against the live ``CoinPilotX`` Railway service (297
variables, up from 295) and the source tree at ``e6b61c2de``. Two independent
methods — ``scripts/control_plane_audit.py`` and a plain substring grep of
``bot.py`` and ``services/`` — returned the same fourteen names, unchanged from
Mission 1's finding.

Why a dead switch is worse than no switch
------------------------------------------

An unread variable is not merely clutter. It is a control that answers. Somebody
runs ``railway variables --set ENABLE_SMS=false`` during an incident, the CLI
accepts it, the service redeploys, the variable reads ``false`` in every
dashboard — and SMS keeps sending, because the gate that actually decides is
``BREVO_SMS_ENABLED`` (``services/sms_service.py:25``). The operator has a
plausible, confirmed, and false belief that they have stopped it.

That is why the dispositions below are not uniform. Fourteen dead variables are
fourteen rows of clutter; two of them are dead *kill switches for live
integrations*, and those are a different severity from a stale UNDX toggle.

Why this module recommends rather than executes
------------------------------------------------

Deleting a Railway variable is not a local edit. Railway applies variable
changes at container boot, so a deletion triggers a redeploy of the production
service — and the risk being taken is that redeploy, not the removal of a
variable nothing reads. The redeploy rebuilds from whatever the connected source
currently resolves to, which is not guaranteed to be the commit production is
running right now. Fourteen inert variables are not worth an unplanned
production rebuild, and bundling the two makes the rollback story incoherent:
if the redeploy goes wrong, re-adding the variables does not undo it.

So the catalog is the deliverable, and the deletion is a separate, single,
deliberate act with its own rollback. Each entry carries the exact command.
"""

from __future__ import annotations

from dataclasses import dataclass

#: What to do with a control point that no code reads.
#:
#: ``KEEP`` and ``MIGRATE`` are not decorative. ``KEEP`` is for a variable this
#: repository does not read but something else does — another service, a build
#: step, an operator runbook. ``MIGRATE`` is for one whose *intent* is real and
#: belongs somewhere with a reader. Collapsing all four into DELETE would be
#: fast and would eventually delete something load-bearing.
DISPOSITIONS = ("DELETE", "KEEP", "MIGRATE", "UNKNOWN")

#: Where the audit was taken. Reprinted in any report built from this, because
#: "read by nothing" is a claim about a tree at a moment.
AUDITED_AT = "2026-09-24"
AUDITED_AGAINST = "Railway service CoinPilotX (297 variables) @ e6b61c2de"


@dataclass(frozen=True)
class DeadGate:
    """One environment variable that is set in production and read by nothing."""

    name: str
    production_value: str
    disposition: str
    #: What actually decides this behaviour, or "" when nothing does.
    real_gate: str
    #: Why the disposition is what it is, in enough detail to be re-checked.
    rationale: str
    #: HIGH when an operator could plausibly reach for this believing it works.
    #: LOW when it is inert clutter. The distinction is the point of the module.
    severity: str

    def __post_init__(self) -> None:
        if self.disposition not in DISPOSITIONS:
            raise ValueError(f"unknown disposition {self.disposition!r}")
        if self.severity not in ("HIGH", "LOW"):
            raise ValueError(f"unknown severity {self.severity!r}")
        if self.disposition == "MIGRATE" and not self.real_gate:
            raise ValueError(
                f"{self.name}: MIGRATE must name where the behaviour is going"
            )

    @property
    def removal_command(self) -> str:
        return f"railway variables --service CoinPilotX --remove {self.name}"


DEAD_GATES: tuple[DeadGate, ...] = (
    # ---- the two that would deceive an operator under pressure ----
    DeadGate(
        name="ENABLE_SMS",
        production_value="true",
        disposition="DELETE",
        real_gate="BREVO_SMS_ENABLED (services/sms_service.py:25, notification_service.py:449)",
        rationale=(
            "Reads as the master switch for a live integration and is not read "
            "anywhere. Setting it to false would be accepted, would redeploy, "
            "would display as false, and SMS would keep sending."
        ),
        severity="HIGH",
    ),
    DeadGate(
        name="ENABLE_TELEGRAM",
        production_value="true",
        disposition="DELETE",
        real_gate="presence of TELEGRAM_BOT_TOKEN or BOT_TOKEN (bot.py:461)",
        rationale=(
            "Same shape as ENABLE_SMS. The Telegram integration is gated by the "
            "token being present, so the only way to stop it is to unset the "
            "token — which is not what this variable's name suggests."
        ),
        severity="HIGH",
    ),
    # ---- inert sub-switches of features whose real gate is elsewhere ----
    DeadGate(
        name="TRANSLATION_AUTO_DETECT_ENABLED",
        production_value="true",
        disposition="DELETE",
        real_gate="TRANSLATION_ENABLED (services/content_translation.py)",
        rationale=(
            "One of three translation sub-toggles nobody reads. Auto-detection "
            "is unconditional in the provider layer; this variable has never "
            "been consulted."
        ),
        severity="LOW",
    ),
    DeadGate(
        name="TRANSLATION_GLOSSARY_ENABLED",
        production_value="false",
        disposition="DELETE",
        real_gate="TRANSLATION_ENABLED (services/content_translation.py)",
        rationale=(
            "Set to false, which reads as 'glossary support is switched off'. "
            "There is no glossary support to switch off."
        ),
        severity="LOW",
    ),
    DeadGate(
        name="TRANSLATION_USER_OVERRIDE_ENABLED",
        production_value="true",
        disposition="DELETE",
        real_gate="TRANSLATION_ENABLED (services/content_translation.py)",
        rationale="Third translation sub-toggle with no reader.",
        severity="LOW",
    ),
    DeadGate(
        name="ENABLE_TOOL_SEARCH",
        production_value="true",
        disposition="DELETE",
        real_gate="",
        rationale=(
            "No reader and no corresponding feature. If the intent is real the "
            "fix is a reader, not a retained variable: a switch with no code "
            "behind it cannot be distinguished from one whose code was deleted."
        ),
        severity="LOW",
    ),
    DeadGate(
        name="META_MODEL_API_ENABLED",
        production_value="true",
        disposition="DELETE",
        real_gate="",
        rationale="No reader. Named for a model-routing feature that is not gated on it.",
        severity="LOW",
    ),
    DeadGate(
        name="META_MUSE_FALLBACK_ENABLED",
        production_value="true",
        disposition="DELETE",
        real_gate="",
        rationale="No reader. The router's fallback order is fixed in undx_router.py.",
        severity="LOW",
    ),
    # ---- UNDX toggles ----
    DeadGate(
        name="UNDX_AGENT_COUNCIL_ENABLED",
        production_value="true",
        disposition="DELETE",
        real_gate="",
        rationale="No reader. UNDX_AGENT_ENABLED and UNDX_ROUTER_ENABLED are the live gates.",
        severity="LOW",
    ),
    DeadGate(
        name="UNDX_HTTP_RUNTIME_ENABLED",
        production_value="true",
        disposition="DELETE",
        real_gate="",
        rationale="No reader.",
        severity="LOW",
    ),
    DeadGate(
        name="UNDX_METRICS_ENABLED",
        production_value="true",
        disposition="DELETE",
        real_gate="",
        rationale=(
            "The one name with a match outside tests — "
            "scripts/undx_railway_variable_audit.py, which audits variables "
            "rather than being gated by one. An auditor naming a variable is "
            "not a reader of it, and counting it as one is how a dead gate "
            "stays alive in an inventory forever."
        ),
        severity="LOW",
    ),
    DeadGate(
        name="UNDX_NATIVE_CONTEXT_ENABLED",
        production_value="true",
        disposition="DELETE",
        real_gate="",
        rationale=(
            "No reader. undx_knowledge_map.py has a NATIVE_CONTEXT_REQUIRED "
            "classification and a requires_native_context field, but neither "
            "consults this variable."
        ),
        severity="LOW",
    ),
    DeadGate(
        name="UNDX_NATIVE_CONTEXT_ALLOW_TEXT_ID_OVERRIDE",
        production_value="false",
        disposition="DELETE",
        real_gate="",
        rationale="No reader; paired with the above.",
        severity="LOW",
    ),
    # ---- the realtime one ----
    DeadGate(
        name="AGORA_OWNER_LIVE_TEST_ENABLED",
        production_value="false",
        disposition="DELETE",
        real_gate="",
        rationale=(
            "No reader anywhere in the tree — not in services/, bot.py, "
            "mobile-native/src/, or the realtime audio path. Named in the Agora "
            "domain, which is under a change hard-lock, so it is worth being "
            "explicit: this variable cannot affect the Agora session, because "
            "nothing loads it. Removing it is a Railway change, not a realtime "
            "change, and docs/realtime_audio_change_policy.md does not apply. "
            "It is listed last so that the one name a reviewer will stop on is "
            "the one with the longest justification."
        ),
        severity="LOW",
    ),
)


def by_disposition(disposition: str) -> tuple[DeadGate, ...]:
    return tuple(g for g in DEAD_GATES if g.disposition == disposition)


def deceptive_gates() -> tuple[DeadGate, ...]:
    """The dead switches an operator could plausibly trust. Fix these first."""
    return tuple(g for g in DEAD_GATES if g.severity == "HIGH")


def removal_plan() -> str:
    """The exact commands, in one block, with the rollback beside them.

    Printed rather than executed. Each ``--set`` line restores the audited
    value, so the rollback is complete and does not depend on anybody having
    written the values down — which, for a variable nothing reads, nobody would.
    """
    lines = [
        f"# Dead environment gates, audited {AUDITED_AT}",
        f"# {AUDITED_AGAINST}",
        "#",
        "# Railway applies variable changes at boot, so this triggers ONE redeploy",
        "# of production. Run it as a single deliberate act, not alongside other work.",
        "",
        "# --- remove ---",
    ]
    lines.extend(g.removal_command for g in DEAD_GATES)
    lines.append("")
    lines.append("# --- rollback (restores the exact audited values) ---")
    lines.extend(
        f"railway variables --service CoinPilotX --set {g.name}={g.production_value}"
        for g in DEAD_GATES
    )
    return "\n".join(lines)
