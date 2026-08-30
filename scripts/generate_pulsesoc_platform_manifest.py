#!/usr/bin/env python3
"""Generate deterministic, source-derived PulseSoc knowledge for UNDX.

The checked-in manifest is an offline inventory. Runtime requests receive only
small, query-relevant public summaries; source paths and the complete manifest
are never copied into a provider prompt.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "data/pulse_ai/pulsesoc_platform_manifest.json"
EXCLUDED_ROUTE_TERMS = {"admin", "internal", "debug", "test", "webhook", "token", "secret"}


def _words(value: str) -> str:
    value = re.sub(r"<[^>]+>", " item ", value)
    value = re.sub(r"[_/.-]+", " ", value)
    return " ".join(value.split()).strip()


def _entry(kind: str, name: str, source: Path, **extra: Any) -> dict[str, Any]:
    relative = source.relative_to(ROOT).as_posix()
    return {
        "id": f"{kind}:{relative}:{name}",
        "kind": kind,
        "name": name,
        "source": relative,
        **extra,
    }


def native_surfaces() -> list[dict[str, Any]]:
    navigation = ROOT / "mobile-native/src/navigation/AppNavigator.tsx"
    text = navigation.read_text(encoding="utf-8")
    entries: list[dict[str, Any]] = []
    for match in re.finditer(
        r"<(?:Tabs|Stack)\.Screen\s+name=\"([^\"]+)\"(?:[^>]*?component=\{([A-Za-z0-9_]+)\})?",
        text,
        re.DOTALL,
    ):
        route, component = match.group(1), match.group(2) or ""
        entries.append(_entry(
            "native_surface",
            route,
            navigation,
            component=component,
            public_summary=f"{_words(route)} is a navigable PulseSoc app surface.",
            search_text=f"{route} {component} {_words(route)}",
        ))
    return entries


def native_api_capabilities() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for source in sorted((ROOT / "mobile-native/src/api").glob("*.ts")):
        text = source.read_text(encoding="utf-8")
        functions = sorted(set(re.findall(r"export\s+(?:async\s+)?function\s+([A-Za-z0-9_]+)", text)))
        paths = sorted(set(re.findall(r"[\"'`](/api/[A-Za-z0-9_?&=./:{}<>-]+)[\"'`]", text)))
        if not functions and not paths:
            continue
        area = source.stem
        entries.append(_entry(
            "native_api",
            area,
            source,
            functions=functions,
            routes=paths,
            public_summary=f"PulseSoc supports native {_words(area)} capabilities.",
            search_text=" ".join([area, *functions, *paths]),
        ))
    return entries


def _decorator_route(node: ast.AST) -> tuple[str, list[str]] | None:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    if node.func.attr != "route" or not node.args:
        return None
    route = node.args[0].value if isinstance(node.args[0], ast.Constant) else None
    if not isinstance(route, str):
        return None
    methods = ["GET"]
    for keyword in node.keywords:
        if keyword.arg == "methods" and isinstance(keyword.value, (ast.List, ast.Tuple)):
            values = [item.value for item in keyword.value.elts if isinstance(item, ast.Constant)]
            methods = [str(item).upper() for item in values if isinstance(item, str)] or methods
    return route, methods


def server_routes() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    sources = [ROOT / "bot.py", *sorted((ROOT / "services").rglob("*.py"))]
    for source in sources:
        try:
            tree = ast.parse(source.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for decorator in node.decorator_list:
                parsed = _decorator_route(decorator)
                if not parsed:
                    continue
                route, methods = parsed
                if not route.startswith("/api/"):
                    continue
                terms = set(re.findall(r"[a-z]+", route.lower()))
                is_public_knowledge = not bool(terms & EXCLUDED_ROUTE_TERMS)
                entries.append(_entry(
                    "server_route",
                    f"{','.join(methods)} {route}",
                    source,
                    handler=node.name,
                    methods=methods,
                    route=route,
                    public=is_public_knowledge,
                    public_summary=f"PulseSoc supports {_words(route.removeprefix('/api/'))}.",
                    search_text=f"{route} {node.name} {_words(route)}",
                ))
    return entries


def data_entities() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    sources = [ROOT / "bot.py", *sorted((ROOT / "services").rglob("*.py"))]
    pattern = re.compile(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`']?([A-Za-z][A-Za-z0-9_]*)", re.I)
    for source in sources:
        try:
            text = source.read_text(encoding="utf-8")
        except OSError:
            continue
        for table in sorted(set(pattern.findall(text))):
            entries.append(_entry(
                "data_entity",
                table,
                source,
                public_summary=f"PulseSoc has server-managed {_words(table)} records.",
                search_text=f"{table} {_words(table)}",
            ))
    return entries


#: Field that tells two same-named records of a kind apart. ``server_route`` keys on
#: "METHODS /path", but Flask permits two handlers on one method+path (Werkzeug serves
#: the first-registered rule and the rest are dead code), so the path alone is not an
#: identity. Kinds absent here have no known collision mode and fall back to a content
#: digest if one ever appears.
_DISAMBIGUATORS = {"server_route": "handler"}


def _dedupe_and_disambiguate(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Give every entry an id nothing else shares, without inventing records.

    Two situations arrive here looking the same, and the downstream ``doc_id`` primary
    key collapses both -- which is right for one of them and a silent data loss for the
    other:

    * The same record emitted twice. ``Reels``/``Saved``/``Search`` are declared both as
      a tab and as a stack screen with the same component, so the generator sees one
      navigable surface twice. These are byte-identical and must collapse to one record:
      indexing them under two keys would put the same document in the corpus twice and
      dilute retrieval rather than improve it.
    * Two genuinely different records sharing a path-derived key -- two Flask handlers
      registered on one method+path. These must NOT collapse. Overwriting one with the
      other is invisible from the outside, because the indexer reports the number of
      documents it wrote, not the number that survived the write.

    So exact repeats collapse and real conflicts get a suffixed id. The suffix is applied
    only inside a colliding group, which keeps every already-unique id byte-stable: the
    semantic index is keyed by ``doc_id``, and rewriting all 1,600-odd of them to fix
    five would churn the entire table to no purpose.
    """
    grouped: dict[str, list[dict[str, Any]]] = {}
    for entry in entries:
        grouped.setdefault(str(entry.get("id")), []).append(entry)

    resolved: list[dict[str, Any]] = []
    for entry_id, group in grouped.items():
        unique: list[dict[str, Any]] = []
        seen: set[str] = set()
        for entry in group:
            fingerprint = json.dumps(entry, sort_keys=True, ensure_ascii=False)
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            unique.append(entry)

        if len(unique) == 1:
            resolved.append(unique[0])
            continue

        field = _DISAMBIGUATORS.get(str(unique[0].get("kind")))
        for entry in unique:
            if field and str(entry.get(field) or "").strip():
                suffix = str(entry[field]).strip()
            else:
                # No declared disambiguator: fall back to a digest of the entry so the id
                # stays deterministic and unique. Reaching this branch means a new
                # collision mode appeared and _DISAMBIGUATORS wants updating.
                payload = json.dumps(entry, sort_keys=True, ensure_ascii=False)
                suffix = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
            entry["id"] = f"{entry_id}#{suffix}"
            resolved.append(entry)

    counts = Counter(str(entry.get("id")) for entry in resolved)
    collisions = sorted(key for key, total in counts.items() if total > 1)
    if collisions:
        # Refuse to emit a manifest that cannot be indexed without loss. A duplicate id
        # downstream is not a warning: undx_semantic_index deletes by doc_id before it
        # inserts, so the later record replaces the earlier one and the stored row count
        # quietly disagrees with the reported document count.
        raise SystemExit(
            "FAIL: %d manifest id(s) still collide after disambiguation: %s"
            % (len(collisions), ", ".join(collisions[:5]))
        )
    return resolved


def build_manifest() -> dict[str, Any]:
    entries = native_surfaces() + native_api_capabilities() + server_routes() + data_entities()
    entries = _dedupe_and_disambiguate(entries)
    entries.sort(key=lambda item: (item["kind"], item["name"], item["source"], item["id"]))
    counts: dict[str, int] = {}
    for item in entries:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    return {
        "schema_version": "1.0",
        "generation": "deterministic_source_inventory",
        "prompt_policy": "bounded_query_relevant_public_summaries_only",
        "counts": counts,
        "entries": entries,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail if the checked-in manifest is stale")
    args = parser.parse_args()
    rendered = json.dumps(build_manifest(), indent=2, ensure_ascii=False) + "\n"
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != rendered:
            raise SystemExit("FAIL: PulseSoc platform manifest is stale; run this generator.")
        print("PASS: PulseSoc platform manifest is current")
        return
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(rendered, encoding="utf-8")
    print(f"WROTE: {OUTPUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
