#!/usr/bin/env python3
"""Prove `tests/test_profile_audience_matrix.py` would fail if the gate were wrong.

A privacy gate whose test cannot fail is worse than no test, because it is
quoted as evidence. So each mutation below re-introduces one specific past or
plausible defect and the battery records which tests notice.

Every mutation is applied to a *copy* of the checkout in a temporary directory.
There is no restore step: a `git checkout` restore would delete uncommitted
mission work, and a `cp` restore leaves a stale `__pycache__` so the mutation
stays live into the next run. The copy is thrown away instead.

An anchor that matches zero times is a harness failure, not a kill, and is
reported as `NOT-APPLIED` so it cannot be miscounted as a pass.

Run: python3 scripts/protection/profile_audience_mutation_battery.py
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SUITE = "tests/test_profile_audience_matrix.py"

#: A failed `subTest` is reported as `SUBFAILED(viewer=96302, ...) <nodeid> - msg`
#: when pytest-subtests is absent, which it is here. Matching only `FAILED ` hid
#: every subtest-driven assertion in this suite, and splitting on the first space
#: picked a word out of the parenthesised id instead of the node id.
SUMMARY_LINE = re.compile(r"^(?:FAILED|ERROR|SUBFAILED)(?:\([^)]*\))?\s+(\S+)")

#: Directories that are large, regenerable, or environment-specific. Copying
#: `.git` alone would multiply the runtime of every mutation.
SKIP = shutil.ignore_patterns(
    ".git", "node_modules", ".venv", "__pycache__", "*.pyc",
    ".pytest_cache", "mobile", "mobile-native", ".fuse_hidden*",
)


class Mutation:
    def __init__(self, name, defect, path, anchor, replacement, expect, extra=None):
        self.name = name
        self.defect = defect
        self.path = path
        self.anchor = anchor
        self.replacement = replacement
        #: Substrings of the test names that must fail. Naming them is the
        #: point: a mutation killed by some unrelated test proves nothing about
        #: the assertion written for it.
        self.expect = expect
        #: A second ``(anchor, replacement)`` in the same file, for a defect that
        #: only shows when two independent defences go at once.
        self.extra = extra

    def edits(self):
        yield self.anchor, self.replacement
        if self.extra:
            yield self.extra


MUTATIONS = [
    Mutation(
        name="page_gate_removed",
        defect="The original defect: the page authenticates and renders, with "
               "no access gate at all.",
        path="bot.py",
        anchor="""    if access_state in profile_viewer_permissions.CLOSED_STATES:
        if access_state == profile_viewer_permissions.ACCESS_UNKNOWN:""",
        replacement="""    if False:
        if access_state == profile_viewer_permissions.ACCESS_UNKNOWN:""",
        expect=["a_stranger_is_refused", "a_follower_is_still_refused",
                "an_account_the_subject_blocked_is_refused",
                "a_suspended_account_is_refused",
                "the_page_and_the_api_agree_on_every_audience"],
    ),
    Mutation(
        name="block_check_one_directional",
        defect="Checking only 'did the owner block me' lets a viewer keep "
               "reading someone they themselves blocked.",
        path="services/profile_viewer_permissions.py",
        anchor='''        "SELECT 1 FROM blocked_users WHERE (blocker_user_id=? AND blocked_user_id=?) "
        "OR (blocker_user_id=? AND blocked_user_id=?) LIMIT 1",
        (target_user_id, viewer_user_id, viewer_user_id, target_user_id),''',
        replacement='''        "SELECT 1 FROM blocked_users WHERE (blocker_user_id=? AND blocked_user_id=?) "
        "AND (blocker_user_id=? AND blocked_user_id=?) LIMIT 1",
        (target_user_id, viewer_user_id, target_user_id, viewer_user_id),''',
        expect=["an_account_that_blocked_the_subject_is_refused"],
    ),
    Mutation(
        name="private_opens_to_followers",
        defect="Treating a follow as acceptance opens a private profile to "
               "anyone who presses Follow.",
        path="services/profile_viewer_permissions.py",
        # The returned *state* has to move too. Opening only the flag dict is a
        # half-defect: the page keeps refusing the follower off the still-closed
        # state, so the only test that notices is the closed-state invariant --
        # which is already its own mutation below.
        anchor='''        permissions["can_view_public_profile"] = bool(friends)
        if friends:
            permissions["can_view_friend_content"] = True
            permissions["can_view_follower_content"] = True
            for flag in PUBLIC_CONTENT_FLAGS:
                permissions[flag] = True
        permissions["can_message"] = bool(friends)
        return (ACCESS_OK if friends else ACCESS_PRIVATE), permissions''',
        replacement='''        permissions["can_view_public_profile"] = bool(friends or follows)
        if friends or follows:
            permissions["can_view_friend_content"] = True
            permissions["can_view_follower_content"] = True
            for flag in PUBLIC_CONTENT_FLAGS:
                permissions[flag] = True
        permissions["can_message"] = bool(friends or follows)
        return (ACCESS_OK if (friends or follows) else ACCESS_PRIVATE), permissions''',
        expect=["a_follower_is_still_refused"],
    ),
    Mutation(
        name="feed_lane_gate_removed",
        defect="The profile-scoped feed serves a private account's "
               "public-visibility posts to any stranger who asks.",
        path="services/pulse_feed_engine.py",
        anchor="            if access_state in profile_viewer_permissions.CLOSED_STATES:",
        replacement="            if False:",
        # Only the stranger. A blocked viewer is covered twice over -- by this
        # gate and by the per-post block predicates -- so this mutation alone
        # cannot expose them, and naming their test here would credit this gate
        # with a kill the other layer earned. The mutation below removes both.
        expect=["the_feed_lane_returns_no_posts_to_a_stranger"],
    ),
    Mutation(
        name="feed_lane_block_defence_fully_removed",
        defect="Compound on purpose: in the profile lane the resolver gate and "
               "the per-post block predicates each independently hide a blocked "
               "author, so only removing both proves the blocked-viewer feed "
               "assertion can fail at all.",
        path="services/pulse_feed_engine.py",
        anchor='''            if access_state in profile_viewer_permissions.CLOSED_STATES:''',
        replacement='''            if False:''',
        extra=(
            # Keeps the placeholder count aligned with `params` so the query
            # still binds; only the two block directions stop being enforced.
            '''        where.append("NOT EXISTS (SELECT 1 FROM blocked_users bu WHERE bu.blocker_user_id=? AND bu.blocked_user_id=p.user_id)")
        params.append(int(viewer_user_id))
        where.append("NOT EXISTS (SELECT 1 FROM blocked_users bu WHERE bu.blocker_user_id=p.user_id AND bu.blocked_user_id=?)")
        params.append(int(viewer_user_id))''',
            '''        where.append("(? IS NOT NULL)")
        params.append(int(viewer_user_id))
        where.append("(? IS NOT NULL)")
        params.append(int(viewer_user_id))''',
        ),
        expect=["the_feed_lane_refuses_a_blocked_viewer"],
    ),
    Mutation(
        name="api_gate_removed",
        defect="The API renders the native profile payload with no gate, which "
               "is how the two surfaces came to disagree in the first place.",
        path="bot.py",
        anchor="""    if access_state in profile_viewer_permissions.CLOSED_STATES:
        conn.close()
        return pulse_profile_closed_api_error(access_state)
    payload = pulse_native_profile_payload(cur, target_user_id, user["user_id"])""",
        replacement="""    if False:
        conn.close()
        return pulse_profile_closed_api_error(access_state)
    payload = pulse_native_profile_payload(cur, target_user_id, user["user_id"])""",
        expect=["the_api_refuses_a_stranger_too", "the_api_refuses_a_blocked_viewer",
                "the_page_and_the_api_agree_on_every_audience"],
    ),
    Mutation(
        name="api_refuses_accepted_friends",
        defect="The drift in the other direction: the API's own gate refused "
               "every non-owner on a private profile, including accepted "
               "friends the resolver allows.",
        path="bot.py",
        anchor="""    access_state, _permissions = profile_viewer_permissions.profile_access(
        cur, target_user_id, int(user["user_id"])
    )""",
        replacement="""    cur.execute("SELECT COALESCE(profile_visibility,'public') AS v FROM users WHERE user_id=? LIMIT 1", (target_user_id,))
    _legacy = dict(cur.fetchone() or {})
    access_state, _permissions = profile_viewer_permissions.profile_access(
        cur, target_user_id, int(user["user_id"])
    )
    if str(_legacy.get("v") or "public").lower() == "private" and int(target_user_id) != int(user["user_id"]):
        access_state = profile_viewer_permissions.ACCESS_PRIVATE""",
        expect=["the_api_lets_an_accepted_friend_in",
                "the_page_and_the_api_agree_on_every_audience"],
    ),
    Mutation(
        name="post_count_not_viewer_aware",
        defect="Counting every non-deleted row tells a visitor how many posts "
               "the Posts tab is withholding from them.",
        path="bot.py",
        anchor='    post_count = pulse_feed_engine.count_user_posts(target_user_id, viewer_user_id=viewer["user_id"])',
        replacement='''    cur.execute("SELECT COUNT(*) AS total FROM pulse_posts WHERE user_id=? AND deleted_at IS NULL", (target_user_id,))
    post_count = int(dict(cur.fetchone() or {}).get("total") or 0)''',
        expect=["a_stranger_is_not_told_how_many_posts_are_hidden_from_them"],
    ),
    Mutation(
        name="deny_names_the_reason",
        defect="Telling a viewer they were blocked is a disclosure the owner "
               "never asked us to make, and distinguishes a block from a "
               "moderation action.",
        path="bot.py",
        anchor='''        heading = "This profile is not available"
        body = "You cannot view this PulseSoc profile right now."''',
        replacement='''        heading = "This profile is not available"
        body = ("You have been blocked by this member."
                if state == profile_viewer_permissions.ACCESS_BLOCKED
                else "This account is suspended.")''',
        expect=["a_blocked_viewer_is_not_told_they_were_blocked",
                "blocked_and_restricted_are_indistinguishable",
                "a_suspended_account_does_not_narrate_its_moderation_state"],
    ),
    Mutation(
        name="closed_state_leaves_a_flag_open",
        defect="A deny that still opens a content flag renders a profile the "
               "resolver meant to close.",
        path="services/profile_viewer_permissions.py",
        anchor="        return ACCESS_BLOCKED, dict(DENY_ALL, can_report=True, can_block=True)",
        replacement="        return ACCESS_BLOCKED, dict(DENY_ALL, can_report=True, can_block=True, can_view_public_media=True)",
        expect=["a_closed_state_opens_no_content_flag"],
    ),
    Mutation(
        name="viewer_permissions_rederives_its_own_answer",
        defect="Two entry points with two precedence chains. The flags and the "
               "state are then free to disagree about the same viewer.",
        path="services/profile_viewer_permissions.py",
        anchor="    return profile_access(cur, target_user_id, viewer_user_id, account=account)[1]",
        replacement="""    if _int(target_user_id) == _int(viewer_user_id):
        return dict(OWNER_PERMISSIONS)
    return dict(DENY_ALL, can_report=True, can_block=True)""",
        expect=["viewer_permissions_is_a_projection_of_profile_access"],
    ),
    Mutation(
        name="rail_author_filter_drops_private",
        defect="Folding `public_author_sql` back into `discovery_visible_sql` "
               "puts a private account back on the platform-wide rail, which "
               "has no viewer to resolve it against.",
        path="services/discovery_visibility.py",
        anchor='''    return (
        f"({discovery_visible_sql(alias)} "
        f"AND COALESCE({alias}.profile_visibility, 'public') <> 'private')"
    )''',
        replacement="    return discovery_visible_sql(alias)",
        # Not the suspended test: `discovery_visible_sql` still covers that, so
        # naming it here would credit this clause with a kill it did not earn.
        expect=["the_rail_does_not_name_a_private_account",
                "a_private_accounts_post_count_is_not_published",
                "posts_today_counts_only_what_a_stranger_can_reach"],
    ),
    Mutation(
        name="rail_counts_private_posts_per_creator",
        defect="Counting every approved post per creator publishes the size of "
               "what a stranger cannot see, even for an account whose name is "
               "fair to show.",
        path="services/pulse_feed_engine.py",
        anchor="""          AND COALESCE(p.visibility,'public')='public' AND {author_is_public}
        GROUP BY p.user_id, u.display_name, u.username""",
        replacement="""          AND {author_is_public}
        GROUP BY p.user_id, u.display_name, u.username""",
        expect=["a_public_accounts_published_count_is_only_its_public_posts"],
    ),
    Mutation(
        name="rail_quotes_private_posts",
        defect="`scam_warnings` titles a post from its body, so an unfiltered "
               "query publishes the first 80 characters of a private one.",
        path="services/pulse_feed_engine.py",
        anchor="""          AND COALESCE(p.visibility,'public')='public' AND {author_is_public}
          AND (p.post_type='scam_report' OR p.risk_score>=50 OR p.tags_json LIKE ?)""",
        replacement="          AND (p.post_type='scam_report' OR p.risk_score>=50 OR p.tags_json LIKE ?)",
        expect=["the_rail_does_not_quote_a_private_post"],
    ),
    Mutation(
        name="rail_republishes_risk_score",
        defect="`risk_score` is an internal moderation signal. It shipped on "
               "every feed response beside the post it judged, and no client "
               "ever read it.",
        path="services/pulse_feed_engine.py",
        anchor="""        SELECT p.id, p.title, p.body
        FROM pulse_posts p
        JOIN users u ON u.user_id=p.user_id
        WHERE p.deleted_at IS NULL AND p.moderation_status='approved'""",
        replacement="""        SELECT p.id, p.title, p.body, p.risk_score
        FROM pulse_posts p
        JOIN users u ON u.user_id=p.user_id
        WHERE p.deleted_at IS NULL AND p.moderation_status='approved'""",
        extra=(
            '''        {"id": row["id"], "title": row["title"] or (row["body"] or "Scam warning")[:80], "permalink": f"/pulse/post/{row['id']}"}''',
            '''        {"id": row["id"], "title": row["title"] or (row["body"] or "Scam warning")[:80], "permalink": f"/pulse/post/{row['id']}", "risk_score": row["risk_score"]}''',
        ),
        expect=["the_rail_never_publishes_a_risk_score"],
    ),
]


def run_suite(root: Path):
    """Run the suite inside `root`. Returns (exit_code, failed_test_names)."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", SUITE, "-p", "no:randomly", "-q",
         "-W", "ignore::DeprecationWarning", "--no-header", "-rf"],
        cwd=root, capture_output=True, text=True,
    )
    failed = []
    for line in proc.stdout.splitlines():
        match = SUMMARY_LINE.match(line)
        if match:
            failed.append(match.group(1))
    return proc.returncode, failed, proc.stdout


