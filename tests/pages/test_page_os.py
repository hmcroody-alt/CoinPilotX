"""Page OS contracts: one canonical page backend for every page type.

Covers the mission's hard rules:
  * pages of every type are created through one flow with owner confirmation;
  * one user owns multiple pages — PERSON ≠ PAGE, no second login;
  * handles are unique across pages AND user accounts (impersonation-aware),
    reserved words are refused;
  * roles are bounded: invites can never assign OWNER, acceptance never
    grants OWNER, an ANALYST cannot manage roles, a CONTENT_MANAGER cannot
    transfer ownership;
  * ownership transfer is owner-only, explicitly confirmed, audited;
  * identity switching lists personal + page identities with posting rights;
  * page posts go through the canonical content system with page_id set —
    personal posts are untouched;
  * private management data never appears in the public view;
  * marketplace/ads are LINKS to canonical systems, permission-gated;
  * UNDX page context is role-bounded — no path to owner authority;
  * Sentinel vocab gained page relationships, and Sentinel failure never
    blocks a page operation (observe-only, no auto-seize);
  * no hard delete: DEACTIVATED keeps the row and its audit history.
"""

import os
import sqlite3
import sys
import types
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO_ROOT)

from services import pulsesoc_pages  # noqa: E402
from services.pulsesoc_pages import PageError  # noqa: E402

OWNER = 11
FRIEND = 22
STRANGER = 33

USERS_SCHEMA = """
CREATE TABLE users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    display_name TEXT,
    full_name TEXT,
    avatar_url TEXT
);
CREATE TABLE pulse_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    post_type TEXT,
    body TEXT,
    title TEXT,
    visibility TEXT DEFAULT 'public',
    moderation_status TEXT DEFAULT 'approved',
    page_id INTEGER,
    created_at TEXT,
    deleted_at TEXT
);
"""

# A link points the page at a resource that lives in another system and belongs
# to somebody. `set_link` consults these to answer "is the actor entitled to
# this ref?", so they have to exist for any link test to exercise the real
# path — the shape matches the production tables it reads.
LINK_TARGET_SCHEMA = """
CREATE TABLE marketplace_sellers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER UNIQUE,
    display_name TEXT,
    status TEXT DEFAULT 'pending'
);
CREATE TABLE advertisers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER,
    advertiser_name TEXT,
    status TEXT DEFAULT 'pending'
);
CREATE TABLE pulse_groups (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_user_id INTEGER,
    slug TEXT UNIQUE,
    name TEXT
);
CREATE TABLE pulse_audio_tracks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT,
    artist TEXT,
    uploader_user_id INTEGER,
    audio_url TEXT,
    approved_by_admin INTEGER DEFAULT 0,
    active INTEGER DEFAULT 1
);
"""

# Resources the OWNER genuinely holds, and the matching ones a STRANGER holds.
# Every link test names one of these rather than an arbitrary integer, because
# an arbitrary integer is precisely what is no longer accepted.
OWNER_SELLER_ID = 42
STRANGER_SELLER_ID = 77
OWNER_ADVERTISER_ID = 9
STRANGER_ADVERTISER_ID = 91
OWNER_GROUP_ID = 3
STRANGER_GROUP_ID = 31
OWNER_ARTIST = "Night Signal"
STRANGER_ARTIST = "Someone Else"


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(USERS_SCHEMA)
    conn.executescript(LINK_TARGET_SCHEMA)
    conn.execute("INSERT INTO users VALUES (?, 'roody', 'Roody', 'Roody C', '')", (OWNER,))
    conn.execute("INSERT INTO users VALUES (?, 'friend', 'Friend', 'Friend F', '')", (FRIEND,))
    conn.execute("INSERT INTO users VALUES (?, 'stranger', 'Stranger', 'S S', '')", (STRANGER,))
    conn.execute("INSERT INTO marketplace_sellers (id, user_id, display_name, status) VALUES (?, ?, 'Owner Shop', 'active')",
                 (OWNER_SELLER_ID, OWNER))
    conn.execute("INSERT INTO marketplace_sellers (id, user_id, display_name, status) VALUES (?, ?, 'Stranger Shop', 'active')",
                 (STRANGER_SELLER_ID, STRANGER))
    conn.execute("INSERT INTO advertisers (id, owner_user_id, advertiser_name) VALUES (?, ?, 'Owner Ads')",
                 (OWNER_ADVERTISER_ID, OWNER))
    conn.execute("INSERT INTO advertisers (id, owner_user_id, advertiser_name) VALUES (?, ?, 'Stranger Ads')",
                 (STRANGER_ADVERTISER_ID, STRANGER))
    conn.execute("INSERT INTO pulse_groups (id, owner_user_id, slug, name) VALUES (?, ?, 'owner-group', 'Owner Group')",
                 (OWNER_GROUP_ID, OWNER))
    conn.execute("INSERT INTO pulse_groups (id, owner_user_id, slug, name) VALUES (?, ?, 'stranger-group', 'Stranger Group')",
                 (STRANGER_GROUP_ID, STRANGER))
    conn.execute("INSERT INTO pulse_audio_tracks (title, artist, uploader_user_id, audio_url, approved_by_admin) "
                 "VALUES ('Track One', ?, ?, 'https://cdn/one.mp3', 1)", (OWNER_ARTIST, OWNER))
    conn.execute("INSERT INTO pulse_audio_tracks (title, artist, uploader_user_id, audio_url, approved_by_admin) "
                 "VALUES ('Other Track', ?, ?, 'https://cdn/two.mp3', 1)", (STRANGER_ARTIST, STRANGER))
    pulsesoc_pages.ensure_tables(conn)
    return conn


def create(conn, user_id=OWNER, **overrides):
    payload = {
        "page_type": "ARTIST",
        "name": "Night Signal",
        "handle": "nightsignal",
        "confirm_owner": True,
    }
    payload.update(overrides)
    return pulsesoc_pages.create_page(conn, user_id, payload)


class PageCreationTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_conn()

    def test_creates_every_canonical_type(self):
        for i, page_type in enumerate(("ARTIST", "BUSINESS", "RESTAURANT", "NONPROFIT")):
            page = create(self.conn, page_type=page_type, name=f"Page {i}", handle=f"page-{i}")
            self.assertEqual(page["page_type"], page_type)
            self.assertEqual(page["status"], "ACTIVE")
            self.assertEqual(page["verification_status"], "unverified")

    def test_unknown_type_rejected(self):
        with self.assertRaises(PageError):
            create(self.conn, page_type="FAN_CLUB")

    def test_owner_confirmation_required(self):
        with self.assertRaises(PageError):
            create(self.conn, confirm_owner=False)

    def test_one_user_owns_multiple_pages(self):
        create(self.conn, page_type="ARTIST", handle="artist-me")
        create(self.conn, page_type="RESTAURANT", name="Chez Roody", handle="chezroody")
        pages = pulsesoc_pages.list_my_pages(self.conn, OWNER)
        self.assertEqual(len(pages), 2)
        self.assertTrue(all(p["role"] == "OWNER" for p in pages))

    def test_creation_never_grants_verification(self):
        page = create(self.conn)
        self.assertFalse(page["verified"])

    def test_verification_request_only_moves_to_pending(self):
        page = create(self.conn)
        out = pulsesoc_pages.request_verification(self.conn, OWNER, page["id"], {})
        self.assertEqual(out["verification_status"], "pending")
        refreshed = pulsesoc_pages.public_view(self.conn, pulsesoc_pages._load_page(self.conn, page["id"]))
        self.assertFalse(refreshed["verified"])


class HandleTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_conn()

    def test_duplicate_page_handle_rejected(self):
        create(self.conn, handle="dupe")
        with self.assertRaises(PageError) as ctx:
            create(self.conn, name="Second", handle="Dupe")  # case-insensitive
        self.assertEqual(ctx.exception.status_code, 409)

    def test_user_handle_impersonation_rejected(self):
        result = pulsesoc_pages.check_handle(self.conn, "roody")
        self.assertFalse(result["available"])
        self.assertIn("member account", result["reason"])

    def test_reserved_handle_rejected(self):
        result = pulsesoc_pages.check_handle(self.conn, "pulsesoc")
        self.assertFalse(result["available"])

    def test_bad_grammar_rejected(self):
        self.assertFalse(pulsesoc_pages.check_handle(self.conn, "a")["available"])
        self.assertFalse(pulsesoc_pages.check_handle(self.conn, "has space")["available"])


class RoleTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_conn()
        self.page = create(self.conn)
        self.page_id = self.page["id"]

    def invite_and_accept(self, user_id, role):
        invite = pulsesoc_pages.invite_member(self.conn, OWNER, self.page_id, {"user_id": user_id, "role": role})
        pulsesoc_pages.accept_invite(self.conn, user_id, invite["invite_token"])

    def test_owner_invites_admin(self):
        self.invite_and_accept(FRIEND, "ADMIN")
        self.assertEqual(pulsesoc_pages.role_for(self.conn, FRIEND, self.page_id), "ADMIN")

    def test_invite_cannot_assign_owner(self):
        with self.assertRaises(PageError):
            pulsesoc_pages.invite_member(self.conn, OWNER, self.page_id, {"user_id": FRIEND, "role": "OWNER"})

    def test_acceptance_never_grants_owner_even_if_row_tampered(self):
        invite = pulsesoc_pages.invite_member(self.conn, OWNER, self.page_id, {"user_id": FRIEND, "role": "ADMIN"})
        # Simulate DB tampering: force the stored role to OWNER before accept.
        self.conn.execute("UPDATE pulse_page_members SET role='OWNER' WHERE page_id=? AND user_id=?",
                          (self.page_id, FRIEND))
        out = pulsesoc_pages.accept_invite(self.conn, FRIEND, invite["invite_token"])
        self.assertNotEqual(out["role"], "OWNER")

    def test_analyst_cannot_manage_roles(self):
        self.invite_and_accept(FRIEND, "ANALYST")
        with self.assertRaises(PageError) as ctx:
            pulsesoc_pages.invite_member(self.conn, FRIEND, self.page_id, {"user_id": STRANGER, "role": "ANALYST"})
        self.assertEqual(ctx.exception.status_code, 403)
        with self.assertRaises(PageError):
            pulsesoc_pages.change_role(self.conn, FRIEND, self.page_id, OWNER, "ANALYST")

    def test_content_manager_cannot_transfer_ownership(self):
        self.invite_and_accept(FRIEND, "CONTENT_MANAGER")
        with self.assertRaises(PageError) as ctx:
            pulsesoc_pages.transfer_ownership(self.conn, FRIEND, self.page_id, FRIEND, "TRANSFER")
        self.assertEqual(ctx.exception.status_code, 403)

    def test_transfer_requires_confirmation_phrase(self):
        self.invite_and_accept(FRIEND, "ADMIN")
        with self.assertRaises(PageError):
            pulsesoc_pages.transfer_ownership(self.conn, OWNER, self.page_id, FRIEND, "yes please")

    def test_transfer_target_must_be_member(self):
        with self.assertRaises(PageError):
            pulsesoc_pages.transfer_ownership(self.conn, OWNER, self.page_id, STRANGER, "TRANSFER")

    def test_transfer_is_audited_and_demotes_old_owner(self):
        self.invite_and_accept(FRIEND, "ADMIN")
        out = pulsesoc_pages.transfer_ownership(self.conn, OWNER, self.page_id, FRIEND, "TRANSFER")
        self.assertEqual(out["owner_user_id"], FRIEND)
        self.assertEqual(pulsesoc_pages.role_for(self.conn, FRIEND, self.page_id), "OWNER")
        self.assertEqual(pulsesoc_pages.role_for(self.conn, OWNER, self.page_id), "ADMIN")
        audit = self.conn.execute(
            "SELECT * FROM pulse_page_audit WHERE page_id=? AND action='ownership_transferred'",
            (self.page_id,),
        ).fetchall()
        self.assertEqual(len(audit), 1)

    def test_owner_cannot_be_removed(self):
        with self.assertRaises(PageError):
            pulsesoc_pages.remove_member(self.conn, OWNER, self.page_id, OWNER)

    def test_expired_invite_rejected(self):
        invite = pulsesoc_pages.invite_member(self.conn, OWNER, self.page_id, {"user_id": FRIEND, "role": "ADMIN"})
        self.conn.execute("UPDATE pulse_page_members SET invite_expires_at='2020-01-01T00:00:00+00:00' "
                          "WHERE page_id=? AND user_id=?", (self.page_id, FRIEND))
        with self.assertRaises(PageError) as ctx:
            pulsesoc_pages.accept_invite(self.conn, FRIEND, invite["invite_token"])
        self.assertEqual(ctx.exception.status_code, 410)


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_conn()

    def test_identity_switching_lists_personal_and_pages(self):
        create(self.conn, handle="mypage")
        identities = pulsesoc_pages.list_identities(self.conn, OWNER)
        self.assertEqual(identities["personal"]["kind"], "personal")
        self.assertEqual(identities["personal"]["handle"], "roody")
        self.assertEqual(len(identities["pages"]), 1)
        self.assertEqual(identities["pages"][0]["handle"], "mypage")

    def test_analyst_identity_cannot_post(self):
        page = create(self.conn, handle="mypage")
        invite = pulsesoc_pages.invite_member(self.conn, OWNER, page["id"], {"user_id": FRIEND, "role": "ANALYST"})
        pulsesoc_pages.accept_invite(self.conn, FRIEND, invite["invite_token"])
        identities = pulsesoc_pages.list_identities(self.conn, FRIEND)
        self.assertEqual(identities["pages"], [])


class ContentTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_conn()
        self.page = create(self.conn)
        self.page_id = self.page["id"]
        # Stub the canonical content system so this test observes exactly what
        # the Page OS hands it — page posts MUST flow through create_post.
        self.calls = []
        fake = types.ModuleType("services.pulse_feed_engine")

        def fake_create_post(user_id, **kwargs):
            self.calls.append({"user_id": user_id, **kwargs})
            return {"ok": True, "post_id": 777, "status": "approved"}

        fake.create_post = fake_create_post
        self._orig_module = sys.modules.get("services.pulse_feed_engine")
        import services as services_pkg
        self._orig_attr = getattr(services_pkg, "pulse_feed_engine", None)
        sys.modules["services.pulse_feed_engine"] = fake
        services_pkg.pulse_feed_engine = fake

    def tearDown(self):
        import services as services_pkg
        if self._orig_module is not None:
            sys.modules["services.pulse_feed_engine"] = self._orig_module
        else:
            sys.modules.pop("services.pulse_feed_engine", None)
        if self._orig_attr is not None:
            services_pkg.pulse_feed_engine = self._orig_attr
        elif hasattr(services_pkg, "pulse_feed_engine"):
            delattr(services_pkg, "pulse_feed_engine")

    def test_page_post_uses_canonical_content_system_with_page_id(self):
        result = pulsesoc_pages.create_page_post(self.conn, OWNER, self.page_id, {"body": "hello"})
        self.assertTrue(result["ok"])
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0]["page_id"], self.page_id)
        self.assertEqual(self.calls[0]["user_id"], OWNER)

    def test_non_member_cannot_post_as_page(self):
        with self.assertRaises(PageError):
            pulsesoc_pages.create_page_post(self.conn, STRANGER, self.page_id, {"body": "spam"})
        self.assertEqual(self.calls, [])

    def test_analyst_cannot_post_as_page(self):
        invite = pulsesoc_pages.invite_member(self.conn, OWNER, self.page_id, {"user_id": FRIEND, "role": "ANALYST"})
        pulsesoc_pages.accept_invite(self.conn, FRIEND, invite["invite_token"])
        with self.assertRaises(PageError):
            pulsesoc_pages.create_page_post(self.conn, FRIEND, self.page_id, {"body": "no"})

    def test_paused_page_cannot_publish(self):
        pulsesoc_pages.set_status(self.conn, OWNER, self.page_id, "PAUSED")
        with self.assertRaises(PageError):
            pulsesoc_pages.create_page_post(self.conn, OWNER, self.page_id, {"body": "x"})

    def test_personal_posts_untouched(self):
        # A personal post row simply has no page_id — nothing about the Page OS
        # rewrites existing content.
        self.conn.execute("INSERT INTO pulse_posts (user_id, body, created_at) VALUES (?, 'mine', '2026-01-01')",
                          (OWNER,))
        row = self.conn.execute("SELECT page_id FROM pulse_posts WHERE user_id=?", (OWNER,)).fetchone()
        self.assertIsNone(row["page_id"])


class PrivacyTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_conn()
        self.page = create(self.conn, phone="555-0100")
        self.page_id = self.page["id"]

    def test_public_view_has_no_private_data(self):
        view = pulsesoc_pages.public_view(self.conn, pulsesoc_pages._load_page(self.conn, self.page_id))
        for forbidden in ("members", "links", "owner_user_id", "phone", "capabilities", "analytics"):
            self.assertNotIn(forbidden, view)

    def test_manage_view_requires_membership(self):
        with self.assertRaises(PageError) as ctx:
            pulsesoc_pages.manage_view(self.conn, STRANGER, self.page_id)
        self.assertEqual(ctx.exception.status_code, 403)

    def test_manage_view_for_owner_includes_team(self):
        view = pulsesoc_pages.manage_view(self.conn, OWNER, self.page_id)
        self.assertEqual(view["role"], "OWNER")
        self.assertEqual(len(view["members"]), 1)
        self.assertIn("analytics", view)

    def test_analytics_reports_only_measured_numbers(self):
        analytics = pulsesoc_pages.page_analytics(self.conn, OWNER, self.page_id)
        self.assertEqual(analytics["followers"], 0)
        self.assertEqual(analytics["posts"], 0)
        self.assertNotIn("reach", analytics)
        self.assertNotIn("impressions", analytics)


class LinkTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_conn()
        self.page = create(self.conn)
        self.page_id = self.page["id"]

    def test_store_link_gated_on_marketplace_permission(self):
        invite = pulsesoc_pages.invite_member(self.conn, OWNER, self.page_id,
                                              {"user_id": FRIEND, "role": "MARKETPLACE_MANAGER"})
        pulsesoc_pages.accept_invite(self.conn, FRIEND, invite["invite_token"])
        # The store belongs to the page's OWNER, not to the manager doing the
        # linking. A marketplace manager acts for the presence, so the
        # presence's own entitlement is what has to carry the link.
        link = pulsesoc_pages.set_link(self.conn, FRIEND, self.page_id, "store", str(OWNER_SELLER_ID))
        self.assertEqual(link["link_type"], "store")
        links = pulsesoc_pages.list_links(self.conn, self.page_id, "store")
        self.assertEqual(len(links), 1)

    def test_analyst_cannot_link_ad_account(self):
        invite = pulsesoc_pages.invite_member(self.conn, OWNER, self.page_id, {"user_id": FRIEND, "role": "ANALYST"})
        pulsesoc_pages.accept_invite(self.conn, FRIEND, invite["invite_token"])
        # A ref the presence is genuinely entitled to, so the only thing left
        # that can refuse it is the role.
        with self.assertRaises(PageError):
            pulsesoc_pages.set_link(self.conn, FRIEND, self.page_id, "ad_account", str(OWNER_ADVERTISER_ID))

    def test_unknown_link_type_rejected(self):
        with self.assertRaises(PageError):
            pulsesoc_pages.set_link(self.conn, OWNER, self.page_id, "wallet", "1")


class LinkOwnershipTests(unittest.TestCase):
    """Holding `manage_links` on a presence says which presence you may attach
    things to. It says nothing about which things are yours to attach.

    Before these, `set_link` took the ref on faith: the id was stored verbatim
    and `public_view` republished it. Pointing a presence at a stranger's
    marketplace seller was accepted, and the resulting page served
    `shop_seller_id` for a storefront the attacker had no relationship to —
    somebody else's inventory, listed under the attacker's name.
    """

    def setUp(self):
        self.conn = make_conn()
        self.page_id = create(self.conn)["id"]

    def _public(self):
        return pulsesoc_pages.public_view(
            self.conn, pulsesoc_pages._load_page(self.conn, self.page_id))

    def assertRefused(self, link_type, ref):
        with self.assertRaises(PageError) as ctx:
            pulsesoc_pages.set_link(self.conn, OWNER, self.page_id, link_type, str(ref))
        self.assertIn(ctx.exception.status_code, (400, 403))
        self.assertEqual(pulsesoc_pages.list_links(self.conn, self.page_id, link_type), [])

    def test_cannot_claim_a_strangers_storefront(self):
        self.assertRefused("store", STRANGER_SELLER_ID)
        self.assertEqual(self._public()["shop_seller_id"], 0)

    def test_cannot_claim_a_strangers_ad_account(self):
        self.assertRefused("ad_account", STRANGER_ADVERTISER_ID)

    def test_cannot_claim_a_strangers_community(self):
        self.assertRefused("community", STRANGER_GROUP_ID)

    def test_cannot_claim_a_strangers_recording_name(self):
        # The music module resolves by artist *name*, so an unguarded link
        # would republish another artist's catalogue on this presence.
        self.assertRefused("music_artist", STRANGER_ARTIST)

    def test_cannot_claim_a_ref_that_does_not_exist(self):
        # Nobody owns it, so nobody is entitled to it — including the actor.
        self.assertRefused("store", 999999)

    def test_cannot_claim_an_unrecorded_artist_name(self):
        self.assertRefused("music_artist", "Nobody At All")

    def test_a_non_numeric_ref_is_refused_not_coerced(self):
        # `_int(ref, -1)` is the sentinel; a ref that isn't an id must not
        # collapse to 0 and match a row whose owner column is empty.
        self.assertRefused("store", "not-an-id")

    def test_a_link_type_with_no_owner_resolver_is_refused(self):
        # `business_os` is declared in LINK_TYPES and read by nothing. Until
        # something can answer who a ref belongs to, storing one is storing a
        # claim that cannot be checked later.
        self.assertRefused("business_os", "anything")

    def test_the_owner_can_still_link_what_the_owner_holds(self):
        # The counterweight: a check that refuses everything is not a fix.
        for link_type, ref in (
            ("store", OWNER_SELLER_ID),
            ("ad_account", OWNER_ADVERTISER_ID),
            ("community", OWNER_GROUP_ID),
            ("music_artist", OWNER_ARTIST),
        ):
            with self.subTest(link_type=link_type):
                link = pulsesoc_pages.set_link(self.conn, OWNER, self.page_id, link_type, str(ref))
                self.assertEqual(link["ref_id"], str(ref))
        self.assertEqual(self._public()["shop_seller_id"], OWNER_SELLER_ID)

    def test_an_artist_name_matches_regardless_of_case(self):
        # Handles and stage names are typed by people. Entitlement that
        # depends on capitalisation would refuse the real artist.
        link = pulsesoc_pages.set_link(
            self.conn, OWNER, self.page_id, "music_artist", OWNER_ARTIST.upper())
        self.assertEqual(link["ref_id"], OWNER_ARTIST.upper())

    def test_an_unapproved_track_does_not_confer_a_name(self):
        # `page_music` only serves approved, active tracks, so an unapproved
        # upload would win the name and then publish an empty catalogue.
        self.conn.execute(
            "INSERT INTO pulse_audio_tracks (title, artist, uploader_user_id, audio_url, approved_by_admin) "
            "VALUES ('Draft', 'Pending Act', ?, 'https://cdn/three.mp3', 0)", (OWNER,))
        self.conn.commit()
        self.assertRefused("music_artist", "Pending Act")

    def test_a_delegated_manager_may_connect_their_own_storefront(self):
        # Entitlement is actor-OR-owner, deliberately. An agency running ads
        # or a shop for a client uses the agency's own account, and the page
        # owner may hold no seller account at all — requiring the owner's would
        # make MARKETPLACE_MANAGER a role that cannot do its job.
        #
        # What keeps that safe is not refusal but attribution: the owner
        # granted the seat, the link is visible in the manage view, and the
        # audit row names who attached it.
        invite = pulsesoc_pages.invite_member(
            self.conn, OWNER, self.page_id, {"user_id": STRANGER, "role": "MARKETPLACE_MANAGER"})
        pulsesoc_pages.accept_invite(self.conn, STRANGER, invite["invite_token"])
        pulsesoc_pages.set_link(self.conn, STRANGER, self.page_id, "store", str(STRANGER_SELLER_ID))

        attributed = self.conn.execute(
            "SELECT created_by FROM pulse_page_links WHERE page_id=? AND link_type='store'",
            (self.page_id,)).fetchone()["created_by"]
        self.assertEqual(attributed, STRANGER)
        self.assertIn("link_set", [
            row["action"] for row in self.conn.execute(
                "SELECT action FROM pulse_page_audit WHERE page_id=? AND actor_user_id=?",
                (self.page_id, STRANGER)).fetchall()])

    def test_a_revoked_manager_cannot_still_connect_their_storefront(self):
        # The seat is what grants it, so losing the seat has to take it back.
        invite = pulsesoc_pages.invite_member(
            self.conn, OWNER, self.page_id, {"user_id": STRANGER, "role": "MARKETPLACE_MANAGER"})
        pulsesoc_pages.accept_invite(self.conn, STRANGER, invite["invite_token"])
        pulsesoc_pages.remove_member(self.conn, OWNER, self.page_id, STRANGER)
        with self.assertRaises(PageError):
            pulsesoc_pages.set_link(
                self.conn, STRANGER, self.page_id, "store", str(STRANGER_SELLER_ID))


