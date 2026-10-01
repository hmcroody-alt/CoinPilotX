from . import intelligence


def run_ai_assistant(prompt, user=None, context=None):
    user = user or {}
    return intelligence.assistant_response(user.get("user_id") or 0, prompt, bool(user.get("has_pro_access") or user.get("plan") == "pro"))


def ai_unavailable_response(reason=""):
    return {
        "ok": False,
        "message": "AI intelligence is temporarily unavailable. Please try again shortly.",
        "reason": reason,
    }
