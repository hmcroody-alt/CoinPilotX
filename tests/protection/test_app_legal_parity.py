"""The web copy of a legal document and the copy inside the iPhone app are one document.

`/legal/cookies` and `/legal/licenses` exist because
`mobile-native/src/screens/settings/legalContent.ts` names both as absolute
`canonicalUrl`s and the Legal screen offers an "Open full document" handoff to
them. Both URLs returned 404 in production, which meant the app was telling
members the legally operative version of a document lived at an address with
nothing behind it.

The fix publishes them, and publishing them creates the risk the three sibling
URLs avoid by redirecting: two copies of one document, where the one that
drifted is still the one a user was shown. This file is what makes the second
copy safe. Every paragraph the binary ships must either appear verbatim in
`seo/app_legal.py` or be listed in `APP_TEXT_DIVERGENCES` with the reason it
does not. There is no third option, so editing the app text fails this test
until the published document is brought along, and vice versa.

WHY A DIVERGENCE REGISTER AND NOT PLAIN EQUALITY
------------------------------------------------
Plain equality was the first design and it was wrong, because four paragraphs of
the shipped cookie notice describe mechanisms that are not in the binary -- a
rotating analytics identifier, an App Tracking Transparency grant, an
advertising identifier, and a settings switch wired to the advertising engine.
None of those exist. Equality would have forced the published page to repeat
them, which is to say it would have forced a privacy page to disclose
collection that does not happen. Over-disclosure is as wrong on a privacy page
as under-disclosure and considerably harder to notice.

So divergence is permitted and *recorded*. The register doubles as the work list
for the next iOS build: when the app text is corrected, its entry here must be
deleted, and `test_divergences_are_still_present_in_the_app` is what fails if
someone corrects the app and forgets.

WHAT IS CHECKED AGAINST CODE RATHER THAN AGAINST THE APP
--------------------------------------------------------
The published page makes two factual claims a future commit could silently
falsify, so both are re-derived here on every run:

* "That is the entire list" of cookies. The backend has exactly two
  `set_cookie` call sites plus Flask's own session cookie. A third would make
  the sentence false, and nothing else in the suite would notice.
* The dependency versions. They are the *resolved* versions from
  `mobile-native/package-lock.json`, not the ranges in `package.json`. A
  licences page naming `^0.21.0` when `0.21.2` shipped names the wrong
  artefact.
"""

import json
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from seo import app_legal  # noqa: E402

LEGAL_CONTENT_TS = ROOT / "mobile-native/src/screens/settings/legalContent.ts"
PACKAGE_LOCK = ROOT / "mobile-native/package-lock.json"
BOT_PY = ROOT / "bot.py"
GUEST_CUSTOMER = ROOT / "services/marketplace_guest_customer.py"

#: `name` in `OPEN_SOURCE_DEPENDENCIES` is a display name for three packages.
#: Resolving them by hand rather than guessing from the string, because
#: "React Navigation" is not a package and `@react-navigation/native` is.
_DISPLAY_NAME_TO_PACKAGE = {
    "React": "react",
    "React Native": "react-native",
    "Expo": "expo",
    "React Navigation": "@react-navigation/native",
}


def _ts_source():
    return LEGAL_CONTENT_TS.read_text(encoding="utf-8")


def _ts_document_block(source, const_name):
    """The source text of one `const <NAME>: LegalDocument = {...};` literal."""

    opening = f"const {const_name}: LegalDocument = {{"
    start = source.index(opening)
    end = source.index("\n};", start)
    return source[start:end]


def _ts_string(raw):
    """Decode one TypeScript double-quoted literal body to the string it denotes.

    Via the JSON decoder because the escape grammar that matters here -- `\\"`
    and `\\\\` -- is the same in both languages, and a hand-rolled unescape is
    one bug away from comparing subtly different strings and reporting a
    divergence that is really a parser defect.
    """

    return json.loads('"' + raw + '"')


