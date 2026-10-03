"""A page must not contradict the robots policy, and must not be advertised
to Google while it is refusing to be indexed.

WHY THIS FILE EXISTS
--------------------
`test_sitemap_entries_are_indexable.py` already asks part of this, and asks it
well, in `ARenderedPageDeliversTheDeclaredDirective`. It asks it of eleven
hand-written paths.

A hand-written corpus cannot grow when someone adds a page. That is not a
criticism of the corpus -- it was assembled to pin a specific regression and it
pins it. It is a statement about what a curated list can see: the pages someone
already suspected. Measured on 2026-10-03 against `origin/main` @ 5bdf4e431,
34 pages contradicted the table, and the curated corpus contained none of
them, because nobody had suspected them yet.

So this file asks the whole `url_map` instead: every parameterless GET rule
that answers 200 with an HTML body. 923 rules, 55 judgeable documents.

WHAT A "DOCUMENT" IS, AND WHY THE FILTER IS NOT AN ALLOWLIST
-----------------------------------------------------------
This file judges 55 of 923 routes, so that reduction has to be principled
rather than convenient:

  - 804 do not answer 200 to an anonymous client; most redirect to `/login`.
    A crawler cannot read them, so they have no directive to be wrong about.
  - 64 answer 200 but are not HTML -- JSON APIs, `sitemap*.xml`, `robots.txt`,
    `manifest.json`, `sw.js`. A `<meta>` tag means nothing in a JSON body.

Neither filter names a path, and none may ever be added. A path leaves this
corpus only by ceasing to be an anonymously-readable HTML document, which is a
fact about the route rather than a decision about the test. A single named
exemption would make the next real defect exemptible too.

NORMALIZING BEFORE COMPARING
----------------------------
`noindex` and `noindex,follow` are the same instruction -- `follow` is the
crawler default, so omitting it changes nothing, and `/pulse/app` sends the
bare form. Comparing raw token sets would report it as a defect, and a gate
that reports non-defects gets switched off, after which it protects nothing.

So `_normalize` expands the two directives that have defaults and leaves the
preview directives alone, because those do *not* default to the values we
choose. Dropping `max-snippet:-1` genuinely changes Google's behaviour -- it
hands snippet length back to Google -- so it stays visible.

THE DIRECTIVE IS NOT ALWAYS A FUNCTION OF THE PATH
--------------------------------------------------
This is the finding that shaped the file, and it is why the obvious assertion
("rendered == `robots_meta(path)`") is not the one below.

`/pulse/marketplace` chooses its directive from the catalogue:

    populated catalogue -> index,follow,max-image-preview:large,...  (27127 bytes)
    empty catalogue     -> noindex,follow                            ( 8634 bytes)

while `robots_meta` says `index,follow,...` either way. The page is right: an
empty storefront should not invite indexing, because that is thin content. The
policy table simply cannot express "depends on how many rows there are."

Two things follow. First, an exact-agreement assertion over this corpus is
environment-dependent -- it passes against a populated developer database and
fails in CI, where `tests/conftest.py` redirects any in-repo DSN to an empty
fallback. A gate whose verdict depends on row counts is the Phase 100 failure
mode: it cries wolf, gets ignored, and then protects nothing.

Second, and much more important: `sitemap_eligible('/pulse/marketplace')` is
`True` in *both* states. So whenever that page declines indexing, the sitemap
is still submitting it. That contradiction is the real defect, it is checkable
in any environment, and it is what `test_no_page_that_declines_indexing_is_
offered_to_search_engines` below asserts.

That test subsumes the mismatch it replaced and points at a fix instead of a
discrepancy. It also catches a second, unrelated instance: `/forgot-password`,
`/forgot-username`, `/offline`, `/reset-pwa` and `/scam-shield/scan` all send
`noindex` while the table calls them indexable *and* sitemap-eligible, because
`classify`'s fallthrough declares anything unenumerated outside `/pulse` to be
public content. Same contradiction, different cause.

DIRECTION MATTERS MORE THAN AGREEMENT
-------------------------------------
A bare mismatch does not say which side is wrong, and a test that implies the
page always is would be actively dangerous here. An engineer who read "page
must equal policy" and dutifully made `/forgot-password` call `robots_meta()`
would publish the password-reset flow to Google, having obeyed the test exactly
as written. So every failure message below names the side to change.

See docs/search_os/AGENT_12_FINDINGS.md (A12-02, A12-03, A12-06).
"""

