"""The training corpus is a snapshot of the registry, and the snapshot has moved.

``UNDX_TRAINING/03_CAPABILITIES.yaml`` names ``services/undx_capability_registry.py::REGISTRY``
as its ``authority`` and records ``capability_count`` live at build time. It says 87. The
registry holds 125. Both numbers are honest: the corpus was generated once, in
``97c617162`` (2026-08-26), and the registry has taken 11 commits since without the corpus
being rebuilt. So 38 capabilities have never been described to the model at all, 12 of them
``consequential_write``.

That is the general fault behind #39, which was filed against four prose lines. The four
lines are a symptom: ``messages.send`` is one of the 38, and the corpus cannot teach a
refusal that accounts for a capability it does not know exists.

This file does not fix the staleness. Rebuilding the corpus is a content change to training
data, and it cannot be done mechanically anyway — ``scripts/build_undx_training_corpus.py``
hardcodes "87 capabilities", "85 of 87", "read_only: 70 of 87", "reversible_write: 13 of 87"
and "consequential_write: 4 of 87" in six places while computing ``capability_count`` live
from the registry, so a bare regeneration would emit a file whose counted fields say 125 and
whose prose still says 87. Which side is authoritative, and what the corpus should teach
about ``messages.send``, is #39's decision to make.

What this file does is stop the gap growing in silence. The 38 are written down. Add a
capability to the registry without teaching it and these tests fail, naming it, instead of
the corpus quietly falling one further behind. That is the same shape as the CI manifest's
equality ratchet (#34) and the default-deny route-auth gate: a drift that used to be
invisible becomes a declared list somebody has to edit in a diff.

The asymmetry is deliberate. An untaught capability makes the model refuse something it
holds, which fails closed — the gateway is the enforcer either way, so nothing is widened.
A capability taught but no longer granted fails the other way: the model offers an action
the gateway will reject. There are none of those today and
:func:`test_the_corpus_never_teaches_a_capability_the_registry_no_longer_grants` keeps it so.

Related: 23 of these 38 are also among the 26 capabilities with no ``undx_knowledge_map``
record (#38). Two artifacts that describe the registry, both stale in the same direction,
with a shared 23-capability blind spot. Neither gap is evidence for the other — they have
different causes, a missed rebuild here and a merge that dropped one side there — but a
decision on #38 that restores records should expect to touch this list too.
"""

from __future__ import annotations

import collections
import pathlib

import pytest
import yaml

from services import undx_capability_registry as registry

_CORPUS = pathlib.Path(__file__).resolve().parents[2] / "UNDX_TRAINING" / "03_CAPABILITIES.yaml"

#: Registered capabilities that ``03_CAPABILITIES.yaml`` does not describe, as of the
#: corpus built in ``97c617162``. Every entry is a capability the model holds and has not
#: been taught. Ordered by risk then id so the expensive half of the list reads first.
#:
#: Shrinking this list is the goal: teach a capability, delete its line. Growing it is a
#: reviewed act — it means shipping a grant the corpus will not describe, and the diff is
#: the place to say why.
UNTAUGHT_BY_THE_CORPUS: frozenset[str] = frozenset({
    # consequential_write — no cheap undo. The corpus tells the model there are 4 of these
    # in total; these 12 are additional to the 4 it describes.
    "business.profile.update",
    "crypto.portfolio.holding.delete",
    "feed.report",
    "marketplace.listing.create",
    "marketplace.listing.delete",
    "messages.send",
    "profile.bio.update",
    "reels.comment.create",
    "reels.comment.delete",
    "reels.comment.update",
    "reels.delete",
    "settings.privacy.audience.update",
    # reversible_write
    "business.campaign.pause",
    "business.campaign.resume",
    "crypto.portfolio.holding.add",
    "crypto.portfolio.holding.update",
    "crypto.watchlist.add",
    "crypto.watchlist.remove",
    "feed.posts.hide",
    "localization.region.update",
    "localization.translation.update",
    "marketplace.listing.pause",
    "marketplace.listing.resume",
    "marketplace.listing.update",
    "messages.mark_read",
    "notifications.mark_all_read",
    "notifications.mark_read",
    "presence.privacy.update",
    "profile.block",
    "profile.unblock",
    "settings.appearance.theme.update",
    # read_only
    "crypto.market.compare",
    "crypto.market.history",
    "crypto.market.overview",
    "crypto.market.quote",
    "crypto.portfolio.holdings.list",
    "crypto.watchlist.list",
    "private.people.list",
})

#: How the untaught population breaks down by risk. Pinned separately from the id list so a
#: new ``consequential_write`` arriving untaught reports as that, rather than as one more
#: line in a 38-line diff. The corpus's own prose claims 4 consequential writes exist.
UNTAUGHT_RISK_PROFILE = {"consequential_write": 12, "reversible_write": 19, "read_only": 7}