def _ts_sections(const_name):
    """`[(heading, [paragraph, ...]), ...]` for one app legal document.

    Paragraph lists built from the double-quoted literals in source order.
    Spread elements such as `...OPEN_SOURCE_DEPENDENCIES.map(...)` contribute no
    literal and so contribute no paragraph, which is correct: those lines are
    compared separately, against the dependency table they are generated from.
    """

    block = _ts_document_block(_ts_source(), const_name)
    sections = []
    for match in re.finditer(
        r'heading:\s*"((?:[^"\\]|\\.)*)"\s*,\s*\n\s*paragraphs:\s*\[(.*?)\n\s*\]',
        block,
        re.S,
    ):
        heading = _ts_string(match.group(1))
        paragraphs = [
            _ts_string(raw) for raw in re.findall(r'"((?:[^"\\]|\\.)*)"', match.group(2))
        ]
        sections.append((heading, paragraphs))
    return sections


def _ts_field(const_name, field):
    block = _ts_document_block(_ts_source(), const_name)
    return _ts_string(re.search(rf'{field}:\s*"((?:[^"\\]|\\.)*)"', block).group(1))


def _ts_dependencies():
    source = _ts_source()
    start = source.index("export const OPEN_SOURCE_DEPENDENCIES")
    end = source.index("\n];", start)
    rows = []
    for match in re.finditer(
        r'\{\s*name:\s*"((?:[^"\\]|\\.)*)",\s*version:\s*"([^"]*)",\s*'
        r'license:\s*"([^"]*)",\s*purpose:\s*"((?:[^"\\]|\\.)*)"\s*\}',
        source[start:end],
    ):
        rows.append(tuple(_ts_string(group) for group in match.groups()))
    return tuple(rows)


def _ts_notice(const_name):
    source = _ts_source()
    match = re.search(rf'const {const_name} =\s*\n?\s*"((?:[^"\\]|\\.)*)";', source, re.S)
    return _ts_string(match.group(1))


def _published_paragraphs(slug):
    document = app_legal.BY_SLUG[slug]
    return [
        paragraph
        for section in document["sections"]
        for paragraph in section["body"]
    ]


#: Which app document corresponds to which published slug.
_PAIRS = (("COOKIES", "cookies"), ("LICENSES", "licenses"))


class AppTextIsCarriedOrRegistered(unittest.TestCase):
    def test_every_app_paragraph_is_published_or_registered(self):
        registered = {entry["app_text"] for entry in app_legal.APP_TEXT_DIVERGENCES}
        for const_name, slug in _PAIRS:
            published = _published_paragraphs(slug)
            for heading, paragraphs in _ts_sections(const_name):
                for paragraph in paragraphs:
                    with self.subTest(document=slug, heading=heading, text=paragraph[:60]):
                        self.assertTrue(
                            paragraph in published or paragraph in registered,
                            f"The iPhone app ships this paragraph under “{heading}” and "
                            f"/legal/{slug} neither carries it verbatim nor lists it in "
                            "seo.app_legal.APP_TEXT_DIVERGENCES. Publish it, or register "
                            "it with the reason it is wrong.\n\n"
                            f"{paragraph}",
                        )

    def test_divergences_are_still_present_in_the_app(self):
        """A register entry for text the app no longer ships is stale.

        This is the half that catches the happy path: someone fixes the app copy
        in a later build, and the explanation of why the web page says something
        different stops being true. Deleting the entry is the whole fix.
        """

        shipped = {
            paragraph
            for const_name, _slug in _PAIRS
            for _heading, paragraphs in _ts_sections(const_name)
            for paragraph in paragraphs
        }
        for entry in app_legal.APP_TEXT_DIVERGENCES:
            with self.subTest(text=entry["app_text"][:60]):
                self.assertIn(
                    entry["app_text"],
                    shipped,
                    "APP_TEXT_DIVERGENCES explains why /legal/* departs from a paragraph "
                    "the app no longer contains. If the app has been corrected, delete "
                    "this entry.",
                )

    def test_every_divergence_states_a_reason_and_its_evidence(self):
        for entry in app_legal.APP_TEXT_DIVERGENCES:
            with self.subTest(text=entry["app_text"][:60]):
                self.assertIsInstance(entry["reason"], str)
                self.assertGreater(
                    len(entry["reason"]), 40,
                    "A one-word reason is not a reason. Say what the code does instead.",
                )
                self.assertIsInstance(entry["evidence"], str)
                self.assertTrue(entry["evidence"].strip())


