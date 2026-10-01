"""Narrow output policy for PulseSoc-owned automated accounts."""

import re


AUTOMATED_ACCOUNT_TYPE = "PULSESOC_AUTOMATED"
DISALLOWED_AUTOMATED_PHRASE = re.compile(r"\bhot\s+take\b", re.IGNORECASE)


def normalize_automated_post_type(post_type):
    value = str(post_type or "quick_insight").strip().lower()
    return "quick_insight" if value == "hot_take" else value


def sanitize_automated_text(value):
    """Remove the retired label without applying policy to member content."""
    return DISALLOWED_AUTOMATED_PHRASE.sub("Quick Insight", str(value or ""))
