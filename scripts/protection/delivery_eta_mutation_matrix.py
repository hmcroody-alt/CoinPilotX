#!/usr/bin/env python3
"""Prove each delivery-estimate refusal is load-bearing by removing it.

A green delivery suite says the tests pass. It does not say they would notice if
the refusal they are named after were deleted, and on this domain that is the
only claim worth anything: every wrong number here is *actioned by a human*. The
buyer waits, then escalates. And every refusal in this slice is one token away
from silently disappearing — ``handling or 0``, ``buffer_days or 0``, a default
transit range, ``floor`` for ``ceil``, a dropped tie-break.

Those are not hypothetical operator flips. They are what the code would look like
if it had been written on a bad day, which is why each mutation below is
hand-written rather than generated.

Direction matters more than magnitude here. A mutation that moves a date *later*
costs a little conversion; one that moves it *earlier* produces a promise the
platform cannot keep. Almost every mutation in this file moves it earlier, and
that asymmetry is the reason the suite has to see them.

Two things this deliberately does not claim:

* Killing a mutant proves the refusal is *observed*, not that it is *correct*. A
  test pinning the wrong behaviour still goes red when that behaviour is removed.
* Absence of a discriminating mutation is reported, not hidden. ``_freight`` uses
  ``Decimal`` because money should not be binary floating point, but at two
  decimal places no test here can tell a float implementation apart, so there is
  no entry claiming otherwise.

The repository is never mutated. Everything runs against a ``copytree`` of it in
a temporary directory, so there is no restore step to get wrong and no chance of
a mutation outliving the run.

Usage:  python3 scripts/protection/delivery_eta_mutation_matrix.py [--verbose]
Exit 0 only when every mutation is killed.
"""
from __future__ import annotations

import argparse
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]

NORMALIZE = "services/business_os/suppliers/normalize.py"
ESTIMATE = "services/delivery/estimate.py"
ROUTING = "services/delivery/routing.py"
CACHE = "services/delivery/cache.py"
BREAKER = "services/delivery/breaker.py"
QUOTE = "services/delivery/quote.py"
CJ_PROVIDER = "services/delivery/providers/cj_logistics.py"
STORE = "services/delivery/store.py"
FACTS = "services/delivery/variant_facts.py"
ORIGIN = "services/delivery/origin.py"
ENTRY = "services/delivery/entry.py"
DESTINATION = "services/delivery/destination.py"
LISTING = "services/delivery/listing.py"
POLICY = "services/delivery/policy.py"
ROUTES = "services/delivery_routes.py"
PROMISE = "services/delivery/promise.py"
COPY = "services/delivery/copy.py"
WEB = "services/delivery/web.py"
#: The app's half of the vocabulary, in TypeScript. Mutated here because the
#: contract test's entire job is to notice a sentence edited on *one* side, and
#: one side is the side no Python mutation can reach.
TS_COPY = "mobile-native/src/components/commerce/deliveryCopy.ts"
# The monolith. Only the settlement seam is mutated here, and only because a
# perfect delivery package that nothing calls is indistinguishable from none.
WIRING = "bot.py"

AGING_SUITE = "tests/dropshipping/test_supplier_normalization.py"
ESTIMATE_SUITE = "tests/delivery/test_delivery_estimate.py"
ROUTING_SUITE = "tests/delivery/test_delivery_routing.py"
CACHE_SUITE = "tests/delivery/test_delivery_cache.py"
BREAKER_SUITE = "tests/delivery/test_delivery_breaker.py"
QUOTE_SUITE = "tests/delivery/test_delivery_quote.py"
CJ_PROVIDER_SUITE = "tests/delivery/test_cj_logistics_provider.py"
STORE_SUITE = "tests/delivery/test_delivery_store.py"
FACTS_SUITE = "tests/delivery/test_variant_facts.py"
ORIGIN_SUITE = "tests/delivery/test_delivery_origin.py"
ENTRY_SUITE = "tests/delivery/test_delivery_entry.py"
DESTINATION_SUITE = "tests/delivery/test_delivery_destination.py"
LISTING_SUITE = "tests/delivery/test_delivery_listing.py"
POLICY_SUITE = "tests/delivery/test_delivery_policy.py"
ROUTES_SUITE = "tests/delivery/test_delivery_routes.py"
PROMISE_SUITE = "tests/delivery/test_delivery_promise.py"
WIRING_SUITE = "tests/delivery/test_delivery_promise_wiring.py"
COPY_SUITE = "tests/delivery/test_delivery_copy.py"
WEB_SUITE = "tests/delivery/test_delivery_web.py"

# Directories with nothing this slice imports. Copying them costs 300MB and
# several seconds per run for no added coverage.
SKIP = shutil.ignore_patterns(
    ".git", "__pycache__", "*.pyc", ".pytest_cache", "node_modules",
    "reports", "release-assets", "mobile", "mobile-native", "static", "assets",
    ".fuse_hidden*", "*.db",
)

#: Files the skipped directories hold that two suites nonetheless read.
#:
#: ``SKIP`` exists because copying `mobile-native/` and `static/` costs hundreds
#: of megabytes and several seconds per run. But the copy contract test reads the
#: app's TypeScript and the web test reads the browser script and asserts the
#: absence of delivery vocabulary in it, so without these three files both suites
#: fail in the sandbox — and a red baseline makes every mutation look killed,
#: which the runner aborts on rather than reports as a pass.
#:
#: Named individually rather than by un-skipping the directories: three files is
#: the whole dependency, and widening ``SKIP`` would put `mobile-native/ios/`
#: back in the copy.
CARRIED = (
    TS_COPY,
    "mobile-native/src/api/delivery.ts",
    "static/js/pulse_delivery.js",
)

