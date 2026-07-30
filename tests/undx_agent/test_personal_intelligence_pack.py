"""Executable privacy, provenance, registry and intent tests for Phase 3B."""

from __future__ import annotations

import json
import re
import sqlite3
import unittest
from unittest.mock import patch

from tests.undx_agent import bootstrap as _bootstrap  # noqa: F401
from services import pulsesoc_notification_system as notifications
from services import undx_personal_intelligence_service as personal
from services.undx_agent_contracts import (
    AgentOutcome,
    VerificationResult,
    VerificationState,
)
from services.undx_agent_runtime import match_capability
from services.undx_capability_registry import REGISTRY
from services.undx_agent_tools import resolve


class _ConnectionProxy:
    def __init__(self, connection):
        self.connection = connection

    def cursor(self):
        return self.connection.cursor()

    def commit(self):
        return self.connection.commit()

    def rollback(self):
        return self.connection.rollback()

    def close(self):
        return None


class PersonalIntelligencePackTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript("""
        CREATE TABLE users(
          user_id INTEGER PRIMARY KEY, username TEXT, display_name TEXT, bio TEXT,
          updated_at TEXT, deleted_at TEXT, account_status TEXT, profile_visibility TEXT);
        CREATE TABLE pulse_posts(
          id INTEGER PRIMARY KEY, user_id INTEGER, title TEXT, body TEXT, created_at TEXT,
          deleted_at TEXT, status TEXT, visibility TEXT);
        -- Column and table names here mirror production exactly (bot.py:38804
        -- for pulse_messages, bot.py:38775 for the participants table).  An
        -- invented name would make the fixture agree with a query that can only
        -- fail against the real database, and the test would then certify the
        -- bug instead of catching it.
        CREATE TABLE pulse_messages(
          id INTEGER PRIMARY KEY, conversation_id INTEGER, sender_user_id INTEGER, body TEXT, created_at TEXT);
        CREATE TABLE pulse_conversation_participants(conversation_id INTEGER,user_id INTEGER);
        CREATE TABLE pulse_reels(
          id INTEGER PRIMARY KEY,user_id INTEGER,caption TEXT,created_at TEXT,
          share_count INTEGER,completion_rate REAL,replay_count INTEGER);
        CREATE TABLE pulse_statuses(
          id INTEGER PRIMARY KEY,user_id INTEGER,body TEXT,created_at TEXT,deleted_at TEXT);
        CREATE TABLE mobile_security_sessions(
          id INTEGER PRIMARY KEY,user_id INTEGER,device_label TEXT,platform TEXT,country TEXT,
          last_seen_at TEXT,created_at TEXT,status TEXT,revoked_at TEXT);
        CREATE TABLE security_events(
          id INTEGER PRIMARY KEY,user_id INTEGER,event_type TEXT,status TEXT,
          ip_address TEXT,created_at TEXT);
        CREATE TABLE marketplace_listings(
          id INTEGER PRIMARY KEY,seller_user_id INTEGER,title TEXT,description TEXT,
          category TEXT,price_label TEXT,status TEXT,updated_at TEXT);
        CREATE TABLE business_os_mkt_orders(
          order_id TEXT PRIMARY KEY,buyer_user_id TEXT,seller_user_id TEXT,status TEXT,
          currency TEXT,total_cents INTEGER,created_at TEXT,updated_at TEXT);
        CREATE TABLE pulse_live_sessions(
          id INTEGER PRIMARY KEY,user_id INTEGER,title TEXT,category TEXT,audience TEXT,
          status TEXT,viewer_count INTEGER,started_at TEXT,ended_at TEXT,created_at TEXT);
        CREATE TABLE pulse_courses(
          id INTEGER PRIMARY KEY,title TEXT,description TEXT,status TEXT,updated_at TEXT);
        CREATE TABLE education_progress(
          id INTEGER PRIMARY KEY,user_id INTEGER,path TEXT,lesson_slug TEXT,status TEXT,
          score INTEGER,updated_at TEXT);
        CREATE TABLE user_settings(
          id INTEGER PRIMARY KEY AUTOINCREMENT,user_id INTEGER,setting_key TEXT,
          setting_value TEXT,updated_at TEXT,UNIQUE(user_id,setting_key));
        CREATE TABLE pulse_groups(
          id INTEGER PRIMARY KEY,owner_user_id INTEGER,slug TEXT,name TEXT,description TEXT,
          category TEXT,group_type TEXT,status TEXT,member_count INTEGER,trust_level TEXT,
          featured INTEGER,updated_at TEXT,created_at TEXT,deleted_at TEXT);
        CREATE TABLE pulse_group_members(group_id INTEGER,user_id INTEGER,role TEXT,created_at TEXT);
        CREATE TABLE business_os_events(
          event_id TEXT PRIMARY KEY,business_id TEXT,title TEXT,description TEXT,venue TEXT,
          starts_at TEXT,ends_at TEXT,capacity INTEGER,currency TEXT,status TEXT,updated_at TEXT,created_at TEXT);
        CREATE TABLE account_health_events(
          id INTEGER PRIMARY KEY,user_id INTEGER,event_type TEXT,severity TEXT,status TEXT,
          public_summary TEXT,created_at TEXT,updated_at TEXT,expires_at TEXT);
        CREATE TABLE account_strikes(
          id INTEGER PRIMARY KEY,user_id INTEGER,policy_category TEXT,severity TEXT,status TEXT,
          public_summary TEXT,created_at TEXT,updated_at TEXT,expires_at TEXT);
        CREATE TABLE account_warnings(
          id INTEGER PRIMARY KEY,user_id INTEGER,policy_category TEXT,status TEXT,
          public_summary TEXT,created_at TEXT,updated_at TEXT,expires_at TEXT);
        CREATE TABLE account_restrictions(
          id INTEGER PRIMARY KEY,user_id INTEGER,restriction_type TEXT,status TEXT,
          public_summary TEXT,created_at TEXT,updated_at TEXT,expires_at TEXT);
        CREATE TABLE account_system_events(
          id INTEGER PRIMARY KEY,user_id INTEGER,event_type TEXT,severity TEXT,status TEXT,
          public_summary TEXT,created_at TEXT,updated_at TEXT,expires_at TEXT);
        CREATE TABLE verification_requests(
          id INTEGER PRIMARY KEY,user_id INTEGER,verification_type TEXT,status TEXT,
          submitted_at TEXT,reviewed_at TEXT,decision_at TEXT,updated_at TEXT,created_at TEXT);
        CREATE TABLE support_tickets(
          id INTEGER PRIMARY KEY,user_id INTEGER,issue_type TEXT,subject TEXT,status TEXT,
          priority TEXT,created_at TEXT,updated_at TEXT);
        CREATE TABLE presence_privacy_settings(
          user_id INTEGER,hide_last_seen INTEGER,invisible_mode INTEGER,updated_at TEXT);
        CREATE TABLE comm_v2_user_settings(
          user_id INTEGER,presence_privacy TEXT,updated_at TEXT);
        """)
        self.conn.executescript("""
        INSERT INTO users VALUES(1,'owner','Owner','launch owner','2026-07-29',NULL,'active','public');
        INSERT INTO users VALUES(2,'public','Public','launch public','2026-07-29',NULL,'active','public');
        INSERT INTO users VALUES(3,'private','Private','launch secret','2026-07-29',NULL,'active','private');
        INSERT INTO pulse_posts VALUES(10,1,'Launch plan','owner detail','2099-01-01',NULL,'published','private');
        INSERT INTO pulse_posts VALUES(20,2,'Launch public','public detail','2099-01-01',NULL,'published','public');
        INSERT INTO pulse_posts VALUES(30,3,'Launch secret','must not leak','2099-01-01',NULL,'published','private');
        INSERT INTO pulse_conversation_participants VALUES(100,1);
        INSERT INTO pulse_conversation_participants VALUES(100,2);
        INSERT INTO pulse_conversation_participants VALUES(200,2);
        INSERT INTO pulse_conversation_participants VALUES(200,3);
        INSERT INTO pulse_messages VALUES(1,100,2,'launch authorized','2099-01-01');
        INSERT INTO pulse_messages VALUES(2,200,3,'launch secret message','2099-01-01');
        INSERT INTO mobile_security_sessions VALUES(1,1,'Owner iPhone','ios','US','2099-01-01','2099-01-01','active','');
        INSERT INTO mobile_security_sessions VALUES(2,2,'Other iPhone','ios','US','2099-01-01','2099-01-01','active','');
        INSERT INTO security_events VALUES(1,1,'login','ok','203.0.113.4','2099-01-01');
        INSERT INTO security_events VALUES(2,2,'password_change','ok','203.0.113.9','2099-01-01');
        INSERT INTO marketplace_listings VALUES(1,2,'Launch Camera','Camera','gear','400 USD','active','2099-01-01');
        INSERT INTO business_os_mkt_orders VALUES('1','1','2','shipped','usd',400,'2099-01-01','2099-01-01');
        INSERT INTO business_os_mkt_orders VALUES('2','2','3','private-order','usd',900,'2099-01-01','2099-01-01');
        INSERT INTO pulse_courses VALUES(1,'Launch editing','Editing course','published','2099-01-01');
        INSERT INTO education_progress VALUES(1,1,'creator','editing-basics','in_progress',50,'2099-01-01');
        INSERT INTO education_progress VALUES(2,2,'creator','editing-basics','done',90,'2099-01-01');
        INSERT INTO pulse_groups VALUES(1,2,'photo-club','Photo Club','Photography','Creators','public','active',42,'trusted',1,'2099-01-01','2099-01-01',NULL);
        INSERT INTO pulse_groups VALUES(2,2,'private-club','Private Club','Secret','Private','private','active',4,'standard',0,'2099-01-01','2099-01-01',NULL);
        INSERT INTO pulse_group_members VALUES(2,2,'member','2099-01-01');
        INSERT INTO business_os_events VALUES('evt_1','biz_1','Creator Summit','Learn together','Online','2099-02-01','2099-02-01',100,'usd','published','2099-01-01','2099-01-01');
        INSERT INTO account_health_events VALUES(1,1,'profile_review','low','open','Review your public profile','2099-01-01','2099-01-01',NULL);
        INSERT INTO account_health_events VALUES(2,2,'other_event','high','open','Other account only','2099-01-01','2099-01-01',NULL);
        INSERT INTO verification_requests VALUES(1,1,'creator','in_review','2099-01-01',NULL,NULL,'2099-01-01','2099-01-01');
        INSERT INTO verification_requests VALUES(2,2,'business','approved','2099-01-01','2099-01-01','2099-01-01','2099-01-01','2099-01-01');
        INSERT INTO support_tickets VALUES(1,1,'account','My ticket','open','normal','2099-01-01','2099-01-01');
        INSERT INTO support_tickets VALUES(2,2,'billing','Other ticket','open','high','2099-01-01','2099-01-01');
        INSERT INTO presence_privacy_settings VALUES(1,1,0,'2099-01-01');
        INSERT INTO comm_v2_user_settings VALUES(1,'followers','2099-01-01');
        """)
        self.proxy = _ConnectionProxy(self.conn)
        self.db_patch = patch.object(personal.db_service, "connect", return_value=self.proxy)
        self.db_patch.start()
        self.notification_list = patch.object(
            notifications, "list_notifications",
            side_effect=lambda user_id, **_: {
                "notifications": [{
                    "id": 9, "title": "Launch mention", "body": f"owner {user_id}",
                    "created_at": "2099-01-01", "category": "social", "source_type": "post",
                    "source_id": 10, "deep_link": "/pulse/post/10", "read": False,
                }]
            },
        )
        self.notification_get = patch.object(
            notifications, "get_notification",
            side_effect=lambda user_id, notification_id: ({
                "id": notification_id, "title": "Launch mention", "created_at": "2099-01-01",
                "source_type": "post", "source_id": 10, "deep_link": "/pulse/post/10",
            } if user_id == 1 and notification_id == 9 else None),
        )
        self.notification_list.start()
        self.notification_get.start()

    def tearDown(self) -> None:
        self.notification_get.stop()
        self.notification_list.stop()
        self.db_patch.stop()
        self.conn.close()

    def test_daily_summary_is_sourced_timestamped_scoped_and_navigable(self) -> None:
        summary = personal.activity_daily_summary(1)
        self.assertGreaterEqual(summary["count"], 3)
        for item in summary["items"]:
            self.assertTrue(item["source"])
            self.assertTrue(item["timestamp"])
            self.assertEqual(item["authorization_scope"], "self_account_only")
            self.assertTrue(item["native_route"].startswith("/pulse"))
        self.assertNotIn("must not leak", json.dumps(summary))
        self.assertNotIn("secret message", json.dumps(summary))

    def test_search_respects_profile_content_and_message_authorization(self) -> None:
        result = personal.search_global(1, "launch", limit=40)
        serialized = json.dumps(result)
        self.assertIn("Launch plan", serialized)
        self.assertIn("Launch public", serialized)
        self.assertIn("launch authorized", serialized)
        self.assertNotIn("Launch secret", serialized)
        self.assertNotIn("launch secret message", serialized)

    def test_notifications_are_owner_scoped_and_explanations_use_source_event(self) -> None:
        inbox = personal.notifications_inbox(1)
        self.assertEqual(inbox[0]["source_id"], "9")
        self.assertEqual(inbox[0]["data"]["source_id"], 10)
        explanation = personal.notification_explain(1, 9)
        self.assertIn("post event 10", explanation["detail"])
        self.assertIsNone(personal.notification_explain(2, 9))

    def test_security_and_orders_do_not_cross_accounts(self) -> None:
        self.assertEqual([item["data"]["id"] for item in personal.security_sessions(1)], [1])
        self.assertNotIn("password_change", json.dumps(personal.security_activity_summary(1)))
        self.assertEqual(personal.marketplace_order_status(1, 1)["detail"], "shipped")
        self.assertIsNone(personal.marketplace_order_status(1, 2))

    def test_memory_is_retrieval_only_and_writes_no_sensitive_copy(self) -> None:
        state = personal.memory_activity_inspect(1)
        self.assertEqual(state["storage"], "source_retrieval_only")
        self.assertFalse(state["sensitive_memory_written"])
        self.assertEqual(state["deletion_support"], "source-owned")

    def test_all_phase3b_capabilities_are_registered_read_only_and_dispatchable(self) -> None:
        phase3b = [capability_id for capability_id in REGISTRY if capability_id in {
            "activity.daily_summary", "notifications.inbox.list", "notifications.explain",
            "notifications.group_summary", "search.global", "search.people", "search.content",
            "search.messages", "search.activity", "settings.inspect", "settings.explain",
            "settings.recommend", "security.sessions.list", "security.activity.summary",
            "security.device.list", "marketplace.search", "marketplace.listing.summary",
            "marketplace.order.status", "premium.status", "premium.entitlements",
            "ads.performance.summary", "live.search", "live.summary", "live.performance",
            "learning.search", "learning.progress", "memory.activity.inspect",
        }]
        self.assertEqual(len(phase3b), 27)
        for capability_id in phase3b:
            spec = REGISTRY[capability_id]
            self.assertEqual(spec.risk, "read_only")
            self.assertEqual(spec.permission, "self_account_only")
            self.assertTrue(callable(resolve(spec.executor)))

    def test_required_natural_language_routes_to_personal_intelligence(self) -> None:
        expected = {
            "What happened today?": "activity.daily_summary",
            "Summarize my notifications.": "notifications.group_summary",
            "Find everything about launch.": "search.global",
            "Show my privacy settings.": "settings.inspect",
            "What devices are logged in?": "security.sessions.list",
            "What plan am I on?": "premium.status",
            "Show my learning progress.": "learning.progress",
            "What do you know about my PulseSoc activity?": "memory.activity.inspect",
        }
        for command, capability_id in expected.items():
            with self.subTest(command=command):
                matched = match_capability(command)
                self.assertIsNotNone(matched)
                self.assertEqual(matched.capability_id, capability_id)

    def test_expert_reads_preserve_scope_and_public_visibility(self) -> None:
        self.assertEqual([item["title"] for item in personal.groups_list(1)], ["Photo Club"])
        self.assertEqual(personal.events_upcoming(1)[0]["source_id"], "evt_1")
        self.assertNotIn("Other account only", json.dumps(personal.account_health_summary(1)))
        self.assertEqual(personal.verification_status(1)[0]["detail"], "in_review")
        self.assertEqual(personal.support_tickets_list(1)[0]["title"], "My ticket")
        self.assertNotIn("Other ticket", json.dumps(personal.support_tickets_list(1)))
        presence = personal.presence_privacy_status(1)
        self.assertTrue(presence["hide_last_seen"])
        self.assertEqual(presence["presence_privacy"], "followers")

    def test_expert_wave_is_registered_read_only_and_dispatchable(self) -> None:
        expert = {
            "groups.list", "groups.search", "events.upcoming", "music.search",
            "account.health.summary", "verification.status", "support.tickets.list",
            "creator.analytics.summary", "localization.preferences",
            "presence.privacy.status",
        }
        self.assertTrue(expert.issubset(REGISTRY))
        for capability_id in expert:
            spec = REGISTRY[capability_id]
            self.assertEqual(spec.risk, "read_only")
            self.assertEqual(spec.permission, "self_account_only")
            self.assertTrue(callable(resolve(spec.executor)))

    def test_expert_commands_route_deterministically(self) -> None:
        expected = {
            "Show my groups": "groups.list",
            "Find a group for photography": "groups.search",
            "What events are coming up": "events.upcoming",
            "Find music for a cinematic Reel": "music.search",
            "Is my account healthy": "account.health.summary",
            "What is my verification status": "verification.status",
            "Show my support tickets": "support.tickets.list",
            "How is my content performing": "creator.analytics.summary",
            "Show my language and region settings": "localization.preferences",
            "Who can see me online": "presence.privacy.status",
        }
        for command, capability_id in expected.items():
            with self.subTest(command=command):
                matched = match_capability(command)
                self.assertIsNotNone(matched)
                self.assertEqual(matched.capability_id, capability_id)