@pytest.fixture(scope="module")
def corpus() -> dict:
    return yaml.safe_load(_CORPUS.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def taught(corpus) -> frozenset[str]:
    return frozenset(entry["id"] for entry in corpus["capabilities"])


def test_the_corpus_states_the_authority_these_tests_compare_it_against(corpus):
    """If the corpus stops claiming the registry as its authority, this comparison is moot."""
    assert corpus["authority"] == "services/undx_capability_registry.py::REGISTRY"


def test_the_corpus_capability_count_matches_the_capabilities_it_lists(corpus, taught):
    """``capability_count`` is generated, so a partial rebuild would show up here first.

    This is the one number in the file that is not stale relative to its own contents. It
    is stale relative to the registry, which is what the rest of this file is about.
    """
    assert corpus["capability_count"] == len(corpus["capabilities"])
    assert len(taught) == corpus["capability_count"], "duplicate capability ids in the corpus"


def test_the_corpus_never_teaches_a_capability_the_registry_no_longer_grants(taught):
    """The direction that misleads rather than merely omitting.

    An untaught capability makes the model decline something it holds. A taught one that
    has been withdrawn makes it offer something the gateway will refuse, after the user has
    been told it will work.
    """
    withdrawn = sorted(taught - set(registry.REGISTRY))
    assert withdrawn == [], (
        "the corpus teaches capabilities that are no longer registered, so UNDX would "
        f"offer an action the gateway now refuses: {withdrawn}"
    )


def test_the_untaught_capabilities_are_exactly_the_declared_gap(taught):
    """The ratchet. A registry addition that skips the corpus fails here, by name."""
    live = set(registry.REGISTRY)
    untaught = live - taught

    newly_untaught = sorted(untaught - UNTAUGHT_BY_THE_CORPUS)
    assert newly_untaught == [], (
        "these capabilities are registered but the training corpus does not describe them, "
        "and they are not in the declared gap: "
        f"{newly_untaught}. Either rebuild the corpus so it teaches them (see #39 first — "
        "scripts/build_undx_training_corpus.py hardcodes counts that a bare rebuild leaves "
        "wrong) or add them to UNTAUGHT_BY_THE_CORPUS with a reason in the commit."
    )

    # Asserted before the next one: an id that is not registered at all is also "no longer
    # untaught", and would otherwise be reported as newly taught, which is the wrong diagnosis.
    stale_entries = sorted(UNTAUGHT_BY_THE_CORPUS - live)
    assert stale_entries == [], (
        "these are declared untaught but are not registered at all, so the declaration is "
        f"carrying dead ids: {stale_entries}"
    )

    now_taught = sorted(UNTAUGHT_BY_THE_CORPUS - untaught - set(stale_entries))
    assert now_taught == [], (
        "these are declared untaught but the corpus describes them now, which is the good "
        f"direction — delete them from UNTAUGHT_BY_THE_CORPUS: {now_taught}"
    )


def test_the_untaught_gap_has_the_recorded_risk_profile(taught):
    """Pinned so a new unteaught ``consequential_write`` reports as one.

    ``07_SECURITY_AND_AUTHORITY.yaml`` teaches "consequential_write: 4 of 87". The registry
    holds 16, and 12 of those are in the gap below — which is why that line is not merely
    out of date but understates, by a factor of four, the population the model is told to
    treat as irreversible.
    """
    untaught = set(registry.REGISTRY) - taught
    profile = collections.Counter(registry.REGISTRY[cid].risk for cid in untaught)
    assert dict(profile) == UNTAUGHT_RISK_PROFILE, (
        "the risk profile of the untaught population changed; if a consequential_write "
        "arrived untaught, teaching it is worth more than declaring it: "
        f"{dict(sorted(profile.items()))}"
    )


def test_every_declared_untaught_capability_is_absent_from_all_twelve_yaml_files():
    """Guards the list against the cheapest way it could go vacuous.

    ``taught`` is read from a single key of a single file. If a rebuild moved capability ids
    elsewhere, every id would read as untaught, and the ratchet above would still pass on the
    strength of this list happening to be long. So each declared id is also required to be
    absent from the raw text of all twelve teaching files — "the model was never told about
    this", rather than "one key does not list it".

    ``13_INGESTION_REPORT.md`` is excluded because it is the build report rather than
    training content. It is not silent on the matter: line 115 states that ``messages.send``
    "is absent from R1", which was true when it was written and is a fifth stale location for
    #39 to correct alongside the four in its description.
    """
    corpus_dir = _CORPUS.parent
    texts = {
        path.name: path.read_text(encoding="utf-8", errors="replace")
        for path in sorted(corpus_dir.glob("*.yaml"))
    }
    assert len(texts) == 12, f"expected twelve teaching files, found {sorted(texts)}"

    described = {
        capability_id: sorted(name for name, text in texts.items() if capability_id in text)
        for capability_id in sorted(UNTAUGHT_BY_THE_CORPUS)
    }
    leaked = {cid: where for cid, where in described.items() if where}
    assert leaked == {}, (
        "these are declared untaught but appear in the corpus text, so either they are "
        f"taught somewhere this list does not read, or the declaration is wrong: {leaked}"
    )
