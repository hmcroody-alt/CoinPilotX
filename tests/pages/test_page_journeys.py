"""End-to-end presence journeys: Artist and Business, owner and visitor.

Everything in `test_page_os.py` tests one call. This file tests the joins
between them, which is where the mission's two defects actually live: real
backend capability that nothing reaches, and controls with nothing behind them.
Both survive a suite of green unit tests, because each piece is individually
correct and it is the agreement between them that fails.

Three joins are pinned here.

**The advertised tab is the readable tab.** `public_view` decides which tabs a
viewer gets from `module_availability`; a separate function answers each tab's
contents. A tab offered to a stranger with nothing behind it is a promise
broken in public, and a tab withheld from the team hides the very thing they
are supposed to fill in.

**The setup line resolves.** Every unready management section carries one
sentence naming the one thing missing. `SETUP_RESOLUTIONS` does that thing and
asserts the section flips to ready. This is table-driven and exhaustive: a
section that can be unready and is not in the table fails the exhaustiveness
test rather than quietly shipping advice that leads nowhere.

**The same page reads differently to the team and to a stranger, in exactly
the ways it should and no others.** Not "the visitor sees less" — that is a
privacy test and it already exists — but that the visitor is never shown a
setup prompt, and never shown a tab whose emptiness is the team's problem.
"""

import os
import re
import sys
import unittest
from contextlib import contextmanager

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

from services import pulsesoc_pages  # noqa: E402
from services.pulsesoc_pages import PageError  # noqa: E402

from tests.pages.test_page_os import (  # noqa: E402
    OWNER,
    OWNER_ADVERTISER_ID,
    OWNER_ARTIST,
    OWNER_BUSINESS_ID,
    OWNER_SELLER_ID,
    STRANGER,
    STRANGER_SELLER_ID,
    _now_iso,
    create,
    events_switched,
    make_conn,
    stub_catalogue,
)


def _publish(conn, page_id, post_type="text", body="hello"):
    """A published post, written where `_counts` reads.

    `create_page_post` delegates to the feed engine, which these journeys do
    not own and should not stub — what matters here is the count moving, and
    the count is read straight off `pulse_posts`.
    """
    conn.execute(
        "INSERT INTO pulse_posts (user_id, page_id, body, post_type, created_at) VALUES (?, ?, ?, ?, ?)",
        (OWNER, int(page_id), body, post_type, _now_iso()),
    )
    conn.commit()


def _sections(view):
    return {section["key"]: section for section in view.get("sections", [])}


@contextmanager
def catalogue(tracks):
    """The canonical music catalogue, holding exactly these releases.

    `page_music` reads music_service, which is a different domain with its own
    storage — it is not in the in-memory database these journeys build, so
    without this it answers every presence with nothing and the join under test
    would pass for the wrong reason. `stub_catalogue` is shared with
    `test_page_os` rather than copied, because the way it has to be installed
    is subtle enough that a second copy would get it wrong; see its docstring.
    """
    with stub_catalogue(lambda query="", limit=12, **kw: list(tracks)):
        yield


A_RELEASE = {"id": "t1", "title": "Signal", "artist": OWNER_ARTIST, "genre": "Synth"}


