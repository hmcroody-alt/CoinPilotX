#!/usr/bin/env python3
"""Derive the environment half of the control-plane inventory by scanning.

The fifteen ``feature_flags`` rows are historical and fixed, so they are written
down in :mod:`services.pulse_control_plane.legacy`. The environment gates are
not: they change whenever somebody adds an ``os.getenv`` or edits a Railway
variable, and a hand-maintained list of them would be stale within a week and
would then be believed. So this script rediscovers them every run.

Usage::

    python3 scripts/control_plane_audit.py                 # scan source only
    python3 scripts/control_plane_audit.py --env-file F    # compare against a
                                                           # KEY=VALUE dump, e.g.
                                                           # `railway variables --kv`

Exit status is 1 when the audit finds a gate that is set in the supplied
environment but read by nothing. That is the failure mode worth breaking a build
over, because it is indistinguishable from a working switch until somebody
relies on it during an incident.

Deliberately not wired into CI by this change. Turning a new audit red on its
first run is how audits get disabled; the follow-up issue proposes enabling it
once the thirteen already-dead variables are removed from Railway.
"""

from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[1]

#: Names that look like a switch. Substring match on purpose — the point is to
#: over-collect and then classify, because a gate this misses is a gate the
#: inventory never mentions.
GATE_NAME = re.compile(r"ENABLE|DISABL|FLAG|ROLLOUT|BETA|KILL|ALLOW|FEATURE|BUSINESS_OS")

#: ``os.getenv("X")`` / ``os.getenv("X", default)`` / ``os.environ.get(...)``.
#: The default is captured loosely; it is reported verbatim rather than parsed,
#: since the useful signal is "what literal sits there", not its Python value.
#:
#: This pattern finds *defaults*. It must never be used to decide that a gate
#: has no reader — see :func:`readers_for`.
GETENV = re.compile(
    r"""(?:os\.getenv|os\.environ\.get)\(\s*["']([A-Z][A-Z0-9_]+)["']\s*(?:,\s*(.{0,60}?))?\)"""
)

#: A gate whose call site tests membership in the FALSY set is open by default.
#: Detected from the line itself rather than from the default string, because
#: two gates can share a default of "" and mean opposite things.
FALSY_TEST = re.compile(r"not\s+in\s*[\(\{][^)}]*(?:\"0\"|'0'|\"false\"|'false')")


#: This package names dozens of environment variables in its own prose — the
#: ``real_gate`` field of every legacy row is a variable name. Counting those as
#: readers would let the inventory vouch for itself, so the audit excludes its
#: own source from reader detection.
SELF = ("services/pulse_control_plane/", "scripts/control_plane_audit.py")


def source_files() -> list[pathlib.Path]:
    files = [REPO / "bot.py"]
    files += sorted((REPO / "services").rglob("*.py"))
    files += sorted(REPO.glob("*.py"))
    seen, out = set(), []
    for f in files:
        if not f.exists():
            continue
        rel = str(f.relative_to(REPO))
        if rel in seen or rel.startswith(SELF):
            continue
        seen.add(rel)
        out.append(f)
    return out


def readers_for(names: set[str], files: list[pathlib.Path]) -> dict[str, list[str]]:
    """Locate readers by **name mention**, not by call shape.

    An earlier version of this script looked for ``os.getenv("NAME")`` and
    concluded that 51 production variables were dead. It was wrong about 38 of
    them, including ``MARKETPLACE_CARD_PAYMENTS_ENABLED`` — the gate that keeps
    checkout open — because this codebase reads environment variables through at
    least four indirections:

    * ``_flag("UNDX_ROUTER_ENABLED", False)``          (``undx_router.py:231``)
    * ``_env_bool("COMMAND_CENTER_ENABLED", False)``   (``command_center_client.py:63``)
    * ``_env_enabled("PUSH_NOTIFICATIONS_ENABLED", True)`` (``push_service.py:182``)
    * ``CARD_PAYMENTS_ENABLED_ENV_VAR = "MARKETPLACE_CARD_PAYMENTS_ENABLED"``
      then read through the constant (``marketplace_payment_pause.py:55``)

    and one case where the name is assembled at runtime
    (``bot.py:52554`` picks between two names depending on which is set).

    So detection is a plain substring search for the variable name. That
    over-counts — a name in a comment registers as a reader — and over-counting
    is the correct bias for a tool whose output is "nothing reads this, you may
    delete it". A false DEAD deletes a live kill switch; a false alive merely
    leaves a stale variable in place for someone to check by hand.
    """
    hits: dict[str, list[str]] = {name: [] for name in names}
    for path in files:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        rel = str(path.relative_to(REPO))
        for name in names:
            if name in text:
                hits[name].append(rel)
    return hits


