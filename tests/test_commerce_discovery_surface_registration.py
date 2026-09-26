"""A surface is not registered until every table that keys off it has an entry.

``schema.SURFACES`` is a strict allowlist, and its own comment explains why: the
frequency caps are keyed by that exact string, so an unrecognised surface escapes
them. What the comment does not say is that the allowlist is one of *six* places
a surface has to appear, and that the other five fail quietly when it does not.

Adding the fifth surface made that concrete. Four of the five were obvious
because a missing entry raises or visibly misbehaves. The fifth,
``preferences.SOCIAL_SURFACES``, fails in the one direction nobody inspects: it
is the scope of the master opt-out *and* of an "all" snooze, so a surface absent
from it keeps serving cards to a viewer who switched discovery off. Nothing
raises, no response is empty, and the only symptom is a user seeing exactly what
they asked not to see.

So these cases derive the expectations from ``SURFACES`` rather than listing
surfaces again. A file that restated the names would pass forever after someone
added a sixth surface and forgot it — which is the failure being guarded, not a
hypothetical.

The one deliberate exclusion is ``marketplace``, and it is asserted *as* an
exclusion rather than skipped. ``preferences`` promises the user in as many words
that "Marketplace keeps recommending products inside Marketplace either way", so
that surface appearing in the social set would be a broken promise, not a
tightened default. A test that merely tolerated its absence would not notice it
arriving.
"""

import unittest

from services.commerce_discovery import config, preferences, router, schema


#: The shop, and only the shop. Named once here so each case can say which side
#: of the line it is testing without repeating the reasoning.
NOT_SOCIAL = frozenset({"marketplace"})


class EverySurfaceIsFullyRegistered(unittest.TestCase):
    def test_the_router_has_a_policy_for_each_surface(self):
        for surface in schema.SURFACES:
            with self.subTest(surface=surface):
                # `policy_for` falls back to the feed for unknown surfaces, so
                # asking it is not a test of registration -- it would answer
                # happily for "banana". The dicts are the registration.
                self.assertIn(surface, router._SELLER_CAPS)
                self.assertIn(surface, router._CATEGORY_CAPS)
                self.assertIn(surface, router._COOLDOWN_FACTORS)

    def test_the_router_has_no_policy_for_a_surface_that_does_not_exist(self):
        """The other direction: a removed surface must not leave a policy behind.

        A stale entry is harmless today and becomes a live defect the moment the
        name is reused for something else -- the new surface would silently
        inherit the retired one's caps.
        """
        for table in (router._SELLER_CAPS, router._CATEGORY_CAPS, router._COOLDOWN_FACTORS):
            self.assertEqual(set(table) - set(schema.SURFACES), set())

    def test_each_surface_has_a_response_budget_and_a_session_cap(self):
        caps = config.surface_caps()
        self.assertEqual(set(caps), set(schema.SURFACES))
        for surface, (per_response, per_session) in caps.items():
            with self.subTest(surface=surface):
                # A budget of zero is a surface that is switched off, which is a
                # legitimate configuration; a negative one is a bug that would
                # reach `range()` and behave as zero without saying so.
                self.assertGreaterEqual(per_response, 0)
                self.assertGreaterEqual(per_session, 0)

    def test_each_surface_resolves_a_relevance_floor_inside_the_unit_range(self):
        for surface in schema.SURFACES:
            with self.subTest(surface=surface):
                floor = config.min_score(surface)
                self.assertGreaterEqual(floor, 0.0)
                self.assertLessEqual(floor, 1.0)

    def test_each_surface_has_a_cadence(self):
        for surface in schema.SURFACES:
            with self.subTest(surface=surface):
                cadence = config.cadence(surface)
                self.assertEqual(
                    set(cadence), {"lead_in", "interval", "max_per_page"},
                    "the client destructures exactly these three keys",
                )
                # An interval of 0 would place a card on every item; the client
                # uses it as a modulus, so it must also never be negative.
                self.assertGreaterEqual(cadence["interval"], 1)
                self.assertGreaterEqual(cadence["lead_in"], 0)


class TheMasterSwitchCoversEverySocialSurface(unittest.TestCase):
    """The consent invariant, and the reason this file exists."""

    def test_every_social_surface_is_covered_by_the_master_switch(self):
        expected = set(schema.SURFACES) - NOT_SOCIAL
        self.assertEqual(
            preferences.SOCIAL_SURFACES, expected,
            "a surface outside SOCIAL_SURFACES keeps serving cards to a viewer "
            "who turned discovery off -- add it there, or add it to NOT_SOCIAL "
            "in this file with the reason why the shop's exemption applies to it",
        )

    def test_the_shop_is_still_exempt(self):
        # Asserted positively so that adding `marketplace` to the social set
        # fails here rather than passing as a stricter default. The settings
        # screen tells the user the shop keeps working either way.
        self.assertNotIn("marketplace", preferences.SOCIAL_SURFACES)

    def test_the_social_set_names_nothing_that_is_not_a_surface(self):
        self.assertEqual(preferences.SOCIAL_SURFACES - set(schema.SURFACES), set())


if __name__ == "__main__":
    unittest.main()