class ArtistJourneyTests(unittest.TestCase):
    """A music presence from empty to furnished, watched from both sides."""

    def setUp(self):
        self.conn = make_conn()
        self.page = create(self.conn, page_type="ARTIST", name="Night Signal", handle="nightsignal")
        self.page_id = self.page["id"]

    def _public(self, viewer):
        return pulsesoc_pages.public_view(
            self.conn, pulsesoc_pages._load_page(self.conn, self.page_id), viewer_user_id=viewer
        )

    def test_day_one_shows_the_stranger_nothing_that_is_not_there(self):
        view = self._public(STRANGER)
        # Posts, About and (for a business) Home are backed by the page row
        # itself and can never be empty in a way that reads as broken. Music,
        # Events, Videos and Merch have nothing behind them on day one.
        self.assertEqual(view["tabs"], ["posts", "about"])
        self.assertIsNone(view["viewer"]["role"])

    def test_day_one_keeps_the_unfilled_tabs_for_the_team(self):
        view = self._public(OWNER)
        # The team can see what a stranger cannot, precisely because they are
        # the ones who can fill it. A tab hidden from its own owner is a
        # capability with no way in.
        self.assertIn("music", view["tabs"])
        self.assertIn("merch", view["tabs"])
        self.assertEqual(view["viewer"]["role"], "OWNER")

    def test_connecting_a_catalogue_opens_the_tab_to_everyone(self):
        pulsesoc_pages.set_link(self.conn, OWNER, self.page_id, "music_artist", OWNER_ARTIST)

        view = self._public(STRANGER)
        self.assertIn("music", view["tabs"])

        # And the tab now reads. The join that matters: the thing that decided
        # to advertise the tab and the thing that answers it agree.
        with catalogue([A_RELEASE]):
            music = pulsesoc_pages.page_music(self.conn, self.page_id, viewer_user_id=STRANGER)
        self.assertTrue(music["linked"])
        self.assertEqual(music["artist"], OWNER_ARTIST)
        self.assertTrue(music["tracks"])

    def test_a_connected_catalogue_with_nothing_in_it_still_says_it_is_connected(self):
        # The one case the client could not tell apart until it was given
        # `linked`: the same empty list as "nothing connected", and the
        # opposite sentence.
        #
        # The link is made first and the catalogue emptied second, because the
        # two facts are checked against different sources. `set_link` proves
        # entitlement from the uploader of the artist's own tracks; the module
        # read asks the catalogue what is published now. An artist who has
        # taken everything down is entitled to the pointer and has nothing
        # behind it, which is precisely this state.
        pulsesoc_pages.set_link(self.conn, OWNER, self.page_id, "music_artist", OWNER_ARTIST)

        with catalogue([]):
            music = pulsesoc_pages.page_music(self.conn, self.page_id, viewer_user_id=STRANGER)
        self.assertTrue(music["linked"])
        self.assertEqual(music["tracks"], [])

    def test_publishing_moves_the_count_both_sides_read(self):
        _publish(self.conn, self.page_id)
        self.assertEqual(self._public(STRANGER)["posts_count"], 1)
        self.assertEqual(self._public(OWNER)["posts_count"], 1)

    def test_a_video_post_opens_the_videos_tab(self):
        self.assertNotIn("videos", self._public(STRANGER)["tabs"])
        _publish(self.conn, self.page_id, post_type="video")
        view = self._public(STRANGER)
        self.assertIn("videos", view["tabs"])
        self.assertEqual(view["videos_count"], 1)

    def test_unpublishing_takes_it_away_from_the_stranger_and_not_from_the_team(self):
        pulsesoc_pages.set_status(self.conn, OWNER, self.page_id, "UNPUBLISHED")

        with self.assertRaises(PageError):
            pulsesoc_pages._load_visible_page(self.conn, self.page_id, STRANGER)
        # The team keeps working on it — that is what unpublished is for.
        still_theirs = pulsesoc_pages._load_visible_page(self.conn, self.page_id, OWNER)
        self.assertEqual(int(still_theirs["id"]), self.page_id)

    def test_an_unpublished_presence_refuses_the_follow_it_no_longer_offers(self):
        pulsesoc_pages.set_status(self.conn, OWNER, self.page_id, "UNPUBLISHED")
        # A stranger is answered 404, not 403 — the follow route is not an
        # existence oracle. A team member gets the honest refusal, which is the
        # one the screen now withholds the button for rather than showing.
        with self.assertRaises(PageError) as stranger_refusal:
            pulsesoc_pages.toggle_follow(self.conn, STRANGER, self.page_id)
        self.assertEqual(stranger_refusal.exception.status_code, 404)

        with self.assertRaises(PageError) as team_refusal:
            pulsesoc_pages.toggle_follow(self.conn, OWNER, self.page_id)
        self.assertEqual(team_refusal.exception.status_code, 403)

    def test_a_paused_presence_is_still_followable(self):
        pulsesoc_pages.set_status(self.conn, OWNER, self.page_id, "PAUSED")
        result = pulsesoc_pages.toggle_follow(self.conn, STRANGER, self.page_id)
        self.assertTrue(result["following"])
        self.assertEqual(result["followers_count"], 1)

    def test_reactivating_gives_it_back(self):
        pulsesoc_pages.set_status(self.conn, OWNER, self.page_id, "UNPUBLISHED")
        pulsesoc_pages.set_status(self.conn, OWNER, self.page_id, "ACTIVE")
        view = pulsesoc_pages._load_visible_page(self.conn, self.page_id, STRANGER)
        self.assertEqual(view["status"], "ACTIVE")