class PublishedDocumentsMatchTheAppsOwnPointers(unittest.TestCase):
    def test_canonical_urls_named_by_the_app_are_the_paths_served(self):
        from services import search_visibility

        for const_name, slug in _PAIRS:
            with self.subTest(document=slug):
                self.assertEqual(
                    _ts_field(const_name, "canonicalUrl"),
                    search_visibility.canonical_url(app_legal.canonical_path(slug)),
                    "The app sends members to this URL and calls it canonical. The route "
                    "has to be at exactly that path -- the page comes to the URL, because "
                    "the URL is already in an App Store build.",
                )

    def test_effective_dates_agree(self):
        for const_name, slug in _PAIRS:
            with self.subTest(document=slug):
                self.assertEqual(
                    _ts_field(const_name, "effectiveDate"),
                    app_legal.EFFECTIVE_DATE,
                    "One document cannot have two effective dates.",
                )


class LicenceFactsAreDerivedNotTyped(unittest.TestCase):
    def test_dependency_table_matches_the_app(self):
        self.assertEqual(
            app_legal.OPEN_SOURCE_DEPENDENCIES,
            _ts_dependencies(),
            "The published licences page and the app's acknowledgements screen list "
            "different packages. They are one list.",
        )

    def test_versions_are_the_resolved_versions_that_shipped(self):
        packages = json.loads(PACKAGE_LOCK.read_text(encoding="utf-8"))["packages"]
        for name, version, _licence, _purpose in app_legal.OPEN_SOURCE_DEPENDENCIES:
            package = _DISPLAY_NAME_TO_PACKAGE.get(name, name)
            with self.subTest(package=package):
                self.assertEqual(
                    packages[f"node_modules/{package}"]["version"],
                    version,
                    "A licences page must name the artefact that was built, which is the "
                    "lockfile's resolved version and not package.json's range.",
                )

    def test_licence_notices_are_verbatim(self):
        self.assertEqual(app_legal.MIT_NOTICE, _ts_notice("MIT_NOTICE"))
        self.assertEqual(app_legal.APACHE_NOTICE, _ts_notice("APACHE_NOTICE"))

    def test_every_declared_licence_has_a_notice_section(self):
        headings = {
            section["heading"] for section in app_legal.BY_SLUG["licenses"]["sections"]
        }
        expected = {"MIT": "MIT License", "Apache-2.0": "Apache License 2.0"}
        for _name, _version, licence, _purpose in app_legal.OPEN_SOURCE_DEPENDENCIES:
            with self.subTest(licence=licence):
                self.assertIn(
                    expected.get(licence, licence),
                    headings,
                    "A package is listed under a licence whose terms the page does not "
                    "reproduce, so a reader cannot find what they agreed to.",
                )


