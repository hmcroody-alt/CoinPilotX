"""Read-only: how much commerce does the suitability gate withhold in production?

Writes nothing. Every statement is a ``SELECT`` on a session opened
``readonly=True``, so the guard in
``tests/protection/test_fixture_audits_cannot_reach_production.py`` has nothing
to catch here — and neither does a mistake.

## The question

`services/commerce_discovery/suitability.py` has two entry points that answer
"may commerce sit beside this content", and they see different amounts of it:

``assess_context(context)``
    The **wire** check. Judges the client's ~80-character summary of the post.
    Only ``SENSITIVE_CONTEXT`` refuses here, because a client-supplied string is
    weak evidence.

``assess_adjacency(row)``
    The **row** check. Reads ``pulse_posts`` — and on ``reels`` the joined
    ``pulse_reels`` row too — so it sees the full body, the declared category,
    ``moderation_status`` and ``risk_score``.

Until this branch the route ran only the first of those, which meant a
bereavement announced in the 90th character of a post body reached ranking as a
post about nothing in particular. This script measures the difference: what the
wire check caught as shipped, what the row check catches, and how much of that
is the same content counted twice.

The interesting output is the **overlap**. A large overlap would mean the row
read is mostly ceremony; a zero overlap means the shipped check and the new one
were never looking at the same posts, which is the finding the report rests on.

## Why the mobile caps are parsed out of the TypeScript

The wire context is built on the device, by ``postCommerceContext`` and
``reelCommerceContext``. To measure what the wire check *saw*, this script has to
reproduce that truncation — and a Python copy of a TypeScript constant is
precisely the client/server divergence the gate was wired wrong by in the first
place. So the four caps are read out of the ``.ts`` sources at runtime and the
run aborts if they cannot be found. A mobile retune then either shows up in
these numbers or stops the script; it cannot silently invalidate them.

The two column projections are imported from ``commerce_discovery_routes`` for
the same reason: this script must read what the route reads, not a list that
looked the same on the day it was written.

## What it still cannot tell you

Whether the withheld cards *should* have been withheld. That is a judgement about
195 ``needs_review`` posts and 12 high-risk ones, and it needs a human reading
them, not a counter. The numbers here bound the blast radius; they do not
validate it.

Usage (from the repo root, with a production DSN in the environment)::

    railway run --service Postgres python3 scripts/measure_commerce_suitability_cost.py
"""
from __future__ import annotations

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import psycopg2

from services.commerce_discovery import suitability
from services.commerce_discovery_routes import _POST_COLUMNS, _REEL_COLUMNS

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOBILE = os.path.join(REPO, "mobile-native", "src", "commerce")

# Mirrors `postContext.ts` / `reelContext.ts`'s HASHTAG. Python's `re` has no
# `\p{L}`; `\w` under `re.UNICODE` is the closest equivalent and differs only in
# also admitting a digit as the first character, which cannot change a refusal.
HASHTAG = re.compile(r"#(\w{2,40})", re.UNICODE)


def _caps(filename: str) -> dict:
    """The four wire caps as the device actually has them, or exit nonzero.

    Failing loudly rather than falling back to remembered values: a silent
    default here would be a second copy of the constant, which is the thing this
    function exists to avoid having.
    """
    path = os.path.join(MOBILE, filename)
    try:
        with open(path, encoding="utf-8") as handle:
            source = handle.read()
    except OSError as error:
        sys.exit(f"cannot read {path}: {error}")
    found = {}
    for name in ("MAX_FIELD_CHARS", "MAX_TAGS", "MAX_TAG_CHARS", "MIN_TAG_CHARS"):
        match = re.search(rf"^const {name} = (\d+);$", source, re.MULTILINE)
        if not match:
            sys.exit(
                f"{filename} no longer declares `const {name} = <n>;`.\n"
                "The wire truncation this script reproduces has moved or been "
                "renamed, so every number below would be measuring a shape the "
                "device does not send. Re-read the file and update the mirror "
                "below rather than restoring a hardcoded default."
            )
        found[name] = int(match.group(1))
    return found


def _trimmed(value, limit: int) -> str:
    """`trimmed` from both context modules: ends only, no whitespace collapsing.

    Worth being exact about. Collapsing runs of whitespace — the obvious Python
    idiom — would pack more words into the first 80 characters than the device
    does, and so would credit the wire check with text it never received.
    """
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def _normalize_tag(raw, caps: dict) -> str:
    if not isinstance(raw, str):
        return ""
    tag = raw.strip().lstrip("#").lower()[: caps["MAX_TAG_CHARS"]]
    return tag if len(tag) >= caps["MIN_TAG_CHARS"] else ""


def _hashtags_in(text: str, caps: dict) -> list:
    return [t for t in (_normalize_tag(m.group(1), caps) for m in HASHTAG.finditer(text or "")) if t]


def _json_list(raw) -> list:
    try:
        parsed = json.loads(raw or "[]")
    except Exception:
        return []
    return [item for item in parsed if isinstance(item, str)] if isinstance(parsed, list) else []


