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
from urllib.parse import parse_qsl, urlencode

from .pulse_ai.content_policy import is_automated_author


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
    # `/admin-dashboard` is a *sibling* of `/admin`, not a child, so the rule
    # above never classified it -- `classify` matches path segments. It was
    # nevertheless uncrawlable, because robots.txt matched `Disallow: /admin`
    # as a raw string prefix. Naming it here is what makes the two channels
    # agree deliberately instead of by accident; `robots_disallow_patterns`
    # explains why that accident had to end.
    ("/admin-dashboard", NOINDEX_NOFOLLOW, "administrative surface"),
    # Both spellings are live: `/webhook/stripe` and `/webhooks/stripe` are
    # separate routes. `/webhooks/` is a sibling of `/webhook`, so only the
    # raw-prefix accident was covering it -- and `GET /webhooks/stripe` answers
    # 200 with no robots meta of its own. That makes this the one entry in this
    # block whose absence was a live indexability hole rather than untidiness.
    ("/webhook", NOINDEX_NOFOLLOW, "machine callback"),
    ("/webhooks/", NOINDEX_NOFOLLOW, "machine callback"),
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
    # Two versioned siblings of the messaging surface, both outside the segment
    # match above. `/pulse/messages-v2` 302s anonymous traffic to
    # `/login?next=/pulse/messages-v2`, which mints one more crawlable
    # `?next=` URL for the login wall -- the trap this module should be
    # shrinking, not feeding. `/pulse/messages-legacy` 301s onto the blocked
    # parent, so it is harmless on its own and declared for symmetry.
    ("/pulse/messages-v2", NOINDEX_NOFOLLOW, "private messaging"),
    ("/pulse/messages-legacy", NOINDEX_NOFOLLOW, "private messaging"),
    ("/pulse/settings", NOINDEX_NOFOLLOW, "account settings"),
    ("/pulse/my-posts", NOINDEX_NOFOLLOW, "personal content list"),
    ("/portfolio", NOINDEX_NOFOLLOW, "financial information"),
    ("/checkout", NOINDEX_NOFOLLOW, "commerce workflow"),
    ("/billing", NOINDEX_NOFOLLOW, "commerce workflow"),
    ("/seller", NOINDEX_NOFOLLOW, "seller administration"),
    # The two marketplace paths that are still authenticated. Declared for the
    # reason stated above this block: they must not inherit indexability from
    # the product pages they sit next to, which are now public.
    #
    # Ordering matters here. `_RULES` is first-match-wins, and both of these are
    # longer than any marketplace prefix in this table, so neither can be
    # shadowed. `/pulse/marketplace/create` is the seller's listing composer and
    # `/pulse/merchant/<username>` is a storefront that still calls
    # `require_account()` -- a URL we cannot fetch is a URL we must not
    # recommend.
    ("/pulse/marketplace/create", NOINDEX_NOFOLLOW, "seller listing composer, authenticated"),
    ("/pulse/merchant", NOINDEX_NOFOLLOW, "seller storefront still behind require_account"),
    ("/private-office", NOINDEX_NOFOLLOW, "restricted workspace"),

    # --- Authentication workflow ------------------------------------------
    # noindex but follow: /signup and /login carry the footer, which is a real
    # crawl path into the public site.
    ("/signup", NOINDEX_FOLLOW, "registration workflow state"),
    ("/login", NOINDEX_FOLLOW, "authentication workflow state"),
    ("/logout", NOINDEX_NOFOLLOW, "authentication workflow state"),
    ("/reset-password", NOINDEX_NOFOLLOW, "password reset URL"),
    ("/verify", NOINDEX_NOFOLLOW, "one-time verification URL"),
    # `/verify-email` is a sibling of `/verify` and falls squarely inside that
    # rule's stated intent. Its template already hardcodes `noindex, nofollow`,
    # so this changes nothing a crawler sees -- it moves the statement into the
    # table that the sitemap gate and robots.txt both read, instead of leaving
    # it in one Jinja file where neither can see it.
    ("/verify-email", NOINDEX_NOFOLLOW, "one-time verification URL"),
    ("/oauth", NOINDEX_NOFOLLOW, "OAuth callback"),
    # The two account-recovery forms, siblings of `/reset-password` above and
    # declared for the same reason. Both answer an anonymous `200` with a real
    # form, so unlike the redirect cases further down they are URLs Googlebot
    # can and does fetch -- and before this entry the table called them
    # `index,follow` *and sitemap-eligible*, because no rule named them and the
    # fallthrough is "public content". Their own templates say
    # `noindex, nofollow`, which is the only reason they are not in the index.
    #
    # `nofollow` rather than the `follow` that `/login` and `/signup` carry:
    # that pair is reachable from the public site and carries the footer back
    # into it, so blocking them would cost a real crawl path. These two are
    # reached only from the login form itself -- nothing public links to
    # either -- so there is no crawl path to preserve and no reason to keep
    # fetching a dead-end form.
    ("/forgot-password", NOINDEX_NOFOLLOW, "account recovery form"),
    ("/forgot-username", NOINDEX_NOFOLLOW, "account recovery form"),

    # --- App hand-off -----------------------------------------------------
    # The /open/ interstitial exists to bounce a visitor into the native app.
    # It duplicates the public page it points at, so indexing it would put a
    # blank hand-off screen in front of the content that earned the ranking.
    ("/open/", NOINDEX_NOFOLLOW, "app hand-off interstitial"),

    # --- Internal search --------------------------------------------------
    # Google's guidance is explicit that internal search results should not be
    # indexed; "follow" keeps the result links crawlable.
    ("/search", NOINDEX_FOLLOW, "internal search results"),

    # --- Machine-readable product feed ------------------------------------
    # `/feeds/merchant-center.xml` is fetched by Merchant Center on a schedule,
    # so it must stay crawlable -- but it is an XML file whose entire content is
    # duplicated from the product pages it links to. Indexing it would put a raw
    # feed in the results competing with the pages that earned the ranking.
    #
    # `noindex` and not a `Disallow`, for the reason `robots_disallow_prefixes`
    # states at length: this is a URL we positively want fetched. It is also why
    # this rule is `NOINDEX_FOLLOW` rather than `NOINDEX_NOFOLLOW` -- "nofollow"
    # would make it eligible for the Disallow list, which would block the very
    # fetch the feed exists for. The `follow` half is true on its own terms too:
    # every `<link>` in the feed is a product page we want crawled.
    ("/feeds/", NOINDEX_FOLLOW, "machine-readable product feed"),

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

    # --- Authenticated surface reached through a redirect ------------------
    # /day-signal calls require_account() and 302s anonymous visitors to
    # /signup, so Googlebot has never seen anything else. It sat in the
    # sitemap anyway -- we were recommending a URL that cannot be fetched.
    # "follow" rather than "nofollow" because seo/content.py links to it from
    # three public pages: Disallow-ing it would leave Google with inbound
    # links to a URL and no instruction about it.
    ("/day-signal", NOINDEX_FOLLOW, "authenticated surface behind a redirect"),

    # The arena, for the same reason and with the same evidence. Probed
    # anonymously against production on 2026-10-03, `/arena` and every
    # `/arena/<anything>` answered `302 -> /login?next=...`; not one of them has
    # ever shown Googlebot a page. Six of them were in `sitemap-live.xml` and
    # `sitemap-replays.xml` regardless, because those two routes hardcode a
    # path list that was written when the arena was public and never revisited.
    #
    # Declaring the subtree here is what retires those entries: `sitemap_xml`
    # already gates every path on `sitemap_eligible`, and `sitemap_eligible`
    # reads this table, so the hardcoded lists stop being able to publish a
    # login redirect no matter what anyone adds to them later.
    #
    # `/arena` and not `/arena/`: the bare path redirects too. The segment
    # matcher stops there, which is the point -- `/arena-preview` and
    # `/alpha-arena` are public `200`s that must keep their indexability, and
    # under the old bare-prefix robots.txt emitter a `nofollow` here would have
    # blocked both of them. See `robots_disallow_patterns`.
    #
    # `follow`, matching `/day-signal`: public pages link into the arena, and a
    # `Disallow` would leave Google holding inbound links to a URL it has no
    # instruction about. The crawl then lands on `/login?next=/arena`, which is
    # a separate defect -- the login wall mints one crawlable URL per `next`
    # target -- and it is fixed at `/login`, not by blocking every page that
    # redirects there.
    ("/arena", NOINDEX_FOLLOW, "authenticated arena surface behind a redirect"),

    # The AI command center answers on four paths. `/app` now branches on
    # authentication and serves a public landing page to anonymous visitors, so
    # it is deliberately absent from this table. The other three still 302 to
    # /signup and must not inherit the public page's indexability just because
    # they share its handler. (`/dashboard/intelligence` is already covered by
    # the `/dashboard` rule above.)
    ("/command-center", NOINDEX_NOFOLLOW, "authenticated surface behind a redirect"),
    ("/intelligence", NOINDEX_NOFOLLOW, "authenticated surface behind a redirect"),

    # Three more of the same, found by re-probing production anonymously after
    # the `/arena` entry above shipped. `/alerts`, `/simulator` and
    # `/scam-shield` all answer `302 -> /login`, and all three fell through to
    # "public content" -- declared `index,follow` *and sitemap-eligible*. None
    # of them is in a sitemap today, but that is luck rather than policy: the
    # generators gate on `sitemap_eligible`, so anything that reads this table
    # was free to publish a login redirect. That is precisely how six arena
    # URLs got into `sitemap-live.xml`.
    #
    # `nofollow` for the first two. Nothing public links to `/simulator` at
    # all, and `/alerts` is linked only from `templates/app.html`, the
    # authenticated shell -- so there is no inbound crawl path to strand.
    #
    # `follow` for `/scam-shield`, and the distinction is load-bearing rather
    # than stylistic. `nofollow` is what makes a prefix eligible for the
    # robots.txt Disallow list, and `/scam-shield/scan` is linked from the home
    # page (`templates/index.html`) and from an SEO page's `related` list. A
    # Disallow would leave Google holding two real inbound links to a URL it
    # has been given no instruction about -- the failure mode
    # `robots_disallow_patterns` is written to avoid.
    #
    # Worth a product decision separately: `/scam-shield/scan` answers an
    # anonymous `200`, the home page promotes it, and a live Google Ads
    # campaign spends on it (`utm_campaign=crypto-scam-scanner`), yet its own
    # template serves `noindex,nofollow`. This entry records what production
    # does; it does not settle whether a public anti-scam tool should rank.
    ("/alerts", NOINDEX_NOFOLLOW, "authenticated surface behind a redirect"),
    ("/simulator", NOINDEX_NOFOLLOW, "authenticated surface behind a redirect"),
    ("/scam-shield", NOINDEX_FOLLOW, "authenticated surface behind a redirect"),

    # `/pulse/marketplace` and `/pulse/marketplace/<id>` are deliberately absent,
    # for the same reason `/app` is: they branch on authentication and serve a
    # public page to anonymous readers, so they fall through to `index,follow`
    # like any other public content. They were absent before this too -- but
    # then the absence was a latent disagreement of exactly the kind this module
    # exists to remove, because the table called them indexable and
    # sitemap-eligible while the routes 302'd every anonymous request. The
    # classification is the same today; what changed is that it is now true.
    #
    # Being indexable by path is not sufficient for a product page to enter the
    # sitemap. `marketplace_seo.eligibility` decides that per row, because a
    # public path can still hold a listing with no description or no image --
    # the same split this module draws between `classify` and
    # `content_eligibility`.

    # --- The Pulse app: private by default, public by exception -------------
    #
    # Everything above this point enumerates what is *private*. For `/pulse`
    # that approach had failed quantitatively, not marginally. Probed
    # anonymously against production on 2026-10-03:
    #
    #   145 static `/pulse/*` GET routes      5 serve an anonymous 200
    #                                       138 serve 302 -> the auth wall
    #                                         1 serves 301, 1 serves 404
    #    35 parameterised `/pulse/*` routes   1 serves an anonymous 200
    #
    # and this table classified **115 of the redirecting ones `index,follow`**,
    # by the fallthrough at the bottom of `classify`, because each was simply
    # never named. `/pulse` is PulseSoc's authenticated social application --
    # feed, reels, messages, orders, live studio, private office. The table was
    # declaring the whole of it indexable while `pulse_social_shell` gated every
    # route in it.
    #
    # That is the arena defect at twenty times the scale, and it runs in the
    # opposite direction to the rest of this module: not "we recommend a URL we
    # cannot fetch" but "we have published an indexability claim over a private
    # application". Each of those 115 also mints a crawlable
    # `/login?next=<path>` URL when Googlebot follows an internal link to it.
    # Google has discovered 40 so far. Nothing was converging; the count grows
    # with every authenticated route anyone adds.
    #
    # So the default inverts here, and only here. A new `/pulse` route is
    # non-indexable until someone makes it public on purpose and says so below.
    # That matches how `route_auth` already treats this application -- new
    # routes are authenticated until declared `@public_route` -- and it is the
    # only version of this rule that does not rot, because the failure mode of
    # an enumerated private list is silence.
    #
    # ORDERING: `classify` is first-match-wins, so every carve-out must precede
    # the broad rule. They are longer strings but that buys nothing; this table
    # is not longest-prefix.
    #
    # `NOINDEX_FOLLOW` on the broad rule, and the choice is load-bearing rather
    # than stylistic. `robots_disallow_prefixes` offers up exactly the
    # `NOINDEX_NOFOLLOW` prefixes, so `nofollow` here would emit
    # `Disallow: /pulse/` -- which matches `/pulse/marketplace`,
    # `/pulse/marketplace/<id>` and `/pulse/post/<id>`, i.e. the entire commerce
    # graph and every indexed product page on this site. Note that the
    # segment-exact patterns do *not* save us: `Disallow: /pulse/` is a
    # perfectly correct rendering of a rule about `/pulse`, and a parent
    # blocking its own children is the intended reading, not an overreach bug.
    # The thing standing between this entry and a site-wide deindexing is the
    # directive, nothing else. `follow` is also true on the merits: public pages
    # link into `/pulse`, and those links need to stay walkable.
    #
    # The five public paths, each verified as an anonymous 200 in production on
    # 2026-10-03 rather than inferred from the route table:
    #
    #   /pulse/marketplace       index,follow + self-canonical   (collection)
    #   /pulse/marketplace/<id>  index,follow + self-canonical   (product)
    #   /pulse/post/<id>         index,follow + self-canonical
    #   /pulse/help              index,follow, canonical -> /help
    #   /pulse/support           index,follow, canonical -> /help
    #
    # `/pulse/app` and `/pulse/cart` are the other two anonymous 200s and are
    # deliberately *not* carved out: both already render `noindex` of their own
    # accord, so the broad rule agrees with the page instead of contradicting
    # it. `/pulse/app` is the SPA shell -- a container whose content arrives by
    # fetch -- and `/pulse/cart` is a commerce workflow covered in spirit by
    # `/checkout` above.
    #
    # Two surfaces this rule closes that are worth naming, because both were
    # `index,follow` until now and neither is reachable:
    #
    #   /pulse/search          internal search. `?q=<anything>` 302s to
    #                          `/login?next=/pulse/search%3Fq%3D<query>`, so an
    #                          indexable internal-search path over an unbounded
    #                          query space was feeding the login wall one URL per
    #                          distinct query. The site-wide `/search` rule has
    #                          said `noindex,follow` for this reason all along;
    #                          `/pulse/search` is a sibling and escaped it.
    #   /pulse/premium/success post-checkout confirmation.
    #
    # What this rule does NOT fix, and must not be read as fixing: the 66
    # `/pulse/topic/<tag>` URLs in Search Console's noindex bucket. Classifying
    # them correctly stops us *claiming* they are indexable and keeps them out of
    # every sitemap, but they are in Google's index report because public pages
    # link to them as though they were public hubs. The link graph is the defect
    # there; see the report. Making them public is not the answer either -- they
    # are hashtag pages over marketplace products, which is the thin
    # mass-generated duplicate of `/pulse/marketplace?category=` that the
    # category threshold exists to prevent.
    ("/pulse/marketplace", INDEX_DIRECTIVE, "public product collection and product pages"),
    ("/pulse/post", INDEX_DIRECTIVE, "public post permalink"),
    ("/pulse/help", INDEX_DIRECTIVE, "public help centre"),
    ("/pulse/support", INDEX_DIRECTIVE, "public help centre"),
    ("/pulse", NOINDEX_FOLLOW, "authenticated social application"),
)


