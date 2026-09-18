"""One decision about what search engines may index, and why.

Before this module the answer was spread across four places that disagreed:
`seo_engine.robots_txt()` listed a handful of Disallow prefixes, each page
template hard-coded its own `<meta name=robots>`, `seo/content.py`'s
`all_public_paths()` decided what entered the sitemap, and the route bodies
decided who got a 302. Nothing reconciled them, so `/signup` shipped
`noindex,nofollow` *and* sat in sitemap.xml -- we asked Google to crawl a page
we had told it to ignore.

Every surface that needs an indexability answer asks here. The classification
is pure and path-based so it can be tested without a request context, and the
content-level check (`content_eligibility`) is separate because a public path
can still hold a deleted, hidden or moderated record.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
-----------------------------------------
It does not make anything private. `noindex` is a search-visibility
instruction, not an access control -- a `noindex` page is still served to
anyone who requests it. Authorization stays in the route bodies where it
already is. The rule we hold to is the one Google states plainly: robots.txt
must never be the only thing standing between the public and private data.
"""

from __future__ import annotations

from dataclasses import dataclass


CANONICAL_HOST = "pulsesoc.com"
CANONICAL_ORIGIN = f"https://{CANONICAL_HOST}"

# Emitted on pages we want in the index. `max-image-preview:large` is what
# makes a page eligible for a large thumbnail in Discover and image results;
# without it Google defaults to a thumbnail-sized preview.
INDEX_DIRECTIVE = "index,follow,max-image-preview:large,max-snippet:-1,max-video-preview:-1"

# "follow" matters: these pages are crawl paths to content we *do* want
# indexed. Telling Google to ignore the page while still walking its links is
# the difference between excluding a page and amputating a section.
NOINDEX_FOLLOW = "noindex,follow"

# For pages whose outbound links lead nowhere worth crawling, or which sit
# behind an intent we do not want attributed at all.
NOINDEX_NOFOLLOW = "noindex,nofollow"


@dataclass(frozen=True)
class Decision:
    directive: str
    indexable: bool
    sitemap_eligible: bool
    reason: str


def _d(directive, sitemap, reason):
    return Decision(
        directive=directive,
        indexable=directive.startswith("index"),
        sitemap_eligible=sitemap,
        reason=reason,
    )


# Ordered. First match wins, so the more specific prefix must come first.
#
# Every entry carries the reason it exists because the cost of an unexplained
# rule here is a future engineer "fixing" it and silently dropping a section
# out of Google, which takes weeks to notice and weeks more to recover.
_RULES = (
    # --- Never indexable: operational and machine surfaces -----------------
    ("/api/", NOINDEX_NOFOLLOW, "JSON API, not a page"),
    ("/admin", NOINDEX_NOFOLLOW, "administrative surface"),
    ("/webhook", NOINDEX_NOFOLLOW, "machine callback"),
    ("/.well-known/", NOINDEX_NOFOLLOW, "protocol metadata"),
    ("/static/", NOINDEX_NOFOLLOW, "asset path"),

    # --- Authenticated surfaces -------------------------------------------
    # These already redirect anonymous visitors, so Googlebot normally sees a
    # 302 and never reads a meta tag. The rule is kept anyway: a future change
    # that renders a shell to anonymous users must not silently become
    # indexable, and the sitemap gate below reads from the same table.
    ("/dashboard", NOINDEX_NOFOLLOW, "authenticated dashboard"),
    ("/account", NOINDEX_NOFOLLOW, "account settings"),
    ("/settings", NOINDEX_NOFOLLOW, "account settings"),
    ("/notifications", NOINDEX_NOFOLLOW, "personal notifications"),
    ("/saved", NOINDEX_NOFOLLOW, "personal collection"),
    ("/messages", NOINDEX_NOFOLLOW, "private messaging"),
    ("/chat", NOINDEX_NOFOLLOW, "private messaging"),
    ("/pulse/messages", NOINDEX_NOFOLLOW, "private messaging"),
    ("/pulse/settings", NOINDEX_NOFOLLOW, "account settings"),
    ("/pulse/my-posts", NOINDEX_NOFOLLOW, "personal content list"),
    ("/portfolio", NOINDEX_NOFOLLOW, "financial information"),
    ("/checkout", NOINDEX_NOFOLLOW, "commerce workflow"),
    ("/billing", NOINDEX_NOFOLLOW, "commerce workflow"),
    ("/seller", NOINDEX_NOFOLLOW, "seller administration"),
    ("/private-office", NOINDEX_NOFOLLOW, "restricted workspace"),

    # --- Authentication workflow ------------------------------------------
    # noindex but follow: /signup and /login carry the footer, which is a real
    # crawl path into the public site.
    ("/signup", NOINDEX_FOLLOW, "registration workflow state"),
    ("/login", NOINDEX_FOLLOW, "authentication workflow state"),
    ("/logout", NOINDEX_NOFOLLOW, "authentication workflow state"),
    ("/reset-password", NOINDEX_NOFOLLOW, "password reset URL"),
    ("/verify", NOINDEX_NOFOLLOW, "one-time verification URL"),
    ("/oauth", NOINDEX_NOFOLLOW, "OAuth callback"),

    # --- App hand-off -----------------------------------------------------
    # The /open/ interstitial exists to bounce a visitor into the native app.
    # It duplicates the public page it points at, so indexing it would put a
    # blank hand-off screen in front of the content that earned the ranking.
    ("/open/", NOINDEX_NOFOLLOW, "app hand-off interstitial"),

    # --- Internal search --------------------------------------------------
    # Google's guidance is explicit that internal search results should not be
    # indexed; "follow" keeps the result links crawlable.
    ("/search", NOINDEX_FOLLOW, "internal search results"),

    # --- Scaled templated pages -------------------------------------------
    # /markets/<symbol>{,/prediction,/live} and /country-intelligence/<slug>
    # are produced by substituting a name into one shared template. Measured
    # against each other on 2026-09-18 they are 99.2% and 98.9% textually
    # identical, against a 59.6% floor for two genuinely different templates.
    # Google calls this scaled content abuse. They stay reachable for anyone
    # who wants them and stay crawlable for their outbound links, but they no
    # longer ask to be ranked and they no longer enter the sitemap.
    ("/markets/", NOINDEX_FOLLOW, "templated near-duplicate page"),
    ("/country-intelligence/", NOINDEX_FOLLOW, "templated near-duplicate page"),
)


