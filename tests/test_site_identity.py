"""Who this domain says it belongs to, checked across every page that says it.

`@id` is what makes two JSON-LD nodes one entity. Google consolidates nodes
sharing an `@id`, so two pages describing `https://pulsesoc.com/#organization`
differently do not produce two entities -- they produce one entity with
contradictory properties, resolved by whichever page was crawled last. A node
with *no* `@id` is worse in a different way: it joins nothing, corroborates
nothing, and competes with the real one.

Both failures were live. `/terms` and `/privacy` each declared the canonical
`@id` with `name: "CoinPlotXAI Inc."` while `/app`, `/features/*` and `/pricing`
declared it with `name: "PulseSoc"`. Separately `/` and `/about` published
Organization nodes with no `@id` at all, and the one on `/about` carried a
`description` about an educational crypto simulation platform -- which, under
the canonical `@id`, would not have sat beside the WebSite's description but
merged with it.

`name` and `legalName` are different fields and both are true. `name` is the
brand a person searches for; `legalName` is the company that signs things, and
the split is corroborated outside this repo -- Apple records the App Store
seller as COINPLOTXAI INC. That is why these tests pin the split rather than
banning the company name: a Terms page that says "CoinPlotXAI Inc. is not
liable" is correct and must not be rewritten. Only the site-identity layer is
in scope.

This walks every static public GET route rather than a hand-written list,
because the failure mode is a page nobody remembered. Vacuity guards below
assert the walk actually found something, since a scan that silently matches
nothing passes every assertion it makes.

Run: python3 -m pytest tests/test_site_identity.py
"""

import json
import os
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="site_identity_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from seo import schema as seo_schema  # noqa: E402

CANONICAL_ID = "https://pulsesoc.com/#organization"
BRAND = "PulseSoc"
LEGAL_ENTITY = "CoinPlotXAI Inc."

_LD_BLOCK = re.compile(r'type="application/ld\+json">(.*?)</script>', re.S)
_TITLE = re.compile(r"<title>(.*?)</title>", re.S)
_SITE_NAME = re.compile(r'property="og:site_name"\s+content="([^"]+)"')
_NOINDEX = re.compile(r'name=[\'"]robots[\'"]\s+content=[\'"][^\'"]*noindex')


@pytest.fixture(scope="module")
def pages():
    """Every static public GET route that returns indexable HTML.

    Dynamic rules are skipped because there is no id to substitute; `/api`,
    `/admin`, `/static` and `/webhook` because they are not pages. `noindex`
    pages are skipped because this file is about what the domain asks Google to
    rank, and a page that asks for nothing makes no identity claim.
    """

    client = bot.webhook_app.test_client()
    collected = {}
    for rule in bot.webhook_app.url_map.iter_rules():
        path = str(rule.rule)
        if "<" in path or "GET" not in (rule.methods or set()):
            continue
        if path.startswith(("/api", "/admin", "/static", "/webhook")):
            continue
        try:
            response = client.get(path)
        except Exception:
            continue
        if response.status_code != 200:
            continue
        if "html" not in response.headers.get("Content-Type", ""):
            continue
        body = response.get_data(as_text=True)
        if _NOINDEX.search(body):
            continue
        collected[path] = body

    # 37 against an empty database. Most of the ~300 public rules 302 to /signup
    # for an anonymous visitor, which is also what Googlebot is, so the set this
    # crawl reaches is close to the set that can be indexed at all. The floor is
    # a vacuity guard: a crawl that reaches nothing passes every assertion below.
    assert len(collected) >= 30, f"the crawl found only {len(collected)} pages; it is broken, not clean"
    return collected


def _ld_nodes(body):
    """Every typed node in the page's JSON-LD, nested ones included.

    A shallow walk of `@graph` misses the shape that caused this whole problem.
    The home page's orphan Organization was never a graph member -- it was the
    `publisher` of the app node, inline and anonymous. A version of this file
    that only looked at top level passed while the defect was still live, which
    is how the gap was found.
    """

    nodes = []

    def visit(value):
        if isinstance(value, dict):
            if value.get("@type"):
                nodes.append(value)
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    for block in _LD_BLOCK.findall(body):
        visit(json.loads(block))
    return nodes


def _organizations(body):
    return [n for n in _ld_nodes(body) if n.get("@type") == "Organization"]


def test_every_json_ld_block_on_every_public_page_parses(pages):
    """The graph is assembled from a template and an injected node.

    `organization_ld()` renders a dict into a `@graph` literal written by hand
    in `terms.html`, `privacy.html` and `index.html`. A trailing comma or a
    mis-indented brace produces a page that looks completely normal and ships
    structured data Google discards in full.
    """

    broken = []
    for path, body in pages.items():
        for block in _LD_BLOCK.findall(body):
            try:
                json.loads(block)
            except ValueError as exc:
                broken.append(f"{path}: {exc}")
    assert not broken, "\n".join(broken)