# Paths that serve the same page as another path and say so in their canonical.
#
# /support and /help are two decorators on one handler, so /support is not a
# near-duplicate of /help -- it is /help. The page already emits the right
# canonical; what it should not also do is enter the sitemap, because
# submitting a URL we have declared non-canonical asks Google to crawl a page
# we have already told it to fold into another one.
#
# These stay `index,follow`. Adding `noindex` to a page that carries a
# cross-page `canonical` is the conflicting-signal pair Google explicitly warns
# against: the canonical says "credit /help instead", the noindex says "drop
# this", and the risk is that the noindex propagates to the canonical target.
# The canonical alone is the complete instruction.
_CANONICAL_ALIASES = {
    "/support": "/help",
}


# Prefixes that stay crawlable even though they are `noindex`.
#
# Blocking `/static/` stops Google fetching the CSS and JS it needs to render
# the page. That degrades what it understands about the content *and* what it
# measures for Core Web Vitals, in exchange for hiding files nobody would have
# ranked. `noindex` is the right instruction for an asset; `Disallow` is not.
_CRAWLABLE_DESPITE_NOINDEX = ("/static/", "/.well-known/")


def robots_disallow_prefixes():
    """The `Disallow:` lines, derived from the same table as everything else.

    THE RULE THAT GOVERNS THIS FUNCTION
    -----------------------------------
    `Disallow` stops the crawl, so a disallowed page's `noindex` is never read.
    Google can still index a URL it was never allowed to fetch, on the strength
    of inbound links alone, and it then has no instruction from us to stop. So
    disallowing a page we want de-indexed achieves the opposite of the intent.

    Which means only `noindex,nofollow` paths are eligible: "nofollow" is the
    statement that there is nothing here worth crawling. Every `noindex,follow`
    path is deliberately left crawlable -- `/search`, `/signup` and the
    templated market pages are excluded from the index but are real crawl paths
    into content that should rank, and `Disallow` would sever them.

    This is derived rather than hand-listed because the hand-list in
    `seo_engine.robots_txt()` is precisely how robots.txt and the page-level
    directives came to disagree with each other.
    """

    prefixes = []
    for prefix, directive, _reason in _RULES:
        if directive != NOINDEX_NOFOLLOW:
            continue
        if prefix in _CRAWLABLE_DESPITE_NOINDEX:
            continue
        prefixes.append(prefix)
    return tuple(prefixes)


