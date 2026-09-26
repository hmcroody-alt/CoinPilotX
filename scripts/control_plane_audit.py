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
#:
#: The second line is the one that was missing. Three dead declarations —
#: ``TRANSLATION_FAIL_OPEN``, ``TRANSLATION_PRESERVE_ORIGINAL`` and
#: ``TRANSLATION_SHOW_ORIGINAL_OPTION`` — were gate-shaped semantically and not
#: lexically, so this audit never classified them and a human root-cause report
#: found them instead. ``TRANSLATION_FAIL_OPEN`` is the one that mattered: the
#: name asserts a failure policy for a live integration, so an operator reaching
#: for it during a translation outage would have set a variable nothing reads and
#: come away believing they had changed the behaviour.
#:
#: Widening this is safe in a way that widening reader collection is not. It
#: cannot invent a false DEAD, because it does not change what counts as a
#: reader — it only enlarges the set of names being judged. The risk is instead a
#: gate that is red on arrival, which gets the audit disabled rather than fixed,
#: so each token below was measured against ``.env.example`` before being added.
#: The added tokens take the matched set from 189 names to 213, and **all 24 of
#: the newly matched names already have readers** — every one is a real gate this
#: audit had simply never looked at, so nothing is newly condemned.
#:
#: Tokens deliberately *not* added, having been measured and rejected: ``LOCK``,
#: ``USE_``, ``AUTO_``, ``HIDE_`` and ``GATE`` match numeric thresholds and plain
#: accidents of spelling (``BLOCKSTREAM_BASE_URL``, ``AGGREGATE_ANALYTICS_``
#: ``RETENTION_DAYS``, ``PULSESOC_REFRESH_REUSE_GRACE_SECONDS``). Those are false
#: alives rather than false DEADs and so would do no harm, but they would fill
#: the inventory with things that are not switches.
GATE_NAME = re.compile(
    r"ENABLE|DISABL|FLAG|ROLLOUT|BETA|KILL|ALLOW|FEATURE|BUSINESS_OS"
    r"|FAIL_OPEN|FAIL_CLOSED|REQUIRE|DRY_RUN|STRICT|FORCE|GUARD|SANDBOX|ONLY|PRESERVE|SHOW_"
)

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


#: Sources that *name* environment variables rather than *read* them, and would
#: therefore vouch for the very variables they exist to retire.
#:
#: ``services/pulse_control_plane/``, ``scripts/control_plane_audit.py``
#:     This package names dozens of variables in its own prose — the
#:     ``real_gate`` field of every legacy row is a variable name. Counting
#:     those as readers would let the inventory vouch for itself.
#: ``scripts/undx_railway_variable_audit.py``
#:     A *second* variable inventory, found when widening the scan scope in
#:     :func:`source_files` silently rescued ``UNDX_METRICS_ENABLED`` from the
#:     dead list. Its only mention of that name is in an ``EQUIVALENTS`` map of
#:     old name → new name: a record that the variable was **renamed**, which is
#:     the strongest possible evidence it is dead, read by this audit as proof it
#:     is alive. A retirement record must never vouch for what it retires.
#:
#: This is an enumeration, and :func:`source_files` argues at length against
#: enumerations. The difference is direction. A stale entry *there* omits a
#: package and invents a false DEAD — advice to delete a live switch. A stale
#: entry *here* omits an inventory and invents a false alive — a dead variable
#: left in place for someone to check by hand. Only one of those is an outage,
#: so only one of them may be left to a list somebody has to remember to update.
SELF = (
    "services/pulse_control_plane/",
    "scripts/control_plane_audit.py",
    "scripts/undx_railway_variable_audit.py",
)

#: Exceptions to :data:`SELF` — files inside an excluded inventory that are
#: nonetheless genuine readers, and must be scanned.
#:
#: Excluding ``services/pulse_control_plane/`` wholesale was too blunt. The
#: package is almost entirely prose about variables, but ``runtime.py`` is the
#: one module that actually *reads* one: ``CONSULTATION_ENV`` is
#: ``PULSE_CONTROL_PLANE_CONSULTATION``, the wave-1 arming switch, set on the
#: production service. With the package excluded the audit saw zero readers for
#: it and put the live arming switch on the dead list — advice to disarm the
#: control plane, from the control plane's own audit.
#:
#: It did no harm only because ``PULSE_CONTROL_PLANE_CONSULTATION`` does not
#: match :data:`GATE_NAME`, so it was never classified and never reported. That
#: is luck, not a safeguard: the same blindness applies to any gate-shaped
#: variable this package's runtime ever reads.
#:
#: The split is not a special case. ``runtime.py`` is the only module of the
#: package allowed on a request path, and it is the only module that reads the
#: environment — the architectural boundary and the reader boundary are the same
#: line, so keeping them in step is one rule rather than two.
SELF_READERS = ("services/pulse_control_plane/runtime.py",)

#: Top-level packages that are **not** production readers, and why each is
#: excluded rather than simply forgotten.
#:
#: ``tests``
#:     A test that sets a variable is not a reader of it. Counting tests would
#:     make every gate look alive the moment somebody wrote a fixture for it,
#:     which is the failure direction that leaves dead variables in place —
#:     tolerable, but it would make the audit's output useless rather than
#:     merely conservative.
#: ``mobile``, ``mobile-native``
#:     JavaScript/TypeScript. Their ``EXPO_PUBLIC_*`` flags are a different
#:     control plane with different failure modes (see the note in
#:     ``services/pulse_control_plane/classify.py``) and are not Railway service
#:     variables.
#:
#: ``scripts`` is deliberately **not** on this list. A one-off script is not a
#: production gate, but deleting a variable that only a script reads still
#: breaks that script, and this tool's output is read as "you may delete this".
NON_READER_DIRS = ("tests", "mobile", "mobile-native")


def source_files() -> list[pathlib.Path]:
    """Every Python file that could plausibly read a gate.

    The scope used to be ``bot.py`` plus ``services/`` plus root-level modules,
    and that silently defeated the over-counting argument in
    :func:`readers_for`. A gate read *only* from a top-level package outside
    that set contributed zero readers and was reported DEAD — advice to delete
    a live switch, which is the one error this tool must not make.

    ``pulse_communications_v2/`` is the concrete case. It is a registered route
    pack (``bot.py:1389``), and ``PULSE_COMM_V2_SSE_ENABLED`` gates a real SSE
    route at ``pulse_communications_v2/routes.py:899``. The audit called it
    dead. It was saved from doing harm only by the accident that the variable
    is not currently set in Railway, so the environment comparison never
    reached it.

    So the scope is now derived — every top-level Python package and every
    top-level module — rather than enumerated. An enumeration is a list that
    goes stale the next time somebody adds a package, and goes stale silently,
    in the direction of false DEAD.
    """
    files = [REPO / "bot.py"]
    files += sorted(REPO.glob("*.py"))
    for entry in sorted(REPO.iterdir()):
        if not entry.is_dir() or entry.name in NON_READER_DIRS:
            continue
        if entry.name.startswith(".") or entry.name == "node_modules":
            continue
        if not (entry / "__init__.py").exists() and entry.name != "scripts":
            continue
        files += sorted(entry.rglob("*.py"))
    seen, out = set(), []
    for f in files:
        if not f.exists():
            continue
        rel = str(f.relative_to(REPO))
        if rel in seen or (rel.startswith(SELF) and rel not in SELF_READERS):
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
