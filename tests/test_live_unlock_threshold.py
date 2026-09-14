"""The Live unlock requirement is two certified invites, stated once.

This suite exists because the product shipped two different answers to the same
question. ``privilege_engine`` opened the gate at the ``live_creator`` rung —
two invites — while ``bridge.referral_status`` reported the *Founding Path*
target of thirty as ``required``, and five surfaces typed "Invite 30 real
members" into their copy by hand. A creator who had earned Live was told they
were 2/30 of the way there.

So the assertions here are about *agreement* as much as about the number. Every
test either pins the single source of truth or proves that some second opinion
cannot exist: the fallback mirrors the constant, the status payload reports the
gate rather than the campaign, and the strings are generated from the threshold
instead of containing it.

Mutation contract: raising the canonical threshold back to 30 must turn this
file red. The boundary cases (1 → locked, 2 → unlocked) are asserted exactly,
so a threshold that moves in either direction is caught rather than sampled.
"""

import os
import re
import sys
import tempfile

# Bind a throwaway database before anything can import ``services.db``. An empty
# DATABASE_URL falls back to the developer's real ``coinpilotx.db``.
os.environ.setdefault(
    "DATABASE_URL",
    "sqlite:///" + os.path.join(tempfile.mkdtemp(prefix="live_unlock_"), "test.db"),
)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest  # noqa: E402

from services import privilege_engine  # noqa: E402
from services.business_os.progress import bridge  # noqa: E402
from services.business_os.progress import campaign as campaign_mod  # noqa: E402

REPO_ROOT = os.path.join(os.path.dirname(__file__), "..")
BOT_PY = os.path.join(REPO_ROOT, "bot.py")


class _DeadCursor:
    """A cursor that cannot answer, so every count fails closed to zero and
    whatever ``required`` comes back as was decided by the campaign, not a row.
    """

    def execute(self, *_args, **_kwargs):
        raise RuntimeError("database unavailable")


# =====================================================================
# One source of truth
# =====================================================================

def test_live_001_canonical_threshold_is_two():
    assert campaign_mod.LIVE_MIN_VERIFIED_MEMBERS == 2


def test_live_002_ladder_rung_carries_the_canonical_constant():
    """The milestone must not hold a number of its own.

    ``live_threshold()`` reads the ladder, so a literal typed into the rung
    would become a second threshold that still looks derived from the outside.
    """
    assert campaign_mod.get().live_threshold() == campaign_mod.LIVE_MIN_VERIFIED_MEMBERS
    live_rungs = [m for m in campaign_mod.get().milestones
                  if m.kind == campaign_mod.LIVE_ELIGIBILITY]
    assert len(live_rungs) == 1, "exactly one rung may unlock Live"
    assert live_rungs[0].threshold == 2


def test_live_003_gate_reads_two():
    assert privilege_engine.live_creator_threshold() == 2


def test_live_004_live_gate_is_not_the_campaign_target():
    """The Founding Path target and the Live gate are different promises.

    Conflating them is the original defect: the whole thirty-invite challenge
    was demanded for a rung the ladder grants at two.
    """
    camp = campaign_mod.get()
    assert camp.qualification_target == 30
    assert camp.live_threshold() == 2
    assert camp.live_threshold() != camp.qualification_target


# =====================================================================
# The gate cannot move when Progress OS is down
# =====================================================================

def test_live_005_fallback_mirrors_the_canonical_constant():
    """``privilege_engine`` cannot import the constant on the path that needs

    it — that path exists precisely because the import failed — so the mirror
    is pinned here instead. Previously it read 30, which would have re-locked
    Live for every creator who qualified under the real threshold at exactly
    the moment the system was least able to explain why.
    """
    assert privilege_engine.LIVE_THRESHOLD_FALLBACK == campaign_mod.LIVE_MIN_VERIFIED_MEMBERS


def test_live_006_threshold_stays_two_when_progress_os_is_unimportable(monkeypatch):
    monkeypatch.setitem(sys.modules, "services.business_os.progress.campaign", None)
    assert privilege_engine.live_creator_threshold() == 2


# =====================================================================
# Backend eligibility: 0 and 1 locked, 2 and 3+ unlocked
# =====================================================================