class BusinessJourneyTests(unittest.TestCase):
    """The same arc for a presence that sells things and puts on dates."""

    def setUp(self):
        self.conn = make_conn()
        self.page = create(
            self.conn, page_type="RESTAURANT", name="Kofi's", handle="kofis"
        )
        self.page_id = self.page["id"]

    def _public(self, viewer, page_id=None):
        page_id = self.page_id if page_id is None else page_id
        return pulsesoc_pages.public_view(
            self.conn, pulsesoc_pages._load_page(self.conn, page_id), viewer_user_id=viewer
        )

    def _venue(self):
        """A presence whose type actually carries dates.

        A restaurant's page has no Events tab in `TYPE_TABS` — it has a menu —
        and that is the whole point of the type deciding presentation. Testing
        the events join against a restaurant would be testing a tab the design
        never offers it, which passes or fails for reasons that have nothing to
        do with events.
        """
        return create(self.conn, page_type="VENUE", name="The Room", handle="theroom")["id"]

    def test_day_one_offers_the_stranger_only_what_the_row_itself_backs(self):
        view = self._public(STRANGER)
        self.assertEqual(view["tabs"], ["home", "about"])

    def test_connecting_a_shop_opens_the_menu_tab_and_names_the_seller(self):
        pulsesoc_pages.set_link(self.conn, OWNER, self.page_id, "store", OWNER_SELLER_ID)
        view = self._public(STRANGER)

        self.assertIn("menu", view["tabs"])
        # The client fetches the storefront by this id and by nothing else, so
        # a tab that opens without it opens onto the whole marketplace.
        self.assertEqual(view["shop_seller_id"], OWNER_SELLER_ID)

    def test_a_stranger_cannot_reach_the_link_at_all(self):
        with self.assertRaises(PageError):
            pulsesoc_pages.set_link(self.conn, STRANGER, self.page_id, "store", OWNER_SELLER_ID)

    def test_the_owner_of_the_page_still_has_to_own_the_shop(self):
        """The hijack the ownership check exists for, aimed the way it happens.

        The test above is refused before the ownership check is reached — a
        stranger has no seat on this page, so `require_permission` stops them
        and the link rule is never consulted. The attack that gets that far is
        this one: somebody with every right to manage *their own* presence
        pointing it at a storefront that is not theirs. A seller id is just an
        integer, and without this the Merch tab is a way to mount anyone's
        catalogue under your own name.
        """
        with self.assertRaises(PageError) as refused:
            pulsesoc_pages.set_link(self.conn, OWNER, self.page_id, "store", STRANGER_SELLER_ID)
        self.assertEqual(refused.exception.status_code, 403)
        # And nothing was written on the way to refusing.
        self.assertEqual(pulsesoc_pages.list_links(self.conn, self.page_id, "store"), [])

    def test_the_shop_tab_is_given_the_shop_and_not_the_nearest_other_link(self):
        # A presence carries several pointers at once and they are not
        # interchangeable: the client deep-links into Marketplace with whatever
        # `shop_seller_id` holds, and a business id is not a seller id.
        #
        # The shop is connected first and the business second, because
        # `list_links` returns newest-first — so it is the *business* link that
        # sits at the head of the list, and a read that took the first row
        # rather than the store row would hand the shop tab a business id. Made
        # the other way round the same wrong code would pass.
        pulsesoc_pages.set_link(self.conn, OWNER, self.page_id, "store", OWNER_SELLER_ID)
        pulsesoc_pages.set_link(self.conn, OWNER, self.page_id, "business_os", OWNER_BUSINESS_ID)

        self.assertEqual(self._public(STRANGER)["shop_seller_id"], OWNER_SELLER_ID)

    def test_a_restaurant_is_not_offered_a_tab_its_type_does_not_carry(self):
        # The type decides presentation, and it decides it the same way for
        # everyone: connecting a business to a restaurant furnishes its
        # operations without growing it an Events tab it was never given.
        with events_switched(True):
            pulsesoc_pages.set_link(self.conn, OWNER, self.page_id, "business_os", OWNER_BUSINESS_ID)
            self.assertNotIn("events", self._public(STRANGER)["tabs"])
            self.assertNotIn("events", self._public(OWNER)["tabs"])

    def test_events_need_both_a_business_and_an_environment_that_serves_them(self):
        venue_id = self._venue()
        with events_switched(False):
            pulsesoc_pages.set_link(self.conn, OWNER, venue_id, "business_os", OWNER_BUSINESS_ID)
            self.assertNotIn("events", self._public(STRANGER, venue_id)["tabs"])

        with events_switched(True):
            self.assertIn("events", self._public(STRANGER, venue_id)["tabs"])

    def test_a_switched_on_environment_is_not_on_its_own_enough(self):
        # The other half of the conjunction, and the half the test above cannot
        # reach: it turns the flag on with the link already made, so a rule
        # that had quietly stopped consulting the link would pass it. The flag
        # is global — an environment that serves events serves them for every
        # presence — so if it alone decided the tab, every venue in the product
        # would advertise dates and none of them would have any.
        venue_id = self._venue()
        with events_switched(True):
            self.assertNotIn("events", self._public(STRANGER, venue_id)["tabs"])
            # And the team keeps it, because connecting the business is the
            # thing they are being asked to do.
            self.assertIn("events", self._public(OWNER, venue_id)["tabs"])

    def test_one_business_link_answers_both_the_events_tab_and_operations(self):
        # They read the same link on purpose. An owner who connects a business
        # for its dates has also told the presence which business it is, and
        # asking twice is how a management screen loses their trust.
        venue_id = self._venue()
        with events_switched(True):
            pulsesoc_pages.set_link(self.conn, OWNER, venue_id, "business_os", OWNER_BUSINESS_ID)
            manage = pulsesoc_pages.manage_view(self.conn, OWNER, venue_id)

        sections = _sections(manage)
        self.assertTrue(sections["events"]["ready"])
        self.assertTrue(sections["business_os"]["ready"])

    def test_the_stranger_is_never_shown_a_setup_prompt(self):
        # `sections` is management's answer and lives only in `manage_view`.
        # The public payload has no field that could carry advice.
        view = self._public(STRANGER)
        self.assertNotIn("sections", view)
        self.assertNotIn("links", view)
        self.assertNotIn("completeness", view)

    def test_a_stranger_cannot_open_management_at_all(self):
        with self.assertRaises(PageError):
            pulsesoc_pages.manage_view(self.conn, STRANGER, self.page_id)


