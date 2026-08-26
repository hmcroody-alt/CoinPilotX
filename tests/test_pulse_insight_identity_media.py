"""The PulseSoc Insight account's picture, and why it used to be missing.

PulseSoc Insight is an automated account with no human behind it, so nobody can
open the app and upload a profile picture for it. Its avatar is therefore a
committed brand asset referenced by a constant, and that constant is the whole
of the account's visual identity: feed cards, the profile, comments, search and
notifications all read it through ``_public_author``.

Two things had gone wrong with that arrangement.

The first was invisible on the website and total on the phone. The constant held
a *site-relative* path, ``/static/brand/...``. A browser resolves that against
the current origin and renders the picture, so the defect never showed up in web
QA. React Native's ``Image`` has no origin to resolve against; it just fails, and
the account rendered as an empty circle on every feed card in the native app.

The second was a latch. ``_public_author`` only substituted the brand constant
when the stored avatar was *blank*, and the profile row is seeded with whatever
the constant said at seed time. So once a row existed, changing the artwork
changed nothing: the stale row kept winning, forever.

These tests pin both, plus the properties that make the asset render correctly
inside a circular avatar rather than merely load.
"""

import sqlite3
import struct
from pathlib import Path

import pytest

from services import pulse_feed_engine


ROOT = Path(__file__).resolve().parents[1]


def repo_asset(path: str) -> Path:
    return ROOT / path.lstrip("/")


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n"), f"{path} is not a PNG"
    return struct.unpack(">II", data[16:24])


@pytest.fixture
def profiles_db(tmp_path, monkeypatch):
    """A throwaway ``arena_profiles`` table standing in for the real one.

    On a file rather than ``:memory:`` deliberately: the seeder closes the
    connection it was handed, which would take an in-memory database with it and
    leave nothing to assert against -- the test would then pass for the wrong
    reason on a seeder that wrote nothing at all.
    """
    path = tmp_path / "profiles.db"

    def open_db():
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        return conn

    setup = open_db()
    setup.execute(
        """
        CREATE TABLE arena_profiles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            username TEXT,
            public_player_id TEXT,
            display_name TEXT,
            avatar_url TEXT,
            rank TEXT,
            created_at TEXT,
            updated_at TEXT
        )
        """
    )
    setup.commit()
    setup.close()

    monkeypatch.setattr(pulse_feed_engine.user_context, "connect", open_db)
    # The seeder is a once-per-process no-op after its first success, so an
    # earlier test in the same session would otherwise make this one vacuous.
    monkeypatch.setattr(pulse_feed_engine, "_MEMBER_000_PROFILE_READY", False)
    return open_db


def seed_row(open_db, **columns):
    conn = open_db()
    keys = ", ".join(columns)
    marks = ", ".join("?" for _ in columns)
    conn.execute(f"INSERT INTO arena_profiles ({keys}) VALUES ({marks})", tuple(columns.values()))
    conn.commit()
    conn.close()


def stored_profile(open_db):
    conn = open_db()
    rows = [dict(r) for r in conn.execute("SELECT * FROM arena_profiles").fetchall()]
    conn.close()
    # Exactly one: the seeder must update the account in place. Two rows would
    # mean it had created a duplicate of the system account, which the mission
    # forbids and which no assertion on a single row's contents would catch.
    assert len(rows) == 1, f"expected exactly one system profile row, got {len(rows)}"
    return rows[0]


class TestTheAssetsAreReal:
    """A media reference that does not resolve is the same bug in a new place."""

    def test_the_avatar_is_committed(self):
        assert repo_asset(pulse_feed_engine.MEMBER_000_AVATAR_PATH).is_file()

    def test_the_cover_is_committed(self):
        assert repo_asset(pulse_feed_engine.MEMBER_000_COVER_PATH).is_file()

    def test_the_avatar_is_square(self):
        """A non-square source cannot fill a circular clip without stretching.

        The avatar is rendered with the default ``cover`` resize into a square
        box with ``borderRadius`` half its width. Feed a 4:3 source into that and
        the artwork is cropped off-centre; feed a square one and the clip lands
        exactly on the edge of the disc.
        """
        width, height = png_size(repo_asset(pulse_feed_engine.MEMBER_000_AVATAR_PATH))
        assert width == height

    def test_the_avatar_outresolves_a_retina_profile_render(self):
        """112pt at @3x is 336px; the fullscreen preview asks for more again."""
        width, _ = png_size(repo_asset(pulse_feed_engine.MEMBER_000_AVATAR_PATH))
        assert width >= 1024

    def test_the_cover_is_a_landscape_banner(self):
        """The profile hero crops a wide image; a tall one would be gutted."""
        width, height = png_size(repo_asset(pulse_feed_engine.MEMBER_000_COVER_PATH))
        assert width > height


