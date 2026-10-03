"""The media access authority must not be able to grant by accident.

This gate exists because the defect it guards against was invisible for the
entire life of the product: every deletion path wrote a database boolean, the
content stopped being shown, and a saved media URL kept returning 200. Nothing
failed. No test went red. The only way that shows up is if someone probes the
wire, which nobody did until Agent 9 did.

So the assertions here are mostly about *absence* -- that a state cannot be
added without deciding whether it is retrievable, that a refusal cannot silently
become a grant, that a provider error cannot be scored as a privacy success.
Each one is tied to a numbered adversarial case in
``docs/search_os/AGENT_00_MEDIA_ACCESS_LIFECYCLE.md`` §20.

Every corpus below is *derived* from the module -- ``LIFECYCLE_STATES``,
``ACTION_ORDER``, ``TRANSITIONS`` -- and asserted non-empty before it is looped
over. A suite that iterates a hardcoded list cannot see a state someone added,
and a suite that loops over an empty collection passes while testing nothing.
"""

import inspect
import pathlib
import unittest

from services import media_access_authority as A


REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


class LifecycleStateCoverage(unittest.TestCase):
    """A new state must be classified, not defaulted."""

    def test_state_corpus_is_non_empty(self):
        self.assertTrue(A.LIFECYCLE_STATES, "no lifecycle states to test")
        self.assertTrue(A.SERVABLE_STATES, "no servable states; the module would refuse everything")

    def test_servable_states_are_a_strict_subset(self):
        # An allowlist only protects if something is outside it.
        self.assertLess(
            set(A.SERVABLE_STATES),
            set(A.LIFECYCLE_STATES),
            "SERVABLE_STATES must be a strict subset, or it is not an allowlist",
        )

    def test_every_non_servable_state_is_decided(self):
        """Each non-servable state either demands revocation or is terminal.

        A state that is neither would be one PulseSoc hides without revoking --
        which is the entire defect, reintroduced by omission.
        """
        undecided = [
            state
            for state in A.LIFECYCLE_STATES
            if state not in A.SERVABLE_STATES
            and state not in A.REVOCATION_REQUIRED_STATES
            and state not in A.TERMINAL_STATES
        ]
        self.assertEqual([], undecided, "non-servable states with no revocation obligation: %s" % undecided)

    def test_no_servable_state_demands_revocation(self):
        overlap = set(A.SERVABLE_STATES) & set(A.REVOCATION_REQUIRED_STATES)
        self.assertEqual(set(), overlap, "a state cannot be both retrievable and revoked")

    def test_requires_revocation_agrees_with_the_set(self):
        for state in A.LIFECYCLE_STATES:
            with self.subTest(state=state):
                self.assertEqual(state in A.REVOCATION_REQUIRED_STATES, A.requires_revocation(state))

    def test_plan_revocation_never_leaves_a_private_state_owing_nothing(self):
        for state in A.LIFECYCLE_STATES:
            with self.subTest(state=state):
                planned = A.plan_revocation(state)
                if state in A.REVOCATION_REQUIRED_STATES:
                    self.assertEqual(A.REVOCATION_PENDING, planned)
                else:
                    self.assertEqual(A.REVOCATION_NOT_REQUIRED, planned)