class SchemaGroundingTests(unittest.TestCase):
    """Every table the intelligence layer reads must exist in production.

    This class exists because of a real defect.  ``activity.daily_summary`` and
    ``search.messages`` both queried ``pulse_conversation_members``, a table that
    is declared nowhere in PulseSoc -- the real one is
    ``pulse_conversation_participants`` (bot.py:38775) -- and both selected
    ``sender_id`` where the column is ``sender_user_id`` (bot.py:38807).  The
    reads therefore raised on every production call, were swallowed into an empty
    list, and the summary reported "no messages today" with confidence 1.0.

    The unit tests above could not catch it: their fixture had been written to
    match the query, so the fabricated names agreed with themselves.  A test that
    builds its own schema can only ever prove a query is self-consistent.  Only a
    check against the schema the application actually creates can prove the query
    is *true*, which is why this class reads bot.py rather than a fixture.
    """

    #: Tables that legitimately may not exist in a given deployment.  Reading one
    #: yields an empty section and a ``degraded_sources`` entry, which is an
    #: honest answer; the point of this test is unrepairable *typos*, not
    #: optional subsystems.
    OPTIONAL = frozenset()

    #: SQL words and aggregate names that appear where a column would.  Kept
    #: explicit rather than pulled from a dependency so the test has no reason to
    #: fail for a reason unrelated to the schema.
    SQL_WORDS = frozenset("""
        select from where and or not in is null as on order by group having limit
        desc asc left right inner outer join union all distinct count sum coalesce
        max min avg case when then else end like between exists set values insert
        update delete into offset cast integer text real
    """.split())

    @classmethod
    def setUpClass(cls) -> None:
        import os
        import re

        root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        table_pattern = re.compile(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"'`\[]?(\w+)[\"'`\]]?\s*\((.*?)\)\s*[\"';]",
            re.I | re.S,
        )
        columns: dict[str, set[str]] = {}

        def scan(path: str) -> None:
            with open(path, encoding="utf-8", errors="replace") as handle:
                source = handle.read()
            for match in table_pattern.finditer(source):
                name = match.group(1).lower()
                found = set()
                for line in match.group(2).split(","):
                    word = line.strip().split()
                    if not word:
                        continue
                    head = word[0].strip('"`[]').lower()
                    # Skip table-level constraints, which occupy a comma-separated
                    # slot but declare no column.
                    if head in {"primary", "unique", "foreign", "check", "constraint"}:
                        continue
                    found.add(head)
                # ``add_columns_if_missing`` migrations add columns after the
                # CREATE, so a table may legitimately be wider than its literal.
                columns.setdefault(name, set()).update(found)

        for relative in ("bot.py",):
            path = os.path.join(root, relative)
            if os.path.exists(path):
                scan(path)
        for folder in ("services", "pulse_communications_v2"):
            for dirpath, _dirnames, filenames in os.walk(os.path.join(root, folder)):
                for name in filenames:
                    if name.endswith(".py"):
                        scan(os.path.join(dirpath, name))

        # Migration-added columns, which never appear inside a CREATE literal.
        migration = re.compile(
            r"add_columns_if_missing\(\s*\w+\s*,\s*[\"'](\w+)[\"']\s*,\s*\[(.*?)\]", re.S
        )
        with open(os.path.join(root, "bot.py"), encoding="utf-8", errors="replace") as handle:
            body = handle.read()
        for match in migration.finditer(body):
            table = match.group(1).lower()
            for column in re.findall(r"\(\s*[\"'](\w+)[\"']", match.group(2)):
                columns.setdefault(table, set()).add(column.lower())

        cls.declared_columns = columns
        cls.declared_tables = set(columns)

    def _sql_literals(self) -> list[str]:
        import ast
        import inspect

        tree = ast.parse(inspect.getsource(personal))
        return [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and re.search(r"\bSELECT\b", node.value, re.I)
        ]

    def test_every_table_read_is_declared_somewhere_in_the_application(self) -> None:
        referenced: set[str] = set()
        for sql in self._sql_literals():
            referenced.update(
                name.lower()
                for name in re.findall(r"\b(?:FROM|JOIN)\s+[\"'`\[]?(\w+)", sql, re.I)
            )
        # ``f"...FROM {table}..."`` interpolates its table, so the literal holds
        # a placeholder rather than a name.  Those tables are covered by the
        # loop's own tuple and asserted separately below.
        referenced.discard("table")
        missing = sorted(
            name for name in referenced
            if name not in self.declared_tables and name not in self.OPTIONAL
        )
        self.assertEqual(
            missing, [],
            "the intelligence layer reads tables that PulseSoc never creates; "
            "these queries raise on every production call: " + ", ".join(missing),
        )

    def test_every_column_read_exists_on_some_table_the_query_names(self) -> None:
        """Catches the second half of the defect class: real table, invented column.

        ``sender_id``, ``owner_user_id`` and ``progress_percent`` were all read
        from tables that do exist, so a table-level check passed them.  SQLite
        still raises on an unknown column, so the effect was identical.

        The check is deliberately permissive: a column need only exist on *one*
        of the tables the query names, because resolving which table owns which
        name would mean writing a SQL parser.  Permissive is enough -- a
        fabricated name belongs to none of them.
        """
        failures = []
        for sql in self._sql_literals():
            tables = {
                name.lower()
                for name in re.findall(r"\b(?:FROM|JOIN)\s+[\"'`\[]?(\w+)", sql, re.I)
            }
            tables.discard("table")
            known = set()
            for table in tables:
                known |= self.declared_columns.get(table, set())
            if not known:
                continue
            # Comparison values ('active', 'published') sit exactly where a
            # column sits, so literals go first or every status string in the
            # module reads as a missing column.
            stripped = re.sub(r"'[^']*'", " ", sql)
            aliases = {
                alias.lower()
                for alias in re.findall(r"\b(?:FROM|JOIN)\s+\w+\s+(?:AS\s+)?(\w+)", stripped, re.I)
            } - self.SQL_WORDS
            # ``SELECT x AS y`` names an output column, not a stored one.
            aliases |= {a.lower() for a in re.findall(r"\bAS\s+(\w+)", stripped, re.I)}
            # Function names are dropped by position, after tokenising, so a
            # name is never truncated to the fragment before its parenthesis.
            candidates = {
                word.lower()
                for word, call in re.findall(r"(\w+)(\s*\()?", stripped)
                if word and not call and not word.isdigit()
            }
            candidates |= {c.lower() for c in re.findall(r"\b\w+\.(\w+)", stripped)}
            unknown = sorted(
                candidate for candidate in candidates
                if candidate not in known
                and candidate not in self.SQL_WORDS
                and candidate not in tables
                and candidate not in aliases
                and candidate not in self.declared_tables
            )
            if unknown:
                failures.append(f"{sorted(tables)}: {unknown}")
        self.assertEqual(
            failures, [],
            "columns read that no named table declares:\n" + "\n".join(failures),
        )

    def test_interpolated_activity_tables_are_declared(self) -> None:
        for table in ("pulse_posts", "pulse_reels", "pulse_statuses"):
            with self.subTest(table=table):
                self.assertIn(table, self.declared_tables)

    def test_a_failed_read_is_reported_instead_of_being_shown_as_an_empty_day(self) -> None:
        """A summary built on a broken read must not claim the day was quiet."""
        empty = sqlite3.connect(":memory:")
        empty.row_factory = sqlite3.Row
        with patch.object(personal.db_service, "connect", return_value=_ConnectionProxy(empty)), \
                patch.object(notifications, "list_notifications", return_value={"notifications": []}), \
                self.assertLogs(personal.logger, level="WARNING"):
            summary = personal.activity_daily_summary(1, days=1)
        self.assertFalse(summary["complete"])
        self.assertLess(summary["confidence"], 1.0)
        self.assertIn("messages_received", summary["degraded_sources"])
        empty.close()

    def test_a_degraded_read_is_not_recorded_as_verified(self) -> None:
        """The audit trail must not agree, with confidence, that nothing happened.

        Before this, every read reached ``verified_success`` merely by not raising,
        so a query that failed, was caught, and returned ``[]`` was filed as verified
        against an empty result. That is the confident-zero failure in its most
        durable form: not just a wrong answer on screen, but a wrong answer written
        into the record we would later consult to ask whether UNDX was right.
        """
        from services import undx_agent_tools, undx_capability_registry, undx_tool_gateway

        empty = sqlite3.connect(":memory:")
        empty.row_factory = sqlite3.Row
        with patch.object(personal.db_service, "connect", return_value=_ConnectionProxy(empty)), \
                patch.object(notifications, "list_notifications", return_value={"notifications": []}), \
                self.assertLogs(personal.logger, level="WARNING"):
            result = undx_agent_tools.activity_daily_summary(1, {"days": 1})
        empty.close()

        # The read itself did not raise, so the tool call is still 'ok'. The distinction
        # this test defends is between "ran" and "saw everything".
        self.assertTrue(result.ok)
        self.assertTrue(result.degraded_sources, "degradation did not reach the tool result")
        self.assertIs(result.data["complete"], False)

        spec = undx_capability_registry.REGISTRY["activity.daily_summary"]
        self.assertFalse(spec.is_write)
        status = undx_tool_gateway._status_for(
            spec, result, VerificationResult(state=VerificationState.IMPOSSIBLE),
        )
        self.assertEqual(status, AgentOutcome.ACCEPTED_UNVERIFIED)

        # And the sentence has to match the evidence, not the happy path.
        explanation = undx_tool_gateway._explain(
            spec, status, result, VerificationResult(state=VerificationState.IMPOSSIBLE),
        )
        self.assertNotEqual(explanation, "Here is what I found.")
        self.assertIn("incomplete", explanation)

    def test_a_clean_read_is_still_verified(self) -> None:
        """The degradation check must not downgrade a read that genuinely saw everything."""
        from services import undx_capability_registry, undx_tool_gateway
        from services.undx_agent_contracts import ToolResult

        spec = undx_capability_registry.REGISTRY["activity.daily_summary"]
        clean_result = ToolResult(
            ok=True, tool_name="pulsesoc.activity.daily_summary",
            capability_id="activity.daily_summary", records=[], data={"complete": True},
        )
        self.assertEqual(clean_result.degraded_sources, [])
        status = undx_tool_gateway._status_for(
            spec, clean_result, VerificationResult(state=VerificationState.IMPOSSIBLE),
        )
        self.assertEqual(status, AgentOutcome.VERIFIED_SUCCESS)

    def test_nested_collectors_do_not_swallow_a_failure(self) -> None:
        """An inner collector must report upward, not shadow the outer set.

        ``activity_daily_summary`` opens its own collector while the gateway wraps
        every personal read in one. If the inner block installed a fresh set, it
        would discard its failures on exit and the outer caller would observe a
        clean run — reintroducing the exact silence this machinery exists to break.
        """
        with personal.collecting() as outer:
            with personal.collecting() as inner:
                self.assertIs(inner, outer)
                inner.add("probe")
            self.assertIn("probe", outer)


if __name__ == "__main__":
    unittest.main()
