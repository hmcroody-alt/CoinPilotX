"""Every URL we submit to Google must be one Google can actually index.

WHY THIS TEST EXISTS
--------------------
`seo_engine.sitemap_xml` already gates every entry on
`search_visibility.sitemap_eligible`, and that gate is correct. It is also
structurally incapable of catching the defect this file is about.

`sitemap_eligible` is built on `classify`, a static path-pattern classifier. It
can answer "is this *kind* of path indexable" and nothing else. It cannot know
that a route began redirecting anonymous traffic to `/login`, that a route was
deleted and now answers 404, or that robots.txt forbids the fetch. So a path
list written when a surface was public keeps being published long after the
surface stopped being public, and every signal we have says it is fine.

Measured against production on 2026-10-03, that had happened to **every single
entry** in two of the six child sitemaps:

    sitemap-live.xml      /arena/live            302 -> /login?next=...
                          /arena/roast-battle    302 -> /login?next=...
                          /arena/momentum        302 -> /login?next=...
                          /arena/leaderboard     302 -> /login?next=...
                          /momentum              404  (route deleted)
    sitemap-replays.xml   /arena/highlights      302 -> /login?next=...
                          /arena/momentum        302 -> /login?next=...

and to two entries in a third, by a different mechanism -- `/portfolio-ai` and
`/portfolio-intelligence` are public, self-canonical, `index,follow` 200s that
the generated robots.txt forbade Googlebot from fetching, because
`Disallow: /portfolio` matched them as a raw string prefix.

Both are the same failure in the end: a URL we recommend and cannot serve.
Only an end-to-end check catches either one, so this test resolves each
`<loc>` through the Flask test client instead of asking the policy table what
it thinks.

WHAT COUNTS AS INDEXABLE HERE
-----------------------------
A sitemap entry is a claim that the URL is the canonical, indexable version of
a real document. So each `<loc>` must:

  1. answer 200 -- not a redirect, not a 404
  2. not carry `noindex` in meta robots or `X-Robots-Tag`
  3. be crawlable under our own generated robots.txt
  4. declare itself canonical, not point at some other URL

Any one of those failing makes the submission a contradiction.
"""

from __future__ import annotations

import os
import re
import sys
import unittest
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# `import bot` connects and runs init_db() at module scope, so the scratch DSN
# has to be set before the import, not inside setUpClass.
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(ROOT, "coinpilotx.db"))

import bot  # noqa: E402
from services import search_visibility, seo_engine  # noqa: E402

RE_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)
RE_ROBOTS_META = re.compile(r"""<meta[^>]+name=["']robots["'][^>]*>""", re.I)
RE_CANONICAL = re.compile(r"""<link[^>]+rel=["']canonical["'][^>]*>""", re.I)
RE_HREF = re.compile(r"""href=["']([^"']+)["']""", re.I)
RE_CONTENT = re.compile(r"""content=["']([^"']*)["']""", re.I)


def _path_of(loc):
    parts = urllib.parse.urlsplit(loc.replace("&amp;", "&"))
    return parts.path + (f"?{parts.query}" if parts.query else "")


def _served_disallows(client):
    """The `Disallow` patterns a crawler actually receives, parsed from the body.

    This function exists because of a mutation that survived. Every robots
    assertion in this file used to read
    `search_visibility.robots_disallow_patterns()` directly, and the harness in
    `scripts/protection/mutate_sitemap_and_robots_agreement.py` reverted
    `seo_engine.robots_txt()` to the bare-prefix emitter -- the exact bug that
    shipped -- and `test_no_sitemapped_url_is_blocked_by_our_own_robots_txt`
    went on passing. It was reading the function the fix added, not the file the
    route serves, so it could not see the route being wired back to the wrong
    one. A test named after robots.txt that never fetches robots.txt is
    precisely the vacuous-agreement failure this suite is supposed to catch.

    So the corpus comes through the route. `GET /robots.txt` ->
    `seo_engine.robots_txt()` -> whichever emitter it chooses, which is the
    thing under test.
    """

    resp = client.get("/robots.txt")
    assert resp.status_code == 200, f"/robots.txt did not render: {resp.status_code}"
    body = resp.get_data(as_text=True)
    patterns = tuple(
        value
        for line in body.splitlines()
        if line.lower().startswith("disallow:")
        for value in [line.split(":", 1)[1].strip()]
        if value
    )
    # `Disallow:` with an empty value means "allow everything" and is legal, so
    # an empty tuple is a valid file -- but it is not a valid corpus, and would
    # make every assertion built on it pass by having nothing to check.
    assert patterns, "the served robots.txt has no Disallow lines to test"
    return body, patterns