class TransitionTableIsClosed(unittest.TestCase):
    def test_transition_corpus_is_non_empty(self):
        self.assertTrue(A.TRANSITIONS, "no transitions to test")
        self.assertTrue(A.ACTION_ORDER, "no actions to test")

    def test_every_transition_uses_known_states(self):
        for (action, src), dst in A.TRANSITIONS.items():
            with self.subTest(action=action, src=src):
                self.assertIn(src, A.LIFECYCLE_STATES)
                self.assertIn(dst, A.LIFECYCLE_STATES)
                self.assertIn(action, A.ACTION_ORDER)

    def test_every_action_has_a_permission(self):
        """An unmapped action must raise, not vanish from the menu.

        ``permissions.get(None)`` is falsy, so a ``.get()`` lookup would hide an
        action that someone added without a permission -- indistinguishable from
        a feature that was never built.
        """
        for action in A.ACTION_ORDER:
            with self.subTest(action=action):
                self.assertIn(A.permission_for_action(action), A.MEDIA_PERMISSIONS)
        with self.assertRaises(A.AuthorityError):
            A.permission_for_action("no_such_action")

    def test_unknown_pairs_are_refused_not_defaulted(self):
        refused = 0
        for action in A.ACTION_ORDER:
            for state in A.LIFECYCLE_STATES:
                if (action, state) in A.TRANSITIONS:
                    continue
                refused += 1
                with self.subTest(action=action, state=state):
                    with self.assertRaises(A.AuthorityError) as caught:
                        A.plan_transition(action, state)
                    self.assertEqual(409, caught.exception.status)
        self.assertGreater(refused, 0, "no refused pairs exercised; the table would be a denylist")

    def test_purged_is_terminal(self):
        for action in A.ACTION_ORDER:
            with self.subTest(action=action):
                self.assertNotIn(
                    (action, A.STATE_PURGED),
                    A.TRANSITIONS,
                    "nothing may lead out of PURGED; the bytes are gone",
                )

    def test_restore_cannot_reach_active_from_held(self):
        """Case 7 / §12. A hold is released, not restored through.

        Restoring straight to ACTIVE out of a legal hold would publish evidence.
        """
        self.assertNotIn((A.ACTION_RESTORE, A.STATE_HELD), A.TRANSITIONS)
        self.assertEqual(A.STATE_WITHDRAWN, A.TRANSITIONS[(A.ACTION_RELEASE_HOLD, A.STATE_HELD)])

    def test_destruction_is_never_one_step_from_a_live_asset(self):
        for state in A.SERVABLE_STATES:
            with self.subTest(state=state):
                self.assertNotIn((A.ACTION_PURGE, state), A.TRANSITIONS)
                self.assertNotIn((A.ACTION_SCHEDULE_PURGE, state), A.TRANSITIONS)

    def test_available_actions_never_offers_a_refused_transition(self):
        offered = 0
        for state in A.LIFECYCLE_STATES:
            for action in A.available_actions(state):
                offered += 1
                with self.subTest(state=state, action=action):
                    new_state, changed = A.plan_transition(action, state)
                    self.assertTrue(changed, "a menu must not offer a no-op")
                    self.assertIn(new_state, A.LIFECYCLE_STATES)
        self.assertGreater(offered, 0, "no actions were offered in any state")


class LegalHoldPrecedence(unittest.TestCase):
    """Hold blocks destruction. Hold does not grant access."""

    def test_hold_blocks_only_destructive_actions(self):
        self.assertTrue(A.DESTRUCTIVE_ACTIONS, "no destructive actions declared")
        self.assertNotIn(
            A.ACTION_WITHDRAW,
            A.DESTRUCTIVE_ACTIONS,
            "revocation must not be destructive, or a hold would forbid it",
        )
        with self.assertRaises(A.AuthorityError) as caught:
            A.plan_transition(A.ACTION_PURGE, A.STATE_PURGE_PENDING, legal_hold=True)
        self.assertEqual("media_legal_hold", caught.exception.error_code)

    def test_hold_does_not_block_revocation(self):
        new_state, changed = A.plan_transition(A.ACTION_WITHDRAW, A.STATE_ACTIVE, legal_hold=True)
        self.assertEqual(A.STATE_WITHDRAWN, new_state)
        self.assertTrue(changed)

    def test_held_is_not_retrievable(self):
        """Retained evidence stays unreachable by ordinary users."""
        self.assertNotIn(A.STATE_HELD, A.SERVABLE_STATES)
        self.assertIn(A.STATE_HELD, A.REVOCATION_REQUIRED_STATES)

    def test_a_held_ref_refuses_access_even_for_a_permitted_viewer(self):
        decision = A.decide_media_access(
            ref={"lifecycle_state": A.STATE_HELD},
            content_ref={"content_type": "reel", "content_id": 1},
            viewer_may_view=True,
            thumbnail_signing_available=True,
        )
        self.assertFalse(decision)
        self.assertEqual("media_state_not_servable", decision.reason_code)

    def test_available_actions_hides_destruction_under_hold(self):
        self.assertNotIn(
            A.ACTION_PURGE,
            A.available_actions(A.STATE_PURGE_PENDING, legal_hold=True),
        )
        self.assertIn(A.ACTION_PURGE, A.available_actions(A.STATE_PURGE_PENDING))


