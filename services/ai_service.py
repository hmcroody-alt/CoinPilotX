from . import intelligence


def run_ai_assistant(prompt, user=None, context=None, call_domain=None):
    """Thin wrapper over `intelligence.assistant_response`. Currently uncalled.

    Kept and migrated rather than deleted: nothing in this repository calls it, but it
    is a public name in a package whose modules are imported by name all over `bot.py`,
    and removing a public function is a bigger decision than the one this phase is
    making. The relevant hazard is the opposite of dead code — an unrouted call site
    that nobody exercises is exactly the one that survives a migration, so it is
    counted in the census and routed with the rest.

    `call_domain` is forwarded rather than declared, because a wrapper with no callers
    has no provenance to declare. It defaults to GENERAL inside the router.
    """
    user = user or {}
    return intelligence.assistant_response(
        user.get("user_id") or 0,
        prompt,
        bool(user.get("has_pro_access") or user.get("plan") == "pro"),
        call_domain=call_domain,
    )


def ai_unavailable_response(reason=""):
    return {
        "ok": False,
        "message": "AI intelligence is temporarily unavailable. Please try again shortly.",
        "reason": reason,
    }
