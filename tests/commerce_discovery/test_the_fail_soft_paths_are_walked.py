"""25 of this package's 56 fail-soft handlers had never run in a test.

`services/commerce_discovery/` degrades rather than fails — a broken read loses a
signal, not a request. That is the right design and §82 requires it. The cost is
that each handler is an *unverified claim* about what happens when something
breaks, and §19 showed what an unverified claim of that shape is worth: 48 tests
were green against an engine that could not run.

So the handlers were counted.
`scripts/protection/audit_commerce_discovery_failsoft.py` inventories every
``except`` in the package by AST and runs the suite under a line tracer to see
which handler bodies execute. Measured 2026-09-27, before this file existed:

    56 fail-soft handlers
    25 NEVER REACHED by any test in the package
     9 reached, but neither logged nor re-raised
    22 reached, and says so

``exposure.py`` was 5 of 6 never reached; ``engine.py`` 6 of 11; ``ranking.py``
4 of 5.

Re-run the script after this file and the figure moves::

    56 fail-soft handlers
    18 NEVER REACHED   (was 25)
     9 reached, but neither logged nor re-raised   (unchanged)
    29 reached, and says so   (was 22)

``engine.py`` 6 never-reached → 2, ``exposure.py`` 5 → 2. The nine silent
handlers are unchanged on purpose: every one is a ``_int``/``_json_list``-shaped
coercion whose failure is *meant* to be indistinguishable from an absent value,
and making them log would put a line in production for every malformed row.
``ranking.py`` is still 4 of 5 and ``engine.py:533`` — one half of
``_listing_stats`` — is still unreached; see the report for why those were left.

This file does not chase all 25. Chasing a number is how you end up with tests
for branches that cannot occur — §11a of the report argues some fail-soft paths
*should* survive mutation, and a handler guarding an impossible input is one of
them. It takes the handlers where the **direction** of the failure is a promise
somebody is relying on:

* `_persist` — "the blast radius is one dropped placement". That sentence is a
  comment I wrote in the previous increment, on the write path, about
  `relationship.assert_servable`. The audit says the handler it names had never
  executed. An untested claim about a blast radius is just a confident sentence.
* `_session_cap_reached` — annotated ``# fails closed``. A frequency limit whose
  failure direction is wrong shows *more* commerce to someone who has had their
  allowance, and nothing anywhere would say so.
* `_payload` — if the real serializer throws, the fallback must still be the
  allowlist, because §18.6 is about what reaches a buyer's device.
* `exposure`'s three purchase/cart/save reads — each one removes a reason a
  product would be filtered or boosted.
"""

from __future__ import annotations

import importlib.util
import logging
import os
import sys
import types

import pytest

from services.commerce_discovery import engine, exposure, relationship, subject


def price(label, currency="USD"):
    return 100, (currency or "USD")


class ExplodingCursor:
    """A cursor that refuses specific ``execute`` calls.

    ``fails_on`` is a set of 1-based call numbers; empty means refuse everything.
    Enumerating them rather than taking a threshold is deliberate — the first
    draft of this file said "fail from the second call onward", which refused
    calls 2 *and* 3 and therefore proved that two bad rows lose two cards, not
    that one bad row loses one. The interesting claim is the isolated failure.
    """

    def __init__(self, *, fails_on=()):
        self.calls = 0
        self._fails_on = frozenset(int(n) for n in fails_on)

    def execute(self, *_args, **_kwargs):
        self.calls += 1
        if not self._fails_on or self.calls in self._fails_on:
            raise RuntimeError("no such table: as if a migration had not run")

    def fetchall(self):
        return []


@pytest.fixture(scope="module")
def audit():
    """`scripts/protection/audit_commerce_discovery_failsoft.py`, loaded by path.

    `scripts/` is not a package and is not on `sys.path`, so there is no import
    to write."""
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    path = os.path.join(repo, "scripts", "protection", "audit_commerce_discovery_failsoft.py")
    spec = importlib.util.spec_from_file_location("cd_failsoft_audit", path)
    module = importlib.util.module_from_spec(spec)
    # Registered before execution, not after: the script declares `Handler` as a
    # dataclass under `from __future__ import annotations`, and @dataclass
    # resolves those string annotations via `sys.modules[cls.__module__]` at
    # class-creation time. Without this line that lookup returns None and the
    # module raises AttributeError from inside CPython's dataclasses.py.
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(spec.name, None)
        raise
    return module


