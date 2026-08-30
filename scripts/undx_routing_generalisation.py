#!/usr/bin/env python3
"""How well UNDX routes wording it has never been shown.

``pulsesoc_undx_command_benchmark.py`` reports ``routes_to_capability`` at
4000/4000. Read plainly that says the router is perfect. It is not what the number
means, and this script exists because the difference matters more than the number.

``match_capability`` scores a registered intent phrase against a message by asking
whether the phrase's words appear **in order** in that message, charging a small
penalty per skipped content word. That is the whole of it. No synonym table, no
embedding, no notion that "silence" and "pause" are the same request. So a message
routes when it happens to reuse the registry's vocabulary and returns nothing when it
does not — and a corpus written while watching that matcher will, without anyone
intending it, drift toward bodies that reuse the vocabulary. The benchmark then
measures the corpus against itself, one layer up from the tautology its own docstring
was written to kill.

Three populations are measured here, and the spread between them is the finding:

``co_authored``
    The eighty capabilities the corpus covered before August 2026. Their bodies were
    written alongside the matcher.

``blind``
    The forty capabilities added when the corpus was completed to full registry
    coverage, written from each capability's meaning rather than its phrases.

``held_out``
    ``HELD_OUT_CONTROL`` — the control. Blind paraphrases for capabilities in the
    ``co_authored`` group. If the first two differ because the forty are intrinsically
    harder, this group scores like the eighty. If they differ because of how the
    corpus was written, it scores like the forty.

Exit status is 0 whatever the numbers are. This is an instrument, not a gate: a
threshold here would create pressure to reword the control, and a reworded control
measures nothing. Regressions belong in the benchmark, which asserts.

Usage::

    python3 scripts/undx_routing_generalisation.py
    python3 scripts/undx_routing_generalisation.py --json
    python3 scripts/undx_routing_generalisation.py --misses   # every miss, with target
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scripts.undx_benchmark_corpus import COMMANDS, HELD_OUT_CONTROL  # noqa: E402
from services.undx_agent_runtime import match_capability  # noqa: E402
from services.undx_capability_registry import REGISTRY  # noqa: E402

#: The forty capabilities that had no corpus entry until the corpus was completed to
#: full registry coverage. Named explicitly rather than derived, because the property
#: that matters — "these bodies were written without consulting the matcher" — is a
#: fact about their authorship and cannot be recovered from the file later.
BLIND: frozenset[str] = frozenset({
    "business.campaign.pause", "business.campaign.resume", "business.profile.update",
    "crypto.alerts.activity", "crypto.market.observations", "crypto.market.window",
    "crypto.portfolio.history", "crypto.portfolio.holding.add",
    "crypto.portfolio.holding.delete", "crypto.portfolio.holding.update",
    "crypto.portfolio.holdings.list", "crypto.portfolio.summary",
    "crypto.watchlist.add", "crypto.watchlist.list", "crypto.watchlist.remove",
    "feed.posts.delete", "feed.posts.hide", "feed.report",
    "localization.region.update", "localization.translation.update",
    "marketplace.listing.create", "marketplace.listing.delete",
    "marketplace.listing.pause", "marketplace.listing.resume",
    "marketplace.listing.update", "messages.mark_read", "messages.send",
    "notifications.mark_all_read", "notifications.mark_read",
    "presence.privacy.update", "profile.bio.update", "profile.block", "profile.unblock",
    "reels.comment.create", "reels.comment.delete", "reels.comment.update",
    "reels.delete", "settings.appearance.theme.update",
    "settings.privacy.audience.update", "translation.content.translate",
})


def _measure(corpus: dict[str, tuple[str, ...]]) -> dict[str, object]:
    """Route every body once and record where it went.

    A miss is recorded with its destination rather than as a bare failure, because
    the two kinds read differently. Landing on the wrong capability is a scoring
    problem — two phrases competing, one winning. Landing on *nothing* means no
    registered phrase was a subsequence of the message at all, and no amount of
    tie-breaking would have helped. The second is the overwhelming majority here and
    it is the one that cannot be fixed by tuning ``_GAP_PENALTY``.
    """
    hits = 0
    total = 0
    to_nothing = 0
    misses: list[dict[str, str]] = []
    for capability_id, bodies in sorted(corpus.items()):
        for body in bodies:
            total += 1
            match = match_capability(body)
            observed = match.capability_id if match else ""
            if observed == capability_id:
                hits += 1
                continue
            if not observed:
                to_nothing += 1
            misses.append({"expected": capability_id, "observed": observed,
                           "command": body})
    return {
        "capabilities": len(corpus),
        "bodies": total,
        "routed": hits,
        "rate": round(100.0 * hits / total, 1) if total else 0.0,
        "missed_to_nothing": to_nothing,
        "missed_to_wrong_capability": len(misses) - to_nothing,
        "misses": misses,
    }


def _reject_unrouteable_targets() -> None:
    """A control entry naming a capability that does not exist is a rigged miss.

    Raised rather than reported, and checked before any measurement runs, because the
    number this script produces is only worth reading if every case in it *could* have
    passed. The first draft of ``HELD_OUT_CONTROL`` named ``profile.follow``, which is
    spelled ``social.follow`` in the registry — three cases that would have counted as
    routing failures while being nothing of the kind, quietly making the matcher look
    worse than it is. A control that flatters its own conclusion is as useless as one
    that hides a defect.
    """
    for name, corpus in (("HELD_OUT_CONTROL", HELD_OUT_CONTROL), ("COMMANDS", COMMANDS)):
        unknown = sorted(set(corpus) - set(REGISTRY))
        if unknown:
            raise SystemExit(
                f"{name} names {len(unknown)} capability id(s) absent from the "
                f"registry: {', '.join(unknown)}. Every case must be winnable.")


def report() -> dict[str, object]:
    _reject_unrouteable_targets()
    co_authored = {c: b for c, b in COMMANDS.items() if c not in BLIND}
    blind = {c: b for c, b in COMMANDS.items() if c in BLIND}
    groups = {
        "co_authored": _measure(co_authored),
        "blind": _measure(blind),
        "held_out": _measure(HELD_OUT_CONTROL),
    }
    intents = {
        "co_authored_mean": round(
            sum(len(REGISTRY[c].intents) for c in co_authored) / max(len(co_authored), 1), 1),
        "blind_mean": round(
            sum(len(REGISTRY[c].intents) for c in blind) / max(len(blind), 1), 1),
    }
    return {"groups": groups, "intents_per_capability": intents,
            "registry_intent_phrases": sum(len(s.intents) for s in REGISTRY.values())}


def main(argv: list[str]) -> int:
    data = report()
    if "--json" in argv:
        print(json.dumps(data, indent=2, sort_keys=True))
        return 0

    print("UNDX routing generalisation\n")
    for name in ("co_authored", "blind", "held_out"):
        g = data["groups"][name]
        print(f"  {name:12} {g['routed']:>5}/{g['bodies']:<5} = {g['rate']:>5.1f}%"
              f"   ({g['capabilities']} capabilities, "
              f"{g['missed_to_nothing']} routed to nothing, "
              f"{g['missed_to_wrong_capability']} to the wrong one)")

    ipc = data["intents_per_capability"]
    print(f"\n  Declared intent phrases per capability: "
          f"co-authored {ipc['co_authored_mean']}, blind {ipc['blind_mean']}.")
    print("  The blind group declares more of them and routes far worse, so the gap is")
    print("  not thin registry coverage. It is that the matcher compares words, and only")
    print("  the co-authored bodies were written in the registry's words.")

    if "--misses" in argv:
        print("\nMisses:")
        for name in ("blind", "held_out"):
            for m in data["groups"][name]["misses"]:
                where = m["observed"] or "nothing"
                print(f"  {name:9} {m['expected']:34} -> {where:28} {m['command']!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
