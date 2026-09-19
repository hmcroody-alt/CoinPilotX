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


def is_automated_author(record):
    """Whether a post row or feed payload was written by the system account.

    `pulse_feed_engine._public_author` decides the same thing to render the
    "Official PulseSoc System Account" label. The rule is repeated here rather
    than imported because that module is heavy and this one is a leaf.

    The two are deliberately not identical. `_public_author` requires `user_id
    <= 0` *and* the row carrying no name of its own; this function asks only
    about the id. So this side is the broader of the two, and that is the
    direction the asymmetry has to run: a post the feed badges "Automated"
    while the sitemap offers it to Google as someone's writing is the failure
    that matters, and it cannot happen. The reverse -- excluding a `user_id <=
    0` row that somehow carries a display name -- costs one URL. A test pins
    the containment, not equality.

    Three shapes reach this function: the raw `pulse_posts` row (`user_id`), the
    `get_post` payload (a nested `author` mapping), and a flattened variant that
    carries `account_type` at the top level. Any one of them saying "automated"
    is enough; none of them saying anything means unknown, and unknown is not
    treated as automated.
    """

    r = record or {}
    author = r.get("author")
    if isinstance(author, dict):
        if author.get("automated") or author.get("official_system_account"):
            return True
        if str(author.get("account_type") or "") == AUTOMATED_ACCOUNT_TYPE:
            return True

    if r.get("automated") or r.get("official_system_account"):
        return True
    if str(r.get("account_type") or "") == AUTOMATED_ACCOUNT_TYPE:
        return True

    for key in ("user_id", "author_user_id"):
        if r.get(key) is not None:
            try:
                return int(r[key]) <= 0
            except (TypeError, ValueError):
                return False
    return False