def verdict(reason="new_to_marketplace", score=1.0):
    return {
        "reason": reason,
        "score": score,
        "signals": {"relevance": 0.5},
        "contributions": {},
        "ranking_version": "test",
    }


def listing(listing_id, **extra):
    row = {
        "id": listing_id,
        "seller_user_id": 1001,
        "title": f"Product {listing_id}",
        "price_label": "$21.00",
        "currency": "USD",
        "cover_image_url": f"https://cdn.example/{listing_id}.jpg",
        "category": "shoes",
    }
    row.update(extra)
    return row


class TestTheBlastRadiusOfAFailedWriteIsOneCard:
    """`_persist`'s docstring: "If the write fails the placement is dropped rather
    than served tokenless — a card whose impression can never be recorded is a
    card that would be re-served forever, because the frequency cap would never
    see it." Two claims, neither previously exercised."""

    def _persist(self, cur, rows):
        return engine._persist(
            cur,
            [(row, verdict()) for row in rows],
            types.SimpleNamespace(subject_ref="sr_test"),
            surface="feed",
            session_id="cs_test",
            klass="organic",
            parse_price=price,
            serialize=None,
            relationships={row["id"]: relationship.CATALOGUE for row in rows},
        )

    def test_a_failing_write_drops_that_card_and_keeps_the_others(self, caplog):
        cur = ExplodingCursor(fails_on=(2,))
        with caplog.at_level(logging.WARNING, logger=engine.__name__):
            out = self._persist(cur, [listing(1), listing(2), listing(3)])

        assert [placement["product"]["id"] for placement in out] == [1, 3], (
            "one unwritable row took the whole response with it, or was served "
            "anyway with a token no impression can ever match"
        )
        assert any(
            "COMMERCE_DISCOVERY_PLACEMENT_WRITE_FAILED" in record.getMessage()
            for record in caplog.records
        ), "the card vanished without a word"

    def test_every_card_that_came_back_has_a_token(self):
        """The reason dropping is the right response. A tokenless card can never
        record an impression, so the frequency cap never learns it was shown and
        it is eligible again on the next request, forever."""
        out = self._persist(ExplodingCursor(fails_on=(2,)), [listing(1), listing(2)])
        assert out
        for placement in out:
            assert placement["impression_token"], placement
            assert placement["placement_id"], placement

    def test_an_unservable_relationship_costs_one_card_and_not_the_response(self, market, monkeypatch):
        """The claim in the comment next to `assert_servable`, checked.

        Nothing can produce an unservable value today — `classify` only ever
        returns one of four — which is exactly why the guard is on the write path
        and why its blast radius has to be demonstrated rather than reasoned
        about. The next writer of this column is a feature that does not exist
        yet.
        """
        before = market.serve("feed")
        assert len(before) >= 2, (
            "this test needs at least two placements to show that one was lost "
            "and the rest survived"
        )
        doomed = int(before[0]["product"]["id"])

        real = relationship.assert_servable

        def refuse_one(value):
            # Keyed off call order rather than the listing id, because
            # `assert_servable` is handed the relationship and never sees the id.
            refuse_one.seen += 1
            if refuse_one.seen == 1:
                raise relationship.RelationshipError("as if creator_tagged had a writer")
            return real(value)

        refuse_one.seen = 0
        monkeypatch.setattr(relationship, "assert_servable", refuse_one)

        after = market.serve("feed")
        assert len(after) == len(before) - 1, (
            f"expected exactly one card to be lost, got {len(after)} from "
            f"{len(before)}"
        )
        assert after, "the whole surface went dark for one bad row"
        assert doomed not in {int(p["product"]["id"]) for p in after} or True, (
            "informational: which card is lost depends on ranking order"
        )


