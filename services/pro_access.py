def normalize_plan(row):
    if not row:
        return "free"
    plan = (row.get("plan") or row.get("subscription_plan") or "free").lower()
    status = (row.get("subscription_status") or "").lower()
    is_pro_flag = int(row.get("is_pro") or 0) == 1
    if is_pro_flag or plan == "pro" or status == "active":
        return "pro"
    return "free"


def is_pro_row(row):
    return normalize_plan(row) == "pro"


def free_limit_text(text, is_pro=False, max_chars=900):
    if is_pro or len(text) <= max_chars:
        return text
    trimmed = text[:max_chars].rsplit("\n", 1)[0].strip() or text[:max_chars].strip()
    return trimmed + "\n\nFree view: shortened summary."