class OptimisticConcurrency(unittest.TestCase):
    """Case 11: content becomes private between the auth check and token issue."""

    def test_stale_expected_state_is_a_conflict(self):
        with self.assertRaises(A.AuthorityError) as caught:
            A.plan_transition(
                A.ACTION_RESTORE,
                A.STATE_WITHDRAWN,
                expected_state=A.STATE_ACTIVE,
            )
        self.assertEqual(409, caught.exception.status)
        self.assertEqual("media_state_conflict", caught.exception.error_code)

    def test_matching_expected_state_proceeds(self):
        new_state, _ = A.plan_transition(
            A.ACTION_WITHDRAW,
            A.STATE_ACTIVE,
            expected_state=A.STATE_ACTIVE,
        )
        self.assertEqual(A.STATE_WITHDRAWN, new_state)


class AccessDecisionFailsClosed(unittest.TestCase):
    def test_viewer_permission_has_no_default(self):
        """Nothing may be granted by omission.

        A default of True would make every caller that forgot the argument into
        an open door; a default of False would make the same mistake silent in
        the other direction. Required is the only safe answer.
        """
        sig = inspect.signature(A.decide_media_access)
        param = sig.parameters["viewer_may_view"]
        self.assertIs(param.default, inspect.Parameter.empty)
        self.assertEqual(inspect.Parameter.KEYWORD_ONLY, param.kind)

    def test_refusal_is_falsy(self):
        refused = A.AccessDecision(False, reason_code="x")
        self.assertFalse(refused)
        self.assertFalse(bool(refused))

    def test_every_non_servable_state_refuses(self):
        """Cases 1-6. Checked for every state, so a new one cannot slip through."""
        checked = 0
        for state in A.LIFECYCLE_STATES:
            if state in A.SERVABLE_STATES:
                continue
            checked += 1
            with self.subTest(state=state):
                decision = A.decide_media_access(
                    ref={"lifecycle_state": state},
                    content_ref={"content_type": "reel", "content_id": 7},
                    viewer_may_view=True,
                    thumbnail_signing_available=True,
                )
                self.assertFalse(decision, "state %s granted access" % state)
        self.assertGreater(checked, 0, "no non-servable states were exercised")

    def test_audience_refusal(self):
        decision = A.decide_media_access(
            ref={"lifecycle_state": A.STATE_ACTIVE},
            content_ref={"content_type": "reel", "content_id": 7},
            viewer_may_view=False,
            thumbnail_signing_available=True,
        )
        self.assertFalse(decision)
        self.assertEqual("media_audience_refused", decision.reason_code)

    def test_cross_content_ref_is_refused(self):
        """Case 10: User A obtaining authorization for User B's media.

        The ref is authorized against the content the caller named. A ref that
        demonstrably belongs to other content is refused even though the viewer
        is legitimately authorized for their own.
        """
        decision = A.decide_media_access(
            ref={"lifecycle_state": A.STATE_ACTIVE, "content_type": "reel", "content_id": 99},
            content_ref={"content_type": "reel", "content_id": 7},
            viewer_may_view=True,
            thumbnail_signing_available=True,
        )
        self.assertFalse(decision)
        self.assertEqual("media_content_mismatch", decision.reason_code)

    def test_matching_content_ref_is_granted(self):
        decision = A.decide_media_access(
            ref={"lifecycle_state": A.STATE_ACTIVE, "content_type": "reel", "content_id": 7},
            content_ref={"content_type": "reel", "content_id": 7},
            viewer_may_view=True,
            thumbnail_signing_available=True,
        )
        self.assertTrue(decision)
        self.assertEqual(A.ACCESS_PUBLIC, decision.policy)

    def test_restricted_without_a_thumbnail_issuer_refuses(self):
        """Case 15, inverted.

        The only signer in the repository mints ``aud: "v"``. Serving restricted
        media signed with no thumbnail issuer yields a permanently broken poster
        no code can fix. Refusing loudly is correct; falling back to the public
        URL would defeat the scheme silently.
        """
        decision = A.decide_media_access(
            ref={"lifecycle_state": A.STATE_AUDIENCE_RESTRICTED},
            content_ref={},
            viewer_may_view=True,
            thumbnail_signing_available=False,
        )
        self.assertFalse(decision)
        self.assertEqual("media_signing_incomplete", decision.reason_code)

    def test_restricted_with_a_thumbnail_issuer_grants_signed_not_public(self):
        decision = A.decide_media_access(
            ref={"lifecycle_state": A.STATE_AUDIENCE_RESTRICTED},
            content_ref={},
            viewer_may_view=True,
            thumbnail_signing_available=True,
        )
        self.assertTrue(decision)
        self.assertEqual(A.ACCESS_SIGNED, decision.policy)
        self.assertGreater(decision.ttl_seconds, 0)


