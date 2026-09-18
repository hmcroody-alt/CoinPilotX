"""The indexability policy, asserted as behaviour rather than as structure.

``services/search_visibility.py`` exists because the answer to "may a search
engine index this?" used to live in four places that disagreed, and the visible
symptom was ``/signup`` shipping ``noindex,nofollow`` while also sitting in
sitemap.xml. A module that merely *exists* does not fix that. So the tests below
are written against the two failure modes that would let the same bug back in.

**The vacuous pass.** A policy that answered "no" to everything would satisfy
every privacy assertion in this file and quietly remove the entire site from
Google. Every deny test is therefore paired with an allow test on the nearest
neighbouring input, and ``test_a_good_page_and_a_good_record_are_indexable``
exists solely as the floor: if it fails, no other passing test in this file
means anything.

**The contradiction.** The single invariant the old code broke is that a
sitemap entry must be indexable. It is asserted here over the whole rule table
rather than over a handful of examples, so a rule added later is covered
without anyone remembering to extend the test.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from services import search_visibility as sv  # noqa: E402


# ---------------------------------------------------------------------------
# The floor
# ---------------------------------------------------------------------------


def test_a_good_page_and_a_good_record_are_indexable():
    """If this fails, every other test in this file is passing vacuously."""

    decision = sv.classify("/pulse/post/123")
    assert decision.indexable is True
    assert decision.sitemap_eligible is True
    assert decision.directive == sv.INDEX_DIRECTIVE

    record = {
        "visibility": "public",
        "moderation_status": "approved",
        "body": "x" * 400,
    }
    assert sv.content_eligibility(record).indexable is True
    assert sv.sitemap_eligible("/pulse/post/123", record) is True


# ---------------------------------------------------------------------------
# Path classification
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/api/pulse/feed",
        "/admin",
        "/admin/business-os",
        "/webhook/stripe",
        "/.well-known/apple-app-site-association",
        "/static/js/pulse_app_promotion.js",
        "/dashboard",
        "/account/security",
        "/settings",
        "/messages/42",
        "/chat",
        "/pulse/messages",
        "/pulse/settings",
        "/portfolio",
        "/checkout",
        "/billing",
        "/seller/orders",
        "/private-office",
        "/logout",
        "/reset-password/abc123",
        "/verify/token",
        "/oauth/callback",
        "/open/pulse/post/9",
    ],
    ids=lambda p: p,
)
def test_operational_and_personal_paths_are_never_indexable(path):
    decision = sv.classify(path)
    assert decision.indexable is False
    assert decision.sitemap_eligible is False
    assert decision.reason


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/about",
        "/help",
        "/pulse/post/1781",
        "/pulse/profile/someone",
        "/intel/bitcoin-outlook",
    ],
    ids=lambda p: p,
)
def test_public_paths_stay_indexable(path):
    """The neighbouring-allow half of the pairs above."""

    assert sv.classify(path).indexable is True


@pytest.mark.parametrize(
    "path",
    ["/signup", "/login", "/search", "/markets/btc", "/country-intelligence/kenya"],
    ids=lambda p: p,
)
def test_excluded_pages_that_are_crawl_paths_keep_follow(path):
    """``noindex,follow`` is not a softer ``noindex,nofollow``.

    These pages carry links into content we do want ranked -- the footer on
    ``/signup``, the result links on ``/search``, the section links on a market
    page. Downgrading any of them to ``nofollow`` amputates a section of the
    site from Google's crawl, which is invisible for weeks and slow to recover.
    """

    decision = sv.classify(path)
    assert decision.indexable is False
    assert decision.directive == sv.NOINDEX_FOLLOW
    assert "nofollow" not in decision.directive


def test_prefix_rules_do_not_swallow_unrelated_paths():
    """``/search`` must not capture ``/searchlight``.

    A naive ``startswith`` would, and the damage is silent: a real public page
    stops asking to be ranked and nothing errors.
    """

    for path in ("/searchlight", "/accounts-receivable-guide", "/settlements", "/openings"):
        assert sv.classify(path).indexable is True, path


@pytest.mark.parametrize(
    "variant",
    ["/dashboard", "/dashboard/", "/DASHBOARD", "/dashboard?tab=1", "/dashboard#top"],
    ids=lambda p: p,
)
def test_classification_normalises_case_slashes_and_query_strings(variant):
    assert sv.classify(variant).indexable is False


def test_the_root_path_survives_normalisation():
    """``"/".rstrip("/")`` is the empty string; the root must not become one."""

    assert sv.classify("/").indexable is True
    assert sv.classify("").indexable is True
    assert sv.classify(None).indexable is True


# ---------------------------------------------------------------------------
# The invariant the old code broke
# ---------------------------------------------------------------------------


def test_no_rule_can_produce_a_noindex_sitemap_entry():
    """Asserted over the whole table, so a rule added later is covered too."""

    for prefix, _directive, _reason in sv._RULES:
        decision = sv.classify(prefix)
        assert decision.sitemap_eligible is False, prefix
        assert decision.indexable is False, prefix


def test_signup_is_not_sitemap_eligible():
    """The exact contradiction that motivated the module.

    ``seo/content.py:all_public_paths()`` hardcodes ``/signup`` into the
    sitemap while the page itself ships ``noindex``.
    """

    assert sv.sitemap_eligible("/signup") is False


def test_a_page_that_always_redirects_is_not_recommended_to_google():
    """``/day-signal`` calls ``require_account()`` and 302s anonymous visitors.

    Googlebot is anonymous, so the redirect is the only response it has ever
    received -- and the URL was in sitemap-pages.xml regardless. It keeps
    ``follow`` because ``seo/content.py`` links to it from three public pages.
    """

    decision = sv.classify("/day-signal")
    assert decision.sitemap_eligible is False
    assert decision.directive == sv.NOINDEX_FOLLOW


# ---------------------------------------------------------------------------
# Canonical aliases
# ---------------------------------------------------------------------------


def test_an_alias_leaves_the_sitemap_without_being_deindexed():
    """``/support`` and ``/help`` are two decorators on one handler.

    The page already emits ``canonical: /help``. Submitting it in the sitemap
    as well asks Google to crawl a URL we have declared non-canonical.
    """

    decision = sv.classify("/support")
    assert decision.sitemap_eligible is False
    assert sv.sitemap_eligible("/support") is False
    assert "/help" in decision.reason


def test_an_alias_must_not_be_given_noindex():
    """The pair Google warns about: ``noindex`` beside a cross-page canonical.

    The canonical says "credit /help instead"; a ``noindex`` on the same page
    says "drop this", and the documented risk is that the drop propagates to
    the canonical target. Removing ``/support`` from the sitemap must not be
    implemented by de-indexing it -- that would put ``/help`` at risk to tidy
    up a duplicate that the canonical already resolved.
    """

    decision = sv.classify("/support")
    assert decision.indexable is True
    assert decision.directive == sv.INDEX_DIRECTIVE
    assert "noindex" not in decision.directive


def test_canonical_url_resolves_an_alias_to_its_target():
    assert sv.canonical_url("/support") == "https://pulsesoc.com/help"
    assert sv.canonical_url("/support/") == "https://pulsesoc.com/help"
    assert sv.canonical_url("/SUPPORT") == "https://pulsesoc.com/help"
    assert sv.canonical_url("/support?utm_source=x") == "https://pulsesoc.com/help"


def test_the_alias_target_is_itself_sitemap_eligible():
    """An alias that pointed at an excluded page would remove both from the
    sitemap and leave the content with no submitted URL at all."""

    for target in sv._CANONICAL_ALIASES.values():
        assert sv.sitemap_eligible(target) is True, target
        assert sv.canonical_url(target) == sv.CANONICAL_ORIGIN + target, target


def test_no_alias_shadows_a_rule():
    """A path cannot be both an alias and rule-classified.

    ``_RULES`` is checked first, so an alias added under an excluded prefix
    would be silently dead -- and the dead entry would read as working.
    """

    for alias in sv._CANONICAL_ALIASES:
        assert sv.classify(alias).reason.startswith("canonical alias"), alias


def test_indexable_and_directive_never_disagree():
    paths = ["/", "/about", "/signup", "/markets/eth", "/api/x", "/pulse/post/5"]
    for path in paths:
        decision = sv.classify(path)
        assert decision.indexable == decision.directive.startswith("index"), path


# ---------------------------------------------------------------------------
# Canonical URLs
# ---------------------------------------------------------------------------


def test_canonical_url_drops_app_intent_and_tracking_parameters():
    """``?pulse_app=1`` reaches the same page and must not compete with it."""

    assert sv.canonical_url("/pulse/post/7?pulse_app=1") == "https://pulsesoc.com/pulse/post/7"
    assert sv.canonical_url("/about?utm_source=x&utm_medium=y") == "https://pulsesoc.com/about"


def test_canonical_url_is_absolute_on_one_host():
    assert sv.canonical_url("/about") == "https://pulsesoc.com/about"
    assert sv.canonical_url("about") == "https://pulsesoc.com/about"
    assert sv.canonical_url("/about/") == "https://pulsesoc.com/about"
    assert sv.canonical_url("/") == "https://pulsesoc.com/"


# ---------------------------------------------------------------------------
# Content eligibility
# ---------------------------------------------------------------------------


def _good_record(**overrides):
    record = {
        "visibility": "public",
        "moderation_status": "approved",
        "status": "published",
        "body": "A genuinely written post about something, long enough to be a "
        "search destination rather than a fragment. " * 3,
    }
    record.update(overrides)
    return record


@pytest.mark.parametrize(
    "overrides,expected_reason_fragment",
    [
        ({"deleted_at": "2026-09-01"}, "deleted"),
        ({"is_deleted": 1}, "deleted"),
        ({"takedown_at": "2026-09-01"}, "removed"),
        ({"visibility": "private"}, "visibility"),
        ({"visibility": "followers"}, "visibility"),
        ({"moderation_status": "pending"}, "moderation"),
        ({"moderation_status": "rejected"}, "moderation"),
        ({"status": "draft"}, "not published"),
        ({"status": "scheduled"}, "not published"),
    ],
)
def test_ineligible_records_are_excluded_with_a_stated_reason(overrides, expected_reason_fragment):
    decision = sv.content_eligibility(_good_record(**overrides))
    assert decision.indexable is False
    assert decision.sitemap_eligible is False
    assert expected_reason_fragment in decision.reason


def test_the_two_privacy_gates_fail_closed_when_absent():
    """Visibility and moderation are the only fields without a permissive default.

    Every other column may be missing -- schemas differ across the tables this
    runs against. These two may not: an unset ``visibility`` on a record we are
    about to advertise to Google is missing information, not permission.
    """

    assert sv.content_eligibility({"body": "x" * 400}).indexable is False
    assert sv.content_eligibility({"body": "x" * 400, "visibility": "public"}).indexable is False
    assert sv.content_eligibility({}).indexable is False
    assert sv.content_eligibility(None).indexable is False


@pytest.mark.parametrize(
    "field", ["search_opt_out", "noindex", "hide_from_search"]
)
@pytest.mark.parametrize("value", [True, 1, "1", "true", "yes", "on"])
def test_a_creator_opt_out_overrides_an_otherwise_perfect_record(field, value):
    """Making a profile public is not consent to be indexed.

    This is the one signal that wins against everything above it being correct,
    which is exactly why it is tested against ``_good_record()`` -- a record
    that would otherwise be indexed.
    """

    assert sv.content_eligibility(_good_record()).indexable is True  # control

    decision = sv.content_eligibility(_good_record(**{field: value}))
    assert decision.indexable is False
    assert decision.sitemap_eligible is False
    assert "opted out" in decision.reason
    # Opting out of search is not a request to have one's links ignored.
    assert decision.directive == sv.NOINDEX_FOLLOW


@pytest.mark.parametrize("value", [False, 0, "0", "false", "no", "", None])
def test_a_falsey_opt_out_is_not_an_opt_out(value):
    """``"false"`` is a truthy Python string; treating it as an opt-out would
    silently de-index every record whose column defaults to that text."""

    assert sv.content_eligibility(_good_record(search_opt_out=value)).indexable is True


def test_thin_content_is_excluded_but_stays_crawlable():
    decision = sv.content_eligibility(_good_record(body="Nice.", title="Hi"))
    assert decision.indexable is False
    assert decision.directive == sv.NOINDEX_FOLLOW
    assert "insufficient" in decision.reason


def test_a_substantial_title_rescues_a_short_body():
    """Short posts are not bad content. A real headline is a real destination."""

    record = _good_record(body="Short.", title="PulseSoc marketplace payouts explained")
    assert sv.content_eligibility(record).indexable is True


def test_body_may_arrive_under_any_of_the_column_names_in_use():
    for column in ("body", "content", "description"):
        record = _good_record()
        record.pop("body")
        record[column] = "x" * 400
        assert sv.content_eligibility(record).indexable is True, column


# ---------------------------------------------------------------------------
# The single sitemap gate
# ---------------------------------------------------------------------------


def test_sitemap_gate_requires_both_the_path_and_the_record():
    assert sv.sitemap_eligible("/pulse/post/5", _good_record()) is True
    assert sv.sitemap_eligible("/pulse/post/5", _good_record(visibility="private")) is False
    assert sv.sitemap_eligible("/dashboard", _good_record()) is False