def post_wire_context(post: dict, caps: dict):
    """`postCommerceContext(post)`.

    Note what is *not* here: ``tags_json``, ``ai_tags_json`` and ``ai_summary``.
    ``PulsePost`` does not carry them — ``normalizePost`` builds its result field
    by field and drops anything else — so the only tags a post can put on the
    wire are hashtags scraped out of its own prose. Including the stored tag
    arrays would overstate what the shipped check could see, which is the error
    this script is measuring.
    """
    topic = _trimmed(post.get("title"), caps["MAX_FIELD_CHARS"]) or _trimmed(
        post.get("body"), caps["MAX_FIELD_CHARS"])
    tags, seen = [], set()
    for tag in _hashtags_in(f"{post.get('title') or ''} {post.get('body') or ''}", caps):
        if tag in seen or len(tags) >= caps["MAX_TAGS"]:
            continue
        seen.add(tag)
        tags.append(tag)
    if not topic and not tags:
        return None
    context = {}
    if topic:
        context["topic"] = topic
    if tags:
        context["tags"] = tags
    return context


def reel_wire_context(reel: dict, caps: dict):
    """`reelCommerceContext(reel)` over `pulse_reel_payload`'s merged shape.

    The merge matters: the payload sets ``caption = reel.caption or post.body``
    and has no ``title``, so ``topic`` is the caption in practice. ``ai_tags``
    comes from the reel's ``ai_tags_json``.
    """
    category = _trimmed(reel.get("category"), caps["MAX_FIELD_CHARS"])
    topic = (_trimmed(reel.get("title"), caps["MAX_FIELD_CHARS"])
             or _trimmed(reel.get("caption"), caps["MAX_FIELD_CHARS"])
             or _trimmed(reel.get("body"), caps["MAX_FIELD_CHARS"]))
    tags, seen = [], set()

    def push(candidate: str) -> None:
        if not candidate or candidate in seen or len(tags) >= caps["MAX_TAGS"]:
            return
        seen.add(candidate)
        tags.append(candidate)

    for raw in _json_list(reel.get("ai_tags_json")):
        push(_normalize_tag(raw, caps))
    for tag in _hashtags_in(f"{reel.get('caption') or ''} {reel.get('title') or ''}", caps):
        push(tag)
    if not category and not topic and not tags:
        return None
    context = {}
    if category:
        context["category"] = category
    if topic:
        context["topic"] = topic
    if tags:
        context["tags"] = tags
    return context


def _wire_refuses(context) -> bool:
    """Only `SENSITIVE_CONTEXT`, matching `assess_context`'s one refusing code."""
    return suitability.assess_context(context)["code"] == suitability.SENSITIVE_CONTEXT