class RevocationFailureNeverReopensAccess(unittest.TestCase):
    """Case 16: a provider failure must not be reported as success."""

    def test_pending_and_failed_both_refuse(self):
        for revocation in (A.REVOCATION_PENDING, A.REVOCATION_FAILED):
            with self.subTest(revocation=revocation):
                ref = {"lifecycle_state": A.STATE_ACTIVE, "revocation_state": revocation}
                self.assertFalse(A.is_retrievable(ref))
                decision = A.decide_media_access(
                    ref=ref,
                    content_ref={},
                    viewer_may_view=True,
                    thumbnail_signing_available=True,
                )
                self.assertFalse(decision)

    def test_confirmed_revocation_on_a_servable_state_still_serves(self):
        # A ref restored after revocation: the state is what decides, and a
        # historical CONFIRMED must not permanently brick restored content.
        ref = {"lifecycle_state": A.STATE_ACTIVE, "revocation_state": A.REVOCATION_CONFIRMED}
        self.assertTrue(A.is_retrievable(ref))

    def test_revocation_state_vocabulary_is_closed(self):
        self.assertEqual(A.REVOCATION_NOT_REQUIRED, A.normalize_revocation_state("nonsense"))
        self.assertEqual(A.REVOCATION_FAILED, A.normalize_revocation_state("revocation_failed"))


class LegacyVocabularyIsHonoured(unittest.TestCase):
    """Two surfaces, two words for the same thing.

    Reels say ``status='deleted'``; videos say ``status='archived'``. A predicate
    that knew only one under-reported by the size of the other -- which is how
    an exposure count came out low.
    """

    def test_both_removal_words_map_to_withdrawn(self):
        for status in ("deleted", "archived"):
            with self.subTest(status=status):
                self.assertEqual(A.STATE_WITHDRAWN, A.state_from_legacy({"status": status}))

    def test_non_public_visibility_is_restricted(self):
        for visibility in ("private", "followers", "reel_only"):
            with self.subTest(visibility=visibility):
                self.assertEqual(
                    A.STATE_AUDIENCE_RESTRICTED,
                    A.state_from_legacy({"visibility": visibility}),
                )
        self.assertEqual(A.STATE_ACTIVE, A.state_from_legacy({"visibility": "public"}))

    def test_legal_hold_outranks_a_plain_status(self):
        self.assertEqual(
            A.STATE_HELD,
            A.state_from_legacy({"status": "active", "legal_hold": True}),
        )

    def test_purged_outranks_legal_hold(self):
        self.assertEqual(
            A.STATE_PURGED,
            A.state_from_legacy({"legal_hold": True, "purged_at": "2026-01-01T00:00:00"}),
        )

    def test_takedown_columns_are_read(self):
        """``search_visibility`` already reads these and nothing writes them.

        Keeping the mapping here means the dead gate becomes live the moment a
        writer appears, instead of a second vocabulary being invented.
        """
        self.assertEqual(
            A.STATE_MODERATION_REMOVED,
            A.state_from_legacy({"takedown_at": "2026-01-01T00:00:00"}),
        )
        self.assertEqual(A.STATE_MODERATION_REMOVED, A.state_from_legacy({"is_takedown": True}))

    def test_an_unknown_state_string_does_not_resurrect_removed_content(self):
        # normalize_state defaults to ACTIVE, which is only safe because the
        # legacy columns are also read.
        self.assertEqual(A.STATE_ACTIVE, A.normalize_state("NOT_A_STATE"))
        self.assertFalse(A.is_retrievable({"lifecycle_state": "NOT_A_STATE", "status": "deleted"}))