def main():
    print(f"repo       {REPO}")
    print(f"python     {sys.executable}")
    print(f"suite      {SUITE}\n")

    print("== baseline ==")
    code, failed, out = run_suite(REPO)
    if code != 0:
        print("BASELINE IS RED -- every kill below would be meaningless.")
        print(out[-3000:])
        return 1
    print("baseline green\n")

    killed, survived, not_applied, wrong_test = [], [], [], []

    for mutation in MUTATIONS:
        with tempfile.TemporaryDirectory(prefix="profile_mut_") as tmp:
            root = Path(tmp) / "repo"
            shutil.copytree(REPO, root, ignore=SKIP, symlinks=False)
            target = root / mutation.path
            source = target.read_text(encoding="utf-8")
            miscounted = False
            for anchor, replacement in mutation.edits():
                hits = source.count(anchor)
                if hits != 1:
                    print(f"[NOT-APPLIED] {mutation.name}: anchor matched {hits}x "
                          f"in {mutation.path} (needs exactly 1)")
                    not_applied.append(mutation.name)
                    miscounted = True
                    break
                source = source.replace(anchor, replacement)
            if miscounted:
                continue
            target.write_text(source, encoding="utf-8")
            code, failed, out = run_suite(root)
            if code == 0:
                print(f"[SURVIVED]    {mutation.name}: suite still green -- "
                      f"{mutation.defect}")
                survived.append(mutation.name)
                continue
            names = "\n                ".join(sorted(failed)) or "(none parsed)"
            missing = [
                want for want in mutation.expect
                if not any(want in name for name in failed)
            ]
            if missing:
                print(f"[WRONG-TEST]  {mutation.name}: suite went red but these "
                      f"did not fire: {', '.join(missing)}")
                print(f"                failed: {names}")
                wrong_test.append(mutation.name)
                continue
            print(f"[KILLED]      {mutation.name} ({len(failed)} failing)")
            killed.append(mutation.name)

    total = len(MUTATIONS)
    print(f"\n== {len(killed)}/{total} killed by the test named for them ==")
    for label, group in (("SURVIVED", survived), ("WRONG-TEST", wrong_test),
                         ("NOT-APPLIED", not_applied)):
        if group:
            print(f"{label}: {', '.join(group)}")
    return 0 if len(killed) == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