def test_the_domain_publishes_one_organization_not_several(pages):
    """Every Organization node on the domain agrees about who we are.

    Pinned as a property of the whole crawl rather than of named pages: the node
    that was wrong was always on a page nobody was thinking about.
    """

    seen = 0
    wrong = []
    for path, body in pages.items():
        for node in _organizations(body):
            seen += 1
            actual = (node.get("@id"), node.get("name"), node.get("legalName"))
            if actual != (CANONICAL_ID, BRAND, LEGAL_ENTITY):
                wrong.append(f"{path}: {actual}")

    assert seen >= 10, f"found {seen} Organization nodes; this test is not testing anything"
    assert not wrong, (
        "these pages describe the site's owner differently from the rest of the domain.\n"
        "Under a shared @id that is not a second entity, it is one entity with\n"
        "contradictory properties:\n  " + "\n  ".join(wrong)
    )


def test_no_page_publishes_an_organization_without_an_id(pages):
    """An Organization with no `@id` joins nothing.

    It cannot be consolidated with the canonical node, so it reads as a second,
    thinner claim about a second company. `/` and `/about` each shipped one.
    """

    orphans = [
        f"{path}: {node.get('name')!r}"
        for path, body in pages.items()
        for node in _organizations(body)
        if not node.get("@id")
    ]
    assert not orphans, (
        "an Organization node with no @id corroborates nothing and competes\n"
        "with the canonical one:\n  " + "\n  ".join(orphans)
    )


def test_the_helper_and_the_module_cannot_drift(pages):
    """`bot.organization_ld` must be a rendering of `seo.schema`, not a copy.

    The whole point of passing the node into the templates was to stop each one
    owning a definition. A helper that hand-builds an equivalent dict would pass
    every other test here and reintroduce the original defect the first time
    `seo/schema.py` changed.
    """

    assert json.loads(bot.organization_ld()) == seo_schema.organization_schema()
    assert json.loads(bot.mobile_app_ld()) == seo_schema.mobile_app_schema()


def test_no_indexable_page_presents_the_legal_entity_as_the_site_name(pages):
    """`og:site_name` names the site, so it is the brand and never the company.

    Unlike `<title>`, this field has exactly one correct value across the whole
    domain, which is why it can be pinned without exceptions.
    """

    declared = {}
    for path, body in pages.items():
        match = _SITE_NAME.search(body)
        if match:
            declared[path] = match.group(1)

    assert len(declared) >= 5, f"only {len(declared)} pages declare og:site_name; the regex is wrong"
    wrong = {p: v for p, v in declared.items() if v != BRAND}
    assert not wrong, f"og:site_name must be the brand: {wrong}"


# The eleven titles below still name the company. Renaming them is not the fix
# and would make the problem worse: they describe the crypto and sports product
# this domain used to be, and relabelling them "PulseSoc" would attach the brand
# to pages about a different product rather than detach the company from pages
# about this one. Whether those pages stay indexed at all is a product decision,
# not an engineering one, so they are listed here as a known set instead of
# silently tolerated -- adding a twelfth fails.
TITLES_NAMING_THE_COMPANY = {
    "/about",
    "/arena-preview",
    "/crypto-scam-scanner",
    "/crypto-training-simulator",
    "/education",
    "/education/optimism",
    "/education/scam-alerts",
    "/education/toncoin-scenarios",
    "/predictions/crypto",
    "/quote",
    "/sports-edge",
}


def test_no_new_page_puts_the_company_in_its_title(pages):
    found = set()
    for path, body in pages.items():
        match = _TITLE.search(body)
        if match and "CoinPlotXAI" in match.group(1):
            found.add(path)

    assert found & TITLES_NAMING_THE_COMPANY, "the title scan matched nothing; it is broken"

    added = found - TITLES_NAMING_THE_COMPANY
    assert not added, (
        "a new page names the company in its <title> where it should name the brand:\n  "
        + "\n  ".join(sorted(added))
    )

    # Scored against the pages the crawl actually reached, not the whole pinned
    # set. Which routes return 200 depends on database state, and this file is
    # not the only thing in a pytest process -- run after the protection suite,
    # `/education` stops being reachable and would be reported as fixed. Silence
    # is not evidence: a page nobody looked at has not had its title corrected.
    fixed = (TITLES_NAMING_THE_COMPANY & set(pages)) - found
    assert not fixed, (
        "these are no longer offenders; delete them from TITLES_NAMING_THE_COMPANY\n"
        "so the set keeps meaning what it says:\n  " + "\n  ".join(sorted(fixed))
    )


def test_the_legal_pages_still_name_the_company_in_their_body_text(pages):
    """The change that would look like finishing this job, and would be wrong.

    Every other test in this file pushes in one direction: the company name does
    not belong in the site-identity layer. Run far enough, that becomes a
    find-and-replace across `terms.html` and `privacy.html`, and those two
    documents are the one place the company name is load-bearing. "CoinPlotXAI
    Inc. is not liable" naming PulseSoc instead is a worse document, not a better
    one -- the obligations are the company's and the text has to say whose they
    are.

    A find-and-replace passes everything above. This is the only assertion that
    fails it, which is why it sits at the bottom of the file that would motivate
    it.
    """

    for path in ("/terms", "/privacy"):
        body = pages.get(path)
        assert body is not None, f"{path} was not reached by the crawl"

        title = _TITLE.search(body).group(1)
        assert BRAND in title and LEGAL_ENTITY not in title, title

        prose = body.split("</head>", 1)[-1]
        assert LEGAL_ENTITY in prose, (
            f"{path} no longer names the responsible company anywhere in its body. "
            "The site-identity layer is the scope here; the legal text is not."
        )