class SitemapEntriesAreIndexable(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        bot.webhook_app.config["TESTING"] = True
        cls.client = bot.webhook_app.test_client()
        cls.robots_body, cls.patterns = _served_disallows(cls.client)

        cls.entries = []
        for child in bot.SITEMAP_CHILDREN:
            resp = cls.client.get(child)
            assert resp.status_code == 200, f"{child} did not render: {resp.status_code}"
            body = resp.get_data(as_text=True)
            for loc in RE_LOC.findall(body):
                cls.entries.append((child, loc, _path_of(loc)))

    def test_the_sitemap_index_lists_every_child_and_each_one_renders(self):
        """Expected children come from the url_map, not from `SITEMAP_CHILDREN`.

        Comparing the index against `SITEMAP_CHILDREN` is what this test did
        first, and it was circular: `sitemap_index_xml` is *generated from*
        `SITEMAP_CHILDREN`, so both sides of the assertion moved together.
        A mutation that dropped `/sitemap-replays.xml` from the tuple left the
        route registered and serving, removed it from the index, and this test
        passed -- it had simply stopped expecting it.

        The url_map is the independent source: a child sitemap that is routed
        is a URL Googlebot can be told about, so every one of them has to be in
        the index or it will never be fetched. Dropping a sitemap properly
        means deleting the route, which this notices.
        """

        routed = {
            rule.rule
            for rule in bot.webhook_app.url_map.iter_rules()
            if re.fullmatch(r"/sitemap-[a-z0-9-]+\.xml", rule.rule)
            and "GET" in (rule.methods or ())
        }
        self.assertGreaterEqual(len(routed), 2, f"url_map sweep found {routed}")

        body = self.client.get("/sitemap.xml").get_data(as_text=True)
        listed = {_path_of(loc) for loc in RE_LOC.findall(body)}
        self.assertEqual(
            routed,
            listed,
            "the sitemap index and the routed child sitemaps disagree. A child "
            "that is routed but unlisted is never fetched, so everything in it "
            "goes undiscovered; one listed but not routed is a 404 reported "
            "against us in Search Console",
        )

    def test_no_sitemapped_url_is_blocked_by_our_own_robots_txt(self):
        """The `/portfolio-ai` case: submitted and simultaneously forbidden."""

        offenders = [
            (child, path, search_visibility.robots_blocked(path, self.patterns))
            for child, _loc, path in self.entries
            if search_visibility.robots_blocked(path, self.patterns)
        ]
        self.assertEqual(
            [],
            offenders,
            "these URLs are in a sitemap and disallowed by the robots.txt we "
            "generate ourselves. Submitting a URL asks for a crawl; "
            "Disallow refuses it. Google resolves the contradiction by not "
            "indexing the page, and reports it to us as "
            "'Blocked by robots.txt'",
        )

    def test_every_sitemapped_url_answers_200(self):
        """The `/arena/*` and `/momentum` case: redirects and a dead route."""

        offenders = []
        for child, _loc, path in self.entries:
            resp = self.client.get(path, follow_redirects=False)
            if resp.status_code != 200:
                offenders.append(
                    f"{child}: {path} -> {resp.status_code} "
                    f"{resp.headers.get('Location') or ''}".strip()
                )
        self.assertEqual(
            [],
            offenders,
            "a sitemap entry that redirects is a request to crawl a URL we "
            "have already replaced, and one that 404s is a request to crawl a "
            "page that does not exist. Both are URLs we recommend and cannot "
            "serve. Remove the path, or stop gating the route behind auth",
        )

    #: The one entry allowed to serve `noindex`, carved out by name.
    #
    # `/pulse/marketplace` renders `noindex,follow` when the eligible catalogue
    # is empty -- the soft-404 guard in `marketplace_storefront` -- and
    # `marketplace_public_entries` submits it anyway. That is a deliberate,
    # tested decision, not an oversight, and `marketplace_public_entries`
    # records why: the alternative keys on a row count that cannot tell an
    # empty catalogue from a failed query, so it would drop the collection page
    # whenever the database hiccups.
    #
    # It is carved out here rather than tolerated by a loosened assertion so
    # that the exception is one name in one place. In production the catalogue
    # is stocked and the page serves `index,follow`, so this only fires in a
    # test environment and on a fresh deployment. Anything else serving
    # `noindex` from a sitemap is a defect.
    NOINDEX_ALLOWED = ("/pulse/marketplace",)

    def test_no_sitemapped_url_serves_noindex(self):
        offenders = []
        for child, _loc, path in self.entries:
            if path in self.NOINDEX_ALLOWED:
                continue
            resp = self.client.get(path, follow_redirects=False)
            if resp.status_code != 200:
                continue  # reported by the 200 test; one failure per defect
            blob = resp.headers.get("X-Robots-Tag") or ""
            match = RE_ROBOTS_META.search(resp.get_data(as_text=True))
            if match:
                content = RE_CONTENT.search(match.group(0))
                blob += " " + (content.group(1) if content else "")
            if "noindex" in blob.lower():
                offenders.append(f"{child}: {path} -> {blob.strip()}")
        self.assertEqual(
            [],
            offenders,
            "`noindex` and a sitemap entry are directly contradictory "
            "instructions about the same URL",
        )

    def test_every_sitemapped_url_declares_itself_canonical(self):
        """A non-self-canonical entry asks Google to crawl a URL we folded away."""

        offenders = []
        for child, _loc, path in self.entries:
            resp = self.client.get(path, follow_redirects=False)
            if resp.status_code != 200:
                continue
            match = RE_CANONICAL.search(resp.get_data(as_text=True))
            if not match:
                continue  # absent canonical is a different (weaker) finding
            href = RE_HREF.search(match.group(0))
            if not href:
                continue
            declared = _path_of(href.group(1))
            if declared.rstrip("/") != path.rstrip("/"):
                offenders.append(f"{child}: {path} declares canonical {declared}")
        self.assertEqual(
            [],
            offenders,
            "we are submitting a URL and telling Google it is not the one to "
            "index. The canonical target belongs in the sitemap instead",
        )


class RobotsTxtMatchesTheIndexabilityTable(unittest.TestCase):
    """robots.txt must block exactly what `_RULES` says, and nothing more.

    `classify` matches path *segments*; robots.txt matches raw string
    *prefixes*. Emitting a bare prefix translated one into the other and
    quietly widened it, so `Disallow: /portfolio` blocked `/portfolio-ai`.
    `robots_disallow_patterns` closes that by expanding each prefix into
    `$`/`?`/`/` forms.

    HONEST SCOPE. Most of this asserts the two channels *agree*. It does not
    assert either one is right about a given path: a future route named
    `/admin-something` would be absent from `_RULES`, classified indexable, and
    left crawlable -- agreement intact, verdict wrong. Catching that needs a
    judgement about what the route is, which no assertion here can make. What
    the agreement checks do catch is the whole class of defect where the two
    channels disagree, in either direction, which is what shipped.

    The limit of an agreement check, concretely. Both channels here derive from
    `_RULES`, so a mutation that edits `_RULES` moves both at once and every
    derived assertion stays true with one fewer thing to check. Four mutations
    that each deleted one of this change's new rules survived the first version
    of this file for exactly that reason. The corpus has to come from somewhere
    the mutation cannot reach, so two things here are deliberately *not*
    derived: `MUST_NOT_BE_CRAWLABLE` is a hand-written list carrying its
    production evidence per path, and the expected child sitemaps are read off
    the url_map. Those are the assertions that bite when the table itself is
    wrong rather than merely inconsistent.
    """

    @classmethod
    def setUpClass(cls):
        from seo import content as seo_content

        bot.webhook_app.config["TESTING"] = True
        cls.client = bot.webhook_app.test_client()
        cls.robots_body, cls.patterns = _served_disallows(cls.client)
        paths = {
            rule.rule
            for rule in bot.webhook_app.url_map.iter_rules()
            if "<" not in rule.rule
        }
        for registry, base in (
            ("SEO_PAGES", "/"),
            ("MARKET_PAGES", "/markets/"),
            ("COUNTRY_PAGES", "/country-intelligence/"),
            ("SPORTS_SEO_PAGES", "/"),
            ("ARTICLE_PAGES", "/"),
            ("HUBS", "/"),
        ):
            paths |= {
                base + slug.lstrip("/")
                for slug in getattr(seo_content, registry, {})
            }
        cls.paths = sorted(paths)

    def test_the_corpus_under_test_is_not_empty(self):
        """A silently empty corpus would make every assertion below vacuous."""

        self.assertGreater(len(self.paths), 1000, self.paths[:10])

    def test_no_indexable_path_is_blocked_by_robots_txt(self):
        offenders = [
            f"{p} blocked by Disallow: {search_visibility.robots_blocked(p, self.patterns)}"
            for p in self.paths
            if search_visibility.classify(p).indexable
            and search_visibility.robots_blocked(p, self.patterns)
        ]
        self.assertEqual(
            [],
            offenders,
            "robots.txt is refusing the crawl of pages we classify as "
            "indexable. Disallow also prevents the crawler ever reading a "
            "page's own directives, so this cannot be waved off as redundant",
        )

    def test_every_noindex_nofollow_path_is_actually_blocked(self):
        """Whatever the table calls `noindex,nofollow`, robots.txt must block.

        SCOPE, learned from a surviving mutation. This test selects its own
        corpus from `_RULES`, so it is an agreement check and nothing more.
        Delete a rule and the path it named stops being `noindex,nofollow`,
        drops out of the comprehension below, and this test keeps passing with
        one fewer thing to check -- which is exactly what happened to four
        mutations that each removed one of the five rules this change added.

        That is not a fixable flaw in this test; a derived corpus is the point
        of it, and it is what catches a *future* rule being added to the table
        and not reaching the file. The missing half -- paths that must be
        blocked whether or not anyone remembers to keep them in the table --
        is asserted by name in
        `test_the_paths_the_overreach_was_covering_are_blocked_on_their_own`.
        """

        offenders = [
            p
            for p in self.paths
            if search_visibility.classify(p).directive
            == search_visibility.NOINDEX_NOFOLLOW
            and not search_visibility.robots_blocked(p, self.patterns)
            and not p.startswith(search_visibility._CRAWLABLE_DESPITE_NOINDEX)
        ]
        self.assertEqual([], offenders)

    #: Paths that must never be crawlable, named rather than derived.
    #
    # Each of these was uncrawlable before this change purely by accident: its
    # path happened to begin with a disallowed string, so `Disallow: /admin`
    # and `Disallow: /webhook` caught siblings they were never written to
    # cover. Tightening the patterns to match `classify`'s segment semantics
    # removed that accident, which is why the five rules naming them were
    # added to `_RULES` in the same commit.
    #
    # The verdict for each comes from an anonymous production probe on
    # 2026-10-03, not from the policy table -- the point of this list is to be
    # an independent source of truth, so that removing a rule makes a test go
    # red instead of making a corpus one entry shorter.
    MUST_NOT_BE_CRAWLABLE = (
        # 200 with no robots meta of its own. The live hole: the raw-prefix
        # accident was the only thing keeping a Stripe webhook endpoint out of
        # Google's index.
        ("/webhooks/stripe", "200, no robots meta"),
        ("/webhooks/brevo", "405"),
        ("/admin-dashboard", "401"),
        # 302 -> /login?next=/pulse/messages-v2, which mints one more crawlable
        # URL for the login wall -- the trap this module should shrink.
        ("/pulse/messages-v2", "302 to the login wall"),
        ("/pulse/messages-legacy", "301 onto the blocked parent"),
        # 200, and its template already hardcodes noindex,nofollow. Named here
        # so the statement lives where the sitemap gate and robots.txt can both
        # read it, instead of in one Jinja file where neither can.
        ("/verify-email", "200, one-time verification URL"),
    )

    def test_the_paths_the_overreach_was_covering_are_blocked_on_their_own(self):
        """Named, so that deleting a rule cannot quietly shrink the corpus."""

        for path, evidence in self.MUST_NOT_BE_CRAWLABLE:
            with self.subTest(path=path):
                self.assertIsNotNone(
                    search_visibility.robots_blocked(path, self.patterns),
                    f"{path} ({evidence}) is crawlable. Before the patterns "
                    f"became segment-exact it was blocked only because its "
                    f"path happened to start with a disallowed string. If the "
                    f"`_RULES` entry naming it has been removed, nothing is "
                    f"covering it any more",
                )
                self.assertFalse(
                    search_visibility.classify(path).indexable,
                    f"{path} is classified indexable, which is worse than "
                    f"being crawlable -- it can reach a sitemap",
                )

    def test_a_disallow_does_not_catch_a_sibling_path(self):
        """The regression proper, stated on the paths that were affected."""

        for path in ("/portfolio-ai", "/portfolio-intelligence"):
            with self.subTest(path=path):
                self.assertTrue(search_visibility.classify(path).indexable)
                self.assertIsNone(
                    search_visibility.robots_blocked(path, self.patterns),
                    f"{path} is a public SEO landing page in sitemap-pages.xml",
                )
        # ...while the prefix those two were caught by still blocks its own path
        self.assertIsNotNone(search_visibility.robots_blocked("/portfolio", self.patterns))
        self.assertIsNotNone(
            search_visibility.robots_blocked("/portfolio/anything", self.patterns)
        )

    def test_a_query_string_does_not_escape_a_disallow(self):
        """`/chat?asset=ETH` is in the live robots-blocked report and must stay there.

        This is why each prefix emits a `?` form. Without it, `$` would anchor
        the bare path only and every `?`-bearing variant of a private surface
        would become crawlable -- regressing URLs that are correctly blocked.
        """

        for path in ("/chat?asset=ETH", "/account?tab=security", "/checkout?step=2"):
            with self.subTest(path=path):
                self.assertIsNotNone(
                    search_visibility.robots_blocked(path, self.patterns), path
                )

    def test_robots_txt_still_only_disallows_noindex_nofollow_surfaces(self):
        """The pre-existing rule the new emitter must not have broken.

        A `Disallow` on a `noindex,follow` path would sever a real crawl path
        and stop the directive being read at all -- the reason
        `robots_disallow_prefixes` restricts itself to `noindex,nofollow`.
        """

        follow_only = {
            prefix
            for prefix, directive, _reason in search_visibility._RULES
            if directive == search_visibility.NOINDEX_FOLLOW
        }
        body = seo_engine.robots_txt()
        disallowed = {
            line.split(":", 1)[1].strip()
            for line in body.splitlines()
            if line.lower().startswith("disallow:")
        }
        for prefix in follow_only:
            bare = prefix.rstrip("/") or "/"
            for form in (f"{bare}$", f"{bare}?", f"{bare}/", bare):
                self.assertNotIn(form, disallowed, f"{prefix} must stay crawlable")

    def test_the_arena_subtree_is_not_indexable_but_its_public_siblings_are(self):
        """`/arena` 302s to /login; `/arena-preview` and `/alpha-arena` are 200s."""

        for path in ("/arena", "/arena/live", "/arena/leaderboard", "/arena/play"):
            with self.subTest(path=path):
                self.assertFalse(search_visibility.classify(path).indexable)
                self.assertFalse(search_visibility.sitemap_eligible(path))
                # ...and crawlable, which is the half a mutation caught me
                # missing. Flipping this rule to `noindex,nofollow` kept every
                # other assertion here true -- the siblings stay safe either
                # way, because the patterns are segment-exact now -- while
                # doing the one thing the policy forbids. Public pages link
                # into the arena; a `Disallow` leaves Google holding those
                # links with no instruction about the target, and the `noindex`
                # it would need to read is behind the block. That the login
                # redirect it lands on mints a `?next=` URL is a defect fixed
                # at `/login`, not by blocking every page that points there.
                self.assertIsNone(
                    search_visibility.robots_blocked(path, self.patterns),
                    f"{path} must stay crawlable so its noindex is readable; "
                    f"robots.txt is not a substitute for a directive",
                )
        for path in ("/arena-preview", "/alpha-arena"):
            with self.subTest(path=path):
                self.assertTrue(
                    search_visibility.classify(path).indexable,
                    f"{path} is a public page and a mere string-prefix "
                    f"neighbour of /arena",
                )
                self.assertIsNone(
                    search_visibility.robots_blocked(path, self.patterns)
                )

    #: The public paths carved out of the `/pulse` default-deny rule, each
    #: verified as an anonymous 200 against production on 2026-10-03. Named
    #: rather than derived from `_RULES`, because a corpus read out of the table
    #: cannot notice an entry being deleted from the table.
    PULSE_PUBLIC = (
        "/pulse/marketplace",
        "/pulse/marketplace/102",
        "/pulse/post/2500",
        "/pulse/help",
        "/pulse/support",
    )

    #: Representatives of the 115 authenticated surfaces the rule closed.
    PULSE_PRIVATE = (
        "/pulse",
        "/pulse/reels",
        "/pulse/orders/1",
        "/pulse/live/studio/1",
        "/pulse/topic/rings",
        "/pulse/search",
        "/pulse/premium/success",
        "/pulse/@someone",
    )

    def test_the_pulse_rule_does_not_take_the_commerce_graph_with_it(self):
        """The single most expensive mutation available in this module.

        `/pulse` is declared `noindex,follow`, and the `follow` is doing load-
        bearing work rather than expressing a preference.
        `robots_disallow_prefixes` offers up exactly the `noindex,nofollow`
        prefixes, so flipping one character here emits `Disallow: /pulse/` --
        which matches `/pulse/marketplace`, every `/pulse/marketplace/<id>`
        product page and every `/pulse/post/<id>`. That is the entire commerce
        graph and the majority of what this site has indexed, removed by a
        change that would read in review as a tightening.

        Note what does *not* protect us here. The segment-exact patterns from
        `robots_disallow_patterns` were the fix for `Disallow: /portfolio`
        catching `/portfolio-ai`, a genuine string-prefix accident. A parent
        blocking its own children is not that: `Disallow: /pulse/` matching
        `/pulse/marketplace` is the correct and intended reading of a rule about
        `/pulse`. No pattern shape can distinguish the two. The directive is the
        only thing standing here, so it is asserted directly, against the served
        file.
        """

        for path in self.PULSE_PUBLIC:
            with self.subTest(path=path, half="public"):
                decision = search_visibility.classify(path)
                self.assertTrue(
                    decision.indexable,
                    f"{path} is an anonymous 200 and a public commerce page; "
                    f"if its carve-out has been removed from `_RULES` the "
                    f"`/pulse` rule now swallows it",
                )
                self.assertTrue(
                    search_visibility.sitemap_eligible(path),
                    f"{path} is indexable but no longer sitemap-eligible. "
                    f"`classify` once hardcoded that combination for every "
                    f"rule match, which would have emptied "
                    f"sitemap-products.xml on deploy",
                )
                self.assertIsNone(
                    search_visibility.robots_blocked(path, self.patterns),
                    f"{path} is Disallowed in the robots.txt we serve. If "
                    f"`/pulse` has become `noindex,nofollow`, this is the "
                    f"whole product catalogue going dark",
                )

        for path in self.PULSE_PRIVATE:
            with self.subTest(path=path, half="private"):
                decision = search_visibility.classify(path)
                self.assertFalse(
                    decision.indexable,
                    f"{path} 302s anonymous traffic to the auth wall. If the "
                    f"broad `/pulse` rule has been removed it is being "
                    f"declared indexable by the fallthrough, which is how 115 "
                    f"private app surfaces came to claim indexability",
                )
                self.assertFalse(search_visibility.sitemap_eligible(path))
                self.assertIsNone(
                    search_visibility.robots_blocked(path, self.patterns),
                    f"{path} must stay crawlable so its noindex is readable",
                )


class ARenderedPageDeliversTheDeclaredDirective(unittest.TestCase):
    """The policy table is only worth what the pages actually send.

    Every other test in this file reads `search_visibility` and
    `seo_engine.robots_txt()`. Both are the right sources for the sitemap and
    robots channels -- and neither can see the third channel, which is the
    `<meta name="robots">` the page hands Googlebot.

    That gap was not hypothetical. `templates/seo_page.html` hardcoded
    `index, follow, max-image-preview:large, max-snippet:-1`, so the 42 URLs
    under `/markets/<symbol>{,/prediction,/live}` and
    `/country-intelligence/<slug>` -- which `_RULES` has classified
    `noindex,follow` as scaled near-duplicates for as long as the rule has
    existed -- asked Google to rank them anyway. Confirmed against production
    on 2026-10-03: `/markets/btc`, `/markets/btc/live` and
    `/country-intelligence/nigeria` all served `index, follow, ...`.

    It survived because the sitemap half of the policy worked. Nothing that
    enumerates sitemap entries could see it, the pages answer 200, and the only
    artefact was 42 pages sitting in "crawled - currently not indexed" while
    Google made the quality judgement we had already made and failed to send.
    """

    @classmethod
    def setUpClass(cls):
        bot.webhook_app.config["TESTING"] = True
        cls.client = bot.webhook_app.test_client()

    #: Paths the table calls `noindex` that nonetheless render a page, paired
    #: with paths it calls `index` that render through the same template. Both
    #: halves are required: an all-`noindex` corpus would pass against a
    #: template hardcoding `noindex`, which is the same bug mirrored.
    #:
    #: The last four `DECLARED_INDEX` entries render from a different template
    #: again -- an f-string inside `render_ads_landing_page` -- and are here
    #: because they had the same defect in a quieter form: the literal agreed
    #: with the table's direction but dropped `max-snippet:-1` and
    #: `max-video-preview:-1`, so the four pages Google Ads traffic lands on
    #: were the four capping their own snippet length. Three templates now
    #: answer this question, so the corpus has to span all three.
    DECLARED_NOINDEX = ("/markets/btc", "/markets/btc/live", "/markets/btc/prediction",
                        "/country-intelligence/nigeria")
    DECLARED_INDEX = ("/ai-crypto-assistant", "/portfolio-ai", "/intel",
                      "/alpha-arena", "/live-roast-battle", "/crypto-scam-scanner",
                      "/crypto-training-simulator")

    def test_the_meta_robots_a_page_sends_is_the_one_the_table_declares(self):
        for path in self.DECLARED_NOINDEX + self.DECLARED_INDEX:
            with self.subTest(path=path):
                resp = self.client.get(path)
                self.assertEqual(
                    resp.status_code, 200,
                    f"{path} did not render, so this test cannot see its "
                    f"directive. Fix the corpus rather than dropping the path",
                )
                found = re.findall(
                    r"""<meta\s+name=["']robots["']\s+content=["']([^"']+)["']""",
                    resp.get_data(as_text=True),
                    re.I,
                )
                self.assertEqual(
                    len(found), 1,
                    f"{path} sent {len(found)} robots meta tags; exactly one "
                    f"is readable and two can disagree",
                )
                self.assertEqual(
                    found[0].replace(" ", ""),
                    search_visibility.robots_meta(path).replace(" ", ""),
                    f"{path} sends a directive the policy table did not "
                    f"choose. A literal in a template cannot be overridden by "
                    f"the module that owns the decision",
                )

    def test_the_noindex_half_of_the_corpus_is_not_empty(self):
        """Anti-vacuity, and it is the half that actually constrains.

        If every path above were `index`, the test would pass against the
        hardcoded literal it exists to have removed.
        """

        for path in self.DECLARED_NOINDEX:
            with self.subTest(path=path):
                self.assertFalse(
                    search_visibility.classify(path).indexable,
                    f"{path} is in DECLARED_NOINDEX but the table now calls it "
                    f"indexable, so it no longer tests anything",
                )


if __name__ == "__main__":
    unittest.main()