@pytest.mark.parametrize("referrals,expected", [(0, False), (1, False),
                                                (2, True), (3, True), (30, True)])
def test_live_007_can_go_live_boundary(referrals, expected):
    """Trust is high and Live status is ``locked``, so the invite count is the

    only thing under test. A user is not handed Live by the privilege ladder
    here — ``live_status`` stays locked — which keeps this an assertion about
    the referral gate and nothing else.
    """
    privileges = privilege_engine.get_user_privileges(
        user_id=4242, trust_score=60, referral_count=referrals,
        verification_types=["identity"], live_status="locked",
    )
    assert privileges["can_go_live"] is expected
    assert privileges["can_host_live_rooms"] is expected


def test_live_008_next_step_asks_for_the_remaining_invites_only():
    steps = privilege_engine.get_user_privileges(
        user_id=4242, trust_score=60, referral_count=1, live_status="locked",
    )["required_next_steps"]
    assert "Invite 1 more real member to unlock Live." in steps

    done = privilege_engine.get_user_privileges(
        user_id=4242, trust_score=60, referral_count=2, live_status="locked",
    )["required_next_steps"]
    assert not [s for s in done if "unlock Live" in s]


# =====================================================================
# The status payload reports the gate, not the campaign
# =====================================================================

def test_live_009_referral_status_required_is_the_live_gate():
    standing = bridge.referral_status(_DeadCursor(), 4242)
    assert standing["required"] == 2
    assert standing["required"] == campaign_mod.get().live_threshold()
    assert standing["remaining"] == 2


def test_live_010_legacy_grandfather_count_is_not_the_gate():
    """Thirty survives in exactly one place, and it is not a threshold.

    ``LEGACY_LIVE_UNLOCK_COUNT`` decides who already earned Live under the
    pre-Progress-OS rule and must keep it. It must never be read as the current
    requirement.
    """
    assert bridge.LEGACY_LIVE_UNLOCK_COUNT == 30
    assert bridge.LEGACY_LIVE_UNLOCK_COUNT != campaign_mod.LIVE_MIN_VERIFIED_MEMBERS


# =====================================================================
# UI copy is generated from the gate, never typed beside it
# =====================================================================

def test_live_011_unlock_sentence_states_the_canonical_number():
    assert privilege_engine.live_unlock_sentence() == (
        "Invite 2 real members and build verified trust to unlock Live."
    )


@pytest.mark.parametrize("completed,expected", [
    (0, "0 of 2 verified members"),
    (1, "1 of 2 verified members"),
    (2, "Live unlocked"),
    (3, "Live unlocked"),
])
def test_live_012_progress_label(completed, expected):
    assert privilege_engine.live_progress_label(completed) == expected


@pytest.mark.parametrize("completed,expected", [
    (0, "Invite 2 real members and build verified trust to unlock Live. 0 of 2 verified members."),
    (1, "Invite 2 real members and build verified trust to unlock Live. 1 of 2 verified members."),
    (2, "Live unlocked."),
    (3, "Live unlocked."),
])
def test_live_013_status_line_stops_asking_once_unlocked(completed, expected):
    assert privilege_engine.live_unlock_status_line(completed) == expected


@pytest.mark.parametrize("referrals", [0, 1, 2, 3, 30])
def test_live_014_copy_and_backend_never_disagree(referrals):
    """The whole point of the fix: the string and the gate are one decision.

    "Live unlocked" on the page must mean ``can_go_live`` on the server, at
    every count, with no third state where the copy is ahead of or behind the
    privilege.
    """
    unlocked = privilege_engine.get_user_privileges(
        user_id=4242, trust_score=60, referral_count=referrals,
        live_status="locked",
    )["can_go_live"]
    says_unlocked = privilege_engine.live_progress_label(referrals) == "Live unlocked"
    assert says_unlocked is unlocked


def test_live_015_no_surface_hardcodes_an_invite_count():
    """Source guard. The copy carried the number for five surfaces, so a

    regression here reads as ordinary product copy rather than as a second
    threshold — which is exactly why it went unnoticed.
    """
    with open(BOT_PY, encoding="utf-8") as fh:
        source = fh.read()
    offenders = re.findall(r"Invite \d+ (?:real|verified) members?", source)
    assert offenders == [], f"hardcoded invite counts in bot.py: {offenders}"