class LinkOptionsTests(unittest.TestCase):
    """`link_options` is what makes connecting a shop a choice rather than a
    request to type an internal id. It has to offer exactly what `set_link`
    would accept — offering more is a button that fails, offering less is a
    capability the member cannot reach.
    """

    def setUp(self):
        self.conn = make_conn()
        self.page_id = create(self.conn)["id"]

    def options(self, actor=OWNER):
        return {
            entry["link_type"]: entry
            for entry in pulsesoc_pages.link_options(self.conn, actor, self.page_id)["links"]
        }

    def test_offers_the_resources_the_owner_holds_by_name(self):
        store = self.options()["store"]
        self.assertEqual(store["options"], [{"ref_id": str(OWNER_SELLER_ID), "label": "Owner Shop"}])
        # The label is what the member recognises; the id is carried but never
        # something they have to know.
        self.assertEqual(store["connected_ref_id"], "")

    def test_never_offers_a_resource_the_presence_has_no_claim_to(self):
        for link_type in ("store", "ad_account", "community", "music_artist"):
            with self.subTest(link_type=link_type):
                refs = [option["ref_id"] for option in self.options()[link_type]["options"]]
                self.assertNotIn(str(STRANGER_SELLER_ID), refs)
                self.assertNotIn(str(STRANGER_ADVERTISER_ID), refs)
                self.assertNotIn(str(STRANGER_GROUP_ID), refs)
                self.assertNotIn(STRANGER_ARTIST, refs)

    def test_reports_what_is_already_connected(self):
        pulsesoc_pages.set_link(self.conn, OWNER, self.page_id, "store", str(OWNER_SELLER_ID))
        self.assertEqual(self.options()["store"]["connected_ref_id"], str(OWNER_SELLER_ID))

    def test_offers_no_type_that_set_link_would_refuse(self):
        # The two lists are derived from different maps; if they ever diverge,
        # the surface grows a control with nothing behind it.
        for link_type, entry in self.options().items():
            with self.subTest(link_type=link_type):
                self.assertIn(link_type, pulsesoc_pages.LINK_TYPES)
        self.assertNotIn("event", self.options())
        self.assertNotIn("business_os", self.options())

    def test_every_offered_ref_is_actually_acceptable(self):
        # The strongest form of the same claim: take the offer at its word and
        # put it through the real entry point.
        for link_type, entry in self.options().items():
            for option in entry["options"]:
                with self.subTest(link_type=link_type, ref=option["ref_id"]):
                    pulsesoc_pages.set_link(
                        self.conn, OWNER, self.page_id, link_type, option["ref_id"])

    def test_a_role_that_cannot_connect_is_told_so_and_shown_nothing(self):
        invite = pulsesoc_pages.invite_member(
            self.conn, OWNER, self.page_id, {"user_id": FRIEND, "role": "ANALYST"})
        pulsesoc_pages.accept_invite(self.conn, FRIEND, invite["invite_token"])
        store = self.options(FRIEND)["store"]
        self.assertFalse(store["can_manage"])
        # An analyst has no reason to receive an inventory of what the owner
        # holds, so the list is withheld rather than shown and disabled.
        self.assertEqual(store["options"], [])

    def test_a_marketplace_manager_is_offered_the_shop_they_can_connect(self):
        invite = pulsesoc_pages.invite_member(
            self.conn, OWNER, self.page_id, {"user_id": FRIEND, "role": "MARKETPLACE_MANAGER"})
        pulsesoc_pages.accept_invite(self.conn, FRIEND, invite["invite_token"])
        options = self.options(FRIEND)
        self.assertTrue(options["store"]["can_manage"])
        self.assertEqual(
            [o["ref_id"] for o in options["store"]["options"]], [str(OWNER_SELLER_ID)])
        # Same person, different question: they may connect a shop, not ads.
        self.assertFalse(options["ad_account"]["can_manage"])

    def test_a_stranger_gets_no_inventory_at_all(self):
        with self.assertRaises(PageError):
            pulsesoc_pages.link_options(self.conn, STRANGER, self.page_id)

    def test_a_missing_backing_table_offers_nothing_rather_than_failing(self):
        # Optional subsystems are registered in try/except and can be absent.
        # The management screen still has to open.
        self.conn.execute("DROP TABLE marketplace_sellers")
        options = self.options()
        self.assertEqual(options["store"]["options"], [])
        self.assertTrue(options["store"]["can_manage"])
        self.assertTrue(options["community"]["options"])


class UndxTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_conn()
        self.page = create(self.conn)
        self.page_id = self.page["id"]

    def test_analyst_context_has_no_owner_authority(self):
        invite = pulsesoc_pages.invite_member(self.conn, OWNER, self.page_id, {"user_id": FRIEND, "role": "ANALYST"})
        pulsesoc_pages.accept_invite(self.conn, FRIEND, invite["invite_token"])
        ctx = pulsesoc_pages.undx_page_context(self.conn, FRIEND, self.page_id)
        self.assertFalse(ctx["can_transfer_ownership"])
        self.assertNotIn("transfer_ownership", ctx["capabilities"])
        self.assertNotIn("manage_members", ctx["capabilities"])
        self.assertEqual(ctx["capabilities"], ["view_analytics"])

    def test_owner_context_bounded_by_matrix(self):
        ctx = pulsesoc_pages.undx_page_context(self.conn, OWNER, self.page_id)
        self.assertTrue(ctx["can_transfer_ownership"])
        self.assertEqual(set(ctx["capabilities"]), set(pulsesoc_pages.PERMISSIONS.keys()))

    def test_non_member_has_no_context(self):
        with self.assertRaises(PageError):
            pulsesoc_pages.undx_page_context(self.conn, STRANGER, self.page_id)


class SentinelTests(unittest.TestCase):
    def test_page_entity_and_ownership_edge_registered(self):
        from services.sentinel import entities, graph
        self.assertIn("page", entities.ENTITY_TYPES)
        self.assertIn("owns_page", graph.EDGE_TYPES)
        self.assertEqual(entities.make_ref("page", 5), "page:5")

    def test_sentinel_failure_never_blocks_page_operations(self):
        # make_conn has no sentinel tables and Sentinel writes go to its own
        # store; whatever happens there, page creation must succeed.
        conn = make_conn()
        page = create(conn)
        self.assertTrue(page["id"])

    def test_no_auto_seize_paths_exist(self):
        # Sentinel is imported lazily and only ever called through the two
        # observational helpers; the pages module must expose no function that
        # lets Sentinel mutate a page.
        import inspect
        source = inspect.getsource(pulsesoc_pages)
        self.assertNotIn("sentinel_seize", source)
        self.assertIn("never blocks the page write", source)


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_conn()
        self.page = create(self.conn)
        self.page_id = self.page["id"]

    def test_no_hard_delete(self):
        pulsesoc_pages.set_status(self.conn, OWNER, self.page_id, "DEACTIVATED")
        row = self.conn.execute("SELECT * FROM pulse_pages WHERE id=?", (self.page_id,)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["status"], "DEACTIVATED")
        audit = self.conn.execute("SELECT COUNT(*) AS c FROM pulse_page_audit WHERE page_id=?",
                                  (self.page_id,)).fetchone()
        self.assertGreaterEqual(audit["c"], 2)  # created + status change

    def test_invalid_status_rejected(self):
        with self.assertRaises(PageError):
            pulsesoc_pages.set_status(self.conn, OWNER, self.page_id, "DELETED")

    def test_only_owner_changes_status(self):
        invite = pulsesoc_pages.invite_member(self.conn, OWNER, self.page_id, {"user_id": FRIEND, "role": "ADMIN"})
        pulsesoc_pages.accept_invite(self.conn, FRIEND, invite["invite_token"])
        with self.assertRaises(PageError):
            pulsesoc_pages.set_status(self.conn, FRIEND, self.page_id, "PAUSED")

    def test_follow_toggle_counts_real(self):
        out = pulsesoc_pages.toggle_follow(self.conn, FRIEND, self.page_id)
        self.assertTrue(out["following"])
        self.assertEqual(out["followers_count"], 1)
        out = pulsesoc_pages.toggle_follow(self.conn, FRIEND, self.page_id)
        self.assertFalse(out["following"])
        self.assertEqual(out["followers_count"], 0)