def robots_disallow_patterns():
    """The same prefixes, rewritten so a crawler reads them as `classify` does.

    THE BUG THIS FUNCTION EXISTS TO CLOSE
    -------------------------------------
    `classify` matches **path segments**: `/portfolio` covers `/portfolio` and
    `/portfolio/...` and stops there. robots.txt matches **raw string
    prefixes**: `Disallow: /portfolio` covers `/portfolioanything`. Emitting
    the bare prefix silently translated the first into the second, so robots.txt
    blocked a strictly larger set of URLs than the table it was derived from.

    Measured against the live app on 2026-10-03, that gap cost two real pages:
    `/portfolio-ai` and `/portfolio-intelligence` are public SEO landing pages
    from `seo/content.py`, they classify `index,follow`, they are *in
    sitemap-pages.xml* -- and `Disallow: /portfolio` forbade Googlebot from
    fetching either one. We were submitting two URLs and refusing the crawl.

    It also ran the other way, which is why this is not a one-line fix. Five
    url_map routes were uncrawlable *only* because of the overreach -- their
    paths are siblings of a disallowed prefix, so `classify` called them
    indexable and robots.txt blocked them anyway. Tightening the patterns
    without first naming those five in `_RULES` would have published a Stripe
    webhook health endpoint that answers `200` with no robots meta. They are
    named now; see the comments beside each one.

    WHY `$` AND `?` AND NOT JUST THE SLASH
    --------------------------------------
    Three lines per prefix reproduce segment semantics exactly:

      Disallow: /portfolio$   the bare path, and nothing that merely starts
                              with it
      Disallow: /portfolio?    the bare path carrying a query string -- the
                              GSC robots-blocked export contains
                              `/chat?asset=ETH`, so dropping this line would
                              un-block a URL that is correctly blocked today
      Disallow: /portfolio/   everything beneath it

    `$` and `*` are robots.txt extensions. Google, Bing and Yandex all honour
    them. A crawler that does not will read `/portfolio$` as a literal path
    containing a dollar sign, match nothing, and fall through to the `/` line --
    so for that crawler the bare path alone becomes crawlable while its
    children stay blocked. Every path in this list answers 401, 302 or a
    hardcoded `noindex` to an anonymous request, so the downside of that is a
    wasted fetch, which is the right side of the trade against two sitemapped
    pages we are currently refusing to serve.

    A slash-terminated prefix is expanded the same way on purpose. `/api/`
    alone never blocked `/api` itself, while `classify("/api")` has always
    returned `noindex` -- the same translation gap, pointing the other way.
    """

    patterns = []
    for prefix in robots_disallow_prefixes():
        bare = prefix.rstrip("/") or "/"
        patterns += [f"{bare}$", f"{bare}?", f"{bare}/"]
    return tuple(patterns)