# Each entry: the refusal, what its lapse looks like in source, and which suite
# is supposed to be watching. Suites are named narrowly on purpose — pointing at
# a directory would let an unrelated test take credit for a kill, which is the
# same blind spot one level up.
MUTATIONS = [
    # -- The provider boundary: an unreadable aging string ------------------
    dict(
        name="aging-falls-back-to-a-house-default",
        refusal="An unreadable aging string is None, never a duration.",
        path=NORMALIZE,
        old="    if not found:\n        return None",
        new="    if not found:\n        found = [\"7\", \"15\"]",
        suites=[AGING_SUITE],
    ),
    dict(
        name="aging-range-separator-read-as-a-minus-sign",
        refusal=(
            "Transit numbers are matched unsigned, because a transit string's '-' "
            "is a range separator. Reusing the signed money pattern reads '7-20' "
            "as 7 followed by negative 20."
        ),
        path=NORMALIZE,
        old="    found = _TRANSIT_NUMBER.findall(text)",
        new="    found = _NUMBER.findall(text)",
        suites=[AGING_SUITE],
    ),
    dict(
        name="aging-plausibility-ceiling-removed",
        refusal=(
            "A range past MAX_TRANSIT_DAYS is refused. '2024-2025' parses as a "
            "well-formed 2024-to-2025-day range, so the ceiling is the only thing "
            "stopping a misidentified string becoming a delivery quote."
        ),
        path=NORMALIZE,
        old="    if high > MAX_TRANSIT_DAYS:\n        return None",
        new="    if False:\n        return None",
        suites=[AGING_SUITE],
    ),
    dict(
        name="aging-bounds-round-down",
        refusal="Fractional bounds round up, because later is the safe direction.",
        path=NORMALIZE,
        old="    days = sorted(max(1, math.ceil(bound)) for bound in bounds)",
        new="    days = sorted(max(1, math.floor(bound)) for bound in bounds)",
        suites=[AGING_SUITE],
    ),
    dict(
        name="aging-silence-assumed-to-mean-calendar-days",
        refusal=(
            "An unstated basis stays UNSPECIFIED at the boundary. Recording it as "
            "CALENDAR would launder this module's guess into the provider's "
            "statement, and components.basis_was_stated could no longer tell an "
            "accuracy review which estimates were made on an assumption."
        ),
        path=NORMALIZE,
        old="    basis = TRANSIT_BASIS_UNSPECIFIED\n    if any(marker in lowered",
        new="    basis = TRANSIT_BASIS_CALENDAR\n    if any(marker in lowered",
        suites=[AGING_SUITE],
    ),

    # -- The estimator: a missing component is never a substituted one ------
    dict(
        name="undeclared-handling-becomes-same-day-dispatch",
        refusal=(
            "handling=None yields UNAVAILABLE. `handling or 0` is the whole bug: "
            "a complete, plausible window short by however long the warehouse "
            "actually takes to pick."
        ),
        path=ESTIMATE,
        old="    handling_range = _range(handling, \"handling\")\n    if handling_range is None:\n        return unavailable(REASON_NO_HANDLING)",
        new="    handling_range = _range(handling, \"handling\") or {\"min_days\": 0, \"max_days\": 0, \"basis\": BASIS_UNSPECIFIED}",
        suites=[ESTIMATE_SUITE],
    ),
    dict(
        name="undeclared-buffer-becomes-no-buffer",
        refusal=(
            "buffer_days=None yields UNAVAILABLE; 0 is a decision someone made. "
            "Collapsing them is how an unconfigured deployment quotes unbuffered "
            "dates that look exactly like deliberately unbuffered ones."
        ),
        path=ESTIMATE,
        old="    if buffer_days is None:\n        return unavailable(REASON_NO_BUFFER)",
        new="    if buffer_days is None:\n        buffer_days = 0",
        suites=[ESTIMATE_SUITE],
    ),
    dict(
        name="unknown-transit-becomes-a-guess",
        refusal="transit=None yields UNAVAILABLE rather than a default range.",
        path=ESTIMATE,
        old="    transit_range = _range(transit, \"transit\")\n    if transit_range is None:\n        return unavailable(REASON_NO_TRANSIT)",
        new="    transit_range = _range(transit, \"transit\") or {\"min_days\": 7, \"max_days\": 15, \"basis\": BASIS_UNSPECIFIED}",
        suites=[ESTIMATE_SUITE],
    ),
    dict(
        name="unstated-basis-counted-as-calendar-days",
        refusal=(
            "Silence about the unit is read as business days. N business days is "
            "longer in wall-clock time than N calendar days, so reading calendar "
            "here shortens every estimate the platform makes — against the one "
            "supplier whose strings never state a unit."
        ),
        path=ESTIMATE,
        old="    unspecified_basis: str = BASIS_BUSINESS,",
        new="    unspecified_basis: str = BASIS_CALENDAR,",
        suites=[ESTIMATE_SUITE],
    ),
    dict(
        name="business-day-counting-ignores-weekends",
        refusal="Business-day arithmetic skips Saturdays, Sundays and closures.",
        path=ESTIMATE,
        old="        if moved.weekday() < 5 and moved not in closed:\n            remaining -= 1",
        new="        remaining -= 1",
        suites=[ESTIMATE_SUITE],
    ),
    dict(
        name="declared-holidays-not-modelled",
        refusal=(
            "A supplied holiday calendar moves the window. CJ ships from China and "
            "Chinese New Year moves real arrival dates by weeks."
        ),
        path=ESTIMATE,
        old="    closed = _holiday_set(holidays)",
        new="    closed = frozenset()",
        suites=[ESTIMATE_SUITE],
    ),
    dict(
        name="buffer-pads-both-ends-of-the-window",
        refusal=(
            "The buffer extends the far end only. Padding the near end narrows the "
            "window against the evidence while managing a risk that lives at the "
            "other end."
        ),
        path=ESTIMATE,
        old="    latest = latest + timedelta(days=buffer_days)",
        new="    latest = latest + timedelta(days=buffer_days)\n    earliest = earliest + timedelta(days=buffer_days)",
        suites=[ESTIMATE_SUITE],
    ),
    dict(
        name="dispatch-cutoff-not-modelled",
        refusal="An order placed after the daily handover starts handling tomorrow.",
        path=ESTIMATE,
        old="    if dispatch_cutoff_hour is not None and now.hour >= dispatch_cutoff_hour:\n        start = start + timedelta(days=1)",
        new="    if False:\n        start = start + timedelta(days=1)",
        suites=[ESTIMATE_SUITE],
    ),
    dict(
        name="naive-now-assumed-to-be-utc",
        refusal=(
            "A naive datetime is rejected rather than assigned a zone. Guessing is "
            "a silent off-by-one-day on every estimate produced near midnight."
        ),
        path=ESTIMATE,
        old="    if not isinstance(now, datetime) or now.tzinfo is None or now.tzinfo.utcoffset(now) is None:\n        raise EstimateRejected(\"now must be timezone-aware\")",
        new="    if not isinstance(now, datetime):\n        raise EstimateRejected(\"now must be a datetime\")",
        suites=[ESTIMATE_SUITE],
    ),
    dict(
        name="estimate-presented-as-a-guarantee",
        refusal=(
            "guaranteed is hard False, mirroring the CJ adapter. No supplier in "
            "this system offers one, so no argument turns it on."
        ),
        path=ESTIMATE,
        old="        \"confidence\": confidence, \"guaranteed\": False,",
        new="        \"confidence\": confidence, \"guaranteed\": True,",
        suites=[ESTIMATE_SUITE],
    ),

    # -- The selector: deterministic, and never a fallback pick -------------
    dict(
        name="tie-break-dropped-so-selection-follows-provider-order",
        refusal=(
            "Every comparison ends in option_id. Without it two channels at the "
            "same price and speed rank by whatever order the provider listed them "
            "in, and a product page and a checkout quote different services for "
            "the same basket. Neither is wrong alone, so no single-surface test "
            "fails."
        ),
        path=ROUTING,
        old="        return (speed[0], speed[1], cost, row[\"option_id\"])\n    return (cost, speed[0], speed[1], row[\"option_id\"])",
        new="        return (speed[0], speed[1], cost)\n    return (cost, speed[0], speed[1])",
        suites=[ROUTING_SUITE],
    ),
    dict(
        name="unknown-freight-sorts-as-free",
        refusal=(
            "An unpriced route is not a cheap one. None as zero makes the "
            "least-understood option win, and under FASTEST — which admits "
            "unpriced routes — the cost tie-break is where that still decides a "
            "selection."
        ),
        path=ROUTING,
        old="    cost = row[\"cost\"] if row[\"cost\"] is not None else Decimal(\"Infinity\")",
        new="    cost = row[\"cost\"] if row[\"cost\"] is not None else Decimal(\"0\")",
        suites=[ROUTING_SUITE],
    ),
    dict(
        name="unpriced-route-admitted-to-a-cost-ranked-policy",
        refusal=(
            "A cost policy cannot rank a route it has no price for, so the route "
            "is excluded and the exclusion is reported."
        ),
        path=ROUTING,
        old="        if policy == POLICY_CHEAPEST_ACCEPTABLE and cost is None:",
        new="        if policy == POLICY_FASTEST and cost is None:",
        suites=[ROUTING_SUITE],
    ),
    dict(
        name="provider-unavailable-route-still-ranked",
        refusal=(
            "available=False excludes the route before ranking. Reading only the "
            "absent case lets an explicitly unavailable — and often cheapest — "
            "route reach a buy button."
        ),
        path=ROUTING,
        old="        if not option.get(\"available\"):",
        new="        if option.get(\"available\") is None:",
        suites=[ROUTING_SUITE],
    ),
    dict(
        name="ceiling-checked-against-the-wrong-end-of-the-range",
        refusal=(
            "The ceiling applies to max_days. Checking min_days admits a 7-to-40 "
            "day route under a 21-day ceiling, which is the trivial saving buying "
            "a much slower delivery that the ceiling exists to stop."
        ),
        path=ROUTING,
        old="        if ceiling_days is not None and transit[\"max_days\"] > ceiling_days:",
        new="        if ceiling_days is not None and transit[\"min_days\"] > ceiling_days:",
        suites=[ROUTING_SUITE],
    ),
    dict(
        name="no-candidates-indistinguishable-from-all-rejected",
        refusal=(
            "'The provider offered no routes' and 'every route it offered is "
            "unusable' lead to different investigations, so they are different "
            "reasons."
        ),
        path=ROUTING,
        old="    if not rows:\n        return _result(None, NO_CANDIDATES, eligible, excluded, policy, ceiling_days)",
        new="    if False:\n        return _result(None, NO_CANDIDATES, eligible, excluded, policy, ceiling_days)",
        suites=[ROUTING_SUITE],
    ),
    dict(
        name="malformed-option-ranked-instead-of-excluded",
        refusal=(
            "A route with no usable identifier cannot be booked by fulfillment "
            "later, so it must never be selectable now."
        ),
        path=ROUTING,
        old="        if not isinstance(option_id, str) or not option_id.strip():",
        new="        if option_id is None:",
        suites=[ROUTING_SUITE],
    ),

    # -- The cache: what a key separates, and whether stale exists ----------
    dict(
        name="store-ttl-equals-freshness-so-there-is-no-stale-tier",
        refusal=(
            "The hard TTL is freshness plus the stale window. A TTL equal to "
            "freshness evicts the entry at the instant it becomes "
            "servable-but-stale, so every stale read is really a miss — and it "
            "looks fine until the provider goes down and every page empties."
        ),
        path=CACHE,
        old="    return fresh + stale",
        new="    return fresh",
        suites=[CACHE_SUITE],
    ),
    dict(
        name="a-negative-answer-gains-a-stale-window",
        refusal=(
            "An unsupported route is never served stale. Serving it keeps a "
            "checkout closed on the strength of an old answer that may really "
            "have been a credential problem."
        ),
        path=CACHE,
        old="UNSUPPORTED_STALE_SECONDS = 0",
        new="UNSUPPORTED_STALE_SECONDS = 6 * 3600",
        suites=[CACHE_SUITE],
    ),
    dict(
        name="an-expired-envelope-still-served",
        refusal="Past the stale window an entry is a MISS, not an answer.",
        path=CACHE,
        old="    else:\n        return _miss()",
        new="    else:\n        state = STATE_STALE",
        suites=[CACHE_SUITE],
    ),
    dict(
        name="a-foreign-envelope-version-is-reinterpreted",
        refusal=(
            "An envelope from another schema version is a MISS. Salvaging a body "
            "this module does not understand is how a schema change becomes a "
            "wrong delivery date instead of a cache miss."
        ),
        path=CACHE,
        old="    if not isinstance(entry, dict) or entry.get(\"version\") != VERSION:",
        new="    if not isinstance(entry, dict):",
        suites=[CACHE_SUITE],
    ),
    dict(
        name="a-missing-destination-keys-globally-instead-of-raising",
        refusal=(
            "A key cannot be built without a destination country. Dropping it "
            "yields a still-valid key that serves one country's transit time to "
            "another country's buyer."
        ),
        path=CACHE,
        old="        if required:\n            raise DeliveryCacheRejected(",
        new="        if False:\n            raise DeliveryCacheRejected(",
        suites=[CACHE_SUITE],
    ),
    dict(
        name="the-full-postal-code-reaches-the-cache-key",
        refusal=(
            "Postal codes are truncated to a prefix. A full one is a personal "
            "identifier sitting in a shared Redis instance with a day-long TTL."
        ),
        path=CACHE,
        old="    prefix = _TOKEN_STRIP.sub(\"\", value.upper())[:POSTAL_PREFIX_LENGTH]",
        new="    prefix = _TOKEN_STRIP.sub(\"\", value.upper())",
        suites=[CACHE_SUITE],
    ),
    dict(
        name="warehouse-order-splits-one-answer-across-two-keys",
        refusal=(
            "A set of warehouses has no order, so the key sorts them. Two callers "
            "listing the same warehouses differently would otherwise halve the hit "
            "rate with nothing visibly wrong."
        ),
        path=CACHE,
        old="    tokens = sorted({_token(value) for value in values} - {\"*\"})",
        new="    tokens = [_token(value) for value in values if _token(value) != \"*\"]",
        suites=[CACHE_SUITE],
    ),
    dict(
        name="quantity-bucketed-to-its-floor-instead-of-its-top",
        refusal=(
            "A bucket is quoted at its top, the member whose answer cannot be "
            "optimistic for the others."
        ),
        path=CACHE,
        old="        if quantity <= top:\n            return top",
        new="        if quantity <= top:\n            return QUANTITY_BUCKETS[max(0, QUANTITY_BUCKETS.index(top) - 1)]",
        suites=[CACHE_SUITE],
    ),
    dict(
        name="concurrent-callers-each-call-the-provider",
        refusal=(
            "Single-flight collapses concurrent misses on one key to one call. "
            "Without it a single page render fans out, which is §18's forty cards "
            "becoming forty CJ calls."
        ),
        path=CACHE,
        old="        flight = _FLIGHTS.get(key)\n        leader = flight is None",
        new="        flight = None\n        leader = True",
        suites=[CACHE_SUITE],
    ),
    dict(
        name="a-finished-flight-is-never-deregistered",
        refusal=(
            "A flight outliving its producer pins one answer forever and the next "
            "caller waits for a leader that has already returned."
        ),
        path=CACHE,
        old="                if _FLIGHTS.get(key) is flight:\n                    del _FLIGHTS[key]",
        new="                pass",
        suites=[CACHE_SUITE],
    ),
    dict(
        name="a-follower-waits-out-a-wedged-leader",
        refusal=(
            "A follower's wait is bounded. Waiting out a wedged upstream converts "
            "one slow request into many — coalescing, inverted."
        ),
        path=CACHE,
        old="    if not flight[\"done\"].wait(timeout=max(0.0, float(wait_seconds))):\n        raise CoalesceTimeout(key)",
        new="    flight[\"done\"].wait()",
        suites=[CACHE_SUITE],
    ),
    dict(
        name="the-paired-ttl-ignores-the-stale-window",
        refusal=(
            "``entry_for`` exists so the envelope's deadlines and the row's TTL come "
            "from one pair of numbers. A TTL that drops the stale tier evicts the row "
            "while its own envelope still advertises a stale window to read."
        ),
        path=CACHE,
        old="        \"ttl_seconds\": store_ttl(fresh_seconds, stale_seconds),",
        new="        \"ttl_seconds\": store_ttl(fresh_seconds, 0),",
        suites=[CACHE_SUITE],
    ),

    # -- The CJ provider ----------------------------------------------------
    #
    # Every mutation here substitutes a plausible value for a missing fact. That
    # is the entire risk of the module: CJ answers a well-formed request about an
    # imaginary parcel with a real, confident freight quote, and nothing
    # downstream can tell it from a good one.
    dict(
        name="the-weight-sent-is-one-unit-not-the-parcel",
        refusal=(
            "A five-unit order of a 250g item is a 1.25kg parcel. Sending 250 "
            "quotes a cheaper, faster service than the one that will carry it."
        ),
        path=CJ_PROVIDER,
        old="            \"weight\": float(described[\"weight_grams\"] * units),",
        new="            \"weight\": float(described[\"weight_grams\"]),",
        suites=[CJ_PROVIDER_SUITE],
    ),
    dict(
        name="a-missing-weight-becomes-a-plausible-parcel",
        refusal=(
            "A guessed weight does not produce a guessed answer. It produces a "
            "real freight quote for a parcel whose contents we do not know."
        ),
        path=CJ_PROVIDER,
        old="    grams = facts.get(\"weight_grams\")",
        new="    grams = facts.get(\"weight_grams\") or 200",
        suites=[CJ_PROVIDER_SUITE],
    ),
    dict(
        name="a-boolean-weight-passes-as-one-gram",
        refusal=(
            "``isinstance(True, int)``. Dropping the bool check lets a truthy "
            "flag in a weight column quote a one-gram parcel."
        ),
        path=CJ_PROVIDER,
        old="    if (isinstance(grams, bool) or not isinstance(grams, (int, float))",
        new="    if (not isinstance(grams, (int, float))",
        suites=[CJ_PROVIDER_SUITE],
    ),
    dict(
        name="absent-shipping-properties-become-ordinary",
        refusal=(
            "CJ routes batteries, liquids and magnets differently. \"ORDINARY\" "
            "is the plausible substitution and it is wrong exactly where it "
            "matters most."
        ),
        path=CJ_PROVIDER,
        old="    if not clean:",
        new="    if not clean:\n        clean = [\"ORDINARY\"]\n    if False:",
        suites=[CJ_PROVIDER_SUITE],
    ),
    dict(
        name="an-unknown-origin-defaults-to-china",
        refusal=(
            "Quoting from a warehouse that holds none of the stock prices a "
            "shipment that will not happen."
        ),
        path=CJ_PROVIDER,
        old="    origin = facts.get(\"origin\")",
        new="    origin = facts.get(\"origin\") or \"CN\"",
        suites=[CJ_PROVIDER_SUITE],
    ),
    dict(
        name="the-buyers-full-postal-code-is-sent-to-the-supplier",
        refusal=(
            "The cached answer must be a function of its key, and the key holds "
            "a postal prefix. One buyer's remote-area surcharge would be served "
            "to everyone sharing their prefix."
        ),
        path=CJ_PROVIDER,
        old="        mode = (destination or {}).get(\"shipping_mode\")",
        new=("        if (destination or {}).get(\"postal\"):\n"
             "            line[\"zip\"] = destination[\"postal\"]\n"
             "        mode = (destination or {}).get(\"shipping_mode\")"),
        suites=[CJ_PROVIDER_SUITE],
    ),
    dict(
        name="an-unreadable-answer-is-read-as-no-routes",
        refusal=(
            "An empty list is CJ stating the corridor is unserviceable, which is "
            "cached and blocks a checkout. A malformed body is us not knowing "
            "what CJ said."
        ),
        path=CJ_PROVIDER,
        old="            raise ValueError(\"CJ freight answer carried no quote list\")",
        new="            return []",
        suites=[CJ_PROVIDER_SUITE],
    ),
    dict(
        name="a-catalogue-gap-is-raised-as-a-supplier-failure",
        refusal=(
            "Counted against the provider, three undescribed variants open the "
            "circuit for every product CJ fulfils — a handful of bad rows taking "
            "the feature down for everything."
        ),
        path=CJ_PROVIDER,
        old=("        raise breaker.NotProviderEvidence(\n"
             "            REASON_NO_WEIGHT, \"the variant states no usable shipping weight\")"),
        new="        raise ValueError(\"the variant states no usable shipping weight\")",
        suites=[CJ_PROVIDER_SUITE, QUOTE_SUITE],
    ),

    # -- The cache_engine binding -------------------------------------------
    dict(
        name="a-value-redis-would-stringify-is-stored-anyway",
        refusal=(
            "cache_engine serializes with default=str, so a Decimal freight cost "
            "is a Decimal on every developer machine and \"12.34\" in the only "
            "environment that has Redis. Nothing raises in either place."
        ),
        path=STORE,
        old="    if json.loads(encoded) != value:",
        new="    if False:",
        suites=[STORE_SUITE],
    ),
    dict(
        name="an-encoder-error-escapes-as-a-raw-typeerror",
        refusal=(
            "quote.py treats anything that is not StoreRejected as a cache fault "
            "and swallows it, so an unencodable value would become a silent "
            "no-op instead of the stack trace a caller bug deserves. "
            "(Adding default=str here instead is deliberately not a mutation: "
            "the round-trip comparison below refuses the value either way, so "
            "that edit is equivalent rather than a defect.)"
        ),
        path=STORE,
        old="    except (TypeError, ValueError) as unencodable:",
        new="    except ValueError as unencodable:",
        suites=[STORE_SUITE],
    ),
    dict(
        name="a-nan-is-written-as-a-bare-json-token",
        refusal=(
            "json.dumps emits NaN and Infinity, which are not JSON. Redis stores "
            "them and any non-Python reader of that row chokes."
        ),
        path=STORE,
        old="        encoded = json.dumps(value, allow_nan=False)",
        new="        encoded = json.dumps(value)",
        suites=[STORE_SUITE],
    ),
    dict(
        name="a-zero-ttl-is-handed-to-a-store-that-rewrites-it-to-sixty",
        refusal=(
            "max(1, int(ttl_seconds or 60)): a caller asking for no caching gets "
            "a full minute of it, silently."
        ),
        path=STORE,
        old="    if ttl_seconds < 1:",
        new="    if ttl_seconds < 0:",
        suites=[STORE_SUITE],
    ),
    dict(
        name="a-fractional-ttl-is-truncated-below-its-own-stale-deadline",
        refusal=(
            "int() truncates, so the row is evicted before the stale_until "
            "recorded in its own body and the stale tier loses its tail — the "
            "exact failure store_ttl exists to prevent, reached from the far end."
        ),
        path=STORE,
        old="    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int):",
        new="    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, (int, float)):",
        suites=[STORE_SUITE],
    ),
    dict(
        name="a-boolean-ttl-passes-as-one-second",
        refusal="isinstance(True, int), so a flag in the TTL slot caches for a second.",
        path=STORE,
        old="    if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int):",
        new="    if not isinstance(ttl_seconds, int):",
        suites=[STORE_SUITE],
    ),
    dict(
        name="a-foreign-key-is-written-through",
        refusal=(
            "One flat cache_engine namespace serves presence and counters too. "
            "Reading a foreign key merely misses; writing one evicts whatever "
            "another subsystem had there and replaces it with a delivery envelope."
        ),
        path=STORE,
        old="    if not isinstance(key, str) or not key.startswith(KEY_PREFIX):",
        new="    if not isinstance(key, str):",
        suites=[STORE_SUITE],
    ),
    dict(
        name="the-key-guard-stops-tracking-the-schema-version",
        refusal=(
            "A version bump moves the key builders. If the guard does not move "
            "with them, entries from the old schema are writable again and a v1 "
            "body gets read as v2 — a wrong delivery date instead of a miss."
        ),
        path=STORE,
        old='KEY_PREFIX = f"delivery|{cache.VERSION}|"',
        new='KEY_PREFIX = "delivery|"',
        suites=[STORE_SUITE],
    ),
    dict(
        name="an-unreadable-cache-raises-into-the-page",
        refusal=(
            "store.get is on the path of every product page render. A propagating "
            "fault turns one flapping Redis into a 500 on every PDP at once, while "
            "the provider was reachable the whole time."
        ),
        path=QUOTE,
        old=("    try:\n"
             "        return store.get(key)\n"
             "    except Exception:  # noqa: BLE001 — an unreadable cache is a miss\n"
             "        return None"),
        new="    return store.get(key)",
        suites=[QUOTE_SUITE],
    ),
    dict(
        name="an-unwritable-cache-discards-a-computed-estimate",
        refusal=(
            "The answer is already correct by the time it is stored. Losing it to "
            "a cache fault makes a broken cache serve worse than no cache, which "
            "inverts the reason it is there."
        ),
        path=QUOTE,
        old=("    try:\n"
             "        store.set(key, written[\"envelope\"], written[\"ttl_seconds\"])\n"
             "    except Exception:  # noqa: BLE001 — an unwritable cache costs the next caller a call\n"
             "        pass"),
        new="    store.set(key, written[\"envelope\"], written[\"ttl_seconds\"])",
        suites=[QUOTE_SUITE],
    ),

    # -- A call that was never placed ---------------------------------------
    dict(
        name="an-abandoned-call-counts-against-the-provider",
        refusal=(
            "The provider was never contacted, so there is nothing here for it "
            "to be evidence about."
        ),
        path=BREAKER,
        old=("    except NotProviderEvidence:\n"
             "        with _LOCK:\n"
             "            _CIRCUITS[name] = released(_CIRCUITS.get(name), probe_started_at=probe_at)\n"
             "        raise\n"),
        new="",
        suites=[BREAKER_SUITE],
    ),
    dict(
        name="an-abandoned-probe-is-spent-rather-than-handed-back",
        refusal=(
            "One call per cooldown decides whether the provider is back. Spending "
            "it on a request never sent leaves the provider presumed down for "
            "another full cooldown, with nothing learned."
        ),
        path=BREAKER,
        old="    probe_at = claimed_at if verdict.get(\"probe\") else None",
        new="    probe_at = None",
        suites=[BREAKER_SUITE],
    ),
    dict(
        name="releasing-a-probe-clears-whoever-happens-to-hold-it",
        refusal=(
            "Ours expired and another caller claimed a fresh one. An "
            "unconditional clear admits two concurrent probes into a provider "
            "that is still failing."
        ),
        path=BREAKER,
        old="    if probe_started_at is None or row[\"probe_started_at\"] != probe_started_at:\n        return row",
        new="    if probe_started_at is None and False:\n        return row",
        suites=[BREAKER_SUITE],
    ),
    dict(
        name="a-catalogue-gap-is-reported-as-a-supplier-error",
        refusal=(
            "One invites a retry that cannot help; the other names the column to "
            "go and fill in."
        ),
        path=QUOTE,
        old=("        return _fetch_failed(REASON_VARIANT_INCOMPLETE, \"VARIANT_INCOMPLETE\",\n"
             "                             detail=incomplete.reason)"),
        new="        return _fetch_failed(REASON_PROVIDER_FAILED, \"PROVIDER_ERROR\")",
        suites=[QUOTE_SUITE],
    ),

    # -- The read-path breaker ----------------------------------------------
    #
    # The breaker's whole job is to stop calling a provider that is not
    # answering. Almost every way of getting it wrong replaces a provider outage
    # with a self-inflicted one that lasts longer, and none of them raises an
    # error — the wrong states are all reachable, internally consistent, and
    # invisible until someone asks why estimates stopped.
    #
    # One claim here has no mutation. `guard` reads its clock again when the call
    # finishes so a slow failure is dated correctly; the entry below expresses
    # that by freezing the clock, which is as close as source-level substitution
    # gets to the real mistake (passing a single `now` instead of a clock).
    dict(
        name="failure-threshold-ignored-so-one-blip-opens-the-circuit",
        refusal=(
            "Three consecutive unreachable calls open the circuit, not one. A "
            "single dropped connection must not degrade every shopper's estimate."
        ),
        path=BREAKER,
        old="    if failures < FAILURE_THRESHOLD:",
        new="    if False:",
        suites=[BREAKER_SUITE],
    ),
    dict(
        name="a-success-does-not-clear-the-failure-streak",
        refusal=(
            "Failures have to be consecutive. Counting cumulatively opens the "
            "circuit on a healthy provider eventually, because every provider "
            "fails sometimes over enough requests."
        ),
        path=BREAKER,
        old="    _moment(now)\n    return initial()",
        new="    _moment(now)\n    return _record(record)",
        suites=[BREAKER_SUITE],
    ),
    dict(
        name="a-recovery-does-not-forget-the-backoff",
        refusal=(
            "A success clears the trip count too. Keeping it makes a provider "
            "that recovers and later fails again back off as though it had never "
            "recovered."
        ),
        path=BREAKER,
        old="    _moment(now)\n    return initial()",
        new="    _moment(now)\n    return dict(initial(), trips=_record(record)[\"trips\"])",
        suites=[BREAKER_SUITE],
    ),
    dict(
        name="half-open-admits-every-caller-as-a-probe",
        refusal=(
            "Exactly one probe is admitted. Unlimited probing turns recovery into "
            "a stampede at the moment the provider comes back."
        ),
        path=BREAKER,
        old="        row = dict(row, probe_started_at=moment)",
        new="        row = dict(row)",
        suites=[BREAKER_SUITE],
    ),
    dict(
        name="a-claimed-probe-never-expires",
        refusal=(
            "A probe claim expires. A worker that dies holding the only probe "
            "otherwise leaves the circuit half-open and uncallable forever — an "
            "outage outliving the provider's, with no external cause to find."
        ),
        path=BREAKER,
        old="    held = probe is not None and moment - probe < PROBE_TIMEOUT_SECONDS",
        new="    held = probe is not None",
        suites=[BREAKER_SUITE],
    ),
    dict(
        name="a-failed-probe-reopens-at-the-same-cooldown",
        refusal=(
            "A failed probe reopens for longer. Reprobing at a fixed interval "
            "hammers a provider that is down for an hour."
        ),
        path=BREAKER,
        old=(
            "            \"opened_at\": moment,\n"
            "            \"trips\": row[\"trips\"] + 1,\n"
            "            \"probe_started_at\": None,"
        ),
        new=(
            "            \"opened_at\": moment,\n"
            "            \"trips\": row[\"trips\"],\n"
            "            \"probe_started_at\": None,"
        ),
        suites=[BREAKER_SUITE],
    ),
    dict(
        name="an-in-flight-failure-stacks-the-backoff",
        refusal=(
            "Failures landing after the circuit opened do not advance the trip "
            "count. They were all in flight together, so counting each one turns "
            "a ten-second blip into the maximum cooldown."
        ),
        path=BREAKER,
        old="    if before[\"state\"] == STATE_OPEN:",
        new="    if False:",
        suites=[BREAKER_SUITE],
    ),
    dict(
        name="the-cooldown-is-uncapped",
        refusal=(
            "The backoff is capped. An uncapped exponential reaches days, and a "
            "read path that gives up for a day is a removed feature."
        ),
        path=BREAKER,
        old="    return min(MAX_COOLDOWN_SECONDS, BASE_COOLDOWN_SECONDS * 2 ** (trips - 1))",
        new="    return BASE_COOLDOWN_SECONDS * 2 ** (trips - 1)",
        suites=[BREAKER_SUITE],
    ),
    dict(
        name="an-unreadable-record-fails-closed",
        refusal=(
            "Unreadable bookkeeping resolves to CLOSED. Wrongly allowing a call "
            "costs one bounded timeout; wrongly denying one costs the feature "
            "until a human notices."
        ),
        path=BREAKER,
        old="    if not isinstance(record, dict):\n        return initial()",
        new=(
            "    if not isinstance(record, dict):\n"
            "        return dict(initial(), opened_at=float(\"inf\"))"
        ),
        suites=[BREAKER_SUITE],
    ),
    dict(
        name="an-open-circuit-calls-the-provider-anyway",
        refusal=(
            "An open circuit places no call. A breaker that opens but still dials "
            "records the outage and pays for it too."
        ),
        path=BREAKER,
        old="    if not verdict[\"may_call\"]:",
        new="    if False:",
        suites=[BREAKER_SUITE],
    ),
    dict(
        name="not-calling-is-reported-as-a-provider-failure",
        refusal=(
            "Declining to call is its own signal. A caller that cannot tell it "
            "from a real provider error treats an untried request as a fresh "
            "negative answer, and caches it."
        ),
        path=BREAKER,
        old="        raise ProviderUnreachable(name, verdict[\"retry_after_seconds\"], verdict[\"reason\"])",
        new="        raise RuntimeError(verdict[\"reason\"])",
        suites=[BREAKER_SUITE],
    ),
    dict(
        name="a-slow-failure-is-dated-from-the-claim",
        refusal=(
            "The clock is read again when the call finishes. Dating a "
            "twenty-five second failure from its start shortens every cooldown by "
            "however long the provider took to not answer."
        ),
        path=BREAKER,
        old="    name = _key(key)\n    with _LOCK:\n        claimed_at = _moment(clock())",
        new=("    name = _key(key)\n    _frozen = _moment(clock())\n"
             "    clock = lambda: _frozen\n    with _LOCK:\n"
             "        claimed_at = _moment(clock())"),
        suites=[BREAKER_SUITE],
    ),
    dict(
        name="an-interrupt-counts-as-a-provider-failure",
        refusal=(
            "An interrupt or a shutdown is not evidence about the provider. "
            "Counting it opens circuits during a deploy."
        ),
        path=BREAKER,
        old="    except Exception:",
        new="    except BaseException:",
        suites=[BREAKER_SUITE],
    ),
    dict(
        name="one-global-circuit-for-every-supplier",
        refusal=(
            "Circuits are per supplier. One shared circuit lets a dead supplier "
            "stop estimates for a healthy one."
        ),
        path=BREAKER,
        old="    return key.strip()",
        new="    return \"global\"",
        suites=[BREAKER_SUITE],
    ),

    # -- The composition ----------------------------------------------------
    #
    # Every part below is separately correct. These mutations break the joins,
    # which is where a wrong delivery promise actually comes from — a surface's
    # tests cannot see a bad join because the surface is self-consistently wrong.
    dict(
        name="a-non-supplier-product-is-quoted-by-the-supplier",
        refusal=(
            "Only a supplier-fulfilled product gets a supplier estimate, and the "
            "check happens first so a feed of seller-shipped cards costs no calls."
        ),
        path=QUOTE,
        old="    if fulfillment.strip().upper() != FULFILLMENT_SUPPLIER:",
        new="    if False:",
        suites=[QUOTE_SUITE],
    ),
    dict(
        name="an-unresolved-destination-is-quoted-against-a-house-default",
        refusal=(
            "No destination is a reason, not a default. A plausible date computed "
            "for the wrong country is worse than no date."
        ),
        path=QUOTE,
        old="    if not isinstance(country, str) or not country.strip():",
        new="    if not isinstance(country, str) or not country.strip():\n        country = \"US\"\n    if False:",
        suites=[QUOTE_SUITE],
    ),
    dict(
        name="a-stale-answer-is-presented-as-a-fresh-quote",
        refusal=(
            "A stale estimate is served at lowered confidence. Presenting it as a "
            "live quote removes the only signal that the supplier is unreachable."
        ),
        path=QUOTE,
        old=(
            "        confidence=(estimate.CONFIDENCE_PROVIDER_CACHED if source == \"CACHE_STALE\"\n"
            "                    else estimate.CONFIDENCE_PROVIDER_QUOTED),"
        ),
        new="        confidence=estimate.CONFIDENCE_PROVIDER_QUOTED,",
        suites=[QUOTE_SUITE],
    ),
    dict(
        name="an-unreachable-supplier-is-reported-as-an-unserviceable-route",
        refusal=(
            "§55's distinction. 'We could not reach the supplier' is ours and "
            "transient; 'the supplier does not ship there' blocks a checkout. "
            "Conflating them also skips the stale fallback that exists for exactly "
            "the first case."
        ),
        path=QUOTE,
        old="        return _fetch_failed(REASON_PROVIDER_FAILED, \"PROVIDER_ERROR\")",
        new=(
            "        return {\"evidence\": None, \"negative\": True, \"source\": \"PROVIDER_ERROR\",\n"
            "                \"refusal\": estimate.unavailable(estimate.REASON_UNSUPPORTED)}"
        ),
        suites=[QUOTE_SUITE],
    ),
    dict(
        name="a-stale-positive-outranks-a-fresh-negative",
        refusal=(
            "A supplier that now ships nothing to a destination outranks a stored "
            "estimate that says ten days. Preferring the stale positive keeps a buy "
            "button live on an unshippable route."
        ),
        path=QUOTE,
        old="        elif fetched[\"refusal\"] is not None and fetched[\"negative\"]:",
        new=(
            "        elif (fetched[\"refusal\"] is not None and fetched[\"negative\"]\n"
            "              and not (allow_stale and stored[\"state\"] == cache.STATE_STALE)):"
        ),
        suites=[QUOTE_SUITE],
    ),
    dict(
        name="a-cached-negative-is-reread-as-transit-unknown",
        refusal=(
            "A stored negative carries no transit range, so re-reading it naively "
            "reports 'supplier transit unknown' — a different reason, a different "
            "investigation, and no longer a checkout block."
        ),
        path=QUOTE,
        old="    if (evidence or {}).get(\"supported\") is False:",
        new="    if False:",
        suites=[QUOTE_SUITE],
    ),
    dict(
        name="an-unserviceable-route-is-cached-with-a-stale-window",
        refusal=(
            "A negative gets freshness but no stale tier, because it blocks a "
            "checkout and may have been a credential problem."
        ),
        path=QUOTE,
        old="                stale_seconds=cache.UNSUPPORTED_STALE_SECONDS,",
        new="                stale_seconds=cache.ROUTE_STALE_SECONDS,",
        suites=[QUOTE_SUITE],
    ),
    dict(
        name="the-buyer-half-carries-the-freight-cost",
        refusal=(
            "§35: the buyer half has no cost field at all. Not a rounded one, not a "
            "truncated one — absent, so a future serializer cannot publish it."
        ),
        path=QUOTE,
        old="        \"shipping_price\": SHIPPING_FREE,",
        new=(
            "        \"shipping_price\": SHIPPING_FREE,\n"
            "        \"freight_total\": (route or {}).get(\"provider_total\"),"
        ),
        suites=[QUOTE_SUITE],
    ),
    dict(
        name="free-shipping-is-priced-at-zero-instead-of-declared-free",
        refusal=(
            "The customer-facing price is the word, not the number. A surface "
            "handed 0 eventually formats it as '$0.00' in a total column."
        ),
        path=QUOTE,
        old="SHIPPING_FREE = \"FREE\"",
        new="SHIPPING_FREE = 0",
        suites=[QUOTE_SUITE],
    ),
    dict(
        name="the-composed-estimate-is-presented-as-a-guarantee",
        refusal="§58 and §125: nothing here is guaranteed, at any layer.",
        path=QUOTE,
        old="        \"guaranteed\": False,",
        new="        \"guaranteed\": True,",
        suites=[QUOTE_SUITE],
    ),
    dict(
        name="the-breaker-is-present-but-never-consulted",
        refusal=(
            "The read path calls through the breaker. A composition that skips it "
            "is green in both suites while every shopper pays the full timeout for "
            "the whole duration of an outage."
        ),
        path=QUOTE,
        old="            key, lambda: breaker.guard(f\"delivery:{supplier}\", produce, clock=clock))",
        new="            key, produce)",
        suites=[QUOTE_SUITE],
    ),
    dict(
        name="one-circuit-for-every-supplier",
        refusal=(
            "The circuit is keyed per supplier. Sharing one lets a dead supplier "
            "blank a healthy supplier's estimates."
        ),
        path=QUOTE,
        old="breaker.guard(f\"delivery:{supplier}\"",
        new="breaker.guard(\"delivery\"",
        suites=[QUOTE_SUITE],
    ),
    dict(
        name="the-declared-transit-ceiling-is-dropped-in-composition",
        refusal=(
            "The ceiling reaches route selection. Dropped here, the surface's "
            "declared limit is silently not applied and a forty-day route is quoted."
        ),
        path=QUOTE,
        old="        return routing.select_route(options, policy=policy, ceiling_days=ceiling_days)",
        new="        return routing.select_route(options, policy=policy)",
        suites=[QUOTE_SUITE],
    ),

    # -- Which variant is being weighed -------------------------------------
    dict(
        name="a-weight-is-taken-from-the-first-variant-in-the-list",
        refusal=(
            "A variant is found by (pid, vid). Taking whichever row came first "
            "quotes a 2-kilo pair of boots for a 220-gram shirt, and a supplier "
            "reordering its own variant list silently reassigns every weight in "
            "the catalogue without changing a single byte on this machine."
        ),
        path=FACTS,
        old="        if not isinstance(row, dict) or row.get(\"pid\") != pid or row.get(\"vid\") != vid:",
        new="        if not isinstance(row, dict):",
        suites=[FACTS_SUITE],
    ),
    dict(
        name="an-unknown-variant-key-falls-back-to-the-listing-binding",
        refusal=(
            "A ref naming a variant the listing does not have yields no facts. "
            "Falling back to the listing-level binding answers confidently with "
            "whichever variant the importer happened to bind, which is the exact "
            "failure this whole module exists to avoid."
        ),
        path=FACTS,
        old=(
            "            if variant is None:\n"
            "                # The ref names a variant this listing does not have. Not the\n"
            "                # listing's default: quoting the wrong variant's parcel is the\n"
            "                # failure this whole path exists to avoid.\n"
            "                return None"),
        new=(
            "            if variant is None:\n"
            "                variant = {\"provider_variant_id\": None, \"sku\": None}"),
        suites=[FACTS_SUITE],
    ),
    dict(
        name="a-variant-row-overrides-the-binding-with-its-own-blanks",
        refusal=(
            "A variant row that carries no provider id keeps the listing's. "
            "Overwriting with the blank turns a describable variant into no facts "
            "at all, and the page stops answering for listings it can answer for."
        ),
        path=FACTS,
        old="            vid = _text(variant[\"provider_variant_id\"]) or vid",
        new="            vid = _text(variant[\"provider_variant_id\"])",
        suites=[FACTS_SUITE],
    ),

    # -- The four facts that must not be invented ----------------------------
    dict(
        name="an-origin-the-lookup-cannot-supply-becomes-china",
        refusal=(
            "Nothing local records a warehouse country, so an origin that is not "
            "known is not an origin. A default 'CN' puts a fabricated warehouse "
            "underneath every estimate the domain produces -- and it is the one "
            "line of this module that section 5 names outright."
        ),
        path=FACTS,
        old="    origin = origin_lookup(pid, vid)",
        new="    origin = origin_lookup(pid, vid) or \"CN\"",
        suites=[FACTS_SUITE],
    ),
    dict(
        name="absent-logistics-properties-become-ordinary",
        refusal=(
            "CJ routes batteries, liquids and magnets down different channels and "
            "rejects a freight request with no productProp at all. Substituting "
            "ORDINARY quotes the wrong service for precisely the goods where the "
            "difference matters, and it quotes it successfully."
        ),
        path=FACTS,
        old=(
            "    if not properties:\n"
            "        # CJ routes batteries, liquids and magnets down different channels and\n"
            "        # its freight endpoint rejects a request with no productProp at all.\n"
            "        # Substituting \"ORDINARY\" would quote the wrong service for precisely\n"
            "        # the goods where the difference matters.\n"
            "        return None"),
        new=(
            "    if not properties:\n"
            "        properties = [\"ORDINARY\"]"),
        suites=[FACTS_SUITE],
    ),
    dict(
        name="a-string-of-properties-is-iterated-one-character-at-a-time",
        refusal=(
            "logistics_properties is a list. A payload holding the bare string "
            "\"ORDINARY\" iterates into eight single-letter properties, every one "
            "of which is a non-empty str and therefore passes the element filter. "
            "The freight request then carries productProp 'O', 'R', 'D'... and "
            "nothing in this module ever notices."
        ),
        path=FACTS,
        old="    if not isinstance(declared, (list, tuple)):",
        new="    if False:",
        suites=[FACTS_SUITE],
    ),
    dict(
        name="a-weight-of-zero-is-a-parcel",
        refusal=(
            "normalize._cj_variant leaves weight_grams null for a variant CJ did "
            "not weigh, and zero is not a parcel. Accepting it sends CJ a "
            "weightless shipment and takes back whatever it quotes for one."
        ),
        path=FACTS,
        old="        if not math.isfinite(grams) or grams <= 0:",
        new="        if not math.isfinite(grams):",
        suites=[FACTS_SUITE],
    ),
    dict(
        name="a-boolean-weight-passes-as-one-gram-at-the-facts-resolver",
        refusal=(
            "bool is a subclass of int, so True satisfies isinstance(x, int) and "
            "weighs one gram. The bool check is not defensive noise: it is the "
            "only thing between a truthy flag in a payload and a one-gram parcel."
        ),
        path=FACTS,
        old="        if isinstance(grams, bool) or not isinstance(grams, (int, float)):",
        new="        if not isinstance(grams, (int, float)):",
        suites=[FACTS_SUITE],
    ),

    # -- What the read is allowed to touch and to return ---------------------
    dict(
        name="any-snapshot-kind-is-mined-for-variants",
        refusal=(
            "An inventory or shipping snapshot has neither a variant list nor "
            "logistics properties. Reading one reports 'this variant has no "
            "weight' for a listing whose weight is recorded perfectly well one "
            "row over, and the repair goes looking in the wrong table."
        ),
        path=FACTS,
        old="    if row is None or row[\"kind\"] != \"product\":",
        new="    if row is None:",
        suites=[FACTS_SUITE],
    ),
    dict(
        name="the-snapshot-body-leaves-with-the-facts",
        refusal=(
            "This read has no authenticated actor -- a visitor on a product page "
            "is not in a merchant scope -- and the snapshot payload holds CJ's "
            "wholesale prices beside the weights. Returning the body puts "
            "supplier cost economics one careless caller away from an "
            "unauthenticated page, which sections 33-35 forbid outright."
        ),
        path=FACTS,
        old="    return {\n        \"vid\": vid,",
        new="    return {\n        \"snapshot\": payload,\n        \"vid\": vid,",
        suites=[FACTS_SUITE],
    ),

    # -- The stocked-origin tier ---------------------------------------------
    dict(
        name="an-unstocked-warehouse-is-an-eligible-origin",
        refusal=(
            "A warehouse nobody counted is UNKNOWN, which is not stock, and an "
            "explicit zero is OUT_OF_STOCK. Quoting freight from a country that "
            "holds none of the units prices a shipment that will not happen, and "
            "CJ answers the request without complaint because it is well formed."
        ),
        path=ORIGIN,
        old="            if not isinstance(warehouse, dict) or warehouse.get(\"state\") != IN_STOCK:",
        new="            if not isinstance(warehouse, dict):",
        suites=[ORIGIN_SUITE],
    ),
    dict(
        name="two-stocked-warehouses-resolve-in-reply-order",
        refusal=(
            "The chosen origin travels into the route cache key. Taking whichever "
            "warehouse CJ happened to list first does not merely vary the estimate: "
            "it splits one product's traffic across two route keys and halves the "
            "hit rate for as long as both warehouses hold stock."
        ),
        path=ORIGIN,
        old=(
            "    usable = sorted({c.strip().upper() for c in countries\n"
            "                     if isinstance(c, str) and len(c.strip()) == 2})"),
        new=(
            "    usable = [c.strip().upper() for c in countries\n"
            "              if isinstance(c, str) and len(c.strip()) == 2]"),
        suites=[ORIGIN_SUITE],
    ),
    dict(
        name="the-tier-is-keyed-on-the-variant-instead-of-the-product",
        refusal=(
            "CJ's endpoint takes a pid and returns every variant's warehouses in "
            "one ten-point reply. A variant-keyed tier turns that into one call per "
            "variant and stores the same body under each key -- the fan-out section "
            "18 forbids, reintroduced one layer below where it was fixed. Every "
            "estimate stays correct, so the only symptom is a points bill."
        ),
        path=ORIGIN,
        old="    key = cache.origin_key(supplier=SUPPLIER_NAME, product_ref=pid)",
        new="    key = cache.origin_key(supplier=SUPPLIER_NAME, product_ref=f\"{pid}:{vid}\")",
        suites=[ORIGIN_SUITE],
    ),
    dict(
        name="the-variant-is-passed-to-an-endpoint-that-answers-products",
        refusal=(
            "Passing a vid makes the adapter raise VARIANT_PRODUCT_MISMATCH when "
            "CJ's reply does not mention it, turning 'we cannot say where this "
            "variant ships from' -- a product page that declines to guess -- into a "
            "supplier error counted against the circuit, which then withholds every "
            "other product's estimate too."
        ),
        path=ORIGIN,
        old="        answer = adapter.get_inventory(pid)",
        new="        answer = adapter.get_inventory(pid, pid)",
        suites=[ORIGIN_SUITE],
    ),
    dict(
        name="the-origin-tier-outlives-the-route-tier-resting-on-it",
        refusal=(
            "The route tier's fifteen minutes is justified by 'the thing that "
            "actually invalidates it is a warehouse change'. A route quote cannot "
            "detect one faster than the origin it was keyed on is re-asked, so a "
            "longer origin tier makes that justification decorative: every route "
            "refresh for the next hour re-asks with the same stale warehouse."
        ),
        path=CACHE,
        old="ORIGIN_FRESH_SECONDS = ROUTE_FRESH_SECONDS",
        new="ORIGIN_FRESH_SECONDS = 6 * 3600",
        suites=[ORIGIN_SUITE],
    ),
    dict(
        name="the-origin-key-admits-the-buyers-destination",
        refusal=(
            "Where a supplier's stock sits does not vary by who is asking. "
            "Admitting a destination fragments a tier meant to be shared by every "
            "buyer of the product, and multiplies one ten-point call by the number "
            "of countries the product is viewed from."
        ),
        path=CACHE,
        old="        _variant(product_ref, \"product_ref\"),\n    ))",
        new="        _variant(product_ref, \"product_ref\"),\n        \"US\",\n    ))",
        suites=[ORIGIN_SUITE, CACHE_SUITE],
    ),
    dict(
        name="stock-levels-are-cached-beside-the-warehouse-country",
        refusal=(
            "This body is written to a cache an unauthenticated product page "
            "reads. Where the stock is, is the question the tier answers; how many "
            "units a merchant holds is not, and sections 33-35 forbid the second "
            "travelling with the first."
        ),
        path=ORIGIN,
        old="        origins[row_vid.strip()] = countries",
        new="        origins[row_vid.strip()] = countries + [str(row.get(\"warehouses\"))]",
        suites=[ORIGIN_SUITE],
    ),
    dict(
        name="an-unreadable-inventory-body-is-recorded-as-a-success",
        refusal=(
            "The shape checks sit inside the guarded producer. Moved after guard "
            "returns they raise the same exception and read identically in a test, "
            "but the circuit has already recorded a success -- so a supplier "
            "answering every call with something unreadable is never backed off "
            "from, and the points bill runs indefinitely."
        ),
        path=ORIGIN,
        old=(
            "    def call():\n"
            "        answer = adapter.get_inventory(pid)\n"
            "        if not isinstance(answer, dict):\n"
            "            raise ValueError(\"CJ inventory answer was not a body\")\n"
            "        variants = answer.get(\"variants\")\n"
            "        if not isinstance(variants, list):\n"
            "            raise ValueError(\"CJ inventory answer carried no variant list\")\n"
            "        return variants\n"
            "\n"
            "    return breaker.guard(CIRCUIT, call, clock=clock)"),
        new=(
            "    answer = breaker.guard(CIRCUIT, lambda: adapter.get_inventory(pid), clock=clock)\n"
            "    if not isinstance(answer, dict):\n"
            "        raise ValueError(\"CJ inventory answer was not a body\")\n"
            "    variants = answer.get(\"variants\")\n"
            "    if not isinstance(variants, list):\n"
            "        raise ValueError(\"CJ inventory answer carried no variant list\")\n"
            "    return variants"),
        suites=[ORIGIN_SUITE],
    ),
    dict(
        name="the-inventory-read-shares-the-freight-circuit",
        refusal=(
            "This runs inside quote.py's freight guard and breaker.claim grants one "
            "half-open probe per key. Sharing the name means the inner claim is "
            "denied inside the outer producer, where guard counts it as fresh "
            "evidence against the provider -- so no probe can succeed and the "
            "circuit never closes. Inverted, an inner success calls succeeded(), "
            "which returns a fully closed circuit, declaring freight healthy on "
            "the strength of an inventory read."
        ),
        path=ORIGIN,
        old="CIRCUIT = f\"delivery:{SUPPLIER_NAME}:inventory\"",
        new="CIRCUIT = f\"delivery:{SUPPLIER_NAME}\"",
        suites=[ORIGIN_SUITE],
    ),
    dict(
        name="a-projection-defect-is-blamed-on-the-supplier",
        refusal=(
            "A bug in our own parsing is not evidence about CJ. Counted against "
            "its circuit, our mistake withholds every other product's estimate -- "
            "and the swallow turns a stack trace into a domain-wide silence with "
            "no signal anywhere."
        ),
        path=ORIGIN,
        old="    origins = _origins(variants)",
        new=(
            "    try:\n"
            "        origins = _origins(variants)\n"
            "    except Exception:\n"
            "        return None"),
        suites=[ORIGIN_SUITE],
    ),
    dict(
        name="an-open-circuit-still-costs-a-supplier-call",
        refusal=(
            "An open circuit is the whole point of having one. A call placed "
            "anyway spends points against a provider that is already failing, "
            "which is how a rate limit becomes a suspension."
        ),
        path=ORIGIN,
        old="    return breaker.guard(CIRCUIT, call, clock=clock)",
        new="    return call()",
        suites=[ORIGIN_SUITE],
    ),

    # ---- entry.py: the composition -------------------------------------------
    # Every mutation below is a *seam* defect. Each one leaves all ten modules
    # individually correct and every other suite green, which is exactly why they
    # are the most dangerous entries in this file: nothing in the layers below can
    # see a join that was never made.
    dict(
        name="the-warehouse-never-reaches-the-cache-key",
        refusal=(
            "The origin is resolved two layers down and the route cache key is "
            "built one layer up, so nothing but this line joins them. Dropped, "
            "every quote is filed under origin=None: each estimate is still "
            "correct when computed, and stock moving CN->US keeps serving the CN "
            "quote until the TTL expires while the right answer sits in the "
            "origin tier. A faster route than the warehouse can actually ship."
        ),
        path=ENTRY,
        old="        origin=origin_code,",
        new="        origin=None,",
        suites=[ENTRY_SUITE],
    ),
    dict(
        name="the-shipping-mode-is-honoured-but-not-keyed",
        refusal=(
            "The provider reads the mode off destination[\"shipping_mode\"]; "
            "route_key takes it as its own argument. Without the bridge EXPRESS "
            "and STANDARD share one entry, so whichever was asked first answers "
            "for both -- an express buyer quoted a standard window, or the "
            "reverse, with no refusal anywhere."
        ),
        path=ENTRY,
        old=(
            "        shipping_mode=(shipping_mode if shipping_mode is not None\n"
            "                       else (destination or {}).get(\"shipping_mode\")),"),
        new="        shipping_mode=shipping_mode,",
        suites=[ENTRY_SUITE],
    ),
    dict(
        name="the-facts-are-resolved-again-inside-the-provider",
        refusal=(
            "Resolving twice is not merely a second database read. The two "
            "resolutions can straddle an origin-tier expiry, so the origin that "
            "went into the cache key is not the origin the freight was quoted "
            "from -- an entry filed under a warehouse it was never priced from."
        ),
        path=ENTRY,
        old="            facts=_memo(variant_ref, facts, witness=witness, connect=connect),",
        new="            facts=variant_facts.resolver(origin_lookup=witness, connect=connect),",
        suites=[ENTRY_SUITE],
    ),
    dict(
        name="the-memo-answers-for-any-variant-asked",
        refusal=(
            "A memo that ignores which reference it was asked about quotes one "
            "variant's parcel for another: the boots' freight computed from the "
            "shirt's weight. Same product, same page, wrong number."
        ),
        path=ENTRY,
        old=(
            "        resolved = facts if ref == variant_ref else variant_facts.describe(\n"
            "            ref, origin_lookup=witness, connect=connect)"),
        new="        resolved = facts",
        suites=[ENTRY_SUITE],
    ),
    dict(
        name="a-seller-shipped-product-still-costs-a-read",
        refusal=(
            "Only CJ-backed listings get CJ-derived estimates. A loosened gate "
            "reads the database and can spend a supplier call for a product this "
            "supplier does not ship -- and the answer is refused anyway, so the "
            "cost buys nothing."
        ),
        path=ENTRY,
        old="    if not isinstance(fulfillment, str) or fulfillment.strip().upper() != quote.FULFILLMENT_SUPPLIER:",
        new="    if fulfillment is None:",
        suites=[ENTRY_SUITE],
    ),
    dict(
        name="a-visitor-with-no-country-still-costs-a-supplier-call",
        refusal=(
            "route_key requires a country. Without one there is no route to "
            "quote, so asking CJ is a guaranteed-wasted point spend on a page "
            "that will show the unknown-destination state regardless."
        ),
        path=ENTRY,
        old="    return isinstance(country, str) and bool(country.strip())",
        new="    return True",
        suites=[ENTRY_SUITE],
    ),
    dict(
        name="the-adapter-is-built-before-anything-needs-it",
        refusal=(
            "Hydration reads the credential vault, may refresh a token, spends a "
            "verification call, and writes the merchant's connection status and "
            "quota. Eager, a warm product page -- which contains no supplier call "
            "at all -- performs one, and a buyer's page load can mark a "
            "merchant's connection degraded."
        ),
        path=ENTRY,
        old="        self._built: Any = None",
        new="        self._built: Any = source()",
        suites=[ENTRY_SUITE],
    ),
    dict(
        name="the-adapter-is-rebuilt-for-every-question",
        refusal=(
            "Inventory and freight are two questions to one supplier. Rebuilding "
            "between them doubles the hydration cost of every cold read, "
            "including the verification call and the status write."
        ),
        path=ENTRY,
        old="        if self._built is None:\n            try:",
        new="        if True:\n            try:",
        suites=[ENTRY_SUITE],
    ),
    dict(
        name="a-connection-failure-is-counted-against-the-supplier",
        refusal=(
            "A vault that will not open is not evidence about CJ -- no request "
            "was ever formed. Counted, our own credential state trips CJ's "
            "circuit and stops quoting every other product on the platform. "
            "NotProviderEvidence exists for precisely this."
        ),
        path=ENTRY,
        old=(
            "                raise breaker.NotProviderEvidence(\n"
            "                    REASON_CONNECTION_UNAVAILABLE,\n"
            "                    \"the supplier connection could not be opened\",\n"
            "                ) from exc"),
        new="                raise",
        suites=[ENTRY_SUITE],
    ),
    dict(
        name="the-credential-exception-text-travels-into-the-refusal",
        refusal=(
            "The raised exception comes from a module that handles secrets, and "
            "the refusal detail is a field that reaches logs and, in some states, "
            "a response. A token fragment in an API key error message becomes a "
            "token fragment in a delivery refusal."
        ),
        path=ENTRY,
        old="                    \"the supplier connection could not be opened\",",
        new="                    str(exc),",
        suites=[ENTRY_SUITE],
    ),
    dict(
        name="a-source-that-produces-nothing-is-treated-as-an-adapter",
        refusal=(
            "A source returning None means no connection exists for this "
            "listing. Accepted as an adapter, the next line is an "
            "AttributeError on None -- which the breaker counts as a provider "
            "failure, blaming CJ for a listing with no connection."
        ),
        path=ENTRY,
        old=(
            "            if built is None:\n"
            "                self.unavailable = True\n"
            "                raise breaker.NotProviderEvidence(\n"
            "                    REASON_CONNECTION_UNAVAILABLE,\n"
            "                    \"no supplier connection is available for this listing\",\n"
            "                )"),
        new="            pass",
        suites=[ENTRY_SUITE],
    ),
    dict(
        name="a-blank-warehouse-country-reads-as-a-warehouse",
        refusal=(
            "variant_facts strips the origin before accepting it, so a "
            "whitespace country yields absent facts. Read as answered, the "
            "witness reports a warehouse that was named and the repair goes "
            "hunting for a missing local column instead of a blank supplier row."
        ),
        path=ENTRY,
        old="        self._answered = bool(found.strip() if isinstance(found, str) else found)",
        new="        self._answered = bool(found)",
        suites=[ENTRY_SUITE],
    ),
    dict(
        name="a-stock-gap-outranks-the-connection-that-caused-it",
        refusal=(
            "A connection that would not open also makes the origin lookup "
            "answer nothing, so the stock reading is a symptom. Reported first, "
            "every credential outage on the platform presents as thousands of "
            "products simultaneously stocked nowhere."
        ),
        path=ENTRY,
        old=(
            "        if self._deferred.unavailable:\n"
            "            # Checked first: a connection that would not open also makes the origin\n"
            "            # lookup answer nothing, so the stock reading is a symptom here and\n"
            "            # reporting it would send the repair to the warehouse.\n"
            "            return REASON_CONNECTION_UNAVAILABLE\n"
            "        if self._asked and not self._answered:\n"
            "            return REASON_ORIGIN_UNKNOWN"),
        new=(
            "        if self._asked and not self._answered:\n"
            "            return REASON_ORIGIN_UNKNOWN\n"
            "        if self._deferred.unavailable:\n"
            "            return REASON_CONNECTION_UNAVAILABLE"),
        suites=[ENTRY_SUITE],
    ),
    dict(
        name="a-local-catalogue-gap-is-reported-as-a-stock-gap",
        refusal=(
            "describe returns None for a missing local column without ever "
            "reaching the origin lookup. Without the asked check, that never-run "
            "lookup reads as one that produced nothing, and a listing with no "
            "weight on file is reported as a product stocked in no warehouse -- "
            "an actionable reason that is wrong, which is worse than a vague one."
        ),
        path=ENTRY,
        old="        if self._asked and not self._answered:",
        new="        if not self._answered:",
        suites=[ENTRY_SUITE],
    ),
    dict(
        name="the-entry-point-accepts-an-actor",
        refusal=(
            "A delivery estimate is the platform answering about its own "
            "listing with platform-held credentials. An actor parameter is the "
            "first step toward authorizing a visitor inside a merchant scope, "
            "and toward a buyer's identity appearing in a supplier cache key."
        ),
        path=ENTRY,
        old="    adapter_source: Callable[[], Any],",
        new="    adapter_source: Callable[[], Any],\n    actor_user_id: Optional[int] = None,",
        suites=[ENTRY_SUITE],
    ),
    dict(
        name="the-composition-default-policy-drifts-from-the-domains",
        refusal=(
            "Two places naming a default route policy is one place that can name "
            "a different one. Drifted to FASTEST, every caller that does not pass "
            "a policy silently buys the most expensive route while routing.py's "
            "own tests still prove CHEAPEST_ACCEPTABLE is the default."
        ),
        path=ENTRY,
        old="    policy: str = routing.POLICY_CHEAPEST_ACCEPTABLE,",
        new="    policy: str = routing.POLICY_FASTEST,",
        suites=[ENTRY_SUITE],
    ),
    dict(
        name="the-promise-padding-gets-a-default",
        refusal=(
            "handling and buffer_days are the two inputs that move a date "
            "*earlier* when they lapse, and a default is how they lapse silently: "
            "a caller that forgot them gets a plausible window with no padding "
            "instead of a TypeError. Every mutation in this file that moves a "
            "promise earlier is a promise the platform cannot keep."
        ),
        path=ENTRY,
        old="    buffer_days,\n    adapter_source",
        new="    buffer_days=0,\n    adapter_source",
        suites=[ENTRY_SUITE],
    ),

    # ---- destination.py: what it will guess, and what it must not invent -----
    dict(
        name="an-unknown-destination-gets-a-default-country",
        refusal=(
            "The single most damaging defect this module can have. A default "
            "invents a promise for every buyer it is wrong about, and it is "
            "invisible in testing because the developer is nearly always in the "
            "country that got defaulted to."
        ),
        path=DESTINATION,
        # Anchored on the end of `resolve`'s tier walk, not on the bare `_record`
        # call: `policy_destination` was added later and answers nothing the same
        # way three more times, so the short anchor now resolves 4x and this entry
        # would report AMBIGUOUS -- proving nothing while reading as coverage.
        old="            return _record(tier, country, postal)\n    return _record(TIER_NONE, None, None)",
        new="            return _record(tier, country, postal)\n    return _record(TIER_EDGE, \"US\", None)",
        suites=[DESTINATION_SUITE],
    ),
    dict(
        name="a-three-letter-code-is-truncated-into-another-country",
        refusal=(
            "Truncation survives review because it is right most of the time: "
            "USA->US, GBR->GB, DEU->DE. CHN->CH is Switzerland, and Switzerland "
            "is a supported destination, so nothing downstream refuses it."
        ),
        path=DESTINATION,
        old="    if len(cleaned) != _CODE_LENGTH or not cleaned.isalpha():\n        return None",
        new="    cleaned = cleaned[:_CODE_LENGTH]\n    if not cleaned.isalpha():\n        return None",
        suites=[DESTINATION_SUITE],
    ),
    dict(
        name="a-past-orders-postal-code-is-reused-for-a-browse",
        refusal=(
            "Country is worth inheriting; postal precision is not. Inherited, a "
            "fragment of the address a buyer gave for one purchase ends up in the "
            "cache key of a page they are merely browsing -- for a small accuracy "
            "gain on the domestic tail of a window the international leg "
            "dominates."
        ),
        path=DESTINATION,
        old="            return country, None\n    return None, None",
        new="            return country, _postal(details.get(\"address_postal_code\"))\n    return None, None",
        suites=[DESTINATION_SUITE],
    ),
    dict(
        name="a-tier-that-may-not-carry-a-postal-code-carries-one",
        refusal=(
            "POSTAL_TIERS is enforced on the record, not trusted at the call "
            "site, because everything downstream reads the record. Dropped, a "
            "'deliver to' picker that happens to pass a postal code earns a "
            "postal-precise promise from a country-precise guess."
        ),
        path=DESTINATION,
        old="            if tier not in POSTAL_TIERS:\n                postal = None",
        new="            pass",
        suites=[DESTINATION_SUITE],
    ),
    dict(
        name="precision-is-claimed-rather-than-derived",
        refusal=(
            "A country-only answer labelled POSTAL is a country-only answer with "
            "a postal-precise promise attached. Derived from what was found, the "
            "label cannot disagree with it."
        ),
        path=DESTINATION,
        old="    if country and postal:\n        precision = PRECISION_POSTAL",
        new="    if country:\n        precision = PRECISION_POSTAL",
        suites=[DESTINATION_SUITE],
    ),
    dict(
        name="a-postal-code-with-no-country-is-treated-as-a-place",
        refusal=(
            "route_key accepts a postal prefix and requires a country, so a "
            "postal-only answer either raises at the key or -- worse, if the "
            "requirement ever loosens -- files an entry under a destination it "
            "cannot name."
        ),
        path=DESTINATION,
        old="    return (country, postal) if country else (None, None)",
        new="    return country, postal",
        suites=[DESTINATION_SUITE],
    ),
    dict(
        name="an-unpaid-checkout-address-is-inherited",
        refusal=(
            "An abandoned checkout holds an address no purchase completed with, "
            "and one plausible reason for abandoning it is that the address was "
            "wrong. Inheriting it quotes to a place the buyer rejected."
        ),
        path=DESTINATION,
        old="                \"WHERE buyer_user_id = ? AND status = ? \"",
        new="                \"WHERE buyer_user_id = ? AND status != ? \"",
        suites=[DESTINATION_SUITE],
    ),
    dict(
        name="another-buyers-address-is-inherited",
        refusal=(
            "Silent in every way that matters: the page still renders a plausible "
            "window, the number is internally consistent, and the only evidence "
            "is that it is somebody else's country."
        ),
        path=DESTINATION,
        old="                \"WHERE buyer_user_id = ? AND status = ? \"\n"
            "                \"ORDER BY id DESC LIMIT ?\",\n"
            "                (buyer_user_id, _PAID, _HISTORY_DEPTH),",
        new="                \"WHERE status = ? \"\n"
            "                \"ORDER BY id DESC LIMIT ?\",\n"
            "                (_PAID, _HISTORY_DEPTH),",
        suites=[DESTINATION_SUITE],
    ),
    dict(
        name="a-boolean-buyer-id-is-queried-with",
        refusal=(
            "`True` is an int in Python, so an unguarded check queries user 1 -- "
            "who on this deployment is the only seller, and therefore the one "
            "account whose order history is not empty."
        ),
        path=DESTINATION,
        old="    if not isinstance(buyer_user_id, int) or isinstance(buyer_user_id, bool):",
        new="    if not isinstance(buyer_user_id, int):",
        suites=[DESTINATION_SUITE],
    ),
    dict(
        name="a-history-read-failure-fails-the-product-page",
        refusal=(
            "This tier is a hint. A database that cannot answer it must produce "
            "the next tier, not a product page that will not render -- the "
            "estimate is one line on a page that has a price, a photo and an add "
            "to cart button on it."
        ),
        path=DESTINATION,
        old="    except Exception:\n        return None, None\n\n    for row in rows:",
        new="    except Exception:\n        raise\n\n    for row in rows:",
        suites=[DESTINATION_SUITE],
    ),
    dict(
        name="the-history-scan-is-unbounded",
        refusal=(
            "This runs on a product page. The cost of not finding a shipped order "
            "is falling through to the next tier, which is harmless, and that is "
            "exactly what makes an unbounded scan of a buyer's whole transaction "
            "history unjustifiable."
        ),
        path=DESTINATION,
        old="_HISTORY_DEPTH = 5",
        new="_HISTORY_DEPTH = 100000",
        suites=[DESTINATION_SUITE],
    ),
    dict(
        name="a-client-supplied-country-header-is-believed",
        refusal=(
            "With no trusted edge configured -- which is this deployment's actual "
            "state -- reading the header directly lets any client name its own "
            "country. A wrong estimate is not an authorization bug, but a "
            "deployment that trusts client headers is how one starts."
        ),
        path=DESTINATION,
        old="        return _code(client_address.client_country(headers)), None",
        new="        return _code(headers.get(client_address.GEO_HEADER_ENV) or \"\"), None",
        suites=[DESTINATION_SUITE],
    ),
    dict(
        name="the-geo-tier-being-switched-off-is-indistinguishable-from-an-unplaceable-visitor",
        refusal=(
            "Both produce TIER_NONE and they call for completely different "
            "actions: one is a visitor the edge could not place, the other is a "
            "variable nobody set. Conflated, a missing environment variable is "
            "diagnosed as a population of unlocatable buyers."
        ),
        path=DESTINATION,
        old="    return bool(client_address.geo_header_name())",
        new="    return False",
        suites=[DESTINATION_SUITE],
    ),
    dict(
        name="a-higher-tier-that-yields-nothing-stops-the-search",
        refusal=(
            "An unparseable stated country must not shadow a usable source below "
            "it. Stopping on the first tier that was *offered* rather than the "
            "first that *answered* means one malformed input costs the estimate "
            "entirely."
        ),
        path=DESTINATION,
        old="        country, postal = found()\n        if country:",
        new="        country, postal = found()\n        if True:",
        suites=[DESTINATION_SUITE],
    ),
    dict(
        name="the-tier-label-travels-in-the-destination-handed-to-the-supplier",
        refusal=(
            "quote_delivery passes the whole destination dict to the provider, so "
            "an extra key here is a field sent to CJ -- and the record also holds "
            "what was derived from a buyer's order history."
        ),
        path=DESTINATION,
        old="        \"destination\": {\"country\": country, \"postal\": postal},",
        new="        \"destination\": {\"country\": country, \"postal\": postal, \"tier\": tier},",
        suites=[DESTINATION_SUITE],
    ),

    # ---- listing.py: who ships it, and whether a buyer may ask ---------------
    dict(
        name="the-provider-is-read-without-the-fulfillment-mode",
        refusal=(
            "The most tempting inference available to this module, and the schema "
            "says so in its own comment: a product can be imported from CJ and "
            "stocked in the seller's own garage. Such a listing has a CJ source "
            "row, CJ ids and a real weight, so CJ prices a shipment it is never "
            "going to make and the buyer is shown a warehouse-to-door window for a "
            "parcel the seller is about to post themselves."
        ),
        path=LISTING,
        old="    return provider.lower() == SUPPLIER_NAME and mode.upper() == DROPSHIP",
        new="    return provider.lower() == SUPPLIER_NAME",
        suites=[LISTING_SUITE],
    ),
    dict(
        name="the-declaration-is-read-case-sensitively",
        refusal=(
            "These two columns are written by several callers and one of them "
            "stores the mode lowercase. A case-sensitive read silently reclassifies "
            "every row that caller wrote as seller-shipped, so the estimate "
            "disappears for a population nobody chose."
        ),
        path=LISTING,
        old="    return provider.lower() == SUPPLIER_NAME and mode.upper() == DROPSHIP",
        new="    return provider == SUPPLIER_NAME and mode == DROPSHIP",
        suites=[LISTING_SUITE],
    ),
    dict(
        name="the-supplier-name-is-restated-rather-than-borrowed",
        refusal=(
            "variant_facts and this module must agree about which provider value "
            "means CJ. A second spelling lets a listing be declared "
            "supplier-fulfilled here and then found unparseable there -- an "
            "estimate that refuses itself one layer down."
        ),
        path=LISTING,
        old="SUPPLIER_NAME = variant_facts.SUPPLIER_NAME",
        new="SUPPLIER_NAME = \"CJ\"",
        suites=[LISTING_SUITE],
    ),
    dict(
        name="a-source-row-predating-the-mode-column-inherits-the-ddl-default",
        refusal=(
            "fulfillment_mode arrives through add_columns_if_missing and its DDL "
            "default is DROPSHIP, so 'assume the default' reads as faithfulness to "
            "the schema. The default applies to rows written after the column "
            "existed; these rows were not, and the merchant never declared "
            "anything."
        ),
        path=LISTING,
        old="    if provider is None or mode is None:\n        return False",
        new="    if provider is None:\n        return False\n    mode = mode or DROPSHIP",
        suites=[LISTING_SUITE],
    ),
    dict(
        name="a-listing-with-no-source-row-is-assumed-supplier-fulfilled",
        refusal=(
            "48 says fulfillment is a declared fact and never inferred. No row is "
            "no declaration, and the safe reading of no declaration is that the "
            "seller posts it -- this domain holds no carrier relationship for that "
            "parcel and has nothing to say about how long it takes."
        ),
        path=LISTING,
        old="    if source is None:\n        return False",
        new="    if source is None:\n        return True",
        suites=[LISTING_SUITE],
    ),
    dict(
        name="a-source-row-outranks-a-listing-that-posts-nothing",
        refusal=(
            "Freight quoted for a download. The tie-break is not arbitrary: the "
            "supplier dispatcher already refuses a non-physical order, so this "
            "precedence promises a delivery the rest of the platform is built to "
            "decline."
        ),
        path=LISTING,
        old="    if _effective_type(row) != PHYSICAL:",
        new="    if _effective_type(row) != PHYSICAL and not _supplier_fulfilled(source):",
        suites=[LISTING_SUITE],
    ),
    dict(
        name="the-legacy-product-type-outranks-the-listing-type",
        refusal=(
            "listing_type was added after product_type, so the newer column is the "
            "one a seller edits and the older one is the stale copy. Swapped, a "
            "listing retyped from digital to physical keeps its old classification "
            "and gets no estimate at all."
        ),
        path=LISTING,
        old="        _maybe(row, \"listing_type\"), _maybe(row, \"product_type\"))",
        new="        _maybe(row, \"product_type\"), _maybe(row, \"listing_type\"))",
        suites=[LISTING_SUITE],
    ),
    dict(
        name="an-unpublished-listing-is-quoted",
        refusal=(
            "Nothing else in this package reads marketplace_listings, so without "
            "this predicate the engine quotes drafts, rejected listings and "
            "suspended sellers -- at a supplier call per unpublished product, which "
            "is how a seller's private drafts end up warming CJ's cache."
        ),
        path=LISTING,
        old="            f\"WHERE l.id = ? AND {lifecycle.public_sql('l', 'ms')} LIMIT 1\",",
        new="            f\"WHERE l.id = ? LIMIT 1\",",
        suites=[LISTING_SUITE],
    ),
    dict(
        name="the-visibility-predicate-is-restated-rather-than-asked",
        refusal=(
            "public_sql covers five rules: listing status, moderation status, the "
            "seller's own status, the store-name invariant and stock. A local "
            "spelling of the two obvious ones passes every test written about "
            "*this* module and drifts the day one of the other three changes. This "
            "module asks the canonical question; it does not own it."
        ),
        path=LISTING,
        old="            f\"WHERE l.id = ? AND {lifecycle.public_sql('l', 'ms')} LIMIT 1\",",
        new="            \"WHERE l.id = ? AND LOWER(COALESCE(l.status,'')) IN \"\n"
            "            \"('published','live','active') AND \"\n"
            "            \"LOWER(COALESCE(l.approval_status,'')) = 'approved' LIMIT 1\",",
        suites=[LISTING_SUITE],
    ),
    dict(
        name="an-invisible-listing-declares-a-fulfillment",
        refusal=(
            "None is what makes a caller that ignores `visible` safe: it composes "
            "to fulfillment_undeclared and no estimate. Any declared type here and "
            "that caller gets a real window for a listing nobody may buy."
        ),
        path=LISTING,
        old="    return {\"visible\": False, \"fulfillment\": None, \"supplier\": None}",
        new="    return {\"visible\": False, \"fulfillment\": quote.FULFILLMENT_SELLER,\n"
            "            \"supplier\": None}",
        suites=[LISTING_SUITE],
    ),
    dict(
        name="a-listing-that-does-not-exist-is-distinguishable-from-a-draft",
        refusal=(
            "Raising for a missing row and answering for a hidden one tells an "
            "unauthenticated caller which listing ids exist as drafts. The estimate "
            "has no reason to make that disclosure, so the two are deliberately the "
            "same answer."
        ),
        path=LISTING,
        old="        if row is None:\n            return _absent()",
        new="        if row is None:\n            raise variant_facts.VariantRefInvalid(\"no such listing\")",
        suites=[LISTING_SUITE],
    ),
    dict(
        name="a-partial-connection-triple-is-used-to-authorize",
        refusal=(
            "worker_adapter takes the three coordinates as a triple. Two of three "
            "does not select a degraded version of this merchant's connection -- it "
            "selects a different merchant's, or none at all."
        ),
        path=LISTING,
        old="        value = _text(_maybe(source, column))\n"
            "        if value is None:\n"
            "            return None\n"
            "        found[column] = value",
        new="        found[column] = _text(_maybe(source, column))",
        suites=[LISTING_SUITE],
    ),
    dict(
        name="a-supplier-listing-with-no-coordinates-is-reclassified-as-seller-shipped",
        refusal=(
            "The tempting repair for SUPPLIER-beside-None, and it replaces an "
            "honest gap with a false statement: the buyer is told the seller "
            "arranges delivery, which the checkout then contradicts. Left as it is, "
            "the adapter source raises and the buyer is told the estimate is "
            "unavailable -- which is what is true, and which routes the repair to "
            "the credential vault rather than the catalogue."
        ),
        path=LISTING,
        old="    return {\"visible\": True, \"fulfillment\": quote.FULFILLMENT_SUPPLIER,\n"
            "            \"supplier\": _supplier(source)}",
        new="    coordinates = _supplier(source)\n"
            "    if coordinates is None:\n"
            "        return {\"visible\": True, \"fulfillment\": quote.FULFILLMENT_SELLER,\n"
            "                \"supplier\": None}\n"
            "    return {\"visible\": True, \"fulfillment\": quote.FULFILLMENT_SUPPLIER,\n"
            "            \"supplier\": coordinates}",
        suites=[LISTING_SUITE],
    ),
    dict(
        name="a-neighbouring-listings-source-row-is-read",
        refusal=(
            "One product's supplier binding quoting another product's parcel. "
            "Silent on a catalogue where most listings share a provider, because "
            "the answer is still a plausible window from a real warehouse."
        ),
        path=LISTING,
        old="f\"SELECT * FROM {_SOURCES} WHERE listing_id = ? LIMIT 1\"",
        new="f\"SELECT * FROM {_SOURCES} WHERE listing_id != ? LIMIT 1\"",
        suites=[LISTING_SUITE],
    ),
    dict(
        name="an-absent-column-crashes-the-declaration",
        refusal=(
            "The source row is read with SELECT *, so a table predating "
            "fulfillment_mode or store_id returns a row with no such key -- and "
            "both row types in this codebase raise rather than return None for one. "
            "An absent column is the same fact as an unset one for every decision "
            "here; raising turns it into a product page that will not render."
        ),
        path=LISTING,
        old="    except (KeyError, IndexError, TypeError):\n        return None",
        new="    except ():\n        return None",
        suites=[LISTING_SUITE],
    ),
    dict(
        name="the-free-text-estimate-column-is-selected",
        refusal=(
            "estimated_delivery is a seller-typed duration string that the "
            "storefront already prints under the label 'Estimated delivery' -- a "
            "hard-coded duration in a database column, which is the thing 5 "
            "forbids. Selecting it changes no behaviour, which is exactly why the "
            "guard is at source level: every behavioural test still passes, and the "
            "next commit reads it."
        ),
        path=LISTING,
        old="            f\"SELECT l.listing_type, l.product_type FROM {_LISTINGS} l \"",
        new="            f\"SELECT l.listing_type, l.product_type, l.estimated_delivery \"\n"
            "            f\"FROM {_LISTINGS} l \"",
        suites=[LISTING_SUITE],
    ),
    dict(
        name="the-connection-is-leaked-when-the-listing-is-absent",
        refusal=(
            "Every early return in this function is inside the try. Moved out, the "
            "leak is worst for exactly the traffic that produces it -- an invisible "
            "listing is the cheap path, so it is the one a crawler hits at volume, "
            "against a pool of 8+8 with a 3s timeout."
        ),
        path=LISTING,
        old="    finally:\n        conn.close()",
        new="    finally:\n        pass",
        suites=[LISTING_SUITE],
    ),
    # ---- policy.py: the declared half, and the defaults it refuses to have ----
    dict(
        name="the-handling-allowance-gets-a-default",
        refusal=(
            "The single most damaging mutation in this matrix, because it is the "
            "one that ends shadow mode without anybody deciding to. The absence of "
            "a default IS the rollout gate: with no allowance declared, "
            "arrival_window refuses with handling_time_undeclared, so every path "
            "runs, every cache key is exercised, every supplier call is measured "
            "and no buyer sees a date. A default turns the first deploy into a "
            "platform-wide promise built on a number nobody chose -- and CJ cannot "
            "supply it either, since this repo's own forensic report records that "
            "the catalogue dispatch fields 'are not a universal processing SLA'."
        ),
        path=POLICY,
        old="    raw = _raw(ENV_HANDLING)\n    if raw is None:\n        return None",
        new="    raw = _raw(ENV_HANDLING)\n    if raw is None:\n        raw = \"2-4\"",
        suites=[POLICY_SUITE],
    ),
    dict(
        name="the-buffer-gets-a-default",
        refusal=(
            "The other half of the same gate. A defaulted buffer would leave "
            "can_estimate answering yes on a deployment that configured nothing, "
            "so the health surface would report ready and the estimates would rest "
            "on a padding figure with no owner."
        ),
        path=POLICY,
        old="    raw = _raw(ENV_BUFFER)\n    if raw is None:\n        return None",
        new="    raw = _raw(ENV_BUFFER)\n    if raw is None:\n        raw = \"2\"",
        suites=[POLICY_SUITE],
    ),
    dict(
        name="a-declared-zero-buffer-is-read-as-undeclared",
        refusal=(
            "The inverse error, and the subtler one. `0 or None` is the idiom that "
            "causes it. An operator who decided to add no padding has decided "
            "something; folding that into absence means their deployment shows no "
            "estimates at all and the variable they set sits there looking correct."
        ),
        path=POLICY,
        old="    if low != high:",
        new="    if not low:\n        return None\n    if low != high:",
        suites=[POLICY_SUITE],
    ),
    dict(
        name="a-zero-handling-allowance-is-raised-to-one",
        refusal=(
            "What reusing normalize.transit_days would do. That parser is right to "
            "do it -- a parcel cannot cross a border in no days -- and wrong here, "
            "because a warehouse that picks same-day really does dispatch on day "
            "zero. Borrowed, it adds a day to every promise on the platform and "
            "nothing looks wrong on any screen."
        ),
        path=POLICY,
        old="    low = int(match.group(1))",
        new="    low = max(1, int(match.group(1)))",
        suites=[POLICY_SUITE],
    ),
    dict(
        name="an-unreadable-value-is-read-as-absence",
        refusal=(
            "PULSE_DELIVERY_BUFFER_DAYS=tow must be an error and not silence. "
            "Silence is indistinguishable from an unconfigured deployment, so an "
            "operator who did decide sees no estimates, goes looking for a supplier "
            "outage, and the variable is sitting in the dashboard spelled almost "
            "right. 'We have not decided' and 'we decided and it is not being "
            "applied' are different work."
        ),
        path=POLICY,
        old=("        raise PolicyInvalid(\n"
             "            f\"{name} must be a number of days or a `min-max` range, got {raw!r}\")"),
        new="        return 0, 0",
        suites=[POLICY_SUITE],
    ),
    dict(
        name="a-blank-variable-is-read-as-a-value",
        refusal=(
            "Railway sets every key listed in a service's variables whether or not "
            "it has content, so an empty string is the ordinary shape of 'declared "
            "in the dashboard, never filled in'. Reading it as a value turns that "
            "into an unreadable-policy error, which is the wrong half of the "
            "distinction this module exists to keep: blank is absence."
        ),
        path=POLICY,
        old="    return value.strip() or None",
        new="    return value.strip()",
        suites=[POLICY_SUITE],
    ),
    dict(
        name="a-non-ascii-digit-is-quietly-read-as-a-number",
        refusal=(
            "Without re.ASCII, \\d matches digits from every script, so a pasted "
            "Arabic-Indic two parses and int() returns a perfectly correct 2. The "
            "value would not be wrong -- the situation would be. Non-ASCII digits "
            "in a deployment variable are a mangled encoding or a paste from a "
            "localized spreadsheet, and reading one anyway spends the single "
            "opportunity to tell the operator their configuration is not what they "
            "think it is."
        ),
        path=POLICY,
        old="                    re.IGNORECASE | re.ASCII)",
        new="                    re.IGNORECASE)",
        suites=[POLICY_SUITE],
    ),
    dict(
        name="a-reversed-range-is-refused-instead-of-sorted",
        refusal=(
            "The deliberate leniency, asserted so it cannot be tightened by "
            "accident. '4-2' is an unambiguous transposition whose meaning nobody "
            "could mistake; refusing it converts a typo into a deployment with no "
            "estimates, which is a worse outcome than reading it the only way it "
            "can be read."
        ),
        path=POLICY,
        old="    if low > high:\n        low, high = high, low",
        new=("    if low > high:\n"
             "        raise PolicyInvalid(f\"{name} has its bounds reversed\")"),
        suites=[POLICY_SUITE],
    ),
    dict(
        name="the-sanity-ceiling-is-dropped",
        refusal=(
            "The ceilings exist to catch a variable holding a year or a phone "
            "number, not to second-guess a slow warehouse. Without them a "
            "fat-fingered 200 becomes a 200-day handling allowance, and the "
            "estimator's own MAX_WINDOW_DAYS then refuses the quote for a reason "
            "that names the horizon rather than the variable."
        ),
        path=POLICY,
        old="    if high > ceiling:",
        new="    if False:",
        suites=[POLICY_SUITE],
    ),
    dict(
        name="an-out-of-range-cutoff-hour-is-accepted",
        refusal=(
            "arrival_window validates the hour itself and raises EstimateRejected "
            "on 24. Passing it through means the failure surfaces one layer down, "
            "attributed to the estimator, on a deployment whose actual problem is "
            "one variable."
        ),
        path=POLICY,
        old="    if not 0 <= hour <= 23:",
        new="    if False:",
        suites=[POLICY_SUITE],
    ),
    dict(
        name="a-ceiling-of-zero-is-accepted",
        refusal=(
            "routing requires a positive ceiling and raises RoutingRejected "
            "otherwise, so a zero here is a crash at selection time rather than a "
            "refused variable. Worse, a reader could mistake zero for 'no ceiling' "
            "-- which is spelled None, and means the opposite."
        ),
        path=POLICY,
        old="    if days < 1:",
        new="    if False:",
        suites=[POLICY_SUITE],
    ),
    dict(
        name="the-route-policy-falls-back-to-the-default-on-a-typo",
        refusal=(
            "PULSE_DELIVERY_ROUTE_POLICY=FASTESTT silently selecting cheapest is "
            "the deployment that believes it prioritizes speed and quotes the slow "
            "route. Route selection is also what gets booked at fulfillment, so "
            "this is not only a wrong quote -- it is quoting one route and shipping "
            "another, which is the specific failure routing was separated from "
            "estimation to prevent."
        ),
        path=POLICY,
        old="    if named not in routing.POLICIES:",
        new="    if False:",
        suites=[POLICY_SUITE],
    ),
    dict(
        name="the-route-policy-names-are-restated-here",
        refusal=(
            "Two spellings of the platform's answer to 'which route do we pick' "
            "drift the moment routing adds a third policy. The list is borrowed so "
            "there is one."
        ),
        path=POLICY,
        old="    if named not in routing.POLICIES:",
        new="    if named not in (\"CHEAPEST_ACCEPTABLE\", \"FASTEST\"):",
        suites=[POLICY_SUITE],
    ),
    dict(
        name="the-unspecified-basis-becomes-a-switch",
        refusal=(
            "How to read a supplier that gave a number and no day basis is "
            "estimate's rounding-direction decision, and it chose business days "
            "because that is the later of the two readings. A variable here would "
            "exist for exactly one purpose: letting a deployment choose the earlier "
            "one, which is this domain's forbidden direction sold as a "
            "configuration option."
        ),
        path=POLICY,
        old="    return estimate.BASIS_BUSINESS\n",
        new=("    raw = _raw(\"PULSE_DELIVERY_UNSPECIFIED_BASIS\")\n"
             "    return raw or estimate.BASIS_BUSINESS\n"),
        suites=[POLICY_SUITE],
    ),
    dict(
        name="an-unmapped-origin-is-quoted-in-utc",
        refusal=(
            "UTC is behind every zone in the map, so it is the early direction for "
            "this domain: 18:00 UTC on the 1st is already 02:00 on the 2nd in "
            "Shenzhen, which means handling would start a day early and the window "
            "would close a day early. Two modules in this repo already fall back to "
            "UTC for unrelated reasons, which is why this is a live mistake and not "
            "a hypothetical one."
        ),
        path=POLICY,
        old="    return _most_advanced_zone()",
        new="    return timezone.utc",
        suites=[POLICY_SUITE],
    ),
    dict(
        name="the-no-tzdata-fallback-is-utc",
        refusal=(
            "Same direction, reached differently -- a container with no zone "
            "database at all. This is the path where nothing can be looked up, so "
            "it is the one place a fixed offset is legitimate, and the offset has to "
            "be the most advanced any declared origin can reach."
        ),
        path=POLICY,
        old=("    if best is None:\n"
             "        return timezone(timedelta(hours=_FALLBACK_OFFSET_HOURS))"),
        new="    if best is None:\n        return timezone.utc",
        suites=[POLICY_SUITE],
    ),
    dict(
        name="the-fixed-fallback-offset-is-the-busiest-origin-not-the-latest",
        refusal=(
            "UTC+8 is the tempting value: exact year-round for China, which "
            "observes no daylight saving and holds most of this supplier's stock. It "
            "is still wrong, because Australia/Sydney is in the map at UTC+10 and "
            "+11 in southern summer, so a UTC+8 fallback puts an AU origin's today "
            "up to three hours early. Being ahead of China instead costs at most a "
            "dispatch date one day late, which is the safe error."
        ),
        path=POLICY,
        old="_FALLBACK_OFFSET_HOURS = 11",
        new="_FALLBACK_OFFSET_HOURS = 8",
        suites=[POLICY_SUITE],
    ),
    dict(
        name="the-fallback-zone-is-written-down-rather-than-derived",
        refusal=(
            "A hard-coded winner stops being the winner the moment a warehouse is "
            "declared further east, and it ignores daylight saving -- Sydney is +10 "
            "for half the year and +11 for the other half. Derived from current "
            "offsets, a new map entry moves the fallback on its own and DST is the "
            "zone library's problem rather than a table here."
        ),
        path=POLICY,
        old="        if offset is not None and (best_offset is None or offset > best_offset):",
        new="        if offset is not None and best is None:",
        suites=[POLICY_SUITE],
    ),
    dict(
        name="the-origin-code-is-matched-without-normalizing-it",
        refusal=(
            "Origins arrive from a supplier payload as 'cn', ' CN ' and 'CN' "
            "depending on the field. An exact match sends the first two to the "
            "fallback, which still answers -- with a plausible date computed in the "
            "wrong zone. That is a silent miss rather than an error, which is why "
            "it needs a test rather than a log line."
        ),
        path=POLICY,
        old="    code = origin.strip().upper() if isinstance(origin, str) else \"\"",
        new="    code = origin if isinstance(origin, str) else \"\"",
        suites=[POLICY_SUITE],
    ),
    dict(
        name="a-naive-clock-is-accepted-and-localized",
        refusal=(
            "astimezone on a naive datetime silently assumes system local time, so "
            "the moment would be reinterpreted rather than converted -- and on "
            "Railway the system zone is UTC, which lands back in the early "
            "direction. arrival_window demands awareness for the same reason; "
            "refusing here attributes the fault to the caller that has it."
        ),
        path=POLICY,
        old=('    if instant.tzinfo is None or instant.tzinfo.utcoffset(instant) is None:\n'
             '        raise PolicyInvalid("clock must return a timezone-aware moment")\n'),
        new="",
        suites=[POLICY_SUITE],
    ),
    dict(
        name="a-closure-date-is-accepted-in-a-shape-the-estimator-rejects",
        refusal=(
            "strptime('%Y-%m-%d') accepts an unpadded 2026-2-17 and "
            "date.fromisoformat does not, and _holiday_set uses the latter. So a "
            "lenient parse here produces a string that fails one layer down, after "
            "the supplier call, as an EstimateRejected attributed to the estimator "
            "rather than to the variable that is actually wrong. This was a real "
            "defect in this module, found by the test, not a hypothetical."
        ),
        path=POLICY,
        old="    if not _ISO_DATE.match(candidate):",
        new="    if False:",
        suites=[POLICY_SUITE],
    ),
    dict(
        name="a-closure-date-is-shape-checked-but-never-parsed",
        refusal=(
            "2026-02-30 is the case that matters: shaped exactly like an ISO date "
            "and not one. The shape check alone passes it, and the calendar check is "
            "the only thing that does not."
        ),
        path=POLICY,
        old=("        try:\n"
             "            date.fromisoformat(candidate)\n"
             "        except ValueError:\n"
             "            raise PolicyInvalid(\n"
             "                f\"{ENV_CLOSURES} holds {candidate!r}, which is not a real date\")"),
        new="        pass",
        suites=[POLICY_SUITE],
    ),
    dict(
        name="closures-keep-the-order-and-duplicates-they-were-typed-in",
        refusal=(
            "The count reaches components.holidays_modelled, which is what an "
            "accuracy review reads to tell which windows were computed blind. A "
            "count that moved with typing order, or double-counted a date listed "
            "twice, would make two identical deployments look different to that "
            "review."
        ),
        path=POLICY,
        old="    return tuple(sorted(set(found)))",
        new="    return tuple(found)",
        suites=[POLICY_SUITE],
    ),
    dict(
        name="the-health-surface-raises-instead-of-reporting",
        refusal=(
            "A health surface that 500s when the thing it monitors is misconfigured "
            "is broken at the one moment it needed to render. declared() exists so "
            "an operator can tell 'nobody declared a handling allowance' from 'the "
            "supplier could not price this route' -- both silence to a buyer, "
            "completely different work -- and conflating them is how a deployment "
            "concludes its supplier is down."
        ),
        path=POLICY,
        old="        except PolicyInvalid as exc:",
        new="        except KeyError as exc:",
        suites=[POLICY_SUITE],
    ),

    # ---- entry.py: the clock follows the warehouse ----------------------------
    dict(
        name="the-clock-is-asked-before-the-warehouse-is-known",
        refusal=(
            "The whole reason now_at is a function of the origin. Asking it about "
            "None on the quoting path means every estimate on the platform is "
            "computed in the fallback zone rather than the warehouse's -- plausible "
            "dates, wrong by a day for anyone whose origin is not the most advanced "
            "declared zone, and nothing on any screen looks off."
        ),
        path=ENTRY,
        old="        now=now_at(origin_code),",
        new="        now=now_at(None),",
        suites=[ENTRY_SUITE],
    ),
    dict(
        name="the-clock-is-asked-about-the-buyers-country",
        refusal=(
            "The tempting confusion in this domain: there are two countries in "
            "scope and only one of them picks the item. arrival_window says so -- "
            "'because it is the warehouse that has to pick the item' -- and a "
            "destination-zone clock would move the dispatch date by the buyer's "
            "timezone, so the same product would quote differently in Los Angeles "
            "and Berlin for a reason that has nothing to do with shipping."
        ),
        path=ENTRY,
        old="        now=now_at(origin_code),",
        new="        now=now_at((destination or {}).get(\"country\")),",
        suites=[ENTRY_SUITE],
    ),
    dict(
        name="the-refusal-path-composes-its-own-moment",
        refusal=(
            "A refusal built without going through the clock is a refusal built by "
            "a second code path, which is what this module's docstring refuses: 'a "
            "second place that composes a delivery answer is a second place that "
            "can compose a different one'. utcnow() is also naive, so the record "
            "would carry a moment arrival_window would have rejected."
        ),
        path=ENTRY,
        old="            fulfillment=fulfillment, destination=destination, now=now_at(None),",
        new="            fulfillment=fulfillment, destination=destination, now=None,",
        suites=[ENTRY_SUITE],
    ),

    # ---- delivery_routes.py: the boundary, and the two inputs it may not take ----
    dict(
        name="the-request-body-decides-who-ships-it",
        refusal=(
            "The most dangerous mutation on this surface. Fulfillment is a declared "
            "fact (SS47-48) read from marketplace_product_sources, and a request "
            "parameter for it lets anyone turn a seller-posted item into a "
            "supplier-fulfilled one: the buyer is then shown a CJ "
            "warehouse-to-door window for a parcel the seller is about to hand to "
            "the post office, and every such request spends a real CJ call. Note "
            "the mutation is an `or`, not a replacement -- it is what the change "
            "would actually look like, and it is invisible to any test that does "
            "not deliberately send a contradicting value."
        ),
        path=ROUTES,
        old='        fulfillment=declared["fulfillment"],',
        new='        fulfillment=(body.get("fulfillment") or declared["fulfillment"]),',
        suites=[ROUTES_SUITE],
    ),
    dict(
        name="the-request-body-decides-whose-order-history-is-read",
        refusal=(
            "The buyer id exists here for exactly one purpose: "
            "destination._from_history reads this buyer's past shipping addresses. "
            "Accepting it from a request makes one visitor's order history "
            "readable-by-effect to any other, one country at a time, with no "
            "authentication anywhere in the path -- this route is public by "
            "design."
        ),
        path=ROUTES,
        old="        buyer_user_id=_buyer_user_id(),",
        new='        buyer_user_id=(body.get("buyer_user_id") or _buyer_user_id()),',
        suites=[ROUTES_SUITE],
    ),
    dict(
        name="the-whole-quote-is-serialized-including-the-internal-half",
        refusal=(
            "One character wide, raises nothing, renders fine, and ships the "
            "freight PulseSoc pays CJ straight to a shopper -- which SS33-35 "
            "forbids in as many words. It also hands over the route identifier "
            "fulfillment will book and the component breakdown of the window, "
            "which together let a competitor read this platform's supplier "
            "economics off a product page."
        ),
        path=ROUTES,
        old='        "delivery": result["buyer"],',
        new='        "delivery": result,',
        suites=[ROUTES_SUITE],
    ),
    dict(
        name="the-asserted-country-is-promoted-to-the-checkout-tier",
        refusal=(
            "CHECKOUT is the only tier destination.POSTAL_TIERS lets carry a "
            "postal code, and that precision goes into a cache key. Passing the "
            "request body as the checkout tier means a caller can claim postal "
            "precision the buyer never confirmed for this purchase, and file a "
            "cache entry under it. There is no behavioural difference while the "
            "body carries no postal, which is exactly why a test has to assert the "
            "keyword rather than the outcome."
        ),
        path=ROUTES,
        old='        stated=_stated(body.get("destination") or body.get("country")),',
        new='        checkout=_stated(body.get("destination") or body.get("country")),',
        suites=[ROUTES_SUITE],
    ),
    dict(
        name="the-request-body-is-handed-to-the-resolver-whole",
        refusal=(
            "Copying the caller's object through means any key they invent reaches "
            "destination.resolve, and from there whatever it accepts next. The "
            "narrow dict is the interface: this route asserts a country and "
            "nothing else."
        ),
        path=ROUTES,
        old='        return {"country": raw.get("country")}',
        new="        return dict(raw)",
        suites=[ROUTES_SUITE],
    ),
    dict(
        name="the-composed-answer-becomes-cacheable-by-an-intermediary",
        refusal=(
            "Two failures at once. The answer depends on the session's destination "
            "resolution, so a shared cache hands one visitor's country to another. "
            "And this domain already has a cache with a chosen freshness policy, "
            "stale-while-revalidate and a negative tier -- a proxy caching the "
            "composed response adds a second, dumber layer on top of it whose TTL "
            "nobody chose and which no invalidation reaches."
        ),
        path=ROUTES,
        old='    response.headers["Cache-Control"] = "no-store, max-age=0, must-revalidate"',
        new='    response.headers["Cache-Control"] = "public, max-age=300"',
        suites=[ROUTES_SUITE],
    ),
    dict(
        name="a-draft-listing-is-quoted-and-warms-the-suppliers-cache",
        refusal=(
            "listing.declaration applies the platform's own public_sql predicate, "
            "and dropping the check here is what makes that pointless. A seller's "
            "unpublished drafts, rejected listings and a suspended seller's whole "
            "catalogue become quotable by listing id -- each one a correct estimate "
            "for something nobody can buy, and a CJ call to produce it."
        ),
        path=ROUTES,
        old='    if not declared["visible"]:',
        new="    if False:",
        suites=[ROUTES_SUITE],
    ),
    dict(
        name="an-invisible-listing-is-answered-with-a-403",
        refusal=(
            "A distinct status for 'exists but you cannot see it' is an oracle for "
            "enumerating other sellers' drafts: walk the id space, and 403 marks "
            "the real ones. listing.declaration conflates absent and unreachable "
            "on purpose, and this route must not un-conflate them."
        ),
        path=ROUTES,
        old='                      "message": "This listing is not available."}, 404)',
        new='                      "message": "This listing is not available."}, 403)',
        suites=[ROUTES_SUITE],
    ),
    dict(
        name="a-quantity-above-the-ceiling-is-clamped-instead-of-refused",
        refusal=(
            "max(1, min(n, cap)) is the cart's idiom and is wrong here. The cart is "
            "setting a line quantity, so the nearest legal value is what the "
            "merchant wants; here the number is an input to a freight quote, and "
            "quoting 20 when 500 was asked returns a window for a parcel the caller "
            "did not describe, labelled as though it were theirs."
        ),
        path=ROUTES,
        old="    if raw < 1 or raw > MAX_QUANTITY:\n        return None",
        new="    if False:\n        return max(1, min(raw, MAX_QUANTITY))",
        suites=[ROUTES_SUITE],
    ),
    dict(
        name="a-quantity-that-is-not-a-count-is-read-anyway",
        refusal=(
            "Without the type gate a string quantity reaches a comparison and the "
            "route answers 500 where it should answer 400, and a float reaches the "
            "cache key. Coercing instead looks like kindness until it has to decide "
            "about '4.5' and ' 4 '."
        ),
        path=ROUTES,
        old="    if isinstance(raw, bool) or not isinstance(raw, int):",
        new="    if False:",
        suites=[ROUTES_SUITE],
    ),
    dict(
        name="a-boolean-quantity-is-read-as-one",
        refusal=(
            "bool is a subclass of int in Python, so the isinstance check alone "
            "accepts True and quotes a quantity of one to a caller who sent a flag "
            "-- a body this route should be telling them about rather than "
            "interpreting. Ordering matters: the bool test has to come first."
        ),
        path=ROUTES,
        old="    if isinstance(raw, bool) or not isinstance(raw, int):",
        new="    if not isinstance(raw, int):",
        suites=[ROUTES_SUITE],
    ),
    dict(
        name="the-quantity-ceiling-is-retyped-rather-than-borrowed",
        refusal=(
            "A defect with no behavioural signature: 20 equals MAX_QTY_PER_LINE "
            "today, so every runtime assertion still passes. It stops agreeing the "
            "day the cart moves its ceiling, and then this route refuses a quantity "
            "the cart accepts -- a product page that cannot quote a basket the "
            "shopper is allowed to fill. Only a source-level test can see it."
        ),
        path=ROUTES,
        old="MAX_QUANTITY = MAX_QTY_PER_LINE",
        new="MAX_QUANTITY = 20",
        suites=[ROUTES_SUITE],
    ),
    dict(
        name="a-body-that-is-not-an-object-is-coerced-rather-than-refused",
        refusal=(
            "A JSON array or string passes `or {}` intact and then meets .get(), "
            "so a malformed body becomes a 500 instead of the 400 it is. The "
            "isinstance check is what makes 'not an object' a caller error with an "
            "answer rather than a traceback."
        ),
        path=ROUTES,
        old="    return payload if isinstance(payload, dict) else {}",
        new="    return payload or {}",
        suites=[ROUTES_SUITE],
    ),
    dict(
        name="a-malformed-variant-reference-becomes-a-server-error",
        refusal=(
            "variant_facts.parse_ref raises precisely so a coding mistake stays "
            "distinguishable from a product that honestly cannot say. Letting it "
            "out of the route collapses that distinction into a 500, which reads "
            "as an outage and hides which of the two it was."
        ),
        path=ROUTES,
        old="    except variant_facts.VariantRefInvalid as exc:",
        new="    except ZeroDivisionError as exc:",
        suites=[ROUTES_SUITE],
    ),
    dict(
        name="the-clock-is-handed-over-as-a-moment-rather-than-a-function",
        refusal=(
            "entry resolves the warehouse several database reads after it is "
            "entered, so a moment computed here cannot be in the origin's zone -- "
            "it would be in this process's zone, which on Railway is UTC, and UTC "
            "is behind every warehouse zone CJ uses. 18:00 UTC is already 02:00 "
            "tomorrow in Shenzhen, so the whole platform would start handling a day "
            "early and close its window a day early."
        ),
        path=ROUTES,
        old="        now_at=policy.now_at,",
        new="        now_at=(lambda _origin: policy.now_at(None)),",
        suites=[ROUTES_SUITE],
    ),
    dict(
        name="a-declared-policy-input-is-not-passed-through",
        refusal=(
            "The cutoff hour is the one declared input whose absence is silent: "
            "estimate defaults it away, so every estimate quietly starts handling "
            "on the day the request arrived even when the warehouse's van left "
            "hours ago. An operator who set the variable would see no change and "
            "conclude the cutoff does not work."
        ),
        path=ROUTES,
        old="        dispatch_cutoff_hour=policy.dispatch_cutoff_hour(),\n",
        new="",
        suites=[ROUTES_SUITE],
    ),
    dict(
        name="the-cache-tier-is-never-passed-to-the-engine",
        refusal=(
            "SS18-21 is not satisfied by a cache that exists and is never handed "
            "over. Without a store every product page view is a CJ call: the "
            "coalescing still works within one request and does nothing across "
            "them, and the rate limit is reached by ordinary traffic."
        ),
        path=ROUTES,
        old="        store=store_module.CacheEngineStore(),",
        new="        store=None,",
        suites=[ROUTES_SUITE],
    ),
    dict(
        name="an-incomplete-supplier-connection-yields-no-adapter-and-no-error",
        refusal=(
            "listing._supplier answers None beside fulfillment=SUPPLIER when the "
            "connection mapping is incomplete, and that pair is the honest answer. "
            "The source has to raise so entry reports connection_unavailable, which "
            "sends an operator to the credential mapping; returning None instead "
            "surfaces as a missing weight and sends them to the catalogue."
        ),
        path=ROUTES,
        old='            raise RuntimeError("this listing has no complete supplier connection")',
        new="            return None",
        suites=[ROUTES_SUITE],
    ),
    dict(
        name="the-buyers-page-load-is-routed-through-a-user-authorization-path",
        refusal=(
            "adapter_for authorizes an actor against a merchant's connection. There "
            "is no actor here -- the buyer is not acting on the merchant's "
            "credential, the platform is reading a shipping rate on their behalf -- "
            "so reaching for it means inventing an actor id or widening an existing "
            "authorization to cover anonymous traffic."
        ),
        path=ROUTES,
        old="        return connections.worker_adapter(",
        new="        return connections.adapter_for(",
        suites=[ROUTES_SUITE],
    ),
    dict(
        name="the-health-surface-echoes-the-deployments-configuration-publicly",
        refusal=(
            "policy.declared() returns this deployment's own configuration and, in "
            "`errors`, the raw text of a variable someone typed wrong. "
            "admin_required is declarative and adds no check, so a route wearing it "
            "without calling the gate is an open endpoint that reads as a closed "
            "one -- the worst of the two states, because a reviewer sees the "
            "decorator and stops looking."
        ),
        path=ROUTES,
        old='    _admin, denied = _bot().require_admin_api("system.view")\n    if denied:\n        return denied\n',
        new="",
        suites=[ROUTES_SUITE],
    ),
    dict(
        name="the-health-surface-drops-the-unconfigured-edge-tier-distinction",
        refusal=(
            "An unset handling allowance and a population of visitors nothing could "
            "place are both buyer-visible silence and completely different work. "
            "Without edge_tier_configured beside the policy report, a missing "
            "environment variable is diagnosed as unlocatable buyers -- which is "
            "the exact confusion destination.edge_configured exists to prevent."
        ),
        path=ROUTES,
        old='    report["edge_tier_configured"] = destinations.edge_configured()\n',
        new="",
        suites=[ROUTES_SUITE],
    ),
    dict(
        name="the-postal-code-is-echoed-back-to-the-caller",
        refusal=(
            "A stated destination never carries one, so this field is always null "
            "and reads as precision the answer does not have. Once the checkout "
            "surface feeds this route it stops being null, and an address fragment "
            "is then in a response body for no reason -- the client supplied it."
        ),
        path=ROUTES,
        old='            "country": where["destination"]["country"],',
        new='            "country": where["destination"]["country"],\n            "postal": where["destination"]["postal"],',
        suites=[ROUTES_SUITE],
    ),
    dict(
        name="the-estimate-endpoint-also-answers-a-get",
        refusal=(
            "A GET puts the destination in a query string, which the edge logs, the "
            "browser keeps in history and the page forwards in referrers. The "
            "country alone is not sensitive enough to agonise over; the habit is, "
            "because the checkout surface that comes later has a postal code to "
            "pass to this same endpoint."
        ),
        path=ROUTES,
        old='methods=["POST"])',
        new='methods=["GET", "POST"])',
        suites=[ROUTES_SUITE],
    ),

    # -- The stored promise: §36-37, and the carrier handover ----------------
    dict(
        name="a-redelivered-webhook-overwrites-the-promise",
        refusal=(
            "The write is insert-only. DO UPDATE here is the whole of §36-37 gone: "
            "a webhook redelivered a week later replaces a week-old promise with "
            "today's supplier aging, and the sentence a dispute is about no longer "
            "exists anywhere."
        ),
        path=PROMISE,
        old="\"ON CONFLICT(seller_transaction_id) DO NOTHING\",",
        new=("\"ON CONFLICT(seller_transaction_id) DO UPDATE SET \"\n"
             "                \"earliest=excluded.earliest, latest=excluded.latest, \"\n"
             "                \"state=excluded.state, buyer_json=excluded.buyer_json\","),
        suites=[PROMISE_SUITE],
    ),
    dict(
        name="a-skipped-insert-reports-itself-as-written",
        refusal=(
            "`recorded` is whether this call wrote the row. Hard-coding it true "
            "makes a duplicate checkout log a promise it did not make -- and the "
            "row it returns is still the first one, so the log and the database "
            "disagree about which window was promised."
        ),
        path=PROMISE,
        old="            recorded = cursor.rowcount == 1",
        new="            recorded = True",
        suites=[PROMISE_SUITE],
    ),
    dict(
        name="a-half-quote-is-stored-as-a-best-effort-promise",
        refusal=(
            "A result missing a half is refused rather than stored partially. A "
            "best-effort row claims in the accuracy ledger that a promise was made "
            "and cannot say what it was, which is worse than no row: no row is "
            "excluded, a partial one is scored."
        ),
        path=PROMISE,
        old=('    if not isinstance(buyer, dict) or not isinstance(internal, dict):\n'
             '        raise PromiseRejected("a promise is composed from a whole quote, both halves")'),
        new=('    if not isinstance(buyer, dict):\n'
             '        buyer = {}\n'
             '    if not isinstance(internal, dict):\n'
             '        internal = {}'),
        suites=[PROMISE_SUITE],
    ),
    dict(
        name="an-incomplete-buyer-half-is-accepted",
        refusal=(
            "The buyer half must carry every field the composer produces. A "
            "promise stored without `confidence` cannot be grouped by confidence "
            "tier, which is the one dimension §71-72's calibration needs."
        ),
        path=PROMISE,
        old="    missing = quote.BUYER_FIELDS - set(buyer)\n    if missing:",
        new="    missing = set()\n    if missing:",
        suites=[PROMISE_SUITE],
    ),
    dict(
        name="an-order-with-no-promise-reads-as-a-blank-promise",
        refusal=(
            "An absent row is None, never an empty promise. §70 has to exclude "
            "orders placed before this package existed; a plausible-looking blank "
            "gets scored as a promise of nothing that was missed by every parcel."
        ),
        path=PROMISE,
        old="    if row is None:\n        return None\n    # `row_values`",
        new=('    if row is None:\n'
             '        return {"seller_transaction_id": transaction_id, "buyer": {},\n'
             '                "internal": {}, "destination": {}}\n    # `row_values`'),
        suites=[PROMISE_SUITE],
    ),
    dict(
        name="the-carrier-branch-relabels-the-promise-as-a-carrier-window",
        refusal=(
            "§124. Once a parcel ships the carrier is authoritative, and this "
            "package has not asked one anything. Showing the promised dates under "
            "the carrier's name is the fabricated precision the whole mission "
            "exists to remove -- and it is the single most tempting line in this "
            "file, because it makes an order page look complete."
        ),
        path=PROMISE,
        # Replacing the value, not inserting a second `"window"` key above it. The
        # first attempt here did the latter and survived -- in a dict literal the
        # last assignment of a key wins, so the mutant was a no-op wearing a
        # survivor's clothes. Worth recording: a survivor is a claim about the
        # tests, and this one was a claim about the mutation.
        old='        "window": None,\n        "tracking": {',
        new='        "window": promise_window,\n        "tracking": {',
        suites=[PROMISE_SUITE],
    ),
    dict(
        name="authority-transfers-on-a-state-a-seller-controls",
        refusal=(
            "The handover keys on the tracking reference, the artifact a third "
            "party can be asked about. Keying on the state string lets a merchant "
            "who types 'shipped' silently retire the promise the buyer agreed to, "
            "with no carrier able to confirm anything."
        ),
        path=PROMISE,
        old='    reference = str(row.get("tracking_reference") or "").strip()',
        new=('    reference = str(row.get("tracking_reference") or "").strip()\n'
             '    if str(row.get("state") or "").strip().lower() == "shipped":\n'
             '        reference = reference or "pending"'),
        suites=[PROMISE_SUITE],
    ),
    dict(
        name="a-stored-refusal-grows-a-window-on-read",
        refusal=(
            "A promise whose state is not ESTIMATED has no window. Returning the "
            "earliest/latest columns regardless hands an order page two Nones "
            "shaped like a window, and the surface that formats them prints "
            "whatever `null` renders as."
        ),
        path=PROMISE,
        old="    if buyer.get(\"state\") != estimate.STATE_ESTIMATED:\n        return None",
        new="    if False:\n        return None",
        suites=[PROMISE_SUITE],
    ),
    dict(
        name="a-historical-promise-is-read-back-as-a-guarantee",
        refusal=(
            "§58/§125 do not lapse because the order is in the past. An order "
            "detail page reading a stored promise renders from this dict, so a "
            "True here is the word 'Guaranteed' on a screen about a parcel that "
            "has already been late."
        ),
        path=PROMISE,
        old='        "guaranteed": False,\n        "is_estimate": True,\n    }\n\n\ndef accuracy(',
        new='        "guaranteed": True,\n        "is_estimate": False,\n    }\n\n\ndef accuracy(',
        suites=[PROMISE_SUITE],
    ),
    dict(
        name="an-order-with-no-promise-is-scored-as-a-miss",
        refusal=(
            "§70/§71-72. An order that was never promised a date cannot have "
            "missed one. Scoring it teaches a calibration loop from orders that "
            "carry no information, and the correction it learns is the ratio of "
            "legacy rows in the table."
        ),
        path=PROMISE,
        old="    if not promise:\n        return _unmeasurable(NOT_PROMISED)",
        new=('    if not promise:\n'
             '        return {"measurable": True, "reason": None, "within": False,\n'
             '                "days_early": 0, "days_late": 0}'),
        suites=[PROMISE_SUITE],
    ),
    dict(
        name="a-correct-refusal-is-scored-as-a-broken-promise",
        refusal=(
            "We said we had no date, and that was true. Counting it as a miss "
            "makes the accuracy figure track supplier-outage volume rather than "
            "delivery performance, and the two move in opposite directions when "
            "the breaker is doing its job."
        ),
        path=PROMISE,
        old=('    if buyer.get("state") != estimate.STATE_ESTIMATED:\n'
             '        # We correctly said we had no date. Not a missed promise.\n'
             '        return _unmeasurable(str(buyer.get("reason") or "no_window_promised"))'),
        new=('    if buyer.get("state") != estimate.STATE_ESTIMATED:\n'
             '        return {"measurable": True, "reason": None, "within": False,\n'
             '                "days_early": 0, "days_late": 0}'),
        suites=[PROMISE_SUITE],
    ),
    dict(
        name="an-undelivered-order-is-measured-against-today",
        refusal=(
            "No delivery date means not measurable, not on time. Treating an "
            "absent arrival as a hit reports every in-flight order as a kept "
            "promise, so the figure is highest exactly when a backlog is growing."
        ),
        path=PROMISE,
        old='    if not _is_day(delivered_on):\n        return _unmeasurable("not_delivered_yet")',
        new='    if not _is_day(delivered_on):\n        delivered_on = str(earliest)',
        suites=[PROMISE_SUITE],
    ),
    dict(
        name="a-delivery-inside-the-window-is-measured-against-both-ends",
        refusal=(
            "The two conditionals exist so that only the end that was actually "
            "missed is non-zero. Computing both unconditionally makes an on-time "
            "parcel produce a negative `days_early` and a negative `days_late`, "
            "`within` goes false, and §70 reports a 0% hit rate for a corridor "
            "that is performing perfectly -- then §71-72 pads a buffer to fix it."
        ),
        path=PROMISE,
        old=('    early = _days_between(actual, str(earliest)) if actual < str(earliest) else 0\n'
             '    late = _days_between(str(latest), actual) if actual > str(latest) else 0'),
        new=('    early = _days_between(actual, str(earliest))\n'
             '    late = _days_between(str(latest), actual)'),
        suites=[PROMISE_SUITE],
    ),
    # No entry flips `actual > str(latest)` to `>=`, and the omission is
    # deliberate rather than an oversight. On the boundary day the two are the same
    # program: `>=` takes the branch, and the branch computes
    # `_days_between(latest, latest)` = 0, which is what the `else` supplies. There
    # is no input that tells them apart, so an entry claiming
    # `test_the_window_is_inclusive_at_both_ends` guards that comparison would be
    # claiming coverage of a distinction that does not exist. The test is still
    # worth having -- it pins the inclusive semantics against a future rewrite that
    # is *not* equivalent -- but it is not defended by a mutation, and saying so
    # here is the point of this file.
    dict(
        name="the-internal-half-is-merged-into-the-buyer-half-on-read",
        refusal=(
            "§33-35. The halves are stored in separate columns so the boundary "
            "survives into storage: a surface that serializes `promise['buyer']` "
            "must not be able to ship the freight PulseSoc paid and the route it "
            "booked to a buyer's browser."
        ),
        path=PROMISE,
        old='        "buyer": json.loads(buyer_json),',
        new='        "buyer": {**json.loads(internal_json), **json.loads(buyer_json)},',
        suites=[PROMISE_SUITE],
    ),
    dict(
        name="the-primary-key-is-declared-inline-and-becomes-a-sequence",
        refusal=(
            "`<col> INTEGER PRIMARY KEY` is rewritten to `SERIAL PRIMARY KEY` by "
            "services.db._translate_create_table, which is right for the tables "
            "whose id the database invents and wrong for this one, whose key is the "
            "seller transaction id the caller holds. SQLite accepts both forms "
            "identically, so this defect exists only in production and only a "
            "source-level assertion can see it."
        ),
        path=PROMISE,
        # Anchored on the first and last column lines together so the mutant is a
        # *coherent* schema: the inline key replaces the table constraint rather
        # than sitting beside it. A mutant declaring two primary keys would be
        # killed by SQLite rejecting the DDL, which proves the fixture runs, not
        # that anything notices the dialect divergence.
        old=("        seller_transaction_id INTEGER NOT NULL,\n"
             "        listing_id INTEGER,"),
        new=("        seller_transaction_id INTEGER PRIMARY KEY,\n"
             "        listing_id INTEGER,"),
        also=[("        promised_at TEXT NOT NULL,\n"
               "        PRIMARY KEY (seller_transaction_id))\"\"\"",
               "        promised_at TEXT NOT NULL)\"\"\"")],
        suites=[PROMISE_SUITE],
    ),

    # -- The settlement seam: where a promise stops being a calculation --------
    #
    # These five mutate `bot.py`, which nothing else in this file does, and the
    # reason is that every refusal in this slice is one line away from being
    # unreachable. `services/delivery/` can be perfect and the platform can still
    # store nothing, because the module is only ever entered from a single call in
    # `pulse_finalize_marketplace_settlement`. A suite that exercises the seam by
    # calling it directly — which is the only way to exercise a webhook handler's
    # effects — is green either way. That gap is what the first entry below tests.
    #
    # Mutating a 132k-line module costs ~11s per mutant because the bytecode cache
    # is invalidated, versus ~1s for a service module. That is the whole price and
    # it buys the difference between a tested slice and a reachable one.
    dict(
        name="settlement-never-records-the-promise",
        refusal=(
            "§36-37. A promise is written at the moment the buyer is first owed "
            "something, from the settlement path, and not from anywhere a buyer "
            "can reach later. Delete the call and every behavioural test in the "
            "wiring suite still passes, because they all invoke the seam directly."
        ),
        path=WIRING,
        old="    pulse_snapshot_delivery_promise(tx)\n",
        new="",
        suites=[WIRING_SUITE],
    ),
    dict(
        name="the-promise-is-recorded-only-when-fulfillment-opened-cleanly",
        refusal=(
            "The snapshot is unconditional. Moving it inside the block whose "
            "failure the handler above absorbs would exclude exactly the orders "
            "worth measuring -- the ones where something already went wrong -- from "
            "§70's accuracy ledger, and would do it invisibly, because the ledger "
            "would still look healthy."
        ),
        path=WIRING,
        # A coherent alternative, not a broken one: the call *moves* up into the
        # `try` it currently sits below, so it is skipped whenever the fulfillment
        # record could not be opened. Indentation is part of the edit.
        #
        # The removal is the primary edit and the insertion is the `also`, in that
        # order, because the edits apply sequentially with `str.replace`: the
        # eight-space call this inserts *contains* the four-space call as a
        # substring, so inserting first would make the removal hit the new line and
        # leave the old one standing. The removal is also anchored on the comment
        # above it, since `from services import marketplace_settlement_service`
        # occurs eight times in this file and would be ambiguous.
        old=("    # webhook re-reads it rather than rewriting it.\n"
             "    pulse_snapshot_delivery_promise(tx)\n"),
        new="    # webhook re-reads it rather than rewriting it.\n",
        also=[("    except Exception:  # noqa: BLE001 - never lose a settlement over this\n",
               "        pulse_snapshot_delivery_promise(tx)\n"
               "    except Exception:  # noqa: BLE001 - never lose a settlement over this\n")],
        suites=[WIRING_SUITE],
    ),
    dict(
        name="an-absent-quote-is-recorded-as-an-empty-promise",
        refusal=(
            "An order with no attached quote records nothing *and says nothing*. "
            "Coercing the absent key to `{}` still writes no row -- `snapshot` "
            "refuses it -- so a row-count assertion cannot tell the difference. "
            "What changes is that the path nearly every real order takes starts "
            "logging an exception per settlement, which buries the one defect the "
            "log exists to surface."
        ),
        path=WIRING,
        old='    quoted = details.get("delivery_quote") if isinstance(details, dict) else None\n',
        new='    quoted = (details.get("delivery_quote") if isinstance(details, dict) else None) or {}\n',
        suites=[WIRING_SUITE],
    ),
    dict(
        name="the-window-is-requoted-at-settlement-instead-of-remembered",
        refusal=(
            "§36-37, and the reason this seam exists at all. Recomputing here "
            "answers a different question than the one the buyer agreed to, because "
            "every input has moved since the page they bought from. The mutant is "
            "the plausible version of the mistake: same shape, same confidence, "
            "fresh dates."
        ),
        path=WIRING,
        old="            result=quoted.get(\"result\") or quoted,\n",
        new="            result=_requote_at_settlement(quoted.get(\"result\") or quoted),\n",
        also=[("def pulse_snapshot_delivery_promise(tx, now=\"\"):\n",
               "def _requote_at_settlement(result):\n"
               "    fresh = json.loads(json.dumps(result or {}))\n"
               "    buyer = fresh.get(\"buyer\")\n"
               "    if isinstance(buyer, dict):\n"
               "        today = datetime.utcnow().date()\n"
               "        buyer[\"earliest\"] = str(today + timedelta(days=7))\n"
               "        buyer[\"latest\"] = str(today + timedelta(days=14))\n"
               "    return fresh\n"
               "\n"
               "\n"
               "def pulse_snapshot_delivery_promise(tx, now=\"\"):\n")],
        suites=[WIRING_SUITE],
    ),
    dict(
        name="the-promised-quantity-is-assumed-to-be-one",
        refusal=(
            "§26. The promise is for the line that was bought. A hard-coded 1 makes "
            "`delivery_promises` disagree with `marketplace_orders` about the same "
            "order, and disagree silently, since a window for one unit is a "
            "perfectly well-formed window."
        ),
        path=WIRING,
        old="        quantity, _unit = marketplace_order_line(details, int(tx.get(\"amount_cents\") or 0))\n",
        new="        quantity = 1\n",
        suites=[WIRING_SUITE],
    ),

    # -- The vocabulary: two implementations that must not drift -------------
    #
    # These are the only mutations in this file that edit TypeScript, and they
    # are the point of the contract test: drift is invisible in review, costs
    # nothing at build time on either side, and produces two descriptions of one
    # parcel. A test that compared "both sides have a sentence for this reason"
    # would survive all four of them.
    dict(
        name="app-and-web-disagree-about-the-seller-shipped-sentence",
        refusal=(
            "The one sentence the old blanket copy got right — said on the one "
            "listing it is true of. Reworded on the app side only, a buyer who "
            "opens a shared link and then the app reads two different "
            "explanations of the same listing."
        ),
        path=TS_COPY,
        old=("    \"This seller ships this item themselves — delivery is arranged "
             "with them after your order is confirmed.\","),
        new=("    \"The seller arranges delivery with you after your order is "
             "confirmed.\","),
        suites=[COPY_SUITE],
    ),
    dict(
        name="app-and-web-disagree-about-the-range-separator",
        refusal=(
            "Space, EN DASH, space. A hyphen on one side is invisible until a "
            "store screenshot is put beside the web page, which is exactly when "
            "it is expensive."
        ),
        path="mobile-native/src/api/delivery.ts",
        old="  return `${from} – ${to}`;",
        new="  return `${from} - ${to}`;",
        suites=[COPY_SUITE],
    ),
    dict(
        name="a-reason-becomes-terminal-on-one-surface-only",
        refusal=(
            "The grouping, not the wording: which reasons offer a retry. Moved on "
            "one side, the same listing shows a retry control in a browser and "
            "none on a phone — and the phone tells the buyer to stop."
        ),
        path=TS_COPY,
        old=("const TERMINAL_REASONS = new Set([\n"
             "  \"not_supplier_fulfilled\",\n"
             "  \"fulfillment_undeclared\"\n"
             "]);"),
        new=("const TERMINAL_REASONS = new Set([\n"
             "  \"not_supplier_fulfilled\",\n"
             "  \"fulfillment_undeclared\",\n"
             "  \"supplier_unavailable\"\n"
             "]);"),
        suites=[COPY_SUITE],
    ),
    dict(
        name="the-country-suffix-stops-requiring-a-known-destination",
        refusal=(
            "§14. \"Estimated 16 – 21 Mar to United States\" is a stronger claim "
            "than the window alone, and on the anonymous web page the country is "
            "an inference from how checkout is configured — not an observation of "
            "the reader."
        ),
        path=COPY,
        old="            suffix = (f\" to {where['country']}\"\n"
            "                      if where.get(\"known\") and where.get(\"country\") else \"\")",
        new="            suffix = f\" to {where['country']}\" if where.get(\"country\") else \"\"",
        suites=[COPY_SUITE, WEB_SUITE],
    ),
    dict(
        name="the-loading-line-claims-free-shipping",
        refusal=(
            "The one claim the page is sure of, printed beside the one it cannot "
            "yet support. If the estimate then comes back not_supplier_fulfilled, "
            "\"FREE Shipping\" was attached to a parcel PulseSoc is not shipping."
        ),
        path=COPY,
        old="    return {\"tone\": TONE_RETRYABLE, \"text\": LOADING_TEXT, \"shipping\": None,",
        new="    return {\"tone\": TONE_RETRYABLE, \"text\": LOADING_TEXT,\n"
            "            \"shipping\": FREE_SHIPPING_LINE,",
        suites=[COPY_SUITE],
    ),
    dict(
        name="an-unknown-reason-becomes-terminal",
        refusal=(
            "The eleventh reason a future deployment adds. Landing it in the "
            "terminal group tells a buyer to stop because the server said a word "
            "this build had not heard of."
        ),
        path=COPY,
        old="    if reason in TERMINAL_REASONS:\n        return TONE_TERMINAL\n    return TONE_RETRYABLE",
        new="    if reason in TERMINAL_REASONS:\n        return TONE_TERMINAL\n    return TONE_TERMINAL",
        suites=[COPY_SUITE],
    ),
    dict(
        name="a-half-window-prints-as-a-single-date",
        refusal=(
            "§124. One end of a range read as a promise. `None` is the whole "
            "reason `format_window` returns an optional rather than a best effort."
        ),
        path=COPY,
        old="    if not start or not end:\n        return None",
        new="    if not start and not end:\n        return None\n    start = start or end\n    end = end or start",
        suites=[COPY_SUITE, ESTIMATE_SUITE],
    ),

    # -- The web surface: a shared cache, and no supplier call at render -----
    dict(
        name="the-shared-cached-product-page-resolves-the-visitors-corridor",
        refusal=(
            "The defect this whole module exists for. "
            "`_marketplace_public_product_response` is `public, max-age=300`, so "
            "the first reader's country is stored by every proxy in front of the "
            "route and handed to the next five minutes of readers as their own. "
            "Both renderings are plausible sentences, so neither review nor a "
            "test that only checks the sentence parses can see it."
        ),
        # Mutated at the **call site** and not inside `web.py`, which is worth
        # recording because the obvious module-level mutation is not
        # discriminating: `shared_cache=True` forbids passing `headers` or
        # `buyer_user_id`, so inside `_context` a `resolve()` call has nothing to
        # resolve *from* and returns the same unknown record `policy_destination`
        # falls back to. The module cannot observe its own defect. What can go
        # wrong is a future edit to this page adding the destination inputs that
        # every other surface correctly passes -- so that is the mutation, and the
        # guard that turns it into a refusal has its own entry above.
        path=WIRING,
        old="    delivery_line = delivery_web.context(str(listing_id), shared_cache=True)",
        new="    delivery_line = delivery_web.context(str(listing_id), headers=request.headers)",
        suites=[WEB_SUITE],
    ),
    dict(
        name="the-shared-cache-guard-warns-instead-of-refusing",
        refusal=(
            "Silently dropping the visitor inputs gives a caller who believes "
            "they are rendering a personalised line a page that is merely wrong "
            "about delivery. Raising is what makes the mistake reach a human."
        ),
        path=WEB,
        old=("        raise ValueError(\n"
             "            \"a shared-cached page must not resolve a visitor's destination; \"\n"
             "            \"pass neither headers nor buyer_user_id with shared_cache=True\")"),
        new=("        LOGGER.warning(\"DELIVERY_WEB_SHARED_CACHE_VISITOR_IGNORED\")\n"
             "        headers, buyer_user_id = None, None"),
        suites=[WEB_SUITE],
    ),
    dict(
        name="the-product-page-render-calls-the-supplier",
        refusal=(
            "§19-25 and the performance budget. `cache_only` is the only reason "
            "this module is not a call to the endpoint's body: a cold product "
            "would put two CJ round trips in front of the LCP element, on the "
            "most-crawled page on the site."
        ),
        path=WEB,
        old="        cache_only=True,\n    )",
        new="        cache_only=False,\n    )",
        suites=[WEB_SUITE],
    ),
    dict(
        name="the-picker-offers-countries-checkout-would-refuse",
        refusal=(
            "§55. A buyer shown a correct window for a corridor Stripe rejects at "
            "payment is worse off than one who was never offered it."
        ),
        path=WEB,
        old="        codes = marketplace_fulfillment.shipping_countries()",
        new="        codes = (\"US\", \"CA\", \"GB\", \"AU\")",
        suites=[WEB_SUITE],
    ),
    dict(
        name="a-settled-refusal-is-re-asked-once-per-visitor",
        refusal=(
            "`pending` is what tells the page to fetch, and only a cache miss "
            "earns it. A seller-shipped listing is final: a page that fetched for "
            "it would spend an HTTP request per reader, forever, to be told the "
            "same thing."
        ),
        path=WEB,
        old="    return _rendered(delivery_copy.delivery_copy(delivery=buyer, destination=where),\n"
            "                     variant_ref, quantity, where, buyer, pending=False)",
        new="    return _rendered(delivery_copy.delivery_copy(delivery=buyer, destination=where),\n"
            "                     variant_ref, quantity, where, buyer, pending=True)",
        suites=[WEB_SUITE],
    ),
    dict(
        name="a-broken-estimate-takes-the-product-page-down-with-it",
        refusal=(
            "This is called from inside a product page render. A delivery "
            "estimate must never be the reason a listing 500s, and the pending "
            "shape is the right answer for an unexpected failure because the "
            "client fetch that follows goes to the endpoint, which owns the "
            "reporting."
        ),
        path=WEB,
        old=("    except Exception:  # noqa: BLE001 — see the docstring: never break the page\n"
             "        LOGGER.exception(\"DELIVERY_WEB_CONTEXT_FAILED variant_ref=%s\", variant_ref)\n"
             "        return _pending(variant_ref, quantity)"),
        new=("    except ValueError:\n"
             "        raise"),
        suites=[WEB_SUITE],
    ),
    dict(
        name="the-pending-line-is-injected-by-the-script-instead-of-rendered",
        refusal=(
            "§ performance budget. An element that grows a line of text after a "
            "fetch pushes the buy control down the page — the layout shift Core "
            "Web Vitals measures — and leaves a reader with JavaScript disabled, "
            "or a crawler, with no delivery sentence at all."
        ),
        path=WEB,
        old="    return ('<div %s><span class=\"pulse-delivery__text\">%s</span>%s%s</div>'\n"
            "            % (\" \".join(attributes), _attr(line[\"text\"]), shipping, picker))",
        new="    return ('<div %s><span class=\"pulse-delivery__text\">%s</span>%s%s</div>'\n"
            "            % (\" \".join(attributes),\n"
            "               \"\" if line[\"pending\"] else _attr(line[\"text\"]), shipping, picker))",
        suites=[WEB_SUITE],
    ),
]


