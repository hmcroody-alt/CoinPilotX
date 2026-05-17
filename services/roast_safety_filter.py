"""Safety filter for Alpha Arena Roast Battle banter."""

BLOCKED_TERMS = {
    "kill yourself",
    "kys",
    "dox",
    "address is",
    "real name",
}


def moderate(text):
    lowered = (text or "").lower()
    if any(term in lowered for term in BLOCKED_TERMS):
        return {
            "ok": False,
            "status": "blocked",
            "message": "Too personal. Keep it clever, not harmful.",
            "safe_rewrite": "Your strategy walked into the spotlight before your confidence did.",
        }
    return {"ok": True, "status": "approved", "message": "Roast approved."}