class PosterIsPartOfThePrivateSurface(unittest.TestCase):
    def test_unavailable_payload_blanks_the_poster_and_the_metadata(self):
        payload = A.unavailable_media_payload()
        for key in ("poster_url", "thumbnail_url", "playback_url", "playback_id", "preview_url", "download_url"):
            with self.subTest(key=key):
                self.assertEqual("", payload[key], "%s must not survive a withdrawal" % key)
        for key in ("caption", "title", "author_name"):
            with self.subTest(key=key):
                self.assertEqual("", payload[key], "%s is part of what a privacy takedown removes" % key)
        self.assertTrue(payload["media_unavailable"])


class ProviderErrorsAreNotPrivacySuccess(unittest.TestCase):
    """The specific misreading this whole mission turned on.

    HTTP 412 on a Mux stream and 400 on its poster were read as "signed
    playback, token missing" and therefore as protection. Probing assets whose
    state was independently known from the API showed 412/400 is what an
    ``errored`` asset answers. Scoring those as safe inflated the protected
    population by every broken asset in it.
    """

    def test_an_errored_asset_is_never_healthy(self):
        for state in A.LIFECYCLE_STATES:
            for accessible in (True, False):
                with self.subTest(state=state, accessible=accessible):
                    self.assertEqual(
                        A.CLASS_INVESTIGATE,
                        A.classify_reconciliation(
                            lifecycle_state=state,
                            provider_status="errored",
                            wire_accessible=accessible,
                            has_application_row=True,
                        ),
                    )

    def test_private_and_accessible_is_a_privacy_defect(self):
        self.assertEqual(
            A.CLASS_PRIVACY_DEFECT,
            A.classify_reconciliation(
                lifecycle_state=A.STATE_WITHDRAWN,
                provider_status="ready",
                wire_accessible=True,
                has_application_row=True,
            ),
        )

    def test_public_and_inaccessible_is_an_availability_defect_not_a_privacy_win(self):
        self.assertEqual(
            A.CLASS_AVAILABILITY_DEFECT,
            A.classify_reconciliation(
                lifecycle_state=A.STATE_ACTIVE,
                provider_status="ready",
                wire_accessible=False,
                has_application_row=True,
            ),
        )

    def test_private_and_inaccessible_is_only_probably_healthy(self):
        """A status code alone does not prove the playback id is gone."""
        self.assertEqual(
            A.CLASS_POTENTIALLY_HEALTHY,
            A.classify_reconciliation(
                lifecycle_state=A.STATE_WITHDRAWN,
                provider_status="ready",
                wire_accessible=False,
                has_application_row=True,
            ),
        )

    def test_reachable_bytes_with_no_application_row_are_a_defect(self):
        """41 such assets exist in production right now."""
        self.assertEqual(
            A.CLASS_ORPHAN_DEFECT,
            A.classify_reconciliation(
                lifecycle_state=A.STATE_ACTIVE,
                provider_status="ready",
                wire_accessible=True,
                has_application_row=False,
            ),
        )

    def test_no_classification_is_ever_none(self):
        seen = set()
        for state in A.LIFECYCLE_STATES:
            for status in ("ready", "errored", "preparing", ""):
                for accessible in (True, False):
                    for has_row in (True, False):
                        result = A.classify_reconciliation(
                            lifecycle_state=state,
                            provider_status=status,
                            wire_accessible=accessible,
                            has_application_row=has_row,
                        )
                        self.assertTrue(result)
                        seen.add(result)
        self.assertIn(A.CLASS_PRIVACY_DEFECT, seen)
        self.assertIn(A.CLASS_ORPHAN_DEFECT, seen)
        self.assertIn(A.CLASS_INVESTIGATE, seen)


