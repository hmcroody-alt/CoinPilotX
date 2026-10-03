"""Every legal URL the iPhone app calls canonical has to resolve on this server.

``mobile-native/src/screens/settings/legalContent.ts`` ships five documents, and
each one tells the member in its own words that the ``canonicalUrl`` carries the
full, legally operative version. The app is already in the App Store, so that
sentence is a live claim made by a build nobody can edit -- the only side of the
contract still under our control is this server.

All five 404'd in production when this was audited (2026-09-29), against live
build 1.0.2. All five resolve now: three as 301s to the pages that already
publish that text, and ``/legal/cookies`` and ``/legal/licenses`` as pages of
their own, which they had to be because there was nothing to redirect them to.

``PENDING_PUBLICATION`` is empty as a result, and it cuts both ways on purpose:

* A sixth in-app document with a new ``canonicalUrl`` fails here until that URL
  is either served or added to ``PENDING_PUBLICATION`` with a reason. Shipping a
  document that points at nothing is the defect this test exists for.
* Leaving an entry in ``PENDING_PUBLICATION`` after publishing the page also
  fails. A list of known gaps that can go stale in the direction of looking
  worse than reality is a list nobody trusts -- and that is how it emptied: the
  two entries below were deleted because this file started failing when the two
  pages shipped.

The URLs are read out of the app's own source rather than restated here, because
a copy of them in this file would be a second thing to keep in step and the app
is the side that makes the promise.
"""

from __future__ import annotations

import os
import pathlib
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

_HANDLE, _DB_PATH = tempfile.mkstemp(suffix=".db", prefix="app_canonical_legal_urls_")
os.close(_HANDLE)
os.environ["DATABASE_URL"] = f"sqlite:///{_DB_PATH}"

import bot  # noqa: E402
from services import route_auth  # noqa: E402
from services import search_visibility as sv  # noqa: E402


REPO = pathlib.Path(__file__).resolve().parents[2]
LEGAL_CONTENT = REPO / "mobile-native" / "src" / "screens" / "settings" / "legalContent.ts"

#: Canonical URLs the shipped app promises and this server does not yet answer,
#: mapped to why. Empty, and meant to stay that way: an entry here is a live
#: build telling a member that a 404 carries the operative version of a legal
#: document. It held `/legal/cookies` and `/legal/licenses` until both were
#: published on 2026-10-02 (D-L1 in docs/legal/PULSE_LEGAL_SURFACE_AUDIT.md).
PENDING_PUBLICATION: dict[str, str] = {}


def _canonical_urls() -> list[str]:
    source = LEGAL_CONTENT.read_text(encoding="utf-8")
    urls = re.findall(r'canonicalUrl:\s*"([^"]+)"', source)
    assert urls, f"found no canonicalUrl declarations in {LEGAL_CONTENT}"
    return urls


URLS = _canonical_urls()
PATHS = [url[len(sv.CANONICAL_ORIGIN):] for url in URLS if url.startswith(sv.CANONICAL_ORIGIN)]
SERVED = [path for path in PATHS if path not in PENDING_PUBLICATION]


@pytest.fixture(scope="module")
def client():
    return bot.webhook_app.test_client()


def test_every_canonical_url_points_at_this_site():
    """A legal document pointing off-origin is pointing at someone else's terms."""

    off_origin = [url for url in URLS if not url.startswith(sv.CANONICAL_ORIGIN + "/")]
    assert not off_origin, f"canonicalUrl values outside {sv.CANONICAL_ORIGIN}: {off_origin}"


def test_the_pending_list_names_only_urls_the_app_actually_ships():
    """Otherwise the known-gaps list outlives the gap and starts misleading.

    If the app stops shipping one of these documents, this entry has to go with
    it -- a reason recorded against a URL nobody requests is noise that makes the
    rest of the list look equally speculative.
    """

    stale = sorted(set(PENDING_PUBLICATION) - set(PATHS))
    assert not stale, f"PENDING_PUBLICATION names URLs the app no longer ships: {stale}"


@pytest.mark.parametrize("path", SERVED)
def test_each_served_legal_url_resolves_for_a_signed_out_visitor(client, path):
    """No login wall. The member reading the Terms may not have an account yet,
    and Apple's reviewer never signs in to check a legal link."""

    response = client.get(path)
    assert response.status_code in (200, 301, 308), (
        f"{path} answered {response.status_code}; the shipped app calls this the "
        "canonical location of a legally operative document"
    )
    if response.status_code == 200:
        return
    target = response.headers["Location"]
    landed = client.get(target)
    assert landed.status_code == 200, (
        f"{path} redirects to {target}, which answered {landed.status_code}"
    )
    assert len(landed.get_data()) > 2000, f"{target} rendered {len(landed.get_data())} bytes"


@pytest.mark.parametrize("path", SERVED)
def test_each_served_legal_url_is_declared_public(client, path):
    """The default-deny route-auth gate accepts a declaration, not a guess.

    Asserting it here as well says *which* answer is the right one for these
    three specifically: a terms page behind ``@auth_required`` would satisfy that
    gate and still break the app.
    """

    view = bot.webhook_app.view_functions[
        next(r.endpoint for r in bot.webhook_app.url_map.iter_rules() if r.rule == path)
    ]
    declaration = route_auth.declaration_of(view)
    assert declaration, f"{path} carries no route_auth declaration"
    assert declaration["kind"] == route_auth.AUTH_PUBLIC, (
        f"{path} is declared {declaration['kind']}; the app links it to members "
        "who are not signed in"
    )
    assert declaration["reason"].strip(), f"{path} is declared public with no reason"


@pytest.mark.parametrize("path", sorted(PENDING_PUBLICATION))
def test_a_pending_url_that_started_working_leaves_the_pending_list(client, path):
    """Publishing the page is the fix. This fails when the fix lands, and the
    edit it asks for is deleting the entry -- which is how the list stays true."""

    assert client.get(path).status_code == 404, (
        f"{path} now answers. Remove it from PENDING_PUBLICATION: "
        f"{PENDING_PUBLICATION[path]}"
    )


def test_a_redirect_lands_in_one_hop(client):
    """Two hops is a chain to maintain and a place for a loop to hide."""

    for path in SERVED:
        response = client.get(path)
        if response.status_code == 200:
            continue
        assert client.get(response.headers["Location"]).status_code == 200, (
            f"{path} -> {response.headers['Location']} does not land in one hop"
        )