from __future__ import annotations

import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# `import bot` connects and runs init_db() at module scope, so the scratch DSN
# has to be set before the import, not inside setUpClass.
os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(ROOT, "coinpilotx.db"))

import bot  # noqa: E402
from services import search_visibility  # noqa: E402

#: Single-quoted attributes are the norm here -- robots and canonical tags are
#: emitted from inside single-quoted f-strings in `bot.py`. A double-quote-only
#: character class silently matches nothing on exactly the pages most likely to
#: be wrong, and reads as a clean pass.
RE_ROBOTS_META = re.compile(r"""<meta[^>]+name=['"]robots['"][^>]*>""", re.I)
RE_CONTENT = re.compile(r"""content=['"]([^'"]*)['"]""", re.I)

#: Below this, assume the walk broke rather than that the app got smaller. The
#: measured value is 55. A mass redirect-to-login regression would empty the
#: corpus and every assertion here would pass on nothing.
MIN_JUDGEABLE_DOCUMENTS = 40


def _directive(html):
    tag = RE_ROBOTS_META.search(html or "")
    if not tag:
        return None
    found = RE_CONTENT.search(tag.group(0))
    return found.group(1).strip() if found else None


def _normalize(directive):
    """Token set with the two defaulting directives made explicit.

    `index` and `follow` are crawler defaults, so `noindex` == `noindex,follow`.
    The preview directives are deliberately not defaulted -- see the module
    docstring.
    """

    if directive is None:
        return None
    tokens = {t.strip().lower() for t in directive.split(",") if t.strip()}
    if "index" not in tokens and "noindex" not in tokens:
        tokens.add("index")
    if "follow" not in tokens and "nofollow" not in tokens:
        tokens.add("follow")
    return frozenset(tokens)


def _invites_indexing(tokens):
    """A missing directive invites indexing: absence reads as `index,follow`."""

    return tokens is None or "noindex" not in tokens