def run_suites(sandbox: pathlib.Path, suites, verbose: bool):
    """Red if any suite fails.

    One process per file: these suites are pure, but the repo's convention is
    per-file isolation and borrowing it costs nothing here. Bytecode writing is
    off so a mutated module can never be served from a stale ``__pycache__`` on
    the following run.
    """
    env = {**os.environ, "PYTHONPATH": str(sandbox), "PYTHONDONTWRITEBYTECODE": "1"}
    for suite in suites:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", suite, "-q", "-x", "-p", "no:cacheprovider"],
            cwd=sandbox, capture_output=True, text=True, env=env,
        )
        tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        if verbose:
            print(f"      {suite}: exit {proc.returncode} | {tail}")
        if proc.returncode != 0:
            return False, suite, tail
    return True, None, ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    # A development aid, deliberately loud about what it does. Narrowing the run
    # narrows the baseline with it, so a `--only` pass proves nothing about the
    # mutations it skipped and the exit code says so in the summary line. CI runs
    # this file with no arguments.
    parser.add_argument("--only", default="",
                        help="run only mutations whose name contains this substring")
    args = parser.parse_args()

    selected = [m for m in MUTATIONS if args.only in m["name"]]
    if not selected:
        print(f"ABORT: --only {args.only!r} matched none of the {len(MUTATIONS)} mutations.")
        return 2

    # Names are the only thing a survivor is reported by, so two mutations sharing
    # one makes the report unreadable at exactly the moment it matters: the reader
    # cannot tell which of two modules is unguarded, and "look it up" means
    # grepping a name that matches twice. This has already happened once here -- a
    # bool-weight check exists in both the CJ provider and the facts resolver, and
    # both entries were called the same thing.
    seen: dict = {}
    for mutation in MUTATIONS:
        if mutation["name"] in seen:
            print(f"ABORT: two mutations are named {mutation['name']!r} "
                  f"({seen[mutation['name']]} and {mutation['path']}). A survivor "
                  f"report cannot say which one survived.")
            return 2
        seen[mutation["name"]] = mutation["path"]

    with tempfile.TemporaryDirectory(prefix="pulse-eta-mutation-") as tmp:
        sandbox = pathlib.Path(tmp) / "repo"
        print(f"Copying the tree to {sandbox} — the repository itself is never mutated.")
        shutil.copytree(ROOT, sandbox, ignore=SKIP, symlinks=True)
        for relative in CARRIED:
            target = sandbox / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(ROOT / relative, target)

        print("\nBaseline: the suites must be green before a mutation means anything.")
        baseline = sorted({suite for m in selected for suite in m["suites"]})
        green, suite, tail = run_suites(sandbox, baseline, args.verbose)
        if not green:
            print(f"ABORT: {suite} is already failing ({tail}). A red baseline makes\n"
                  f"       every mutation look killed.")
            return 2
        print(f"  {len(baseline)} suites green.\n")

        survivors = []
        for mutation in selected:
            path = sandbox / mutation["path"]
            original = path.read_text()
            # `also` lets one entry make several edits that are only a coherent
            # alternative implementation *together*. The DDL's primary key is the
            # case that needed it: moving it inline without removing the table
            # constraint declares two primary keys, and SQLite then rejects the
            # CREATE. That mutant dies of a syntax error, which proves the fixture
            # runs and nothing about whether the divergence is observed.
            edits = [(mutation["old"], mutation["new"])] + list(mutation.get("also") or [])
            drifted = [old for old, _ in edits if original.count(old) != 1]
            if drifted:
                # Not a survivor in the interesting sense — a harness failure.
                # Reported as one anyway, because an entry that cannot apply is an
                # entry that proves nothing while reading as coverage.
                counts = ", ".join(str(original.count(old)) for old in drifted)
                state = "DRIFTED " if "0" in counts.split(", ") else "AMBIGUOUS"
                print(f"  {state} {mutation['name']}")
                print(f"           {len(drifted)} of {len(edits)} anchors resolve "
                      f"{counts}x in {mutation['path']}; re-point this entry.")
                survivors.append(mutation["name"])
                continue
            mutated = original
            for old, new in edits:
                mutated = mutated.replace(old, new)
            path.write_text(mutated)
            try:
                still_green, _, _ = run_suites(sandbox, mutation["suites"], args.verbose)
            finally:
                path.write_text(original)
            if still_green:
                print(f"  SURVIVED {mutation['name']}")
                print(f"           {mutation['refusal']}")
                print(f"           Removing it changed no test result. Nothing observes this.")
                survivors.append(mutation["name"])
            else:
                print(f"  killed   {mutation['name']}")

    print()
    if survivors:
        print(f"FAIL: {len(survivors)} of {len(selected)} mutations survived: "
              f"{', '.join(survivors)}")
        return 1
    if len(selected) != len(MUTATIONS):
        print(f"PARTIAL: {len(selected)} of {len(MUTATIONS)} mutations killed "
              f"(--only {args.only!r}). This is not a pass.")
        return 3
    print(f"PASS: all {len(MUTATIONS)} mutations killed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