class TestTheClientGetsALoadableUrl:
    """The empty-circle bug, stated as a property."""

    def test_the_avatar_url_is_absolute(self):
        assert pulse_feed_engine.MEMBER_000_AVATAR_URL.startswith("https://")

    def test_the_cover_url_is_absolute(self):
        assert pulse_feed_engine.MEMBER_000_COVER_URL.startswith("https://")

    def test_the_absolute_url_still_points_at_the_committed_asset(self):
        """Absolute must not mean *detached*. If someone edits the base URL or
        the filename in isolation, the pair stops agreeing and the account 404s
        rather than showing the wrong picture -- which is harder to notice."""
        assert pulse_feed_engine.MEMBER_000_AVATAR_URL.endswith(pulse_feed_engine.MEMBER_000_AVATAR_PATH)
        assert pulse_feed_engine.MEMBER_000_COVER_URL.endswith(pulse_feed_engine.MEMBER_000_COVER_PATH)

    def test_the_author_payload_carries_the_absolute_url(self, monkeypatch):
        monkeypatch.setattr(pulse_feed_engine, "_ensure_member_000_profile", lambda: None)
        author = pulse_feed_engine._public_author({"user_id": 0})
        assert author["avatar_url"] == pulse_feed_engine.MEMBER_000_AVATAR_URL

    @pytest.mark.parametrize("env", ["PULSE_APP_URL", "APP_BASE_URL", "BASE_URL", "DOMAIN"])
    def test_a_self_hosted_deployment_gets_its_own_origin(self, monkeypatch, env):
        for name in ("PULSE_APP_URL", "APP_BASE_URL", "BASE_URL", "DOMAIN"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv(env, "https://staging.example.com/")
        assert pulse_feed_engine._brand_media_url("/static/brand/x.png") == "https://staging.example.com/static/brand/x.png"

    def test_an_already_absolute_reference_is_left_alone(self):
        """So the constant can be repointed at a CDN object without this helper
        mangling it into ``https://pulsesoc.com/https://cdn...``."""
        cdn = "https://cdn.example.com/brand/x.png"
        assert pulse_feed_engine._brand_media_url(cdn) == cdn


class TestASupersededAvatarDoesNotLatch:
    """The reason changing the artwork used to change nothing."""

    def test_a_blank_stored_avatar_is_replaceable(self):
        assert pulse_feed_engine.is_member_000_brand_avatar("") is True

    def test_the_previous_brand_asset_is_replaceable(self):
        for legacy in pulse_feed_engine.MEMBER_000_LEGACY_AVATAR_PATHS:
            assert pulse_feed_engine.is_member_000_brand_avatar(legacy) is True
            assert pulse_feed_engine.is_member_000_brand_avatar(f"https://pulsesoc.com{legacy}") is True

    def test_the_current_brand_asset_is_replaceable(self):
        """Idempotence: re-seeding must not be a no-op merely because the stored
        value already matches, or the next artwork change latches again."""
        assert pulse_feed_engine.is_member_000_brand_avatar(pulse_feed_engine.MEMBER_000_AVATAR_URL) is True

    def test_a_deliberate_override_is_left_alone(self):
        """The check stays narrow on purpose. An operator who points the profile
        at something outside ``static/brand`` has made a decision, and a seeding
        pass on the next boot should not quietly undo it."""
        assert pulse_feed_engine.is_member_000_brand_avatar("https://cdn.example.com/custom.png") is False

    def test_a_stored_legacy_path_is_upgraded_in_the_author_payload(self, monkeypatch):
        """Feed rows join the profile table, so this is the path that actually
        decided what every feed card showed."""
        monkeypatch.setattr(pulse_feed_engine, "_ensure_member_000_profile", lambda: None)
        author = pulse_feed_engine._public_author(
            {"user_id": 0, "arena_avatar_url": pulse_feed_engine.MEMBER_000_LEGACY_AVATAR_PATHS[0]}
        )
        assert author["avatar_url"] == pulse_feed_engine.MEMBER_000_AVATAR_URL

    def test_a_deliberate_override_survives_the_author_payload(self, monkeypatch):
        monkeypatch.setattr(pulse_feed_engine, "_ensure_member_000_profile", lambda: None)
        author = pulse_feed_engine._public_author({"user_id": 0, "arena_avatar_url": "https://cdn.example.com/custom.png"})
        assert author["avatar_url"] == "https://cdn.example.com/custom.png"


class TestTheProfileRowActuallyHoldsTheAvatar:
    """The stored row, not just the payload.

    ``_public_author`` substitutes the constant on the way out, so every test
    above would still pass on a build that never wrote anything to the database.
    That would be a facade: the row is what the web renderer, the profile join
    and any future consumer read, and it is where the artwork has to land.
    """

    def test_a_fresh_deployment_seeds_the_avatar(self, profiles_db):
        pulse_feed_engine._ensure_member_000_profile()
        assert stored_profile(profiles_db)["avatar_url"] == pulse_feed_engine.MEMBER_000_AVATAR_URL

    def test_a_row_holding_the_previous_artwork_is_upgraded_in_place(self, profiles_db):
        """The latch, at the layer where it actually latched."""
        seed_row(
            profiles_db,
            user_id=0,
            public_player_id=pulse_feed_engine.MEMBER_000_PUBLIC_PLAYER_ID,
            display_name=pulse_feed_engine.MEMBER_000_DISPLAY_NAME,
            avatar_url=pulse_feed_engine.MEMBER_000_LEGACY_AVATAR_PATHS[0],
        )
        pulse_feed_engine._ensure_member_000_profile()
        assert stored_profile(profiles_db)["avatar_url"] == pulse_feed_engine.MEMBER_000_AVATAR_URL

    def test_a_row_under_the_old_public_id_is_adopted_rather_than_duplicated(self, profiles_db):
        """Installs seeded before the handle changed are found by the legacy id.

        Miss this and the seeder inserts a *second* system profile beside the
        first, which is the duplicate-account failure the mission calls out.
        """
        seed_row(
            profiles_db,
            user_id=0,
            public_player_id=pulse_feed_engine.MEMBER_000_LEGACY_PUBLIC_PLAYER_ID,
            display_name="PulseSoc Member #000",
            avatar_url="",
        )
        pulse_feed_engine._ensure_member_000_profile()
        row = stored_profile(profiles_db)
        assert row["public_player_id"] == pulse_feed_engine.MEMBER_000_PUBLIC_PLAYER_ID
        assert row["display_name"] == pulse_feed_engine.MEMBER_000_DISPLAY_NAME
        assert row["avatar_url"] == pulse_feed_engine.MEMBER_000_AVATAR_URL

    def test_a_deliberate_override_is_not_overwritten_on_the_next_boot(self, profiles_db):
        seed_row(
            profiles_db,
            user_id=0,
            public_player_id=pulse_feed_engine.MEMBER_000_PUBLIC_PLAYER_ID,
            display_name=pulse_feed_engine.MEMBER_000_DISPLAY_NAME,
            avatar_url="https://cdn.example.com/custom.png",
        )
        pulse_feed_engine._ensure_member_000_profile()
        assert stored_profile(profiles_db)["avatar_url"] == "https://cdn.example.com/custom.png"

    def test_the_stored_url_is_the_one_the_client_is_told_to_load(self, profiles_db):
        """Row and payload agree, so the web page and the app show one picture."""
        pulse_feed_engine._ensure_member_000_profile()
        row = stored_profile(profiles_db)
        author = pulse_feed_engine._public_author({"user_id": 0, "arena_avatar_url": row["avatar_url"]})
        assert author["avatar_url"] == row["avatar_url"]
        assert repo_asset(pulse_feed_engine.MEMBER_000_AVATAR_PATH).is_file()


class TestTheAccountIsUntouchedOtherwise:
    """This mission changed a picture. It must not have changed an identity."""

    def test_the_handle_display_name_and_automated_designation_are_unchanged(self, monkeypatch):
        monkeypatch.setattr(pulse_feed_engine, "_ensure_member_000_profile", lambda: None)
        author = pulse_feed_engine._public_author({"user_id": 0})
        assert author["username"] == "pulsesoc_insight"
        assert author["display_name"] == "PulseSoc Insight"
        assert author["account_type"] == "PULSESOC_AUTOMATED"
        assert author["automated"] is True
        assert author["official_system_account"] is True
        assert author["primary_label"] == "Official PulseSoc System Account"

    def test_a_picture_is_not_a_verification(self, monkeypatch):
        """Giving the account artwork must not light the verified badge."""
        monkeypatch.setattr(pulse_feed_engine, "_ensure_member_000_profile", lambda: None)
        author = pulse_feed_engine._public_author({"user_id": 0})
        assert author["premium_verified"] is False

    def test_ordinary_accounts_are_not_given_the_brand_avatar(self, monkeypatch):
        """The substitution is keyed on the system account, not on emptiness. A
        member with no picture keeps having no picture and gets the app's own
        initial fallback."""
        monkeypatch.setattr(pulse_feed_engine, "_ensure_member_000_profile", lambda: None)
        author = pulse_feed_engine._public_author({"user_id": 4242, "username": "ada", "display_name": "Ada"})
        assert author["avatar_url"] == ""
        assert author["automated"] is False
