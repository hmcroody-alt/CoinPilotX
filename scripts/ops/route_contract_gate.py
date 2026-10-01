#!/usr/bin/env python3
"""Build-time gate: every path a client calls must exist on the server.

Why this exists
---------------
``GET /health/routes`` already answers a different question well: *did this
deployment's route packs register?* It is a **deploy-time liveness** check, and
its evidence is a hand-maintained list of ten paths in ``bot.py``. That list
cannot grow to cover the surface -- the native client alone calls 449 distinct
paths, and the web client will add hundreds more -- and a hand-maintained list
is wrong the moment someone forgets to append to it.

This gate answers the other question, at **build time**: *does the server have a
route for every path a client actually calls?* It does not maintain a list. It
derives one from client source on every run, so a call site added today is
covered today, by nobody's discipline.

The failure it catches is real and has shipped here before: a client ships a
call to an endpoint the backend never implemented, the client swallows the 404,
and the feature is silently dead. Nothing in CI notices, because the client
builds fine and the server starts fine -- the break lives in the space between
them, which is exactly the space no single test suite owns.

Three outcomes, not two
-----------------------
Every call site lands in one of:

  * ``matched``     -- a server rule accepts this path.
  * ``missing``     -- no server rule accepts it. Fails the gate.
  * ``unresolved``  -- the path could not be determined statically.

The third category is the point. 53% of call sites here contain template
interpolation, and a gate that silently skipped those would check half the
surface and report a clean bill of health -- the defect class this gate exists
to catch, committed by the gate itself. ``unresolved`` is therefore **not** a
pass: it exits 3 (``EXIT_NO_DATA``), distinct from both success and failure, so
a caller can tell "the contract is broken" from "I could not check the
contract". Today the extractor resolves all 449 with none left over; the
category exists so that the day a new syntax defeats it, the gate says so
instead of shrinking quietly.

Known-pending endpoints
-----------------------
Some mismatches are deliberate: client scaffolding written ahead of a backend,
behind a flag that is off. Those live in ``config/route-contract-allowlist.json``
with a reason and a reference, because a gate that is permanently red is a gate
nobody reads -- the same argument that kept ``edge_status()`` from warning on
long forwarded chains.

The allowlist is checked in **both** directions. An entry whose route now exists
fails the gate as a stale entry. A list that only ever suppresses failures rots
silently and ends up suppressing a real one; a list that also fails when it
becomes untrue cannot.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import tempfile

REPO = pathlib.Path(__file__).resolve().parents[2]
ALLOWLIST_PATH = REPO / "config" / "route-contract-allowlist.json"

EXIT_OK = 0
EXIT_CONTRACT_BREAK = 1
EXIT_NO_DATA = 3

# Client surfaces to check. Adding the web client is one line here; the
# extraction below is deliberately not mobile-specific.
CLIENTS = [
    ("mobile-native", "mobile-native/src/api", "*.ts"),
]

# `const X = "..."` / `export const X = \`...\`` -- the string constants a call
# site interpolates. Resolved per file: `BASE` is not the same string in
# dropshipping.ts as in presence.ts, and a global table would silently mix them.
CONST_RE = re.compile(
    r"^\s*(?:export\s+)?const\s+([A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
    r"([`'\"])(.*?)\2\s*;?\s*$",
    re.M,
)
# The wrapper every client call goes through. The optional `<...>` is the
# TypeScript generic for the response type.
CALL_RE = re.compile(r"pulseApi\s*(?:<[^>]*>)?\s*\(\s*([`'\"])(.*?)\1", re.S)
# `${...}` with one level of nested braces, which covers object literals passed
# to a query-builder call: `${undxQuery({ org_id: x })}`.
PLACEHOLDER_RE = re.compile(r"\$\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", re.S)
IDENT_PLACEHOLDER_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

SEGMENT = "\x00"  # stands in for a resolved-at-runtime path segment


def resolve_consts(expr: str, consts: dict, depth: int = 0) -> str:
    """Substitute `${NAME}` from this file's constants, recursively.

    Constants reference each other (`const API = `${BASE}/api``), so this runs
    to a fixed point rather than once. The depth cap is for a cycle, which is
    not valid TypeScript but is cheap to survive.
    """
    if depth > 6:
        return expr
    out = IDENT_PLACEHOLDER_RE.sub(
        lambda m: consts.get(m.group(1), m.group(0)), expr)
    return resolve_consts(out, consts, depth + 1) if out != expr else out


def looks_like_query_suffix(name: str, src: str) -> bool:
    """Can this trailing interpolation expand to a query string or nothing?

    Every trailing placeholder in this codebase today is built as
    ``cond ? `?k=v` : ""`` -- a query suffix, which is not part of a Flask rule
    and must be stripped before matching. But *assuming* that of every trailing
    placeholder would hide a genuine miss: `/api/foo/${id}` against a server
    that only has `/api/foo` would match by dropping the segment.

    So the widening is earned per call site rather than granted to the position.
    We require the source to show a `?`-producing or empty-producing binding for
    the identifier. A placeholder that cannot be shown to be a query suffix
    stays a required segment and is allowed to fail.
    """
    if not name:
        return False
    # `const suffix = cond ? `?...` : ""` / `const query = ... ? "?..." : ""`
    for m in re.finditer(
        r"\b(?:const|let|var)\s+" + re.escape(name) + r"\s*=\s*([^\n;]*)", src
    ):
        rhs = m.group(1)
        if "?" in rhs and ('""' in rhs or "''" in rhs or "``" in rhs):
            return True
    # `function queryFor(...)` / `const queryFor = (...) =>` returning a `?...`
    for m in re.finditer(
        r"(?:function\s+" + re.escape(name) + r"\s*\(|"
        r"\b(?:const|let|var)\s+" + re.escape(name) + r"\s*=\s*(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>)",
        src,
    ):
        body = src[m.end(): m.end() + 700]
        if re.search(r"[`\"']\?", body) or "?${" in body:
            return True
    return False


def extract_call_sites():
    """Every `pulseApi()` path in every configured client, with provenance."""
    sites, unresolved = [], []
    for client, rel, glob in CLIENTS:
        root = REPO / rel
        if not root.is_dir():
            unresolved.append({
                "client": client, "file": rel, "raw": "",
                "why": "client source directory not found",
            })
            continue
        for path in sorted(root.glob(glob)):
            src = path.read_text(encoding="utf-8")
            consts = {m.group(1): m.group(3) for m in CONST_RE.finditer(src)}
            consts = {k: resolve_consts(v, consts) for k, v in consts.items()}
            for m in CALL_RE.finditer(src):
                raw = m.group(2)
                resolved = resolve_consts(raw, consts).split("?", 1)[0]
                if not resolved.startswith("/"):
                    unresolved.append({
                        "client": client, "file": path.name, "raw": raw,
                        "why": "path does not resolve to a leading slash; a "
                               "constant is defined outside this file or by an "
                               "expression this extractor does not read",
                    })
                    continue
                # A leftover `${UPPER_CASE}` is an unresolved *constant*, not a
                # runtime value: someone renamed or moved it.
                leftover = IDENT_PLACEHOLDER_RE.findall(resolved)
                if any(n.isupper() for n in leftover):
                    unresolved.append({
                        "client": client, "file": path.name, "raw": resolved,
                        "why": "an upper-case constant did not resolve",
                    })
                    continue
                sites.append({
                    "client": client, "file": path.name,
                    "path": resolved, "src": src,
                })
    return sites, unresolved


def rule_regex(rule: str):
    """A Flask rule as a regex over concrete paths.

    Converters are typed, and the types matter here rather than being cosmetic:

      * `<path:x>` matches slashes, so `/api/media/<path:key>` accepts `a/b/c`.
      * `<int:x>` matches digits only, so a client sending a literal `health`
        into that position is a real break and has to stay unmatched.
      * everything else takes one segment.
    """
    out = []
    for part in re.split(r"(<[^>]+>)", rule):
        if not part.startswith("<"):
            out.append(re.escape(part))
        elif part.startswith("<path:"):
            out.append(r".+")
        elif part.startswith("<int:"):
            out.append(r"-?\d+")
        elif part.startswith("<float:"):
            out.append(r"-?\d+(?:\.\d+)?")
        else:
            out.append(r"[^/]+")
    return re.compile("^" + "".join(out) + "$")


def load_server_rules():
    """Boot the app and read its URL map. Raises if the app will not import."""
    tmp = tempfile.mkdtemp()
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{tmp}/route_contract.db")
    sys.path.insert(0, str(REPO))
    import logging
    logging.disable(logging.CRITICAL)
    import bot  # noqa: E402
    return sorted({str(r.rule) for r in bot.webhook_app.url_map.iter_rules()})


def match(site, rules):
    """(matched, how) for one call site.

    Matching is done on the *shape*: a client placeholder and a server converter
    are both "one segment", so `/api/p/${id}/likes` matches `/api/p/<pid>/likes`
    without inventing a value for `id`.
    """
    path = site["path"]
    pattern = PLACEHOLDER_RE.sub(SEGMENT, path)
    exact = re.compile(
        "^" + "".join(r"[^/]+" if c == SEGMENT else re.escape(c)
                      for c in pattern) + "$")
    if any(exact.match(rule) for rule in rules):
        return True, "exact"

    # The shape match above compares the client path to the rule *text*, so it
    # only sees a rule whose converters line up with the client's placeholders.
    # It is blind to the common case where the server put a converter where the
    # client has a literal: `/api/pulse/live/<id>/guests/<gid>/<action>` really
    # does serve `.../leave`, and `.../connections/<cid>/<action>` really does
    # serve `.../health`. Reported as missing, those are four false alarms, and
    # a gate with false alarms gets switched off.
    #
    # So probe instead: build a concrete path by substituting a value for each
    # client placeholder and ask each rule's own regex whether it accepts it.
    # The dummy is numeric because a numeric segment satisfies every converter
    # type, including `<int:>`; an alphabetic one would spuriously fail against
    # integer ids. The cost is that a client passing a genuinely non-numeric
    # value into an `<int:>` position still matches here. That is a narrower
    # blind spot than the one it removes, and it is the reason the literal
    # segments of the path are matched strictly.
    probe = PLACEHOLDER_RE.sub("8", path)
    if "$" not in probe:
        for rule in rules:
            if rule_regex(rule).match(probe):
                return True, "converter"

    # Last resort, and only where the source earns it: a trailing query suffix.
    # The *last* placeholder, not the first -- `/api/pages/${id}/links${suffix}`
    # has two, and testing the first one for trailing-ness silently declines to
    # apply the rule to every path that also interpolates an id, which is most
    # of them.
    found = list(PLACEHOLDER_RE.finditer(path))
    trailing = found[-1] if found else None
    if trailing and path.endswith(trailing.group(0)):
        inner = trailing.group(0)[2:-1].strip()
        name = re.match(r"([A-Za-z_][A-Za-z0-9_]*)", inner)
        if name and looks_like_query_suffix(name.group(1), site["src"]):
            stripped = path[: trailing.start()]
            base = PLACEHOLDER_RE.sub(SEGMENT, stripped)
            rx = re.compile(
                "^" + "".join(r"[^/]+" if c == SEGMENT else re.escape(c)
                              for c in base) + "$")
            if any(rx.match(rule) for rule in rules):
                return True, "query-suffix"
            base_probe = PLACEHOLDER_RE.sub("8", stripped)
            if "$" not in base_probe:
                for rule in rules:
                    if rule_regex(rule).match(base_probe):
                        return True, "query-suffix"
    return False, "missing"


def load_allowlist():
    if not ALLOWLIST_PATH.exists():
        return {}
    data = json.loads(ALLOWLIST_PATH.read_text(encoding="utf-8"))
    out = {}
    for entry in data.get("pending_backend", []):
        for field in ("path", "reason", "reference", "added"):
            if not entry.get(field):
                raise SystemExit(
                    f"allowlist entry is missing {field!r}: {entry}\n"
                    "Every suppression names why it exists and where the "
                    "decision is written down, or it is not a suppression, it "
                    "is a forgotten failure.")
        out[entry["path"]] = entry
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    ap.add_argument("--verbose", action="store_true",
                    help="list every matched call site, not just the failures")
    args = ap.parse_args()

    try:
        allow = load_allowlist()
    except (ValueError, SystemExit) as exc:
        print(f"route-contract: allowlist unreadable: {exc}", file=sys.stderr)
        return EXIT_NO_DATA

    sites, unresolved = extract_call_sites()
    if not sites:
        print("route-contract: no client call sites found. The extractor, the "
              "client layout, or the wrapper name has changed -- this is not a "
              "pass.", file=sys.stderr)
        return EXIT_NO_DATA

    try:
        rules = load_server_rules()
    except Exception as exc:  # noqa: BLE001 -- any import failure is no-data
        print(f"route-contract: could not load server routes: {exc!r}",
              file=sys.stderr)
        return EXIT_NO_DATA
    if not rules:
        print("route-contract: the app registered zero routes.", file=sys.stderr)
        return EXIT_NO_DATA

    matched, missing, suffixed = [], [], []
    for site in sites:
        ok, how = match(site, rules)
        record = {k: site[k] for k in ("client", "file", "path")}
        if not ok:
            missing.append(record)
        elif how == "query-suffix":
            suffixed.append(record)
            matched.append(record)
        else:
            matched.append(record)

    # Split the failures against the allowlist, and catch the list going stale.
    allowed, unexpected = [], []
    for record in missing:
        entry = allow.get(record["path"])
        (allowed if entry else unexpected).append(record)
    missing_paths = {r["path"] for r in missing}
    stale = [p for p in allow if p not in missing_paths]

    report = {
        "server_rules": len(rules),
        "call_sites": len(sites),
        "matched": len(matched),
        "matched_as_query_suffix": len(suffixed),
        "missing_unexpected": unexpected,
        "missing_allowlisted": allowed,
        "stale_allowlist_entries": stale,
        "unresolved": unresolved,
    }

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(f"route-contract: {len(sites)} client call site(s) vs "
              f"{len(rules)} server rule(s)")
        print(f"  matched            : {len(matched)}"
              f"  ({len(suffixed)} via a trailing query suffix)")
        print(f"  missing (allowed)  : {len(allowed)}")
        print(f"  missing (NEW)      : {len(unexpected)}")
        print(f"  unresolved         : {len(unresolved)}")
        if args.verbose:
            for r in sorted(matched, key=lambda r: r["path"]):
                print(f"    ok        {r['file']:28s} {r['path']}")
        for r in allowed:
            entry = allow[r["path"]]
            print(f"\n  allowed   {r['path']}\n"
                  f"            {entry['reason']}\n"
                  f"            ref {entry['reference']} (added {entry['added']})")
        for r in unexpected:
            print(f"\n  MISSING   {r['path']}\n"
                  f"            called from {r['client']}/{r['file']}, no "
                  f"server rule accepts it")
        for p in stale:
            print(f"\n  STALE     {p}\n"
                  f"            allowlisted as unimplemented, but the server "
                  f"now has a route for it -- remove the entry")
        for u in unresolved:
            print(f"\n  UNRESOLVED {u['file']}: {u['raw'][:90]!r}\n"
                  f"            {u['why']}")

    if unresolved:
        print("\nroute-contract: some call sites could not be checked. That is "
              "not a pass.", file=sys.stderr)
        return EXIT_NO_DATA
    if unexpected or stale:
        return EXIT_CONTRACT_BREAK
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