def classify(path):
    """Indexability for a request path. Query strings are not consulted."""

    p = (path or "/").split("?", 1)[0].split("#", 1)[0] or "/"
    if len(p) > 1:
        p = p.rstrip("/") or "/"
    lowered = p.lower()

    for prefix, directive, reason in _RULES:
        if lowered == prefix or lowered.startswith(prefix if prefix.endswith("/") else prefix + "/") or lowered == prefix.rstrip("/"):
            return _d(directive, False, reason)

    return _d(INDEX_DIRECTIVE, True, "public content")


def robots_meta(path):
    """The value for `<meta name="robots">` on a page."""

    return classify(path).directive


def is_indexable(path):
    return classify(path).indexable


def canonical_url(path):
    """Absolute canonical URL on the one host we rank.

    Tracking and app-intent parameters are dropped: `?pulse_app=1` reaches the
    same public page and must not compete with it for the same content.
    """

    p = (path or "/").split("?", 1)[0].split("#", 1)[0] or "/"
    if not p.startswith("/"):
        p = "/" + p
    if len(p) > 1:
        p = p.rstrip("/") or "/"
    return CANONICAL_ORIGIN + p


# Minimum body length for a user post to be worth asking Google to rank. Short
# posts are not bad content, they are simply not search destinations, and a
# sitemap full of them dilutes the crawl budget that the real pages need.
MIN_INDEXABLE_BODY_CHARS = 180


def content_eligibility(record, *, min_body_chars=MIN_INDEXABLE_BODY_CHARS):
    """Whether one piece of user content may be publicly discoverable.

    Returns a `Decision`. Path classification is not enough for user content:
    `/pulse/post/123` is a public path shape, but the record behind it may be
    deleted, private, pending moderation, or may belong to someone who has
    opted out of search.

    `record` is any mapping with the columns we already store. Missing keys are
    treated as their permissive default *except* for the two that gate privacy
    -- visibility and moderation -- which must be explicitly right.
    """

    r = record or {}

    def get(*names, default=None):
        for n in names:
            if n in r and r[n] is not None:
                return r[n]
        return default

    if get("deleted_at") or get("is_deleted"):
        return _d(NOINDEX_NOFOLLOW, False, "deleted")

    if get("takedown_at") or get("is_takedown"):
        return _d(NOINDEX_NOFOLLOW, False, "removed on request")

    visibility = str(get("visibility", default="") or "").lower()
    if visibility != "public":
        return _d(NOINDEX_NOFOLLOW, False, f"visibility is {visibility or 'unset'}")

    moderation = str(get("moderation_status", default="") or "").lower()
    if moderation != "approved":
        return _d(NOINDEX_NOFOLLOW, False, f"moderation is {moderation or 'unset'}")

    if str(get("status", default="published") or "").lower() in {"draft", "pending", "scheduled", "archived"}:
        return _d(NOINDEX_NOFOLLOW, False, "not published")

    # A creator's opt-out is the one signal that outranks everything above it
    # being correct. Making a profile public is not consent to be indexed.
    if _truthy(get("search_opt_out", "noindex", "hide_from_search")):
        return _d(NOINDEX_FOLLOW, False, "creator opted out of search")

    body = str(get("body", "content", "description", default="") or "").strip()
    title = str(get("title", default="") or "").strip()
    if len(body) < min_body_chars and len(title) < 15:
        return _d(NOINDEX_FOLLOW, False, "insufficient original content")

    return _d(INDEX_DIRECTIVE, True, "public content")


def _truthy(value):
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def sitemap_eligible(path, record=None):
    """The single gate every sitemap entry passes through.

    A sitemap is a recommendation, so an entry that is `noindex`, non-canonical
    or not publicly eligible is a self-contradiction rather than a small
    inaccuracy.
    """

    decision = classify(path)
    if not decision.sitemap_eligible:
        return False
    if record is not None and not content_eligibility(record).sitemap_eligible:
        return False
    return True