# Each entry does the one thing the section's `setup` line names, and the test
# asserts the section then reports itself ready. A section whose advice cannot
# be acted on is the mission's defect in its purest form: a sentence telling an
# owner to do something that changes nothing.
SETUP_RESOLUTIONS = {
    "content": lambda conn, page_id: _publish(conn, page_id),
    "videos": lambda conn, page_id: _publish(conn, page_id, post_type="video"),
    "music": lambda conn, page_id: pulsesoc_pages.set_link(
        conn, OWNER, page_id, "music_artist", OWNER_ARTIST),
    "store": lambda conn, page_id: pulsesoc_pages.set_link(
        conn, OWNER, page_id, "store", OWNER_SELLER_ID),
    "advertising": lambda conn, page_id: pulsesoc_pages.set_link(
        conn, OWNER, page_id, "ad_account", OWNER_ADVERTISER_ID),
    "events": lambda conn, page_id: pulsesoc_pages.set_link(
        conn, OWNER, page_id, "business_os", OWNER_BUSINESS_ID),
    "business_os": lambda conn, page_id: pulsesoc_pages.set_link(
        conn, OWNER, page_id, "business_os", OWNER_BUSINESS_ID),
}

# Verification is the one section an owner cannot resolve alone, and that is
# the point of it: requests are reviewed, never granted automatically. Its
# setup line says so rather than offering a step.
NOT_SELF_RESOLVABLE = {"verification"}


