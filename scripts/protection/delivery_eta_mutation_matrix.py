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

# Directories with nothing this slice imports. Copying them costs 300MB and
# several seconds per run for no added coverage.
SKIP = shutil.ignore_patterns(
    ".git", "__pycache__", "*.pyc", ".pytest_cache", "node_modules",
    "reports", "release-assets", "mobile", "mobile-native", "static", "assets",
    ".fuse_hidden*", "*.db",
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
        name="a-boolean-weight-passes-as-one-gram",
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
    args = parser.parse_args()

    with tempfile.TemporaryDirectory(prefix="pulse-eta-mutation-") as tmp:
        sandbox = pathlib.Path(tmp) / "repo"
        print(f"Copying the tree to {sandbox} — the repository itself is never mutated.")
        shutil.copytree(ROOT, sandbox, ignore=SKIP, symlinks=True)

        print("\nBaseline: the suites must be green before a mutation means anything.")
        baseline = sorted({suite for m in MUTATIONS for suite in m["suites"]})
        green, suite, tail = run_suites(sandbox, baseline, args.verbose)
        if not green:
            print(f"ABORT: {suite} is already failing ({tail}). A red baseline makes\n"
                  f"       every mutation look killed.")
            return 2
        print(f"  {len(baseline)} suites green.\n")

        survivors = []
        for mutation in MUTATIONS:
            path = sandbox / mutation["path"]
            original = path.read_text()
            occurrences = original.count(mutation["old"])
            if occurrences != 1:
                # Not a survivor in the interesting sense — a harness failure.
                # Reported as one anyway, because an entry that cannot apply is an
                # entry that proves nothing while reading as coverage.
                state = "DRIFTED " if occurrences == 0 else "AMBIGUOUS"
                print(f"  {state} {mutation['name']}")
                print(f"           anchor appears {occurrences}x in {mutation['path']};"
                      f" re-point this entry.")
                survivors.append(mutation["name"])
                continue
            path.write_text(original.replace(mutation["old"], mutation["new"]))
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
        print(f"FAIL: {len(survivors)} of {len(MUTATIONS)} mutations survived: "
              f"{', '.join(survivors)}")
        return 1
    print(f"PASS: all {len(MUTATIONS)} mutations killed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
