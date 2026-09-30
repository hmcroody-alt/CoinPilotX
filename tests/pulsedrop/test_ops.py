"""The operator surface: the settings write path, the dashboard, and the page.

What this suite is defending
----------------------------
PulseDrop publishes on its own schedule with nobody watching, so the admin page
is not a convenience — it is the only way to stop it, and the only way to tell
the difference between "it decided not to publish", "it has nothing to publish"
and "it is not running at all". Three properties follow, and they are what the
classes below are organised around.

**A stored setting and a running setting cannot disagree.** The bounds used to
live in each accessor's body. Once a form could write these rows, a form
validating against one copy while the accessor clamped against another would
accept 500, store 500, display 500 and run 200, with nothing anywhere reporting
the disagreement. :class:`TestAStoredValueIsTheRunningValue` asserts the
equality directly, over every setting in the registry, so a future accessor that
grows its own private floor fails here.

**Clearing is not the same as writing the default.** An operator who clears a
row is saying "whatever the deployment says", and a later deploy that changes
the environment must move the value. Writing the default instead pins it
forever, silently, and looks identical on the page.

**Nothing on the page can stop the page loading.** An ops page that 500s when
PulseDrop's own storage is the broken thing is an ops page nobody can use to
switch PulseDrop off. Every reader degrades to an empty section, and
:class:`TestNothingHereCanStopAPageLoad` proves it by breaking the database
underneath a live dashboard call rather than by trusting the docstring.

Why the page tests import the monolith
--------------------------------------
``/admin/pulsedrop`` is a route on ``bot``, and the half of it worth testing is
the half that is not in ``ops``: the permission split between reading the page
and changing what the curator publishes. That needs a request, so it needs the
app, so it needs the ``monolith`` fixture — see this package's conftest for why
that import cannot happen at module scope.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from services.pulsedrop import config, ops

#: Far enough in the past that it is stale under any threshold in the registry.
OLD = "2020-01-01T00:00:00"


@pytest.fixture(autouse=True)
def _no_leftover_settings():
    """Every test starts and ends with an empty overrides table.

    Not optional housekeeping. ``config`` memoises the rows in a process global
    and every other suite in this package reads the same globals, so a single
    leaked ``PULSEDROP_ENABLED=false`` would turn the curator suite's ticks into
    no-ops with no failure pointing back here.
    """
    _wipe_settings()
    config.invalidate_cache()
    yield
    _wipe_settings()
    config.invalidate_cache()


def _wipe_settings() -> None:
    from services import db as platform_db

    conn = platform_db.connect()
    try:
        conn.execute("DELETE FROM pulsedrop_settings")
        conn.commit()
    finally:
        conn.close()


def _lease_row(**columns) -> None:
    """Put the curator lease into a known state."""
    from services import db as platform_db
    from services.pulsedrop import lease

    conn = platform_db.connect()
    try:
        conn.execute("DELETE FROM pulsedrop_leases WHERE lease_key=?", (lease.CURATOR,))
        conn.execute(
            "INSERT INTO pulsedrop_leases (lease_key, owner, acquired_at, expires_at, next_run_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                lease.CURATOR,
                columns.get("owner", ""),
                columns.get("acquired_at"),
                columns.get("expires_at"),
                columns.get("next_run_at"),
                OLD,
            ),
        )
        conn.commit()
    finally:
        conn.close()


class TestAStoredValueIsTheRunningValue:
    """The one invariant the whole write path exists to hold."""

    def test_every_setting_reports_back_exactly_what_it_stored(self):
        # Deliberately over the whole registry rather than over a chosen few.
        # The failure being prevented is a *new* accessor keeping its own bounds,
        # and a hand-picked sample is exactly what such a change would not be in.
        for key, spec in config.SETTINGS.items():
            stored = config.set_override(key, "1" if spec.kind == config.FLAG else 999_999)
            running = config.resolve(key)
            expected = True if spec.kind == config.FLAG else int(stored)
            assert running == expected, f"{key} stored {stored!r} and runs as {running!r}"

    def test_a_value_over_the_ceiling_is_stored_at_the_ceiling(self):
        # Clamped before the write, not at read time. A row reading 500 next to a
        # curator running 200 is the disagreement this whole design avoids, and it
        # is invisible on a page that renders the row.
        assert config.set_override("PULSEDROP_CANDIDATE_LIMIT", 999_999) == "2000"
        assert config.overrides()["PULSEDROP_CANDIDATE_LIMIT"] == "2000"
        assert config.candidate_limit() == 2000

    def test_a_value_under_the_floor_is_stored_at_the_floor(self):
        assert config.set_override("PULSEDROP_EVALUATION_INTERVAL_SECONDS", 5) == "60"
        assert config.evaluation_interval_seconds() == 60

    @pytest.mark.parametrize("text", ["", "   ", "soon", "2 hours", None])
    def test_a_number_field_refuses_text(self, text):
        # Refused rather than coerced to the default: an operator who typed
        # "2 hours" wants to be told, not to have the field silently reset to a
        # number they did not choose and will not notice.
        with pytest.raises(ValueError):
            config.coerce("PULSEDROP_EVALUATION_INTERVAL_SECONDS", text)

    def test_an_unknown_key_is_refused_rather_than_stored(self):
        # The admin form posts arbitrary strings and a background worker reads
        # this table. Junk stored here looks like configuration to the next
        # person who opens it.
        with pytest.raises(KeyError):
            config.coerce("PULSEDROP_MAKE_ME_RICH", "true")
        with pytest.raises(KeyError):
            config.set_override("PULSEDROP_MAKE_ME_RICH", "true")

    @pytest.mark.parametrize("text,expected", [
        ("1", True), ("true", True), ("TRUE", True), ("yes", True), ("on", True),
        ("0", False), ("false", False), ("off", False), ("", False), ("maybe", False),
    ])
    def test_a_flag_stores_a_canonical_string(self, text, expected):
        # Normalised on the way in so the table only ever holds "true" or
        # "false", whatever the form posted.
        assert config.coerce("PULSEDROP_ENABLED", text) == ("true" if expected else "false")


class TestWhereAValueCameFrom:
    def test_an_unset_setting_reports_the_environment(self, monkeypatch):
        monkeypatch.setenv("PULSEDROP_ENABLED", "true")
        item = _setting("PULSEDROP_ENABLED")
        assert item["value"] is True
        assert item["source"] == "environment"
        assert item["overridden"] is False

    def test_an_override_beats_the_environment_and_says_so(self, monkeypatch):
        # The distinction the page is for. "PulseDrop enabled: off" is two
        # different incidents depending on which of these produced it: someone
        # hit the kill switch, or the deployment never set the variable. They
        # call for opposite actions.
        monkeypatch.setenv("PULSEDROP_ENABLED", "true")
        config.set_override("PULSEDROP_ENABLED", "false")
        item = _setting("PULSEDROP_ENABLED")
        assert item["value"] is False
        assert item["source"] == "override"
        assert item["display"] == "off"

    def test_clearing_returns_the_setting_to_the_environment(self, monkeypatch):
        # Specifically not "writes the default". If clearing pinned the current
        # value, a later deploy that changed the variable would have no effect
        # and the page would look identical either way.
        monkeypatch.setenv("PULSEDROP_DAILY_PUBLICATION_CAP", "17")
        config.set_override("PULSEDROP_DAILY_PUBLICATION_CAP", 3)
        assert config.daily_publication_cap() == 3

        config.clear_override("PULSEDROP_DAILY_PUBLICATION_CAP")
        assert config.daily_publication_cap() == 17
        assert "PULSEDROP_DAILY_PUBLICATION_CAP" not in config.overrides()

        monkeypatch.setenv("PULSEDROP_DAILY_PUBLICATION_CAP", "4")
        assert config.daily_publication_cap() == 4

    def test_a_write_is_visible_immediately(self):
        # The read path memoises for 20 seconds, which is right for a worker
        # ticking in a loop and wrong for an operator who just pressed a button
        # and is looking at the result of their own write.
        config.set_override("PULSEDROP_REELS_ENABLED", "false")
        assert config.overrides()["PULSEDROP_REELS_ENABLED"] == "false"
        assert config.reels_enabled() is False

    def test_every_setting_is_presentable(self):
        # A setting with no label or group renders as a bare environment
        # variable name under "Other", which is how a tunable ends up on the
        # page that nobody can safely touch.
        for item in ops.settings_view():
            assert item["label"] and item["label"] != item["key"], item["key"]
            assert item["group"] and item["group"] != "Other", item["key"]
            assert item["help"], item["key"]

    def test_the_kill_switches_come_first(self):
        # Ordering is declaration order, and the first thing an operator needs
        # in an incident is the off switch.
        heading, items = ops.groups(ops.settings_view())[0]
        assert heading == "Kill switches"
        assert [item["key"] for item in items] == [
            "PULSEDROP_ENABLED",
            "PULSEDROP_SIGNALS_ENABLED",
            "PULSEDROP_REELS_ENABLED",
        ]


class TestTheHealthSummary:
    """The few facts that go above the fold, and why they are separate facts."""

    def test_a_recent_run_with_an_old_publication_is_not_a_failure(self):
        from services.pulsedrop import curator

        now = datetime(2026, 9, 27, 12, 0, 0)
        run_log = [
            {"outcome": curator.NONE_ELIGIBLE, "started_at": _iso(now - timedelta(minutes=2))},
            {"outcome": curator.PUBLISHED, "started_at": OLD, "finished_at": OLD},
        ]
        health = ops.health(run_log, [], now=now)
        # Reported side by side on purpose: most ticks are supposed to publish
        # nothing, so the gap between these two is the diagnosis rather than an
        # alarm. Collapsing them into one "last activity" number would make a
        # healthy quiet spell indistinguishable from a dead worker.
        assert health["last_run_outcome"] == curator.NONE_ELIGIBLE
        assert health["last_publication_at"] == OLD
        assert health["failures_shown"] == 0

    def test_failures_are_counted_from_both_outcomes_that_mean_failure(self):
        from services.pulsedrop import curator

        run_log = [{"outcome": curator.FAILED}, {"outcome": curator.ERROR}, {"outcome": curator.SKIPPED}]
        assert ops.health(run_log, [])["failures_shown"] == 2

    def test_a_render_stuck_in_progress_is_counted(self):
        now = datetime(2026, 9, 27, 12, 0, 0)
        render_log = [
            {"state": "rendering", "updated_at": _iso(now - timedelta(minutes=ops.STALL_MINUTES + 1))},
            {"state": "pending", "updated_at": _iso(now - timedelta(minutes=1))},
            # Finished is finished, however long ago. A READY row that has sat
            # for a week is a published Reel, not a casualty.
            {"state": "ready", "updated_at": OLD},
        ]
        assert ops.health([], render_log, now=now)["renders_stuck"] == 1

    def test_an_unreadable_timestamp_is_treated_as_brand_new(self):
        # Deliberately not "infinitely old". These stamps are written by two
        # engines in two formats, and a parse failure that reported every render
        # as stalled would put a permanent red count on the page that no action
        # clears — which is worse than under-reporting, because it trains the
        # operator to ignore the number.
        render_log = [{"state": "rendering", "updated_at": "not a date"}]
        assert ops.health([], render_log)["renders_stuck"] == 0


class TestTheSchedule:
    """Is a tick happening, is one due, or is nobody listening."""

    def test_a_held_lease_is_reported_as_a_tick_in_progress(self):
        now = datetime(2026, 9, 27, 12, 0, 0)
        _lease_row(owner="host:42:abc", expires_at=_iso(now + timedelta(minutes=4)),
                   next_run_at=_iso(now + timedelta(hours=2)))
        view = ops.schedule_view(now=now)
        assert view["held"] is True
        assert view["held_by"] == "host:42:abc"

    def test_an_expired_lease_is_not_held_however_the_owner_column_reads(self):
        # Expiry, not release, is the recovery path after a hard crash, so a
        # stale owner name is the *expected* residue of one. Reporting it as
        # "a tick is running" would mean an operator waiting forever for a tick
        # that died an hour ago.
        now = datetime(2026, 9, 27, 12, 0, 0)
        _lease_row(owner="host:42:abc", expires_at=_iso(now - timedelta(minutes=1)),
                   next_run_at=_iso(now - timedelta(minutes=1)))
        view = ops.schedule_view(now=now)
        assert view["held"] is False
        assert view["held_by"] == ""
        assert view["due"] is True
        assert view["due_in"] == "due now"

    def test_a_future_run_is_reported_as_a_rough_wait(self):
        now = datetime(2026, 9, 27, 12, 0, 0)
        _lease_row(next_run_at=_iso(now + timedelta(minutes=90)))
        assert ops.schedule_view(now=now)["due_in"] == "in ~90 min"

    def test_run_now_makes_the_curator_due_without_publishing_anything(self):
        # The point of the control. It clears the interval gate and stops; the
        # worker owns publication, because the worker is the path that holds the
        # lease, drains renders and has been exercised. A web request that
        # published would be a second, less-tested publisher whose worst case is
        # a video encode inside an HTTP timeout.
        #
        # ``now`` is the real clock here, unlike the fixed moment the rest of this
        # class uses: ``apply_action`` accepts no clock, so ``run_now`` writes
        # ``utcnow()``. Measured against a hardcoded past date that write lands in
        # the future and "due" reads False — a test that passes only until the
        # date it names, which is how this one failed.
        now = datetime.utcnow()
        _lease_row(next_run_at=_iso(now + timedelta(hours=2)))
        assert ops.schedule_view(now=now)["due"] is False

        changed, message = ops.apply_action("run_now", "", None, admin_user_id=7)
        assert changed is True
        assert "next worker cycle" in message
        assert ops.schedule_view(now=now)["due"] is True
        # And it did not take the lease on its way past: a tick that ran here
        # would leave an owner behind.
        assert ops.schedule_view(now=now)["held"] is False


class TestWhatAnOperatorCanDo:
    def test_an_unknown_action_is_refused(self):
        changed, message = ops.apply_action("drop_table", "PULSEDROP_ENABLED", "x")
        assert changed is False
        assert message == "Unknown action."
        assert config.overrides() == {}

    def test_an_unknown_key_is_refused(self):
        changed, message = ops.apply_action("set", "DATABASE_URL", "postgres://elsewhere")
        assert changed is False
        assert message == "Unknown setting."
        assert config.overrides() == {}

    def test_a_refused_write_reports_what_was_wrong_with_it(self):
        changed, message = ops.apply_action("set", "PULSEDROP_DAILY_REEL_CAP", "lots")
        assert changed is False
        assert "PULSEDROP_DAILY_REEL_CAP" in message
        assert config.overrides() == {}

    def test_an_accepted_write_reports_the_value_that_was_actually_stored(self):
        # Says 100, not 999999. The clamp is silent everywhere else, so this
        # message is the only place an operator finds out their number was
        # outside the range.
        changed, message = ops.apply_action("set", "PULSEDROP_MAX_SELLER_SHARE_PERCENT", "999999")
        assert changed is True
        assert "100" in message
        assert config.max_per_seller_share_percent() == 100


class TestNothingHereCanStopAPageLoad:
    """Every reader degrades to an empty section."""

    @pytest.fixture()
    def broken_database(self, monkeypatch):
        from services import db as platform_db
        from services.pulsedrop import account, schema

        def explode(*_args, **_kwargs):
            raise RuntimeError("database is gone")

        # The ready flag has to go too, or ``ensure_schema`` short-circuits and
        # the test proves nothing about the first call after a restart — which
        # is the one that matters, because that is when a deployment with
        # unwritable storage first renders this page.
        monkeypatch.setattr(schema, "_ready", False)
        monkeypatch.setattr(account, "_cached_user_id", 0)
        monkeypatch.setattr(platform_db, "connect", explode)
        return explode

    def test_the_dashboard_still_answers(self, broken_database):
        data = ops.dashboard()
        assert data["runs"] == []
        assert data["publications"] == []
        assert data["renders"] == []
        assert data["account"]["provisioned"] is False
        assert data["schedule"]["known"] is False

    def test_the_settings_still_render_from_the_environment(self, broken_database, monkeypatch):
        # The most important degradation of the lot. A database that cannot be
        # read is exactly when an operator needs to see whether the kill switch
        # is on, and the answer is still knowable — it is in the environment.
        monkeypatch.setenv("PULSEDROP_ENABLED", "true")
        view = ops.dashboard()["settings"]
        assert len(view) == len(config.SETTINGS)
        assert _pick(view, "PULSEDROP_ENABLED")["value"] is True

    def test_a_write_that_cannot_be_stored_is_reported_not_raised(self, broken_database):
        changed, message = ops.apply_action("set", "PULSEDROP_ENABLED", "false")
        assert changed is False
        assert "could not be saved" in message


class TestTheAdminPage:
    """The route: the permission split, the audit line, and that it renders."""

    PAGE = "/admin/pulsedrop"

    @pytest.fixture()
    def client(self, monolith, monkeypatch):
        """A signed-in owner, with the permission answer under the test's control."""
        admin = {"id": 991, "username": "opsadmin", "email": "ops@example.com", "role": "owner"}
        monkeypatch.setattr(monolith, "require_admin_page", lambda permission: (dict(admin), None))
        monkeypatch.setattr(monolith, "admin_login_required", lambda: dict(admin))
        monolith.webhook_app.config["TESTING"] = True
        return monolith.webhook_app.test_client()

    @pytest.fixture()
    def audited(self, monolith, monkeypatch):
        entries = []
        monkeypatch.setattr(monolith, "log_admin_audit",
                            lambda *args, **kwargs: entries.append((args, kwargs)))
        return entries

    def test_it_renders(self, client):
        response = client.get(self.PAGE)
        assert response.status_code == 200
        page = response.get_data(as_text=True)
        # A runtime NameError in the body is invisible to py_compile and to
        # every unit test of ``ops``; loading the page is the only thing that
        # catches it. The three questions the page answers are asserted by name
        # so a section cannot quietly disappear.
        assert "PulseDrop" in page
        assert "Kill switches" in page
        assert "Run log" in page
        assert "Publications" in page

    def test_the_kill_switch_is_one_click_to_the_state_it_is_not_in(self, client):
        # One button, not a select plus a save, and only the one that changes
        # something. During an incident the cost of "which option was I on" is
        # paid in the seconds between deciding to stop PulseDrop and stopping it.
        config.set_override("PULSEDROP_ENABLED", "true")
        card = _card(client.get(self.PAGE).get_data(as_text=True), "PULSEDROP_ENABLED")
        assert "Turn off" in card and "Turn on" not in card
        assert "value='false'" in card

        config.set_override("PULSEDROP_ENABLED", "false")
        card = _card(client.get(self.PAGE).get_data(as_text=True), "PULSEDROP_ENABLED")
        assert "Turn on" in card and "Turn off" not in card
        assert "value='true'" in card

    def test_a_write_takes_effect_and_is_audited(self, client, audited):
        response = client.post(self.PAGE, data={
            "form_action": "set", "key": "PULSEDROP_SIGNALS_ENABLED", "value": "false"})
        assert response.status_code == 200
        assert config.signals_enabled() is False
        assert audited, "a settings change with no audit row is an untraceable change"
        assert audited[0][0][1] == "pulsedrop_set"

    def test_a_refused_write_is_not_audited(self, client, audited):
        # An audit log that records attempts as changes cannot be used to answer
        # "who turned this off", which is the only question it is ever asked.
        client.post(self.PAGE, data={
            "form_action": "set", "key": "PULSEDROP_DAILY_REEL_CAP", "value": "lots"})
        assert [entry for entry in audited if entry[0][1].startswith("pulsedrop_")] == []

    def test_reading_the_page_does_not_grant_changing_it(self, client, monolith, monkeypatch, audited):
        # Two different right answers: everyone on call should be able to see
        # whether PulseDrop is publishing, and a much smaller group should be
        # able to change what it publishes. The read gate is ``system.view``,
        # which 39 other admin pages use.
        monkeypatch.setattr(monolith, "admin_has_permission",
                            lambda admin, permission: permission != "settings.edit")
        assert client.get(self.PAGE).status_code == 200

        response = client.post(self.PAGE, data={
            "form_action": "set", "key": "PULSEDROP_ENABLED", "value": "true"})
        assert response.status_code == 403
        assert config.overrides() == {}
        assert [entry for entry in audited if entry[0][1].startswith("pulsedrop_")] == []

    def test_a_reader_is_shown_no_controls_to_press(self, client, monolith, monkeypatch):
        # The 403 above is the guarantee; this is so a reader is not invited to
        # discover it by pressing a button that was never going to work.
        monkeypatch.setattr(monolith, "admin_has_permission",
                            lambda admin, permission: permission != "settings.edit")
        page = client.get(self.PAGE).get_data(as_text=True)
        assert "Turn on" not in page
        assert "Turn off" not in page
        assert "Read only" in page

    def test_the_page_survives_a_database_that_cannot_answer(self, client, monkeypatch):
        # The whole reason the readers degrade. If this 500s, the page that
        # exists to switch PulseDrop off is unavailable precisely when PulseDrop
        # is the thing going wrong.
        #
        # The database is broken for real here rather than the readers being
        # stubbed out. Replacing ``ops._rows`` and friends with empty lists would
        # only prove the markup can render nothing, which is not the claim being
        # made; and it leaves the real database reachable, so whether the account
        # rendered as provisioned depended on whether some earlier file in the
        # package had provisioned it. It does, so this test used to pass alone
        # and fail after ``test_profile_payload``.
        from services import db as platform_db
        from services.pulsedrop import account, schema

        def explode(*_args, **_kwargs):
            raise RuntimeError("database is gone")

        monkeypatch.setattr(schema, "_ready", False)
        monkeypatch.setattr(account, "_cached_user_id", 0)
        monkeypatch.setattr(platform_db, "connect", explode)
        monkeypatch.setenv("PULSEDROP_ENABLED", "true")

        response = client.get(self.PAGE)
        assert response.status_code == 200
        page = response.get_data(as_text=True)
        # Degraded, and saying so, rather than rendering a half-built account.
        assert "Not provisioned" in page
        # And still answering the question the page exists to answer. The kill
        # switch lives in the environment, which is knowable with the database
        # down, so an operator mid-incident can still read it.
        assert "Kill switches" in page
        assert "PULSEDROP_ENABLED" in page


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _card(page: str, key: str) -> str:
    """The one setting card that names ``key``.

    Scoped rather than searched whole because every flag on the page carries a
    Turn on or a Turn off button, so an unscoped ``"Turn off" not in page``
    passes or fails on whichever *other* switch happens to be set.
    """
    start = page.find(key)
    assert start != -1, f"{key} is not on the page"
    opened = page.rfind("<div class='card'>", 0, start)
    assert opened != -1, f"{key} is not inside a card"
    closed = page.find("</form></div>", opened)
    return page[opened:closed]


def _setting(key: str) -> dict:
    return _pick(ops.settings_view(), key)


def _pick(view: list[dict], key: str) -> dict:
    for item in view:
        if item["key"] == key:
            return item
    raise AssertionError(f"{key} is not in the settings view")
