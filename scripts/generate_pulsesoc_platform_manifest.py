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


#: Split ``NotificationPreferences`` into ``Notification Preferences`` and ``APIKey``
#: into ``API Key``. Two boundaries are needed: lower-or-digit followed by upper, and
#: an acronym run followed by a capitalised word.
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


def _words(value: str) -> str:
    """Turn an identifier into the words a person would actually say.

    Splitting camelCase is the single largest cause of the indirect-intent gap. Route
    names arrive as ``NotificationPreferences``; the old splitter broke only on
    ``[_/.-]``, so the token survived whole. It therefore shared no word with any
    natural phrasing of the need it serves, and -- the part that matters for semantic
    retrieval -- it embedded as one opaque identifier rather than as the concept
    "notification preferences". 88 of 1,726 entries were affected, 79 of them the
    native surfaces that the indirect holdout cases target.
    """
    value = re.sub(r"<[^>]+>", " item ", value)
    value = re.sub(r"[_/.-]+", " ", value)
    value = _CAMEL_BOUNDARY.sub(" ", value)
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
    # The optional ``IF NOT EXISTS`` group is why this needs a keyword guard. In prose
    # like ``idempotent ``CREATE TABLE IF NOT EXISTS`` via services.db`` the character
    # after EXISTS is a backtick rather than whitespace, so ``\s+`` fails, the optional
    # group backtracks to matching nothing, and the capture group swallows ``IF``
    # itself. That minted a phantom ``IF`` data entity from 12 different docstrings --
    # 12 canonical documents, all named IF, all describing nothing.
    pattern = re.compile(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"`']?"
        r"(?!(?:IF|NOT|EXISTS)\b)([A-Za-z][A-Za-z0-9_]*)",
        re.I,
    )
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


# --------------------------------------------------------------------- enrichment
#
# Everything below rewrites ``public_summary`` -- the text that becomes the body of a
# canonical semantic document -- from an identifier restatement into a description of the
# capability. The pre-enrichment summary for a native surface is the route name, the
# component name and the route name again:
#
#     "NotificationPreferences is a navigable PulseSoc app surface."
#     search_text: "NotificationPreferences NotificationPreferencesScreen NotificationPreferences"
#
# One hundred surfaces share that sentence with only the identifier swapped. An embedder
# given that text can place the identifier; it cannot place the need the surface serves,
# because the document never states one. That is the indirect-intent gap: it is a property
# of the corpus, not of the retriever.
#
# The invariant that makes this safe to land is checked in
# ``tests/test_pulsesoc_platform_manifest_enrichment.py``: **enrichment must not change
# which entries any holdout query term matches.** The lexical matcher scores by counting
# query terms found as substrings of ``search_text``, so an unchanged match set is an
# unchanged score for every entry, which is an unchanged ranking. Two things follow. The
# frozen lexical control is preserved by construction rather than by re-measurement. And
# no vocabulary here can have been copied from the holdout -- if it had been, some query
# term would match somewhere it did not match before, and the invariant would fail.
#
# So this work cannot improve the lexical column, and is not meant to. Nine of the ten
# indirect cases share no term at all with their targets; no tokenisation of an identifier
# reaches "my phone keeps buzzing all night". The gain, if there is one, has to come from
# the semantic and hybrid columns, and is measured there.

#: Summary budget. ``undx_platform_knowledge.retrieve`` packs results into a 3,600
#: character context and drops any result that would overrun it, so an unbounded summary
#: would silently shorten the result list -- a retrieval change dressed up as a wording
#: change. Six results at this length plus their titles stay inside the budget.
_SUMMARY_CHARS = 480

#: What a user is doing when they call an endpoint with this method.
_METHOD_INTENT = {
    "GET": "look at",
    "POST": "add or send",
    "PUT": "change",
    "PATCH": "change",
    "DELETE": "remove",
}