def robots_blocked(path, patterns=None):
    """Would the generated robots.txt block this path? Crawler semantics.

    Deliberately implemented the way a crawler reads the file -- raw string
    prefix, with `$` anchoring the end of the URL -- and not by reusing
    `classify`. A checker that shared `classify`'s matcher could not detect a
    disagreement between the two, which is the only thing this is for.
    """

    p = (path or "/") or "/"
    for pattern in (patterns if patterns is not None else robots_disallow_patterns()):
        if pattern.endswith("$"):
            if p == pattern[:-1]:
                return pattern
        elif p.startswith(pattern):
            return pattern
    return None


def _normalize(path):
    """Strip the query string, the fragment and a trailing slash.

    The root is the exception: `"/".rstrip("/")` is the empty string, and a
    path that normalises to nothing matches every prefix rule.
    """

    p = (path or "/").split("?", 1)[0].split("#", 1)[0] or "/"
    if not p.startswith("/"):
        p = "/" + p
    if len(p) > 1:
        p = p.rstrip("/") or "/"
    return p


def classify(path):
    """Indexability for a request path. Query strings are not consulted.

    A matched rule's sitemap eligibility is *derived from its directive*, the
    same way `_d` derives `indexable`, rather than being hardcoded `False`.

    It was hardcoded, and for as long as every entry in `_RULES` was a
    `noindex` of some kind that was indistinguishable from the derived value --
    which is why it went unnoticed. It stops being equivalent the moment the
    table needs to say "this subtree is private *except* for these paths",
    because the carve-out has to be an `INDEX_DIRECTIVE` entry, and under the
    old line a carve-out would have been classified indexable and
    sitemap-*ineligible* at the same time. `sitemap_xml` gates every entry on
    `sitemap_eligible`, so adding the `/pulse` rule below would have silently
    emptied `sitemap-products.xml`, `sitemap-categories.xml` and
    `sitemap-posts.xml` -- a self-inflicted deindexing of the entire commerce
    graph, delivered by a change whose stated purpose was to protect it.

    The two are equivalent for every rule that exists at the time of writing
    (all of them `noindex`, so both forms yield `False`), which is what makes
    this safe to change rather than a behavioural edit smuggled in alongside a
    new rule.
    """

    lowered = _normalize(path).lower()

    for prefix, directive, reason in _RULES:
        if lowered == prefix or lowered.startswith(prefix if prefix.endswith("/") else prefix + "/") or lowered == prefix.rstrip("/"):
            return _d(directive, directive.startswith("index"), reason)

    target = _CANONICAL_ALIASES.get(lowered)
    if target:
        return _d(INDEX_DIRECTIVE, False, f"canonical alias of {target}")

    return _d(INDEX_DIRECTIVE, True, "public content")


