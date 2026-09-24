"""Stage 19/20 — the environment gates that were set and read by nothing.

**All fourteen were deleted from the production ``CoinPilotX`` service on
2026-09-24** (issue #15). This module is now the record of what was removed and
the rollback for putting it back, not a proposal. See "What was actually done"
below; the fourteen rows and their audited values are kept exactly as they were,
because a rollback that does not carry the original values is not a rollback.

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

Why this module recommended rather than executed
-------------------------------------------------

Deleting a Railway variable is not a local edit. Railway applies variable
changes at container boot, so the risk being taken is a redeploy of the
production service, not the removal of a variable nothing reads. The redeploy
rebuilds from whatever the connected source currently resolves to, which is not
guaranteed to be the commit production is running right now. Fourteen inert
variables are not worth an unplanned production rebuild, and bundling the two
makes the rollback story incoherent: if the redeploy goes wrong, re-adding the
variables does not undo it.

So the catalog was the deliverable, and the deletion was held back as a
separate, single, deliberate act with its own rollback.

What was actually done
-----------------------

That act was taken on 2026-09-24, after — and only after — the tool whose output
authorised it was made trustworthy. Two defects were found in
``scripts/control_plane_audit.py`` first, both of which invented false DEADs, and
both of which would have been invisible in this list:

* its scan scope was an enumeration (``bot.py`` + ``services/`` + root), so a
  registered route pack in a top-level package contributed zero readers;
* it excluded its own package wholesale to avoid vouching for itself, which also
  hid ``runtime.py`` — the one module of the package that reads the environment —
  and so reported the live wave-1 arming switch as dead.

Fixed in PRs #21 and #22. The re-run then matched this list exactly: fourteen
names, no more and no fewer, with **zero value drift** from the audited values
below.

The deletion itself was ordered around one empirical finding:
``railway variable delete`` does **not** trigger a redeploy — verified by
deleting the least consequential name first (``AGORA_OWNER_LIVE_TEST_ENABLED``)
and watching the deployment list not move. So all fourteen were removed against a
running container, and then exactly one restart was taken deliberately
(``railway redeploy``), rather than fourteen restarts taken by accident.

The restart is the evidence, and it is worth being explicit about why: until the
container reboots it is still holding the old environment, so a green production
check before the reboot proves nothing at all. After the reboot the service came
back with 284 variables, ``/health`` 200, the home page 200, the capabilities
endpoint 401, no traceback in 182 lines of boot log, and four
``CONTROL_PLANE_ARMED 10 wave-1 row(s) verified against the registry`` lines —
one per gunicorn worker. The control plane armed without the fourteen.

Rolling back is ``removal_plan()``'s second half, which restores each name to the
value recorded here, plus one redeploy.
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

#: Re-audited against live Railway after the Mission 2 merge. All fourteen still
#: hold their audited values; thirteen have zero mentions anywhere in the tree.
#:
#: The fourteenth is worth writing down because it looked like a reader and was
#: not. ``UNDX_METRICS_ENABLED`` appears exactly once, in the ``EQUIVALENTS``
#: rename map of ``scripts/undx_railway_variable_audit.py`` — an auditor naming a
#: variable, which is the same mistake this package has now met five times.
#:
#: Following it was still worthwhile, because a rename map raises a question a
#: mention count cannot answer: deleting the old name is only safe if the new one
#: carries the same intent. Here it does, but not for the obvious reason. The new
#: name ``UNDX_BRAIN_METRICS_ENABLED`` is **absent from production entirely** —
#: it is on because ``services/undx_brain/config.py:649`` defaults it to ``"1"``,
#: and the dead variable said ``true``. The two agree, so the removal changes
#: nothing. Had that default been ``"0"``, deleting the old name would have
#: quietly ratified a setting production was already ignoring.
REAUDITED_AT = "2026-09-24"
REAUDITED_AGAINST = "Railway service CoinPilotX (297 variables) @ 6a57fd162"

#: When the fourteen were actually removed from production, and from what.
#:
#: Recorded as its own pair of constants rather than by editing ``AUDITED_*``,
#: because an audit and a deletion are different events and a reader needs to be
#: able to tell which one a date refers to. The audit says "these were dead on
#: that tree"; this says "these are gone from that service".
RETIRED_AT = "2026-09-24"
RETIRED_FROM = "Railway service CoinPilotX — 298 variables before, 284 after"

#: The single restart that delivered the removal, and the proof it was survived.
#:
#: Named here because the deployment id is the only durable handle on the boot
#: that tested the change: Railway's log retention drops the lines within days,
#: and after that a claim about what the service did on 2026-09-24 has nothing
#: behind it but this constant.
RETIREMENT_DEPLOYMENT = "b0e86d8a-c7de-403e-8547-9cc8670c0c64"


@dataclass(frozen=True)
class DeadGate:
    """One environment variable that is set in production and read by nothing."""

    name: str
    #: The value the variable held in Railway when it was audited, and
    #: therefore the value ``restore_command`` puts back. Past tense since
    #: :data:`RETIRED_AT`: this is the rollback record, not current state.
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
        """The command that was actually run, in the syntax the CLI accepts.

        This used to read ``railway variables --service CoinPilotX --remove
        NAME``, which is not a command: ``--remove`` is not a flag the Railway
        CLI has, and ``variables`` has no such option. It was never executed, so
        nothing caught it, and it sat in the catalog looking authoritative for
        two missions.

        Worth leaving the note rather than just the fix, because it is the
        failure mode of every recorded-but-unrun command — a rollback nobody has
        tried is a rollback nobody knows is a typo, and it is discovered at the
        moment it is needed.
        """
        return f"railway variable delete {self.name} --service CoinPilotX"

    @property
    def restore_command(self) -> str:
        """Put it back with the value it held when audited.

        ``--skip-deploys`` deliberately: a rollback of fourteen variables should
        cost one restart, taken when the operator chooses, not fourteen taken by
        the CLI. Setting variables *does* trigger a deploy by default — unlike
        deleting them, which was measured not to.
        """
        return (
            f"railway variable set {self.name}={self.production_value} "
            "--service CoinPilotX --skip-deploys"
        )


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
    """What was run on :data:`RETIRED_AT`, and the rollback that undoes it.

    Kept as one block with both halves rather than trimmed to the rollback now
    that the removal is done. The two are only trustworthy together: the restore
    lines are checkable against the remove lines — same fourteen names, each
    carrying the value it is being returned to — and a rollback you cannot check
    against the thing it reverses is a list of commands you have to take on
    faith at the worst possible moment.

    Every value here is a boolean that was read by nothing, so this rollback is
    complete. That is not general. It is safe to write down because none of the
    fourteen is a secret; a retirement record for a credential must not look
    like this one.
    """
    lines = [
        f"# Dead environment gates, audited {AUDITED_AT}",
        f"# {AUDITED_AGAINST}",
        f"# re-audited {REAUDITED_AT}: {REAUDITED_AGAINST} — no value drift",
        "#",
        f"# EXECUTED {RETIRED_AT}. {RETIRED_FROM}.",
        f"# Delivered by deployment {RETIREMENT_DEPLOYMENT}, which came back armed.",
        "# Deleting a variable does not trigger a deploy; setting one does. The",
        "# removal was therefore taken against a running container and followed by",
        "# exactly one deliberate restart, which is the only step that tested it.",
        "",
        "# --- removed ---",
    ]
    lines.extend(g.removal_command for g in DEAD_GATES)
    lines.append("")
    lines.append("# --- rollback (restores the exact audited values, then ONE redeploy) ---")
    lines.extend(g.restore_command for g in DEAD_GATES)
    lines.append("railway redeploy --service CoinPilotX -y")
    return "\n".join(lines)