def _bucket(counts: dict, key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


def measure_posts(cur, caps: dict) -> None:
    cur.execute("SELECT " + ", ".join(_POST_COLUMNS) +
                " FROM pulse_posts WHERE deleted_at IS NULL")
    rows = [dict(zip(_POST_COLUMNS, r)) for r in cur.fetchall()]
    print(f"\npost_detail / feed  —  {len(rows)} live pulse_posts")

    wire = row = both = contextless = contextless_permitted = 0
    codes: dict = {}
    for post in rows:
        context = post_wire_context(post, caps)
        refused_on_wire = _wire_refuses(context)
        verdict = suitability.assess_adjacency(post)
        if refused_on_wire:
            wire += 1
        if not verdict["permitted"]:
            row += 1
            _bucket(codes, verdict["code"])
            if refused_on_wire:
                both += 1
        if context is None:
            contextless += 1
            if verdict["permitted"]:
                contextless_permitted += 1

    print(f"  refused by the WIRE check as shipped : {wire}")
    print(f"  refused once the ROW is read         : {row}  {codes}")
    print(f"  both                                 : {both}")
    print(f"  no wire context at all               : {contextless}"
          f"  (of which row-permitted: {contextless_permitted})")
    if rows:
        print(f"  => the row read withholds a card from {row} of {len(rows)} live posts"
              f" ({100.0 * row / len(rows):.1f}%)")
    # The contextless figure is the case for `assess_adjacency` over `assess`:
    # `assess` adds NO_SUBJECT, so every row-permitted contextless post above
    # would lose its card to a rule about our own missing metadata.
    if contextless_permitted == contextless and contextless:
        print(f"  => `assess` would additionally refuse all {contextless} of them"
              " for NO_SUBJECT, none of which the row objects to")


def measure_reels(cur, caps: dict) -> None:
    """The reels surface, over the population that can actually render.

    Three queries rather than one join, for a reason worth stating: both tables
    have a ``moderation_status``, so a single joined ``SELECT`` would have to
    qualify ``_REEL_COLUMNS``' expressions with a table alias — rewriting the
    route's own SQL in order to measure it. Reading each table on its own lets
    both projections be used verbatim, which is the only way this script can
    claim to be reading what the route reads.
    """
    reel_keys = tuple(key for key, _ in _REEL_COLUMNS)
    cur.execute("SELECT COUNT(*) FROM pulse_reels")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM pulse_reels"
                " WHERE COALESCE(status,'active')!='deleted'")
    alive = cur.fetchone()[0]

    # `pulse_reel_payload` goes through `pulse_feed_engine.get_post`, which
    # filters `p.deleted_at IS NULL` and returns None — so a reel hanging off a
    # tombstoned post serves nothing today, with or without this gate. Counting
    # those as "commerce withheld" would inflate the blast radius by an order of
    # magnitude: in the measured population it was 48 of 61.
    cur.execute("SELECT r.post_id FROM pulse_reels r"
                " JOIN pulse_posts p ON p.id=r.post_id"
                " WHERE COALESCE(r.status,'active')!='deleted'"
                " AND p.deleted_at IS NULL")
    live_ids = [r[0] for r in cur.fetchall()]

    post_keys = _POST_COLUMNS
    posts = {}
    if live_ids:
        cur.execute("SELECT id, " + ", ".join(post_keys) +
                    " FROM pulse_posts WHERE id = ANY(%s)", (live_ids,))
        posts = {r[0]: dict(zip(post_keys, r[1:])) for r in cur.fetchall()}

    reels = {}
    if live_ids:
        # `_REEL_COLUMNS`' expressions verbatim, plus `post_id` to match on.
        # `caption` is projected raw by that tuple, which the differs-from-body
        # census below relies on — a COALESCE to the body would make every reel
        # look like it agreed with its post.
        cur.execute("SELECT post_id, " + ", ".join(expr for _, expr in _REEL_COLUMNS) +
                    " FROM pulse_reels WHERE post_id = ANY(%s)"
                    " AND COALESCE(status,'active')!='deleted'", (live_ids,))
        reels = {r[0]: dict(zip(reel_keys, r[1:])) for r in cur.fetchall()}

    pairs = [(reels[post_id], posts[post_id]) for post_id in live_ids
             if post_id in reels and post_id in posts]

    print(f"\nreels  —  {total} pulse_reels, {alive} not deleted,"
          f" {len(pairs)} joined to a live post")
    print(f"           {alive - len(pairs)} hang off a tombstoned post and render nothing")

    wire = by_post = by_reel = 0
    codes: dict = {}
    differs = captioned = 0
    for reel, post in pairs:
        # The caption census is counted first and unconditionally. It is a fact
        # about the data, not about the verdict — counting it below the
        # short-circuit would silently exclude every reel whose post row refused
        # and understate how often a caption carries text of its own.
        caption = (reel.get("caption") or "").strip()
        if caption:
            captioned += 1
            if caption != (post.get("body") or "").strip():
                differs += 1

        # `pulse_reel_payload`'s merge, which is what the device receives and
        # therefore what `reelCommerceContext` had to work with.
        merged = dict(reel)
        merged["caption"] = reel.get("caption") or post.get("body") or ""
        if _wire_refuses(reel_wire_context(merged, caps)):
            wire += 1

        post_verdict = suitability.assess_adjacency(post)
        if not post_verdict["permitted"]:
            by_post += 1
            _bucket(codes, f"post:{post_verdict['code']}")
            # The route returns the post's refusal without reading the reel, so
            # attributing anything further to the reel read would be inventing a
            # query that never runs.
            continue
        reel_verdict = suitability.assess_adjacency(reel)
        if not reel_verdict["permitted"]:
            by_reel += 1
            _bucket(codes, f"reel:{reel_verdict['code']}")

    print(f"  refused by the WIRE check as shipped : {wire}")
    print(f"  refused by the POST row              : {by_post}")
    print(f"  refused by the REEL row (the new read): {by_reel}  {codes}")
    print(f"  captioned                            : {captioned}"
          f"  (of which the caption differs from the post body: {differs})")
    if captioned and not differs:
        # Recorded rather than hidden: the caption read is a structural fix, not
        # a measured win. It becomes load-bearing the moment any edit path
        # writes a caption without rewriting the post body.
        print("  => every caption currently equals its post body, so this read"
              " changes no verdict today")


def main() -> int:
    # Caps first, before the DSN is even read. Two reasons: a mirror that has
    # drifted makes every number below meaningless, so there is no point opening
    # a connection; and it means `--check-caps` can verify the abort offline,
    # which is how this script's one silent-failure mode gets tested at all.
    post_caps = _caps("postContext.ts")
    reel_caps = _caps("reelContext.ts")
    print(f"wire caps: postContext {post_caps}")
    print(f"           reelContext {reel_caps}")
    if "--check-caps" in sys.argv:
        print("caps mirror intact; not connecting")
        return 0

    dsn = os.environ.get("DATABASE_PUBLIC_URL") or os.environ.get("DATABASE_URL")
    if not dsn:
        sys.exit("set DATABASE_PUBLIC_URL (or DATABASE_URL) to the database to measure")
    conn = psycopg2.connect(dsn)
    # Belt and braces over "every statement below is a SELECT": the server
    # refuses a write on this session even if a later edit adds one.
    conn.set_session(readonly=True, autocommit=True)
    try:
        cur = conn.cursor()
        measure_posts(cur, post_caps)
        measure_reels(cur, reel_caps)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
