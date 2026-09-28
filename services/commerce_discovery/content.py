"""What a piece of social content is *about*, derived on the server.

``ranking.relevance`` has always accepted a context. Until now the only thing
that built one for a post or a reel was the mobile client:
``mobile-native/src/commerce/postContext.ts`` and its sibling ``reelContext.ts``
read the post they were rendering and posted four fields back. This module is
the server-side answer to the same question, and it exists for three reasons
that are worth separating because only one of them is about trust.

It recovers a signal the client cannot see
-----------------------------------------
``postContext.ts`` documents, at its own line 20, that ``CreatePostPayload``
accepts ``tags`` from the author but ``PulsePost`` does not carry them back, so
``normalizePost`` drops them before any caller can read them. The client's
workaround is to scrape hashtags out of prose. But ``pulse_posts.tags_json``
holds exactly those author-declared tags, and ``ai_tags_json`` holds the
classifier's, and both are one column read away from here. So the strongest
topical signal a post has was being thrown away and replaced with a weaker
reconstruction of itself — not because anyone chose that, but because the
derivation lived on the side of the wire the column does not cross.

It is the only way the web can have this at all
-----------------------------------------------
The whole commerce discovery UI is React Native. ``templates/`` and
``static/js/`` contain no commerce discovery of any kind, so "mobile and web
must both work" cannot be satisfied by a rule written in TypeScript. A Jinja
template cannot call ``postCommerceContext``. Deriving here means both clients
read one implementation instead of the web growing a second one that disagrees.

It makes a contextual claim checkable
-------------------------------------
This is the trust half, and ``_anchor_context`` in
``services/commerce_discovery_routes.py`` already made the argument for the
product surface: "a claim of similarity is only true if both sides of the
comparison are ours. A client that sent ``category: 'watches'`` while displaying
a lawnmower would otherwise get a row of watches under the word 'similar'."
Every word of that applies to ``related_to_this_post``. ``product_detail`` reads
its anchor server-side for precisely this reason; the content surfaces did not,
so the one placement label that makes a claim about the surrounding content was
the one taking the client's word for what the surrounding content was.

Why this is not a port
----------------------
The client functions stay. They are not redundant: the client knows which reel
is on screen mid-scroll, and that is genuinely client-side knowledge — see
``reelSlots.ts``. What changes is which side is *authoritative*. A caller that
has the row should derive from the row; the client's context remains the input
for the case where the server has no row to read.

What this deliberately does not do
----------------------------------
No category. A post has none — not at any layer, not under any name — and
inventing one by mapping prose onto the marketplace's seller-supplied category
tree would manufacture the strongest signal ``relevance`` has out of the weakest
evidence available. ``relevance`` saturates its term at 0.85 on a bare category
equality, so a guessed category is not a small lie: it is the largest single
thing this module could get wrong. Subject and tags only, and ``None`` when
there is neither.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Optional

#: Mirrors the wire allowlist in ``_context_from_request`` so a context derived
#: here and one that arrived over HTTP are the same shape and the same size.
#: A server-side derivation that produced a wider field than the route accepts
#: would behave differently depending on which path it came in on.
MAX_FIELD_CHARS = 80
MAX_TAGS = 12
MAX_TAG_CHARS = 40

#: ``ranking._tokens`` discards words of two characters or fewer as stopwords, so
#: a shorter tag cannot contribute to a match. Keeping one would consume a slot
#: a real signal could have used.
MIN_TAG_CHARS = 3

#: Unicode-aware: a hashtag is not ASCII-only and neither is the platform.
_HASHTAG = re.compile(r"#([^\W_][\w]{1,39})", re.UNICODE)


def _text(value: Any, limit: int) -> str:
    if value in (None, ""):
        return ""
    return str(value).strip()[:limit]


def _normalize_tag(raw: Any) -> str:
    """One tag, or ``""`` for anything that cannot be one.

    The leading ``#`` is stripped because the listing side has none: a post
    tagged ``#sneakers`` and a listing tagged ``sneakers`` are one word, and
    keeping the hash makes them two — a silent total miss rather than a weak
    match. Same fold as the client's ``normalizeTag`` for the same reason.
    """
    if raw in (None, ""):
        return ""
    tag = str(raw).strip().lstrip("#").lower()[:MAX_TAG_CHARS]
    return tag if len(tag) >= MIN_TAG_CHARS else ""


def _json_list(raw: Any) -> list[str]:
    """A stored JSON array as a list of strings. Never raises.

    A malformed ``tags_json`` is a row we cannot read, not an error worth
    failing a feed request over — and it must not be treated as *one tag whose
    text is the malformed JSON*, which is what a bare ``str()`` fallback would
    produce. An unparseable value yields nothing.
    """
    if raw in (None, ""):
        return []
    if isinstance(raw, (list, tuple)):
        return [str(item) for item in raw if item not in (None, "")]
    text = str(raw).strip()
    if not text.startswith("["):
        return []
    try:
        parsed = json.loads(text)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if item not in (None, "")]


def hashtags_in(text: Any) -> list[str]:
    """Hashtags out of free text, in order of appearance.

    Kept even though author-declared tags are now available, because the two
    populations barely overlap in practice: the compose screen's tag field is
    optional and the habit is to type hashtags into the body. A post with an
    empty ``tags_json`` and four hashtags in its caption is the common shape.
    """
    found: list[str] = []
    for match in _HASHTAG.finditer(str(text or "")):
        tag = _normalize_tag(match.group(1))
        if tag:
            found.append(tag)
    return found


def subject_of(post: Mapping[str, Any]) -> str:
    """The best single statement of what this content is about.

    Ordered by how deliberate the text is, strongest first. An author-written
    title is a summary; ``ai_summary`` is a machine's summary and so is a
    statement *about* the post rather than a greeting inside it; a body is as
    often "look at this 😍" as it is a description. The client can only reach
    the first and the last of those three.
    """
    for key in ("title", "ai_summary", "body"):
        value = _text(post.get(key), MAX_FIELD_CHARS)
        if value:
            return value
    return ""


def tags_of(post: Mapping[str, Any]) -> list[str]:
    """Up to :data:`MAX_TAGS` tags, deduped, strongest provenance first.

    The order is the point, because the list is truncated. Author-declared tags
    come first: the author chose them to say what the thing is. Hashtags next —
    also the author's words, but typed into prose where they carry a second job
    (reach) that pulls them off-topic. ``ai_tags_json`` last: useful, but a
    classifier's guess should not evict a human's statement when only twelve
    slots exist.
    """
    ordered: list[str] = []
    seen: set[str] = set()
    for source in (
        _json_list(post.get("tags_json")) or _json_list(post.get("tags")),
        hashtags_in(f"{post.get('title') or ''} {post.get('body') or ''}"),
        _json_list(post.get("ai_tags_json")) or _json_list(post.get("ai_tags")),
    ):
        for raw in source:
            tag = _normalize_tag(raw)
            if not tag or tag in seen:
                continue
            seen.add(tag)
            ordered.append(tag)
            if len(ordered) >= MAX_TAGS:
                return ordered
    return ordered


def derive(post: Optional[Mapping[str, Any]]) -> Optional[dict]:
    """The ranking context for one piece of content, or ``None``.

    ``None`` rather than ``{}``, and callers must omit the field rather than
    send an empty one. The distinction is load-bearing in the other direction
    from the obvious one: ``relevance`` answers ``NEUTRAL`` for an absent
    context and ``0.0`` for a context that matches nothing, so a context is a
    two-sided bet. Returning ``{}`` for a post we could not read would file it
    as "measured, matched nothing" when the truth is "not measured" — the same
    score a genuine mismatch earns, for the opposite reason.

    Note what this does **not** decide: whether commerce should appear. A
    context is a description, not a permission. ``suitability.assess`` owns that
    question, and it must be asked even when this returns a rich context —
    especially then, because the posts this module reads most confidently
    include the ones commerce must stay away from.
    """
    if not post:
        return None
    subject = subject_of(post)
    tags = tags_of(post)
    if not subject and not tags:
        return None
    context: dict[str, Any] = {}
    if subject:
        # `topic`, never `category`. See the module docstring: passing the same
        # string as both would double one signal's weight against a listing's
        # real category field, which is a stronger claim than prose supports.
        context["topic"] = subject
    if tags:
        context["tags"] = tags
    return context