class CookieClaimsAreRederivedFromTheServer(unittest.TestCase):
    """The page says "that is the entire list". This is what keeps that true."""

    def _cookie_section_text(self):
        document = app_legal.BY_SLUG["cookies"]
        return "\n".join(
            paragraph
            for section in document["sections"]
            for paragraph in section["body"]
        )

    def test_the_backend_sets_exactly_the_cookies_the_page_names(self):
        call_sites = []
        for path in sorted(
            [BOT_PY] + list((ROOT / "services").glob("*.py"))
        ):
            source = path.read_text(encoding="utf-8", errors="replace")
            for match in re.finditer(r"\.set_cookie\(", source):
                line = source.count("\n", 0, match.start()) + 1
                call_sites.append(f"{path.relative_to(ROOT)}:{line}")
        self.assertEqual(
            len(call_sites), 2,
            "/legal/cookies enumerates the first-party cookies and says that is the "
            "whole list. A cookie set somewhere this page does not name makes the "
            "sentence false, and no other test reads that page.\n\n"
            "Call sites found: " + ", ".join(call_sites),
        )

    def test_the_named_cookies_are_the_names_in_the_code(self):
        published = self._cookie_section_text()
        bot_source = BOT_PY.read_text(encoding="utf-8", errors="replace")
        refresh_default = re.search(
            r'PERSISTENT_SESSION_COOKIE = os\.getenv\([^,]+,\s*"([^"]+)"\)', bot_source
        ).group(1)
        guest_name = re.search(
            r'COOKIE_NAME = "([^"]+)"',
            GUEST_CUSTOMER.read_text(encoding="utf-8"),
        ).group(1)
        for name in (refresh_default, guest_name):
            with self.subTest(cookie=name):
                self.assertIn(
                    name, published,
                    "A cookie the server sets is not named on the page that enumerates "
                    "them.",
                )

    def test_the_guest_cart_cookie_lifetime_is_the_one_published(self):
        seconds = GUEST_CUSTOMER.read_text(encoding="utf-8")
        days = int(
            re.search(r"COOKIE_MAX_AGE_SECONDS = (\d+) \* 24 \* 60 \* 60", seconds).group(1)
        )
        self.assertEqual(days, 30)
        self.assertIn("thirty days", self._cookie_section_text())

    def test_no_claim_that_the_site_sets_no_google_cookies(self):
        """The marketing templates can carry Google's tag, so the page may not deny it.

        `templates/index.html` and `templates/seo_page.html` both include
        `googletagmanager.com/gtag/js` when a measurement ID is configured. It is
        unset in production today, which is exactly the condition that makes an
        absolute "we use no third-party cookies" sentence tempting and wrong: one
        environment variable would falsify it with no code change and no test
        failure.
        """

        templates_with_gtag = sorted(
            path.name
            for path in (ROOT / "templates").glob("*.html")
            if "googletagmanager.com" in path.read_text(encoding="utf-8", errors="replace")
        )
        published = self._cookie_section_text()
        if templates_with_gtag:
            self.assertIn(
                "measurement tag", published,
                "Templates still carry Google's tag conditionally, so the page must keep "
                f"describing it. Found in: {', '.join(templates_with_gtag)}",
            )
        for absolute in (
            "no third-party cookies",
            "we use no cookies",
            "does not use any cookies",
        ):
            with self.subTest(claim=absolute):
                self.assertNotIn(absolute, published.lower())


class PublishedPagesAreWholeDocuments(unittest.TestCase):
    def test_both_documents_render_through_the_shared_public_shell(self):
        template = (ROOT / "templates/app_legal.html").read_text(encoding="utf-8")
        self.assertIn('{% extends "_public_shell.html" %}', template)

    def test_page_context_is_complete_for_the_shell(self):
        from services import search_visibility

        for slug in app_legal.BY_SLUG:
            page = app_legal.page(slug, search_visibility.canonical_url)
            with self.subTest(document=slug):
                for key in ("canonical", "title", "description", "h1", "lede", "sections"):
                    self.assertTrue(page[key], f"`{key}` is empty, and the shell renders it.")
                self.assertTrue(page["related"], "A document with no way onward is a dead end.")

    def test_unknown_slugs_return_none_so_the_route_can_404(self):
        from services import search_visibility

        self.assertIsNone(app_legal.page("terms", search_visibility.canonical_url))

    def test_titles_and_descriptions_are_distinct(self):
        titles = [document["title"] for document in app_legal.DOCUMENTS]
        descriptions = [document["description"] for document in app_legal.DOCUMENTS]
        self.assertEqual(len(set(titles)), len(titles))
        self.assertEqual(len(set(descriptions)), len(descriptions))


if __name__ == "__main__":
    unittest.main()