class EveryRenderedPageAgreesWithTheRobotsPolicy(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        bot.webhook_app.config["TESTING"] = True
        client = bot.webhook_app.test_client()

        cls.rule_count = len({r.rule for r in bot.webhook_app.url_map.iter_rules()})
        rules = sorted({r.rule for r in bot.webhook_app.url_map.iter_rules()
                        if "GET" in (r.methods or ()) and not r.arguments})

        cls.documents = []
        for path in rules:
            try:
                resp = client.get(path, follow_redirects=False)
            except Exception:
                # A route that raises is a different bug with a different
                # owner, and swallowing it here would hide it from them. It is
                # simply not a document, so it is not judged.
                continue
            if resp.status_code != 200:
                continue
            if "html" not in (resp.headers.get("Content-Type") or "").lower():
                continue
            cls.documents.append((
                path,
                _directive(resp.get_data(as_text=True)) or resp.headers.get("X-Robots-Tag"),
                search_visibility.robots_meta(path),
            ))

    def test_the_corpus_is_not_empty(self):
        """Anti-vacuity. Every other assertion here iterates this list."""

        self.assertGreaterEqual(
            len(self.documents), MIN_JUDGEABLE_DOCUMENTS,
            f"only {len(self.documents)} anonymously-readable HTML documents "
            f"across {self.rule_count} rules; expected at least "
            f"{MIN_JUDGEABLE_DOCUMENTS}. Either the walk broke or something "
            f"started redirecting public pages, and this file now proves "
            f"nothing. Do not lower the floor to make this pass",
        )

    def test_the_corpus_contains_pages_the_table_wants_kept_out(self):
        """The half that actually constrains.

        If every judged page were classified `index`, this file would pass
        against a template that hardcoded `index,follow` -- the original bug,
        mirrored.
        """

        kept_out = [p for p, _served, policy in self.documents
                    if not _invites_indexing(_normalize(policy))]
        self.assertTrue(
            kept_out,
            "no judged page is classified noindex, so nothing here can detect "
            "a page that wrongly invites indexing",
        )

    def test_no_page_invites_indexing_that_the_table_wants_kept_out(self):
        """The direction that leaks. Zero tolerance, no exemptions.

        Measured clean on 2026-10-03: no parameterless anonymous document
        contradicts the table this way. This test keeps that true rather than
        reporting it.
        """

        for path, served, policy in self.documents:
            with self.subTest(path=path):
                if _invites_indexing(_normalize(policy)):
                    continue
                self.assertFalse(
                    _invites_indexing(_normalize(served)),
                    f"{path} is classified '{policy}' but serves '{served}', "
                    f"which asks Google to index it"
                    + (" -- a page that sends no robots meta at all is read as "
                       "index,follow" if served is None else "")
                    + f". FIX THE PAGE: render search_visibility.robots_meta("
                      f"path). Do not reclassify the path to match the page",
                )

    def test_no_page_that_declines_indexing_is_offered_to_search_engines(self):
        """A page refusing indexing must not also be advertised in a sitemap.

        This is the invariant the exact-agreement assertion should have been
        all along. It holds whatever the directive depends on -- the path, the
        row count, the locale -- because it compares the page's own answer
        against what we tell Google to come and fetch.

        Two live causes, 6 failures as of 2026-10-03:

          - `classify`'s fallthrough calls five auth and PWA pages public, so
            `/forgot-password`, `/forgot-username`, `/offline`, `/reset-pwa`
            and `/scam-shield/scan` are sitemap-eligible while sending
            `noindex`.
          - `/pulse/marketplace` self-noindexes on an empty catalogue and stays
            sitemap-eligible, so in that state we submit a URL that tells
            Google to go away.

        Either side is a valid fix: stop declining, or stop submitting.
        """

        for path, served, _policy in self.documents:
            with self.subTest(path=path):
                if _invites_indexing(_normalize(served)):
                    continue
                self.assertFalse(
                    search_visibility.sitemap_eligible(path),
                    f"{path} serves '{served}' and declines indexing, but "
                    f"sitemap_eligible() is True, so we submit it to Google "
                    f"anyway. FIX ONE SIDE: either the page should not be "
                    f"refusing, or classify() should not be calling this path "
                    f"public. Do not make the page indexable without deciding "
                    f"it genuinely is",
                )

    def test_pages_that_agree_on_indexability_send_the_same_directives(self):
        """Scoped to the comparison that is a function of the path alone.

        Both sides already agree on index-vs-noindex here, so this cannot be
        satisfied by flipping a page's indexability, and it is unaffected by
        the data-dependence described in the module docstring.

        28 failures as of 2026-10-03, every one a page restating the directive
        instead of asking for it:

          11  send no robots meta at all, while the table chose three preview
              directives -- `/education{,/optimism,/scam-alerts,
              /toncoin-scenarios}`, `/legal/{payments,refunds,seller-terms}`,
              `/predictions/crypto`, `/quote`, `/roast-battle-preview`,
              `/sports-edge`
           6  drop `max-snippet:-1` and `max-video-preview:-1`
           8  drop all three preview directives
           3  send `nofollow` where the table said `follow` -- `/login`,
              `/signup`, `/pulse/cart`

        Root causes are four hardcoded literals, not 28 bugs:
        `templates/{support,privacy,terms}.html`, `templates/seo_page.html`,
        `templates/index.html` (which hardcodes the *correct* string, so it
        agrees by luck and will drift silently) and `bot.py:108648`. Not
        allowlisted, and must not be.
        """

        for path, served, policy in self.documents:
            with self.subTest(path=path):
                served_tokens = _normalize(served)
                policy_tokens = _normalize(policy)
                if _invites_indexing(served_tokens) != _invites_indexing(policy_tokens):
                    continue  # a different test's subject
                if served_tokens == policy_tokens:
                    continue

                self.fail(
                    f"{path}\n"
                    f"      table  {policy}\n"
                    f"      page   {served if served is not None else '<no robots meta>'}\n"
                    f"      missing {sorted(policy_tokens - (served_tokens or frozenset()))}"
                    f"  extra {sorted((served_tokens or frozenset()) - policy_tokens)}\n"
                    f"      FIX THE PAGE: render search_visibility.robots_meta("
                    f"path). A literal in a template cannot be overridden by "
                    f"the module that owns the decision"
                )


if __name__ == "__main__":
    unittest.main()