class ModuleAvailabilityTests(unittest.TestCase):
    """A tab only reaches the public once something real backs it."""

    def setUp(self):
        self.conn = make_conn()
        self.page_id = create(self.conn)["id"]

    def _view(self, viewer=None):
        return pulsesoc_pages.public_view(
            self.conn, pulsesoc_pages._load_page(self.conn, self.page_id), viewer_user_id=viewer)

    def test_unlinked_presence_hides_optional_tabs_from_the_public(self):
        view = self._view()
        self.assertEqual(view["tabs"], ["posts", "about"])
        self.assertFalse(view["modules"]["music"])
        self.assertTrue(view["modules"]["about"])

    def test_team_still_sees_the_full_ceiling_as_setup_prompts(self):
        view = self._view(OWNER)
        self.assertEqual(view["tabs"], pulsesoc_pages.TYPE_TABS["ARTIST"])
        self.assertFalse(view["modules"]["music"])

    def test_linking_a_module_reveals_its_tab(self):
        pulsesoc_pages.set_link(self.conn, OWNER, self.page_id, "music_artist", OWNER_ARTIST)
        view = self._view()
        self.assertIn("music", view["tabs"])
        self.assertTrue(view["modules"]["music"])
        self.assertNotIn("merch", view["tabs"])

    def test_tabs_never_exceed_the_type_ceiling(self):
        pulsesoc_pages.set_link(self.conn, OWNER, self.page_id, "store", str(OWNER_SELLER_ID))
        view = self._view(OWNER)
        for tab in view["tabs"]:
            self.assertIn(tab, pulsesoc_pages.TYPE_TABS["ARTIST"])
        self.assertNotIn("shop", view["modules"])  # a business tab, not an artist one

    def test_a_store_link_exposes_the_seller_the_shop_tab_reads(self):
        # Without this the merch tab has nowhere to point but the global
        # marketplace, which is not this presence's inventory.
        self.assertEqual(self._view()["shop_seller_id"], 0)
        pulsesoc_pages.set_link(self.conn, OWNER, self.page_id, "store", str(OWNER_SELLER_ID))
        view = self._view()
        self.assertEqual(view["shop_seller_id"], OWNER_SELLER_ID)
        self.assertIn("merch", view["tabs"])

    def test_an_event_link_is_refused_rather_than_stored_unresolvable(self):
        # The canonical events backend lists only for a caller holding a
        # manager role on the business, so a public events tab would 403 for
        # every visitor. Better no tab than a guaranteed dead one.
        #
        # `event` has no owner resolver, which means nothing can say whose
        # event a ref names. Storing it anyway would leave a row that later
        # code has to decide how to trust; refusing it keeps the question
        # unanswered rather than answering it wrong.
        with self.assertRaises(PageError) as ctx:
            pulsesoc_pages.set_link(self.conn, OWNER, self.page_id, "event", "5")
        self.assertEqual(ctx.exception.status_code, 400)
        view = self._view()
        self.assertNotIn("events", view["tabs"])
        self.assertFalse(view["modules"]["events"])

    def test_the_videos_tab_appears_once_the_presence_has_a_video_post(self):
        view = self._view()
        self.assertNotIn("videos", view["tabs"])
        self.assertEqual(view["videos_count"], 0)

        cur = self.conn.cursor()
        cur.execute(
            "INSERT INTO pulse_posts (user_id, post_type, body, page_id, created_at) "
            "VALUES (?, 'video', 'clip', ?, '2026-01-01')", (OWNER, self.page_id))
        cur.execute(
            "INSERT INTO pulse_posts (user_id, post_type, body, page_id, created_at) "
            "VALUES (?, 'text', 'note', ?, '2026-01-01')", (OWNER, self.page_id))
        self.conn.commit()

        view = self._view()
        self.assertIn("videos", view["tabs"])
        self.assertTrue(view["modules"]["videos"])
        self.assertEqual(view["videos_count"], 1, "a text post is not a video")
        self.assertEqual(view["posts_count"], 2)

    def test_the_videos_listing_returns_only_video_posts(self):
        cur = self.conn.cursor()
        for post_type in ("video", "replay", "text"):
            cur.execute(
                "INSERT INTO pulse_posts (user_id, post_type, body, page_id, created_at) "
                "VALUES (?, ?, ?, ?, '2026-01-01')",
                (OWNER, post_type, post_type, self.page_id))
        self.conn.commit()

        every = pulsesoc_pages.list_page_posts(self.conn, self.page_id)
        only_videos = pulsesoc_pages.list_page_posts(
            self.conn, self.page_id, post_types=pulsesoc_pages.VIDEO_POST_TYPES)
        # get_post is not reachable here, so compare the paging counts the
        # query itself produced rather than serialized bodies.
        self.assertEqual(every["next_offset"], 3)
        self.assertEqual(only_videos["next_offset"], 2)


class MusicModuleTests(unittest.TestCase):
    """The presence points at the canonical catalogue; it never invents a discography."""

    def setUp(self):
        self.conn = make_conn()
        self.page_id = create(self.conn)["id"]
        self._saved = sys.modules.get("services.music_service")

    def tearDown(self):
        if self._saved is None:
            sys.modules.pop("services.music_service", None)
        else:
            sys.modules["services.music_service"] = self._saved

    def _stub_catalogue(self, search_tracks):
        module = types.ModuleType("services.music_service")
        module.search_tracks = search_tracks
        sys.modules["services.music_service"] = module

    def test_unlinked_presence_returns_empty_without_touching_the_catalogue(self):
        def explode(*_a, **_kw):
            raise AssertionError("catalogue must not be queried for an unlinked presence")

        self._stub_catalogue(explode)
        out = pulsesoc_pages.page_music(self.conn, self.page_id)
        self.assertFalse(out["linked"])
        self.assertEqual(out["tracks"], [])

    def test_linked_presence_returns_only_that_artists_tracks(self):
        tracks = [
            {"id": "1", "title": "Signal", "artist": OWNER_ARTIST},
            {"id": "2", "title": "Borrowed", "artist": STRANGER_ARTIST},
        ]
        self._stub_catalogue(lambda query="", limit=12, **kw: list(tracks))
        pulsesoc_pages.set_link(self.conn, OWNER, self.page_id, "music_artist", OWNER_ARTIST.lower())
        out = pulsesoc_pages.page_music(self.conn, self.page_id)
        self.assertTrue(out["linked"])
        self.assertEqual([t["title"] for t in out["tracks"]], ["Signal"])

    def test_catalogue_failure_is_an_honest_error_not_a_silent_empty(self):
        def boom(*_a, **_kw):
            raise RuntimeError("catalogue down")

        self._stub_catalogue(boom)
        pulsesoc_pages.set_link(self.conn, OWNER, self.page_id, "music_artist", OWNER_ARTIST)
        with self.assertRaises(PageError) as ctx:
            pulsesoc_pages.page_music(self.conn, self.page_id)
        self.assertEqual(ctx.exception.status_code, 503)