class SetupAdviceResolvesTests(unittest.TestCase):
    """Every unready section's advice, carried out, makes it ready."""

    # One of each shape rather than all sixteen: the section list is driven by
    # `TYPE_TABS` and `BUSINESS_PAGE_TYPES`, and these three between them reach
    # every branch in `manage_sections`.
    PAGE_TYPES = ("ARTIST", "CREATOR", "RESTAURANT")

    def _fresh(self, page_type, handle):
        conn = make_conn()
        page = create(conn, page_type=page_type, name="Presence", handle=handle)
        return conn, page["id"]

    def test_every_unready_section_has_advice_that_resolves_it(self):
        for page_type in self.PAGE_TYPES:
            conn, page_id = self._fresh(page_type, "presence" + page_type.lower())
            with events_switched(True):
                before = _sections(pulsesoc_pages.manage_view(conn, OWNER, page_id))
            unready = [key for key, section in before.items() if not section["ready"]]
            self.assertTrue(unready, f"{page_type} starts fully furnished, which cannot be right")

            for key in unready:
                if key in NOT_SELF_RESOLVABLE:
                    continue
                with self.subTest(page_type=page_type, section=key):
                    resolve = SETUP_RESOLUTIONS.get(key)
                    self.assertIsNotNone(
                        resolve,
                        f"{page_type}/{key} tells an owner to do something with no way to do it",
                    )
                    conn, page_id = self._fresh(page_type, f"p{key}{page_type.lower()}")
                    resolve(conn, page_id)
                    with events_switched(True):
                        after = _sections(pulsesoc_pages.manage_view(conn, OWNER, page_id))
                    self.assertTrue(
                        after[key]["ready"],
                        f"{page_type}/{key}: did what the setup line said and nothing changed",
                    )

    def test_an_unready_section_always_says_what_is_missing(self):
        for page_type in self.PAGE_TYPES:
            conn, page_id = self._fresh(page_type, "says" + page_type.lower())
            with events_switched(True):
                sections = _sections(pulsesoc_pages.manage_view(conn, OWNER, page_id))
            for key, section in sections.items():
                with self.subTest(page_type=page_type, section=key):
                    if section["ready"]:
                        # The reverse too: a finished section must not carry a
                        # leftover instruction telling the owner to finish it.
                        self.assertEqual(section["setup"], "")
                    else:
                        self.assertTrue(section["setup"].strip())

    def test_the_resolution_table_does_not_name_sections_that_do_not_exist(self):
        # An entry that never runs is a test that looks like coverage and is
        # not — the whole reason the table above is asserted against rather
        # than iterated over.
        seen = set()
        for page_type in self.PAGE_TYPES:
            conn, page_id = self._fresh(page_type, "seen" + page_type.lower())
            with events_switched(True):
                seen.update(_sections(pulsesoc_pages.manage_view(conn, OWNER, page_id)))
        self.assertEqual(set(SETUP_RESOLUTIONS) - seen, set())
        self.assertEqual(NOT_SELF_RESOLVABLE - seen, set())

    def test_a_switched_off_environment_is_not_offered_as_the_owners_to_fix(self):
        # The one unready section whose advice is deliberately not a step: the
        # events flag is not the owner's to turn on, so the line says what is
        # true rather than sending them to connect something that would not
        # help.
        conn, page_id = self._fresh("ARTIST", "flagoff")
        pulsesoc_pages.set_link(conn, OWNER, page_id, "business_os", OWNER_BUSINESS_ID)
        with events_switched(False):
            sections = _sections(pulsesoc_pages.manage_view(conn, OWNER, page_id))

        events = sections["events"]
        self.assertFalse(events["ready"])
        self.assertIn("not switched on", events["setup"])
        self.assertNotIn("Connect", events["setup"])


