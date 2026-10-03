"""The open-web distribution surface: what we would hand IndexNow, and nothing else.

This module answers one question -- which canonical URLs are eligible to be
handed to a search engine right now -- and it answers it by *composing* the
existing authorities rather than by deciding anything itself.

It decides no eligibility. `search_visibility` owns canonical URLs and
indexability; the entity lifecycles own public state; `marketplace_seo` owns
per-listing and per-department verdicts. What is left over, and what lives here,
is the part that is genuinely about distribution: deduplication, host agreement,
per-class accounting, and the switch that keeps outbound traffic off.

Why the sources are injected
----------------------------
`candidate_universe` takes the entry lists as an argument instead of building
them. The builders live in `bot` (they need the request-scoped DB handle and
they are shared with the sitemap routes), and `bot` imports this package, so
reaching back would be circular. Injecting them has a second benefit that
matters more: the same three lists feed the sitemaps and feed this, so the two
surfaces cannot drift. A copy of the queries here would be a second opinion
about what is public, which is the one thing this module exists not to be.

The universe is not a change batch
----------------------------------
What this returns is every eligible URL *at this instant*. It is recomputable
from current state, needs no event log, and is roughly the size of the sitemap.
A change batch -- the subset that materially changed since we last said
anything -- is a different object that requires a material-change producer
PulseSoc does not have. Submitting the universe on a schedule as though it were
a batch would send the same unchanged URLs repeatedly and teach the provider to
discount the signal. `PAYLOAD_KIND` is in the payload so the distinction
survives contact with whoever reads it next.

See `docs/seo/02_open_web_distribution_contract.md`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from services import search_visibility

#: One endpoint serves every participating engine; there is no per-engine fan-out.
SUBMIT_ENDPOINT = "https://api.indexnow.org/indexnow"

#: Public protocol verification material, not a secret. The protocol requires it
#: be fetchable by anyone at `https://<host>/<key>.txt` -- that file *is* the
#: proof we control the host, so a key that were secret could not work. 32 hex
#: chars, within the spec's 8-128 range.
KEY = "4d4dc0c2c0f94b7bb8184fd91b7f0b1e"
KEY_PATH = "/indexnow-key.txt"

#: Protocol ceiling per POST. Recorded for the submitter that does not exist
#: yet; PulseSoc's entire eligible universe is three orders of magnitude below
#: it, so batching here is a correctness concern, not a scale one.
MAX_URLS_PER_SUBMISSION = 10000

#: What the payload is. Read the module docstring before changing this.
PAYLOAD_KIND = "eligible-universe"

#: The one host we hold a key for.
HOST = search_visibility.CANONICAL_HOST
ORIGIN = search_visibility.CANONICAL_ORIGIN

CLASS_PAGES = "pages"
CLASS_POSTS = "posts"
CLASS_PRODUCTS = "products"
CLASS_CATEGORIES = "categories"

#: Entity classes that may be distributed, in the order the sitemap index lists
#: their children. Order is fixed so the payload is diffable between calls.
DISTRIBUTABLE_CLASSES = (CLASS_PAGES, CLASS_POSTS, CLASS_CATEGORIES, CLASS_PRODUCTS)

#: Classes that exist, are public, and are still not submitted -- with the
#: reason, because "absent from a list" is indistinguishable from "forgotten".
WITHHELD_CLASSES = {
    "live": "ephemeral; a room that ends before the crawler arrives is a 404 we asked for. Needs the live lifecycle contract.",
    "replays": "retention and takedown policy not frozen; a submitted replay that is later removed is the deletion case we cannot yet honour.",
    "sitemaps": "engines learn about sitemaps from robots.txt and Webmaster tools; IndexNow carries content URLs.",
    "feeds": "the Merchant Center feed is noindex,follow by design -- it is a feed, not a page.",
}

#: The kill switch. Declared in `.env.example`; absent means off.
SUBMIT_ENABLED_VAR = "OPEN_WEB_SUBMIT_ENABLED"


def _is_rooted_path(path):
    """A candidate must be a rooted relative path, not an absolute URL.

    Found by test rather than by reading: `canonical_url` normalises its input by
    prepending the origin, so handing it `https://evil.example.com/x` returns
    `https://pulsesoc.com/https://evil.example.com/x`. That URL is *on-host*, so
    the host assertion passes it, and it is a guaranteed 404 -- a submission
    asking an engine to hurry up and fetch nothing.

    Not an open redirect and not a host leak; the hardcoded origin holds. It is a
    malformed-input hole, and the input that would reach it is a seller- or
    importer-supplied URL stored where a path was expected, which is exactly the
    shape of data the marketplace carries.

    `//evil.example.com` is rejected for the same reason in the other direction:
    it is protocol-relative, and a consumer joining it against a scheme gets a
    foreign host back.
    """

    candidate = (path or "").strip()
    if not candidate.startswith("/"):
        return False
    if candidate.startswith("//"):
        return False
    return ":" not in candidate.split("?", 1)[0]


def _is_not_content(path):
    """Machine-readable surfaces, rejected here because the policy table allows them.

    This check exists because of a measured surprise: `sitemap_eligible` returns
    **true** for `/sitemap.xml` and `/sitemap-pages.xml`, with the reason
    "public content". That is defensible for the policy's own purpose -- those
    URLs are public, crawlable and not noindex -- but it means the sitemaps are
    kept out of the submission payload today only by the accident that no source
    class supplies them. Hand them in and they would sail through.

    An engine learns about our sitemaps from `robots.txt` and from Webmaster
    tools. Pushing a sitemap URL through a content-change channel asks it to
    re-fetch an index in order to discover URLs we are simultaneously telling it
    about directly, which is at best redundant and at worst a self-reinforcing
    loop.

    The match is deliberately narrow -- `/sitemap*.xml`, not `/sitemap*` -- so a
    future article at `/sitemaps-explained` is not silently withheld.
    """

    normalised = (path or "").split("?", 1)[0]
    if normalised.startswith("/sitemap") and normalised.endswith(".xml"):
        return True
    return normalised.startswith("/feeds/")


@dataclass(frozen=True)
class Candidate:
    url: str
    path: str
    entity_class: str
    lastmod: str = ""


@dataclass(frozen=True)
class Universe:
    """One snapshot of the eligible set, with its own accounting."""

    candidates: tuple
    included: dict
    excluded: dict
    duplicates: int

    @property
    def urls(self):
        return [candidate.url for candidate in self.candidates]

    @property
    def total(self):
        return len(self.candidates)


def _env_bool(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def outbound_enabled(env_mode):
    """Whether an outbound submission is permitted. Four conditions, all affirmative.

    `env_mode` is required rather than defaulted because resolving it means
    reading four Railway/Render variables in a particular precedence, `bot`
    already does that once into `COINPILOTX_ENV_MODE`, and a second resolution
    here would be a second answer to "which environment is this".

    The reason this gate cannot be built out of host correctness: `canonical_url`
    hardcodes the production origin, so a staging container boots already
    emitting production URLs under a valid production key. That is the right
    default for injection resistance and it means staging is *indistinguishable
    from production by payload inspection*. The usual tell -- staging URLs in a
    staging payload -- does not exist here, so the environment has to say so
    itself, and an unset environment has to mean no.

    Nothing calls this to send anything. There is no submitter in this branch.
    """

    if not _env_bool(SUBMIT_ENABLED_VAR, default=False):
        return False
    if (env_mode or "").strip().lower() != "production":
        return False
    if HOST != search_visibility.CANONICAL_HOST:
        return False
    return ORIGIN.endswith(HOST)


def batch_is_submittable(urls):
    """Every URL in a batch must belong to the declared host, checked before sending.

    IndexNow answers a batch containing a URL the host does not own with `422`,
    and it rejects *the whole batch* -- one bad URL loses the other 9,999. So
    this is asserted rather than assumed, even though every URL in the universe
    came out of `canonical_url` and therefore structurally cannot be off-host.
    The assertion is for the batch that was assembled some other way later.
    """

    if not urls:
        return False
    if len(urls) > MAX_URLS_PER_SUBMISSION:
        return False
    return all(str(url).startswith(ORIGIN + "/") for url in urls)


def _pairs(entries):
    for entry in entries or []:
        if isinstance(entry, (tuple, list)):
            path = entry[0]
            lastmod = entry[1] if len(entry) > 1 else ""
        else:
            path, lastmod = entry, ""
        if path:
            yield str(path), str(lastmod or "")


def _exclusion_reason(path):
    """Why `sitemap_eligible` said no, in a form safe to publish as a count label.

    `classify` carries a reason per rule, but it is query-blind by design -- it
    answers for a request path, where `?page=2` is the same page shape as the
    hub. `sitemap_eligible` adds the query check on top, because a sitemap entry
    is a canonical claim rather than a request. So a path can be rejected with
    `classify` still reporting it eligible, and that case needs its own label
    instead of borrowing a reason that does not apply.
    """

    decision = search_visibility.classify(path)
    if not decision.sitemap_eligible:
        return decision.reason
    return "non-canonical query string"


def candidate_universe(sources):
    """The eligible universe, from per-class `(path, lastmod)` entries.

    `sources` maps an entity class to its entries. Classes are consumed in
    `DISTRIBUTABLE_CLASSES` order and an unknown class is ignored rather than
    guessed at -- adding a class is a contract change, so it should require
    editing this module.

    Three things happen to each entry, in this order, and the order matters:

    `sitemap_eligible` is re-applied even though every supplied builder already
    applied it. It is cheap, and it means a caller that hands over a raw path
    list cannot bypass the gate by accident -- the same reasoning
    `seo_engine.sitemap_xml` uses for re-checking its own callers.

    `canonical_url` converts the path, so the payload carries no path that is
    not the canonical form of itself.

    Deduplication is on the resulting URL, not on the input path, because the
    URLs are what get submitted and two paths can canonicalise together.
    `seo.content.all_public_paths()` returns `/sports-edge` twice as of
    `5bdf4e431`; the sitemap never showed it because that caller wraps the list
    in `set()`, and the IndexNow payload shipped it twice because this step did
    not exist. Duplicates are counted, not silently absorbed -- a count that
    starts climbing is the signal that an upstream list has grown a repeat.
    """

    candidates = []
    included = {}
    excluded = {}
    seen = set()
    duplicates = 0

    for entity_class in DISTRIBUTABLE_CLASSES:
        kept = 0
        for path, lastmod in _pairs(sources.get(entity_class)):
            if not _is_rooted_path(path):
                excluded.setdefault("not a rooted path", 0)
                excluded["not a rooted path"] += 1
                continue
            if _is_not_content(path):
                excluded.setdefault("not a content page", 0)
                excluded["not a content page"] += 1
                continue
            if not search_visibility.sitemap_eligible(path):
                reason = _exclusion_reason(path)
                excluded.setdefault(reason, 0)
                excluded[reason] += 1
                continue
            url = search_visibility.canonical_url(path)
            if not url.startswith(ORIGIN + "/"):
                excluded.setdefault("off-host", 0)
                excluded["off-host"] += 1
                continue
            if url in seen:
                duplicates += 1
                continue
            seen.add(url)
            candidates.append(Candidate(url=url, path=path, entity_class=entity_class, lastmod=lastmod))
            kept += 1
        included[entity_class] = kept

    return Universe(
        candidates=tuple(candidates),
        included=included,
        excluded=excluded,
        duplicates=duplicates,
    )


def payload(sources, env_mode):
    """The IndexNow document we would submit, plus the evidence for why.

    The protocol fields (`host`, `key`, `keyLocation`, `urlList`) are exactly
    what a POST body would carry. Everything else is diagnostics, and the
    diagnostics are counts and reasons only -- never the id or title of an
    entity we excluded, since the interesting exclusions are the private ones
    and naming them would publish what the exclusion exists to protect.

    `coverage.indexed` is absent on purpose. We have no provider observation for
    Bing (no Webmaster property is verified), and an `indexed: 0` would read as
    "Bing rejected everything" rather than "we do not know". UNKNOWN is a
    publishable value; a zero that means unknown is not.
    """

    universe = candidate_universe(sources)
    return {
        "host": HOST,
        "key": KEY,
        "keyLocation": f"{ORIGIN}{KEY_PATH}",
        "urlList": universe.urls,
        "submitEndpoint": SUBMIT_ENDPOINT,
        "payloadKind": PAYLOAD_KIND,
        "diagnostics": {
            "candidateCount": universe.total,
            "includedByClass": dict(universe.included),
            "excludedByReason": dict(universe.excluded),
            "duplicatesDropped": universe.duplicates,
            "withheldClasses": dict(WITHHELD_CLASSES),
            "maxUrlsPerSubmission": MAX_URLS_PER_SUBMISSION,
            "batchWouldBeSubmittable": batch_is_submittable(universe.urls),
            "outboundEnabled": outbound_enabled(env_mode),
            "outboundImplemented": False,
            "environmentMode": env_mode,
            "bingCoverage": "unknown",
        },
    }