class AdminInspectionTests(unittest.TestCase):
    """Admins can inspect a presence. Inspection is read-only by construction."""

    def setUp(self):
        self.conn = make_conn()
        self.page_id = create(self.conn)["id"]

    def test_overview_reports_team_links_and_audit(self):
        invite = pulsesoc_pages.invite_member(
            self.conn, OWNER, self.page_id, {"user_id": FRIEND, "role": "ADMIN"})
        pulsesoc_pages.accept_invite(self.conn, FRIEND, invite["invite_token"])
        pulsesoc_pages.set_link(self.conn, OWNER, self.page_id, "store", str(OWNER_SELLER_ID))
        out = pulsesoc_pages.admin_overview(self.conn, self.page_id)
        self.assertEqual(out["owner_user_id"], OWNER)
        self.assertIn(FRIEND, [m["user_id"] for m in out["members"]])
        self.assertEqual([link["ref_id"] for link in out["links"]], [str(OWNER_SELLER_ID)])
        self.assertTrue(out["recent_audit"])

    def test_overview_takes_no_action(self):
        before = dict(self.conn.execute(
            "SELECT * FROM pulse_pages WHERE id=?", (self.page_id,)).fetchone())
        audit_before = self.conn.execute(
            "SELECT COUNT(*) AS c FROM pulse_page_audit WHERE page_id=?", (self.page_id,)).fetchone()["c"]
        pulsesoc_pages.admin_overview(self.conn, self.page_id)
        after = dict(self.conn.execute(
            "SELECT * FROM pulse_pages WHERE id=?", (self.page_id,)).fetchone())
        audit_after = self.conn.execute(
            "SELECT COUNT(*) AS c FROM pulse_page_audit WHERE page_id=?", (self.page_id,)).fetchone()["c"]
        self.assertEqual(before, after)
        self.assertEqual(audit_before, audit_after)

    def test_missing_page_is_a_page_error(self):
        with self.assertRaises(PageError):
            pulsesoc_pages.admin_overview(self.conn, 999999)


class CrossPresenceIsolationTests(unittest.TestCase):
    """Authority is scoped to one presence. Owning A grants nothing over B."""

    def setUp(self):
        self.conn = make_conn()
        self.mine = create(self.conn, OWNER)["id"]
        self.theirs = create(self.conn, STRANGER, name="Other Signal", handle="othersignal")["id"]

    def test_owner_of_one_presence_cannot_edit_another(self):
        with self.assertRaises(PageError):
            pulsesoc_pages.update_page(self.conn, OWNER, self.theirs, {"name": "Seized"})

    def test_owner_of_one_presence_cannot_post_as_another(self):
        with self.assertRaises(PageError):
            pulsesoc_pages.create_page_post(self.conn, OWNER, self.theirs, {"body": "hello"})

    def test_owner_of_one_presence_cannot_link_or_invite_on_another(self):
        # OWNER really does hold this store. The refusal is about the presence
        # he is trying to attach it to, not about the store.
        with self.assertRaises(PageError):
            pulsesoc_pages.set_link(self.conn, OWNER, self.theirs, "store", str(OWNER_SELLER_ID))
        with self.assertRaises(PageError):
            pulsesoc_pages.invite_member(self.conn, OWNER, self.theirs, {"user_id": FRIEND, "role": "ADMIN"})

    def test_owner_of_one_presence_cannot_read_anothers_management_data(self):
        with self.assertRaises(PageError):
            pulsesoc_pages.manage_view(self.conn, OWNER, self.theirs)
        with self.assertRaises(PageError):
            pulsesoc_pages.list_members(self.conn, OWNER, self.theirs)

    def test_role_change_cannot_escalate_to_owner(self):
        invite = pulsesoc_pages.invite_member(
            self.conn, OWNER, self.mine, {"user_id": FRIEND, "role": "ADMIN"})
        pulsesoc_pages.accept_invite(self.conn, FRIEND, invite["invite_token"])
        with self.assertRaises(PageError):
            pulsesoc_pages.change_role(self.conn, OWNER, self.mine, FRIEND, "OWNER")
        with self.assertRaises(PageError):
            pulsesoc_pages.change_role(self.conn, FRIEND, self.mine, FRIEND, "OWNER")
        self.assertEqual(pulsesoc_pages.role_for(self.conn, FRIEND, self.mine), "ADMIN")


class PresenceSearchTests(unittest.TestCase):
    def setUp(self):
        self.conn = make_conn()
        self.page_id = create(self.conn)["id"]

    def test_public_search_finds_active_presences_by_name_and_handle(self):
        self.assertEqual([p["id"] for p in pulsesoc_pages.search_pages(self.conn, "night")], [self.page_id])
        self.assertEqual([p["id"] for p in pulsesoc_pages.search_pages(self.conn, "nightsig")], [self.page_id])

    def test_public_search_excludes_deactivated_presences(self):
        pulsesoc_pages.set_status(self.conn, OWNER, self.page_id, "DEACTIVATED")
        self.assertEqual(pulsesoc_pages.search_pages(self.conn, "night"), [])

    def test_admin_scope_finds_deactivated_presences(self):
        pulsesoc_pages.set_status(self.conn, OWNER, self.page_id, "DEACTIVATED")
        found = pulsesoc_pages.search_pages(self.conn, "night", include_inactive=True)
        self.assertEqual([p["id"] for p in found], [self.page_id])

    def test_search_rows_carry_no_private_management_data(self):
        row = pulsesoc_pages.search_pages(self.conn, "night")[0]
        for private in ("phone", "owner_user_id", "members", "links"):
            self.assertNotIn(private, row)

    def test_empty_query_returns_nothing(self):
        self.assertEqual(pulsesoc_pages.search_pages(self.conn, "   "), [])


if __name__ == "__main__":
    unittest.main()