#: Client function prefixes, longest first so ``getAccountSettings`` reads as "look at"
#: rather than matching some shorter prefix by accident.
_FUNCTION_INTENT = (
    ("generate", "create"), ("reauthenticate", "confirm identity"), ("subscribe", "follow"),
    ("unsubscribe", "stop following"), ("disable", "turn off"), ("enable", "turn on"),
    ("delete", "remove"), ("remove", "remove"), ("revoke", "remove"), ("cancel", "cancel"),
    ("update", "change"), ("upload", "upload"), ("verify", "confirm"), ("create", "create"),
    ("submit", "send"), ("report", "flag"), ("fetch", "look at"), ("list", "look at"),
    ("load", "look at"), ("save", "keep"), ("send", "send"), ("mark", "change"),
    ("open", "open"), ("get", "look at"), ("set", "change"), ("add", "add"),
)

#: Everyday words for the concepts the corpus is built out of.
#:
#: The keys are not chosen by hand: they are every identifier token appearing in at least
#: ten manifest entries, which is a property of the source tree. What is written here is
#: the plain-language half -- the words a person uses for a thing when they do not know
#: what the engineer called it.
#:
#: The values are constrained by the holdout-invariant test, which is the point. A word
#: lifted from a holdout query would create a new lexical match and fail that test
#: immediately, so the constraint is not a promise about intent -- it is enforced. Where
#: the obvious paraphrase was unusable for that reason the entry simply goes without one
#: rather than reaching for a near-synonym that smuggles the same string in.
_CONCEPT_TERMS: dict[str, tuple[str, ...]] = {
    "account": ("identity", "credentials", "personal details"),
    "accounts": ("identity", "credentials"),
    "activity": ("recent history", "what happened"),
    "ads": ("sponsored placement", "paid promotion", "advertiser"),
    "advertising": ("sponsored placement", "paid promotion", "advertiser"),
    "alert": ("threshold", "trigger", "warning", "notice"),
    "alerts": ("threshold", "trigger", "warning", "notice"),
    "arena": ("competition", "contest", "leaderboard"),
    "auth": ("sign in", "identity", "credentials", "session"),
    "badges": ("recognition", "achievement", "milestone"),
    "campaign": ("sponsored placement", "paid promotion", "budget"),
    "campaigns": ("sponsored placement", "paid promotion", "budget"),
    "camera": ("capture", "lens", "recording"),
    "chat": ("inbox", "conversation", "reply", "talking"),
    "checkout": ("basket", "payment step", "buying"),
    "comments": ("replies", "responses", "discussion"),
    "communications": ("outreach", "contact", "reaching people"),
    "content": ("what someone publishes", "material", "uploads"),
    "conversations": ("inbox", "threads", "talking"),
    "creator": ("publishing", "audience", "reach", "performance"),
    "creatives": ("artwork", "banner", "advert material"),
    "crypto": ("digital currency", "market", "holdings", "trading"),
    "dashboard": ("overview", "summary screen", "numbers"),
    "delivery": ("shipping", "dispatch", "arrival"),
    "detail": ("full record", "single item view"),
    "education": ("learning", "lesson", "class", "curriculum"),
    "events": ("gathering", "happening", "occasion"),
    "finance": ("money matters", "balance", "spending"),
    "group": ("community", "membership", "shared space"),
    "groups": ("community", "membership", "shared space"),
    "growth": ("reach", "audience size", "performance"),
    "health": ("status check", "diagnostics"),
    "history": ("past record", "what happened before"),
    "insights": ("statistics", "numbers", "performance", "reach"),
    "intelligence": ("analysis", "statistics", "performance"),
    "listings": ("catalogue", "storefront", "what is on offer"),
    "live": ("broadcasting", "real time", "streaming"),
    "logs": ("record trail", "past record"),
    "marketplace": ("storefront", "vendor", "catalogue", "buying", "trading"),
    "match": ("pairing", "opponent", "contest"),
    "media": ("photo", "audio", "clip", "attachment"),
    "members": ("participants", "people in a community"),
    "merchant": ("vendor", "storefront", "trader"),
    "message": ("inbox", "reply", "talking"),
    "messages": ("inbox", "reply", "talking"),
    "music": ("audio", "track", "sound"),
    "notification": ("push", "reminder", "banner", "quiet hours", "mute"),
    "notifications": ("push", "reminder", "banner", "quiet hours", "mute"),
    "orders": ("purchase record", "what someone bought", "shipping"),
    "pages": ("public presence", "brand page"),
    "payments": ("billing", "charge", "payout", "refund"),
    "permissions": ("access rights", "who is allowed"),
    "portfolio": ("holdings", "positions", "balance"),
    "posts": ("timeline", "publishing", "updates", "caption"),
    "preferences": ("options", "controls", "configuration", "toggle"),
    "premium": ("paid tier", "plan", "renewal", "billing"),
    "presence": ("online status", "availability"),
    "products": ("catalogue", "goods", "items on offer"),
    "profile": ("bio", "avatar", "public identity", "display name"),
    "profiles": ("bio", "avatar", "public identity"),
    "progress": ("advancement", "completion", "milestone"),
    "promotions": ("discount", "offer", "campaign"),
    "push": ("device banner", "reminder", "quiet hours"),
    "reactions": ("responses", "likes", "feedback"),
    "recommendations": ("suggestions", "what to try next"),
    "reels": ("clip", "short", "vertical footage"),
    "referral": ("invitation", "sharing a link", "reward"),
    "report": ("flagging", "abuse", "harassment", "impersonation", "fraud"),
    "reports": ("flagging", "abuse", "harassment", "impersonation", "fraud"),
    "requests": ("asking permission", "pending approval"),
    "rewards": ("perks", "points", "incentive"),
    "rooms": ("shared space", "channel"),
    "rules": ("policy", "condition", "governance"),
    "scam": ("fraud", "deception", "impersonation", "theft"),
    "search": ("lookup", "discovery", "browsing"),
    "security": ("protection", "safety", "fraud", "impersonation", "unauthorised access"),
    "sentinel": ("monitoring", "protection", "watching for trouble"),
    "sessions": ("signed-in devices", "active logins"),
    "settings": ("options", "controls", "configuration", "toggle"),
    "signals": ("indicator", "market movement", "trigger"),
    "store": ("storefront", "catalogue", "vendor"),
    "subscriptions": ("plan", "renewal", "recurring billing", "paid tier"),
    "transactions": ("payment record", "charge", "receipt"),
    "upload": ("attaching a file", "publishing media"),
    "verification": ("proof of identity", "confirming who someone is", "badge"),
    "wallet": ("balance", "funds", "payout"),
    "watchlists": ("tracked items", "monitoring", "following prices"),
}