def scan() -> dict[str, dict]:
    found: dict[str, dict] = {}
    for path in source_files():
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        rel = path.relative_to(REPO)
        for lineno, line in enumerate(text.splitlines(), 1):
            for match in GETENV.finditer(line):
                name, default = match.group(1), (match.group(2) or "").strip()
                if not GATE_NAME.search(name):
                    continue
                entry = found.setdefault(
                    name, {"readers": [], "defaults": set(), "fails_open": False}
                )
                entry["readers"].append(f"{rel}:{lineno}")
                entry["defaults"].add(default or "<none>")
                if FALSY_TEST.search(line):
                    entry["fails_open"] = True
    return found


def load_env(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw in pathlib.Path(path).read_text(errors="replace").splitlines():
        if "=" in raw and not raw.lstrip().startswith("#"):
            key, value = raw.rstrip("\n").split("=", 1)
            values[key.strip()] = value
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", help="KEY=VALUE dump of the environment to compare against")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a report")
    args = parser.parse_args()

    sys.path.insert(0, str(REPO))
    from services.pulse_control_plane.classify import classify_env_gate
    from services.pulse_control_plane.reconcile import summary

    scanned = scan()
    env = load_env(args.env_file) if args.env_file else {}

    names = set(scanned) | {k for k in env if GATE_NAME.search(k)}
    mentions = readers_for(names, source_files())

    results = []
    for name in sorted(names):
        entry = scanned.get(name, {"readers": [], "defaults": set(), "fails_open": False})
        # Call-shape hits are the precise ones and are shown first; name
        # mentions are what decide liveness.
        entry["readers"] = entry["readers"] or [f"{f} (by name)" for f in mentions.get(name, [])]
        defaults = sorted(entry["defaults"])
        # Only a single unambiguous literal default can be used; several call
        # sites disagreeing is itself worth surfacing rather than averaging.
        default = None
        if len(defaults) == 1 and defaults[0] not in ("<none>", ""):
            default = defaults[0].strip("\"'")
        verdict = classify_env_gate(
            name,
            observed=env.get(name),
            default=default,
            reader_count=len(mentions.get(name, [])),
            fails_open=entry["fails_open"],
        )
        results.append(
            {
                "key": name,
                "verdict": verdict.verdict,
                "effective": verdict.effective,
                "evidence": verdict.evidence,
                "readers": entry["readers"][:4],
                "reader_count": len(entry["readers"]),
            }
        )

    dead_but_set = [r for r in results if r["verdict"] == "DEAD" and r["reader_count"] == 0]

    if args.json:
        print(json.dumps({"legacy": summary(), "env_gates": results}, indent=2))
        return 1 if dead_but_set else 0

    legacy = summary()
    print("=== feature_flags (15 legacy rows) ===")
    for verdict, count in sorted(legacy["verdicts"].items()):
        print(f"  {verdict:<18} {count}")
    print(f"  seed understates reality: {', '.join(legacy['seed_understates']) or '-'}")
    print(f"  seed overstates reality:  {', '.join(legacy['seed_overstates']) or '-'}")
    print(f"  activation allowed:       {legacy['activation_allowed']}")
    if legacy["blocked_reason"]:
        print(f"  blocked: {legacy['blocked_reason']}")

    print(f"\n=== environment gates ({len(results)}) ===")
    buckets: dict[str, int] = {}
    for r in results:
        buckets[r["verdict"]] = buckets.get(r["verdict"], 0) + 1
    for verdict, count in sorted(buckets.items()):
        print(f"  {verdict:<18} {count}")

    if dead_but_set:
        print(f"\n!! {len(dead_but_set)} gate(s) set in the environment with no reader:")
        for r in dead_but_set:
            print(f"     {r['key']}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