class AdvertisedTabIsReadableTests(unittest.TestCase):
    """A tab offered to a stranger answers when the stranger opens it.

    The client renders exactly the tabs the server sends and fetches each
    module when its tab is opened. So a tab in `tabs` whose module read
    *refuses*, or reports itself unconnected, is a promise broken in public —
    and the two decisions are made by different functions, which is exactly the
    join a per-function test cannot see.

    Empty is not in that list. `module_availability` advertises on the pointer,
    not on today's row count, and the tests below pin that on purpose rather
    than around it.
    """

    def _tabs(self, conn, page_id, viewer):
        return pulsesoc_pages.public_view(
            conn, pulsesoc_pages._load_page(conn, page_id), viewer_user_id=viewer)["tabs"]

    def test_a_music_tab_shown_to_a_stranger_answers_when_it_is_opened(self):
        conn = make_conn()
        page_id = create(conn, page_type="ARTIST", handle="reads")["id"]
        pulsesoc_pages.set_link(conn, OWNER, page_id, "music_artist", OWNER_ARTIST)

        self.assertIn("music", self._tabs(conn, page_id, STRANGER))
        with catalogue([A_RELEASE]):
            music = pulsesoc_pages.page_music(conn, page_id, viewer_user_id=STRANGER)
        self.assertTrue(music["tracks"])

    def test_an_unconnected_module_is_offered_to_the_team_and_to_nobody_else(self):
        # The other half of the same rule, and the one that catches a tab
        # advertised on nothing at all: with no link there is no catalogue
        # identity to read, so a stranger shown this tab would be shown a
        # section that cannot answer for any reason.
        conn = make_conn()
        page_id = create(conn, page_type="ARTIST", handle="unlinked")["id"]

        self.assertNotIn("music", self._tabs(conn, page_id, STRANGER))
        self.assertIn("music", self._tabs(conn, page_id, OWNER))

        with catalogue([A_RELEASE]):
            music = pulsesoc_pages.page_music(conn, page_id, viewer_user_id=STRANGER)
        # Not "no tracks" — no pointer. The module refuses to guess an artist
        # identity from the page's name, which is how one presence would end up
        # showing another act's releases.
        self.assertFalse(music["linked"])
        self.assertEqual(music["tracks"], [])

    def test_a_tab_is_advertised_on_the_pointer_and_not_on_todays_contents(self):
        """A connected catalogue with nothing in it keeps its tab in public.

        This is the rule the system actually has, and it is worth writing down
        because the obvious alternative reads as more careful and is worse.
        `module_availability` asks whether the presence is *pointed at a
        source*, not how many rows that source returns today. So an artist
        between releases still has a Music section, and it says "connected,
        nothing published" rather than vanishing from their page and coming
        back on release day — a presence whose shape changes under its own
        audience for reasons the owner did not do.

        The cost of the alternative is the giveaway: deciding on contents means
        querying music_service, Marketplace and the events domain on every
        public page load, to answer a question about which headings to draw.
        The tab is a promise that a section exists, and the module read is
        where the honest empty state lives.
        """
        conn = make_conn()
        page_id = create(conn, page_type="ARTIST", handle="empty")["id"]
        pulsesoc_pages.set_link(conn, OWNER, page_id, "music_artist", OWNER_ARTIST)

        self.assertIn("music", self._tabs(conn, page_id, STRANGER))
        with catalogue([]):
            music = pulsesoc_pages.page_music(conn, page_id, viewer_user_id=STRANGER)
        # Connected, and empty, and able to say so — which is the distinction
        # a bare empty list could not draw.
        self.assertTrue(music["linked"])
        self.assertEqual(music["tracks"], [])

    def test_every_tab_the_server_offers_is_one_the_client_can_draw(self):
        # `RENDERABLE_TABS` is PageScreen's branch set written down on the
        # server. A page type naming a tab outside it would render as "this
        # section needs a newer version of the app" against a matching build.
        for page_type, tabs in pulsesoc_pages.TYPE_TABS.items():
            with self.subTest(page_type=page_type):
                self.assertEqual(set(tabs) - set(pulsesoc_pages.RENDERABLE_TABS), set())

    def test_the_servers_renderable_set_is_actually_the_screens_branch_set(self):
        """And `RENDERABLE_TABS` is checked against the screen it describes.

        The test above only says TYPE_TABS stays inside RENDERABLE_TABS. What
        nothing checked is the sentence RENDERABLE_TABS' own comment makes:
        that it *is* PageScreen's branch set. It is a claim about a file in
        another language, so it has been true by attention rather than by
        anything, and the failure it guards against is silent in both
        directions.

        A tab in the constant with no branch behind it reaches a matching
        build as "This section needs a newer version of the app" — on the
        newest version of the app. A branch with no tab in the constant is
        working client code the server will never ask for, which is the
        mission's first defect exactly: capability nothing reaches.
        """
        source_path = os.path.join(
            REPO_ROOT, "mobile-native", "src", "screens", "PageScreen.tsx")
        self.assertTrue(
            os.path.exists(source_path),
            "PageScreen.tsx has moved; RENDERABLE_TABS now describes nothing")
        with open(source_path, encoding="utf-8") as handle:
            source = handle.read()

        branches = set(re.findall(r'tab === "([a-z_]+)"', source))
        # The three storefront tabs share one branch, because they differ only
        # in the word on the button.
        for group in re.findall(r"const SHOP_TABS = \[([^\]]*)\]", source):
            branches.update(re.findall(r'"([a-z_]+)"', group))

        # The extraction is the fragile part, so it is asserted before it is
        # trusted: a rewrite that changes the shapes above would otherwise find
        # nothing and report perfect agreement.
        self.assertGreaterEqual(
            len(branches), 6,
            f"found only {sorted(branches)} in PageScreen — the shapes this "
            "test reads for have changed, and it is no longer checking anything")

        renderable = set(pulsesoc_pages.RENDERABLE_TABS)
        self.assertEqual(
            renderable - branches, set(),
            "the server offers a tab PageScreen has no branch for")
        self.assertEqual(
            branches - renderable, set(),
            "PageScreen draws a tab the server will never send")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