def robots_meta(path):
    """The value for `<meta name="robots">` on a page."""

    return classify(path).directive


def is_indexable(path):
    return classify(path).indexable


#: Query parameters that *select content* rather than track a visit, per path.
#
# Everything else in a query string is dropped, which is right for `utm_*` and
# `pulse_app` -- they reach the same page and must not compete with it. But
# `/pulse/marketplace?category=mens-clothing` is not the same page as
# `/pulse/marketplace`: it has its own `<h1>`, its own title, its own product
# set, and it already declares *itself* canonical in its own `<head>`.
#
# Stripping it here meant this helper disagreed with the page it describes, and
# the disagreement was silent until something tried to put a department URL in a
# sitemap: twelve distinct departments all rendered `<loc>` as the bare hub, so
# `sitemap_xml`'s dedupe (which keys on the *input* path) passed them all through
# and emitted the same URL twelve times with twelve different `lastmod` values.
#
# Allowlisted per path and by name, so this cannot become a general "keep the
# query string" rule -- that would undo the tracking-parameter behaviour above
# and mint a duplicate of every product page for every campaign tag.
_CONTENT_QUERY_PARAMS = {
    "/pulse/marketplace": ("category",),
}


def canonical_url(path):
    """Absolute canonical URL on the one host we rank.

    Tracking and app-intent parameters are dropped: `?pulse_app=1` reaches the
    same public page and must not compete with it for the same content. An
    alias resolves to the path it is an alias of, so a caller that asks for the
    canonical of `/support` is told `/help` rather than being handed back the
    duplicate it started with.

    The exception is `_CONTENT_QUERY_PARAMS` above -- a parameter that selects
    which content the page shows is part of that page's identity, not noise on
    top of it.

    One thing this function cannot do, deliberately: it has no catalogue access,
    so it cannot tell a real department from an invented `?category=` value and
    will hand back a canonical for either. The page is what resolves that -- an
    unknown slug renders `noindex,follow` and canonicalises to the bare hub, so
    nothing a crawler reaches is affected. The callers that *submit* URLs read
    the live taxonomy first, which is why the sitemap never asks about a slug no
    listing carries.
    """

    p = _normalize(path)
    resolved = _CANONICAL_ALIASES.get(p.lower(), p)

    allowed = _CONTENT_QUERY_PARAMS.get(resolved)
    if not allowed or "?" not in (path or ""):
        return CANONICAL_ORIGIN + resolved

    raw = (path or "").split("?", 1)[1].split("#", 1)[0]
    kept = [
        (name, value)
        for name, value in parse_qsl(raw, keep_blank_values=False)
        if name in allowed and value
    ]
    query = ("?" + urlencode(sorted(kept))) if kept else ""
    return CANONICAL_ORIGIN + resolved + query


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

    # Posts written by the PulseSoc system account do not ask to be ranked.
    #
    # Measured against production on 2026-09-18: of 1,806 public, approved,
    # undeleted posts, 1,784 belong to user 0 and 22 to people. The automated
    # ones are template output -- the 446 "Trend Explainer" posts average 0.948
    # pairwise body similarity and differ only in a substituted noun, which is
    # the same shape as the 108 templated pages this module already excludes.
    # Other families are far more varied ("Hot Take" averages 0.097), so a
    # similarity threshold would have split one account down the middle and
    # invited a future generator to write around the number.
    #
    # Authorship is the honest criterion and the stable one. The pages stay
    # served exactly as they are and stay useful inside the product; they simply
    # stop being submitted as work this domain should be ranked on. `follow` is
    # load-bearing as always -- their links to profiles, topics and comments are
    # crawl paths worth keeping.
    if is_automated_author(r):
        return _d(NOINDEX_FOLLOW, False, "automated system account")

    body = str(get("body", "content", "description", default="") or "").strip()
    title = str(get("title", default="") or "").strip()

    # A title alone is not a page. The rule below lets a real headline rescue a
    # short body, which is right -- a two-line post under a written headline is
    # a destination. It is not right when the body is empty, because then the
    # title is the entire text of the page and "a real headline" is measured at
    # fifteen characters.
    #
    # Not theoretical. Running the finished policy over production on
    # 2026-09-18 left a posts sitemap of exactly two URLs, and one of them was a
    # photo post with a zero-length body whose seventeen-character title
    # cleared the bar. That is the thin-content problem this rule exists for,
    # arriving through its own exemption.
    if not body:
        return _d(NOINDEX_FOLLOW, False, "no body text")

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

    A query string the canonical would not keep disqualifies the entry outright.
    `classify` is deliberately query-blind -- it answers for a *request* path,
    and `?page=2` is the same page *shape* as the hub -- but a sitemap entry is
    a *canonical* claim, and the two questions diverge exactly here. Without
    this check, feeding `/pulse/marketplace?page=2` to a sitemap passes the gate
    and then emits `canonical_url(...)`, which drops the unallowlisted `page`
    and writes the bare hub: the same silent duplicate-`<loc>` failure twelve
    department URLs hit before `_CONTENT_QUERY_PARAMS` existed, and it stays
    invisible because `sitemap_xml` dedupes on the *input* path and so never
    sees the collision it emitted.

    Nothing feeds a paginated path to a sitemap today, so this is depth rather
    than a live bug fix -- it makes "paginated URLs stay out of the sitemaps" a
    property of this gate instead of a property of every caller remembering.
    """

    if "?" in (path or ""):
        allowed = _CONTENT_QUERY_PARAMS.get(_normalize(path)) or ()
        raw = (path or "").split("?", 1)[1].split("#", 1)[0]
        for name, value in parse_qsl(raw, keep_blank_values=True):
            if name not in allowed or not value:
                return False

    decision = classify(path)
    if not decision.sitemap_eligible:
        return False
    if record is not None and not content_eligibility(record).sitemap_eligible:
        return False
    return True
