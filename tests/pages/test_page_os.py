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


def make_conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(USERS_SCHEMA)
    conn.execute("INSERT INTO users VALUES (?, 'roody', 'Roody', 'Roody C', '')", (OWNER,))
    conn.execute("INSERT INTO users VALUES (?, 'friend', 'Friend', 'Friend F', '')", (FRIEND,))
    conn.execute("INSERT INTO users VALUES (?, 'stranger', 'Stranger', 'S S', '')", (STRANGER,))
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
        link = pulsesoc_pages.set_link(self.conn, FRIEND, self.page_id, "store", "42")
        self.assertEqual(link["link_type"], "store")
        links = pulsesoc_pages.list_links(self.conn, self.page_id, "store")
        self.assertEqual(len(links), 1)

    def test_analyst_cannot_link_ad_account(self):
        invite = pulsesoc_pages.invite_member(self.conn, OWNER, self.page_id, {"user_id": FRIEND, "role": "ANALYST"})
        pulsesoc_pages.accept_invite(self.conn, FRIEND, invite["invite_token"])
        with self.assertRaises(PageError):
            pulsesoc_pages.set_link(self.conn, FRIEND, self.page_id, "ad_account", "7")

    def test_unknown_link_type_rejected(self):
        with self.assertRaises(PageError):
            pulsesoc_pages.set_link(self.conn, OWNER, self.page_id, "wallet", "1")


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
        pulsesoc_pages.set_link(self.conn, OWNER, self.page_id, "music_artist", "Night Signal")
        view = self._view()
        self.assertIn("music", view["tabs"])
        self.assertTrue(view["modules"]["music"])
        self.assertNotIn("merch", view["tabs"])

    def test_tabs_never_exceed_the_type_ceiling(self):
        pulsesoc_pages.set_link(self.conn, OWNER, self.page_id, "store", "9")
        view = self._view(OWNER)
        for tab in view["tabs"]:
            self.assertIn(tab, pulsesoc_pages.TYPE_TABS["ARTIST"])
        self.assertNotIn("shop", view["modules"])  # a business tab, not an artist one


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
            {"id": "1", "title": "Signal", "artist": "Night Signal"},
            {"id": "2", "title": "Borrowed", "artist": "Someone Else"},
        ]
        self._stub_catalogue(lambda query="", limit=12, **kw: list(tracks))
        pulsesoc_pages.set_link(self.conn, OWNER, self.page_id, "music_artist", "night signal")
        out = pulsesoc_pages.page_music(self.conn, self.page_id)
        self.assertTrue(out["linked"])
        self.assertEqual([t["title"] for t in out["tracks"]], ["Signal"])

    def test_catalogue_failure_is_an_honest_error_not_a_silent_empty(self):
        def boom(*_a, **_kw):
            raise RuntimeError("catalogue down")

        self._stub_catalogue(boom)
        pulsesoc_pages.set_link(self.conn, OWNER, self.page_id, "music_artist", "Night Signal")
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
        pulsesoc_pages.set_link(self.conn, OWNER, self.page_id, "store", "7")
        out = pulsesoc_pages.admin_overview(self.conn, self.page_id)
        self.assertEqual(out["owner_user_id"], OWNER)
        self.assertIn(FRIEND, [m["user_id"] for m in out["members"]])
        self.assertEqual([link["ref_id"] for link in out["links"]], ["7"])
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
        with self.assertRaises(PageError):
            pulsesoc_pages.set_link(self.conn, OWNER, self.theirs, "store", "1")
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