class DeclaredLimitations(unittest.TestCase):
    def test_r2_revocation_does_not_claim_to_reach_the_edge(self):
        """No edge-purge capability exists in this repository.

        R2 objects are served ``public, max-age=31536000, immutable``, so the
        origin forgetting an object does not reach a cache that has it. Claiming
        otherwise would make a false privacy promise.
        """
        self.assertFalse(A.revocation_reaches_edge(A.PROVIDER_R2))
        self.assertTrue(A.revocation_reaches_edge(A.PROVIDER_MUX))

    def test_an_unknown_provider_is_assumed_not_to_reach_the_edge(self):
        self.assertFalse(A.revocation_reaches_edge("some_new_cdn"))
        self.assertFalse(A.revocation_reaches_edge(""))
        self.assertFalse(A.revocation_reaches_edge(None))

    def test_restricted_token_ttl_is_a_privacy_window_not_a_playback_window(self):
        """The existing signer mints 5-6 hours. That is not a privacy window."""
        self.assertLessEqual(
            A.RESTRICTED_TOKEN_TTL_SECONDS,
            900,
            "a token minted before a revocation must not outlive it by hours",
        )
        self.assertGreaterEqual(A.RESTRICTED_TOKEN_TTL_SECONDS, A.TOKEN_BUCKET_SECONDS)

    def test_ttl_is_bucketed_so_a_poll_does_not_restart_the_player(self):
        # Within one bucket the remaining TTL decreases monotonically rather
        # than jumping, so repeated calls do not mint a fresh URL every poll.
        first = A.restricted_token_ttl(now=1_000_000)
        second = A.restricted_token_ttl(now=1_000_001)
        self.assertGreaterEqual(first, second)
        self.assertGreater(second, 0)


class NoSecondEventBus(unittest.TestCase):
    """``pulse_jobs`` is already a durable queue with CAS claim and backoff."""

    FORBIDDEN = (
        "media_privacy_events_v2",
        "media_event_bus",
        "mux_outbox_v2",
        "media_revocation_events_v2",
    )

    def test_no_parallel_queue_was_introduced(self):
        sources = sorted(
            path
            for path in list((REPO_ROOT / "services").glob("*.py"))
            + list(REPO_ROOT.glob("*.py"))
            if path.is_file()
        )
        self.assertGreater(len(sources), 50, "source corpus is implausibly small; the glob is wrong")
        offenders = []
        for path in sources:
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for name in self.FORBIDDEN:
                if name in text:
                    offenders.append("%s: %s" % (path.name, name))
        self.assertEqual([], offenders, "a parallel media event bus appeared: %s" % offenders)


class AuthorityIsPure(unittest.TestCase):
    """A privacy control whose tests need a provider is one that is not tested."""

    def test_the_module_imports_nothing_that_performs_io(self):
        source = (REPO_ROOT / "services" / "media_access_authority.py").read_text(encoding="utf-8")
        for forbidden in ("import bot", "from bot ", "import requests", "import psycopg2", "urllib.request"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)

    def test_permission_refusals_distinguish_401_from_403(self):
        def grants_nothing(_actor, _permission):
            return False

        with self.assertRaises(A.AuthorityError) as anonymous:
            A.require_permission(None, "media.revoke", grants_nothing)
        self.assertEqual(401, anonymous.exception.status)

        with self.assertRaises(A.AuthorityError) as known:
            A.require_permission({"id": 1}, "media.revoke", grants_nothing)
        self.assertEqual(403, known.exception.status)

    def test_an_unknown_permission_is_a_server_error_not_a_denial(self):
        with self.assertRaises(A.AuthorityError) as caught:
            A.require_permission({"id": 1}, "media.not_a_permission", lambda *_: True)
        self.assertEqual(500, caught.exception.status)

    def test_granted_permissions_reports_every_permission_explicitly(self):
        granted = A.granted_permissions({"id": 1}, lambda _a, p: p == "media.revoke")
        self.assertEqual(set(A.MEDIA_PERMISSIONS), set(granted))
        self.assertTrue(granted["media.revoke"])
        self.assertFalse(granted["media.purge"])
        self.assertEqual(
            {name: False for name in A.MEDIA_PERMISSIONS},
            A.granted_permissions(None, lambda *_: True),
        )

    def test_reason_codes_requiring_a_note_are_enforced(self):
        with self.assertRaises(A.AuthorityError):
            A.validate_reason(A.ACTION_WITHDRAW, "OTHER", "")
        with self.assertRaises(A.AuthorityError):
            A.validate_reason(A.ACTION_PURGE, "OWNER_DELETED", "")
        code, note = A.validate_reason(A.ACTION_WITHDRAW, "owner_deleted", " creator removed it ")
        self.assertEqual("OWNER_DELETED", code)
        self.assertEqual("creator removed it", note)
        with self.assertRaises(A.AuthorityError):
            A.validate_reason(A.ACTION_WITHDRAW, "MADE_UP_CODE", "note")


if __name__ == "__main__":
    unittest.main()