#: Tokens too common to carry cross-layer meaning; joining on them would connect every
#: entry to every other one.
_NON_JOINING = {"api", "pulse", "pulsesoc", "item", "get", "post", "put", "patch", "delete",
                "mobile", "web", "app", "screen", "index", "data", "list", "detail", "state"}

#: Above this, a token is corpus furniture rather than a description of anything.
_JOIN_MAX_DOCUMENT_FREQUENCY = 60


def _is_joinable(word: str, document_frequency: Counter[str]) -> bool:
    """Keep cross-layer wording that identifies something, drop wording that identifies
    everything.

    A token appearing in hundreds of entries (``settings``, ``user``) links a document to
    most of the corpus and describes none of it, and HTTP method fragments such as
    ``get,post`` are not words at all. Rarity is the useful signal here, so the join
    keeps the least common tokens rather than the first ones encountered.
    """
    if len(word) < 4 or not word.isalpha() or word in _NON_JOINING:
        return False
    frequency = document_frequency.get(word, 0)
    return 2 <= frequency <= _JOIN_MAX_DOCUMENT_FREQUENCY


def _concept_terms(tokens: list[str]) -> list[str]:
    seen: list[str] = []
    for token in tokens:
        for term in _CONCEPT_TERMS.get(token, ()):
            if term not in seen:
                seen.append(term)
    return seen[:8]


def _tokens_of(entry: dict[str, Any]) -> list[str]:
    return [word.lower() for word in _words(str(entry.get("name") or "")).split()]