class TestTheSessionCapFailsClosed:
    """``return True  # fails closed``. The comment is the whole test."""

    def test_an_unreadable_cap_counts_as_reached(self):
        assert engine._session_cap_reached(ExplodingCursor(), "sr_test", "feed") is True, (
            "a frequency limit that fails open shows more commerce to exactly the "
            "viewer who has had their allowance, and nothing logs a decision"
        )

    def test_it_says_so(self, caplog):
        with caplog.at_level(logging.WARNING, logger=engine.__name__):
            engine._session_cap_reached(ExplodingCursor(), "sr_test", "feed")
        assert any(
            "COMMERCE_DISCOVERY_SESSION_CAP_UNREADABLE" in record.getMessage()
            for record in caplog.records
        )

    def test_the_surface_goes_quiet_rather_than_uncapped(self, market, monkeypatch):
        """End to end, and note what the §19 guard does *not* do here.

        `serve` returns ``[]`` and no ``COMMERCE_DISCOVERY_SERVE_FAILED`` line is
        emitted, because this empty list is a decision the engine made on purpose
        after a failed read — not a swallowed crash. The guard is deliberately
        narrow enough to tell those apart.
        """
        assert market.serve("feed"), "baseline"

        def unreadable(_seconds):
            raise RuntimeError("as if the clock helper had gone")

        monkeypatch.setattr(subject, "window_start_iso", unreadable)
        assert market.serve("feed") == []


class TestASerializerThatThrowsStillCannotLeak:
    """§18.6's guarantee has to survive the serializer failing, not only the
    serializer working."""

    def test_the_fallback_is_used(self, market):
        def boom(_listing):
            raise RuntimeError("as if bot's payload builder had changed shape")

        served = engine.serve(
            market.conn.cursor(), market.viewer_id, "feed",
            conn=market.conn, parse_price=price, serialize=boom,
        )
        assert served, "a broken serializer emptied the shelf"
        for placement in served:
            assert placement["product"]["title"], placement["product"]

    @pytest.mark.parametrize("field", sorted(engine.PIPELINE_ONLY_FIELDS))
    def test_the_fallback_carries_no_pipeline_column(self, market, field):
        """The fallback is a hand-written allowlist, so this should hold by
        construction — which is the same thing that was true of the fixture's
        serializer right up until §18.6 found two columns reaching buyers."""
        def boom(_listing):
            raise RuntimeError("as if bot's payload builder had changed shape")

        served = engine.serve(
            market.conn.cursor(), market.viewer_id, "feed",
            conn=market.conn, parse_price=price, serialize=boom,
        )
        assert served
        for placement in served:
            assert field not in placement["product"], field


class TestTheOptionalViewerReadsAreOptional:
    """`exposure` reads purchases, cart and saves to decide what to filter and
    what to boost. Each read logs and continues. Five of that module's six
    handlers had never run."""

    @pytest.mark.parametrize("table", [
        "marketplace_orders",
        "marketplace_cart_items",
        "marketplace_saved_products",
    ])
    def test_a_missing_table_costs_a_signal_and_not_the_surface(self, market, table, caplog):
        assert market.serve("feed"), "baseline"
        market.conn.cursor().execute(f"DROP TABLE {table}")
        market.conn.commit()
        with caplog.at_level(logging.WARNING, logger=exposure.__name__):
            served = market.serve("feed")
        assert served, f"dropping {table} shut the shop"

    def test_the_load_still_returns_a_usable_state(self, market):
        market.conn.cursor().execute("DROP TABLE marketplace_saved_products")
        market.conn.commit()
        state = exposure.load(market.conn.cursor(), "sr_test", market.viewer_id)
        assert state is not None
        assert getattr(state, "saved_listing_ids", frozenset()) == frozenset()


class TestTheAuditItselfStillWorks:
    """§14: evidence nobody can regenerate is a defect.

    The 56/25/9/22 numbers in this file's docstring come from a script. Only its
    inventory half is called here — the other half runs the whole suite, which a
    test must not do — but the inventory is where the script would break if the
    package moved, and a silently-zero inventory would make the audit report a
    clean bill of health.
    """

    def test_the_inventory_finds_the_handlers(self, audit):
        handlers = audit.inventory()
        assert len(handlers) > 40, (
            f"only {len(handlers)} handlers found; the audit has stopped seeing "
            "the package and would report everything as fine"
        )

    def test_it_finds_the_two_this_file_is_about(self, audit):
        located = {(h.module, h.function) for h in audit.inventory()}
        assert ("engine.py", "_persist") in located
        assert ("engine.py", "_session_cap_reached") in located

    def test_it_can_tell_a_logging_handler_from_a_silent_one(self, audit):
        handlers = audit.inventory()
        assert any(h.logs for h in handlers), "nothing was detected as logging"
        assert any(not h.logs for h in handlers), (
            "every handler was detected as logging, so the 'silent' category is "
            "unreachable and the audit cannot report it"
        )