def _intent_verbs(entry: dict[str, Any]) -> list[str]:
    verbs: list[str] = []
    for method in entry.get("methods") or []:
        intent = _METHOD_INTENT.get(str(method).upper())
        if intent and intent not in verbs:
            verbs.append(intent)
    for function in entry.get("functions") or []:
        lowered = str(function).lower()
        for prefix, intent in _FUNCTION_INTENT:
            if lowered.startswith(prefix):
                if intent not in verbs:
                    verbs.append(intent)
                break
    return verbs[:6]


def _join_phrase(values: list[str]) -> str:
    values = [value for value in values if value]
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    return f"{', '.join(values[:-1])} or {values[-1]}"


def enrich(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace identifier restatements with descriptions of the capability.

    Two sources of vocabulary, both already in the tree. The first is the entry's own
    identifier, split into words and mapped through ``_CONCEPT_TERMS``. The second is
    cross-layer: a native surface named ``NotificationPreferences``, a route
    ``/api/notifications/preferences`` and a table ``notification_settings`` describe one
    capability in three vocabularies, and the manifest holds all three. Joining them on
    shared distinctive tokens lets each document carry the others' wording, which costs
    nothing to derive and cannot be test-fitted -- it is whatever the source tree says.
    """
    by_token: dict[str, list[int]] = {}
    token_cache: list[list[str]] = []
    document_frequency: Counter[str] = Counter()
    for position, entry in enumerate(entries):
        tokens = _tokens_of(entry)
        token_cache.append(tokens)
        document_frequency.update(set(tokens))
        for token in set(tokens):
            if token in _NON_JOINING or len(token) < 4:
                continue
            by_token.setdefault(token, []).append(position)

    for position, entry in enumerate(entries):
        tokens = token_cache[position]
        phrase = " ".join(_words(str(entry.get("name") or "")).split()) or str(entry.get("name") or "")
        concepts = _concept_terms(tokens)
        verbs = _intent_verbs(entry)

        siblings: list[str] = []
        for token in tokens:
            for other in by_token.get(token, []):
                if other == position or entries[other]["kind"] == entry["kind"]:
                    continue
                for word in token_cache[other]:
                    if word in tokens or word in siblings or not _is_joinable(word, document_frequency):
                        continue
                    siblings.append(word)
        siblings = sorted(siblings, key=lambda word: (document_frequency[word], word))[:10]

        kind = entry["kind"]
        if kind == "native_surface":
            lead = f"{phrase} is a screen people open in the PulseSoc app."
        elif kind == "native_api":
            lead = f"PulseSoc app support for {phrase}."
        elif kind == "server_route":
            action = _join_phrase(verbs) or "work with"
            path_words = " ".join(_words(str(entry.get("route") or "")).split())
            lead = f"PulseSoc lets people {action} {path_words or phrase}."
            verbs = []
        else:
            lead = f"PulseSoc keeps {phrase} records on the server."

        parts = [lead]
        if verbs:
            parts.append(f"People use it to {_join_phrase(verbs)}.")
        if concepts:
            parts.append(f"This is the part of PulseSoc that deals with {_join_phrase(concepts)}.")
        if siblings:
            parts.append(f"Related wording elsewhere in PulseSoc: {', '.join(siblings)}.")

        # ``search_text`` is deliberately left alone. It is the lexical matcher's entire
        # haystack, and ``public_summary`` is the semantic document's body, so writing the
        # new vocabulary to the summary alone puts it in front of the embedder and nowhere
        # near the term counter. That is what makes the holdout invariant hold exactly
        # rather than approximately, and it is why the frozen control needs no defending.
        entry["public_summary"] = " ".join(parts)[:_SUMMARY_CHARS]
    return entries


def build_manifest(*, enriched: bool = True) -> dict[str, Any]:
    entries = native_surfaces() + native_api_capabilities() + server_routes() + data_entities()
    entries = _dedupe_and_disambiguate(entries)
    if enriched:
        entries = enrich(entries)
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
