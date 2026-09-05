"""Batch C — the declared-but-unwired UNDX surface for the record primitives.

Run either way::

    python -m pytest tests/private_office/test_private_records_undx_spec.py
    python tests/private_office/test_private_records_undx_spec.py

What these tests are actually defending
---------------------------------------
``services.private_office.undx_records_spec`` registers nothing. It declares
what the six read capabilities will be and provides the one service hook they
will call, because the three files that own registration — the capability
registry, the policy table and the knowledge map — are the authorization
surface and are being edited by the concurrent Private Office security mission.

A module in that state has two failure modes, and every check here is aimed at
one of them.

* **"Deferred" quietly becoming "forgotten", or worse, "half-done".** While
  ``WIRING_COMPLETE`` is False the suite asserts the six capability ids are
  *absent* from all three registration surfaces. If somebody registers them in
  one file and not the others, this suite goes red on the file that has them,
  which is the earliest point anything can notice. When the wiring lands, the
  flag flips and these checks invert into presence checks — so the flag cannot
  be flipped without the registration being real, and the registration cannot
  be done without the flag being flipped.

* **The specs drifting from the thing they describe.** ``VIEW_CHOICES`` is
  derived from ``retrieval.RECORD_VIEWS`` rather than restated, and this suite
  asserts the derivation rather than the values: a vocabulary typed twice
  eventually disagrees with itself, and the failure is quiet — a view the model
  may name that the reader then resolves to nothing, indistinguishable from an
  empty office.

Beyond that, the properties the specs commit to are checked as properties of
the data, not of a comment:

* **Read only, all six.** No write capability, no confirmation-bearing entry.
* **No field names an account.** Owner scope is structural: there is nothing in
  ``FIELDS`` to put another member's id into, which is why ``execute_view``
  takes ``owner_user_id`` as a keyword-only argument unreachable from
  ``arguments``. Stage ``isolation`` proves that by trying.
* **Bounds are refused at the edge.** ``limit`` is capped in the field spec as
  well as clamped in the hook, so an over-large request cannot be silently
  trimmed somewhere in the middle instead.
* **The hook adds no second gate.** ``execute_view`` delegates to
  ``retrieve_records`` and returns its denial verbatim. A second authorization
  gate is a second place for the two to disagree.
"""

import ast
import inspect
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_records_undx_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.private_office import records  # noqa: E402
from services.private_office import retrieval  # noqa: E402
from services.private_office import schema  # noqa: E402
from services.private_office import undx_records_spec as spec  # noqa: E402

USER_A = 9801
USER_B = 9802

_FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> bool:
    if condition:
        print(f"  PASS  {label}")
        return True
    _FAILURES.append(f"{label}{(' — ' + detail) if detail else ''}")
    print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")
    return False


def _connect():
    conn = db.connect()
    cur = conn.cursor()
    schema.ensure_private_schema(cur)
    records.ensure_records_schema(cur)
    return conn, cur


def _iso_in(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def setup_environment() -> None:
    schema.reset_schema_cache()
    records.reset_records_schema_cache()
    conn, cur = _connect()
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# The deferral itself
# ---------------------------------------------------------------------------

def stage_deferral_is_honest() -> None:
    """The check that makes ``WIRING_COMPLETE`` load-bearing rather than a note.

    Three surfaces are cross-checked against each other precisely so that a
    capability cannot exist in one and be missing from another. That mutual
    check runs at import; it cannot notice a capability that is in *none* of
    them but is promised by a spec module. This stage is that missing check.
    """
    print("\n[deferral]")

    check("the deferral reason is the one the mission named",
          spec.DEFERRAL_REASON == "UNDX_WIRING_DEFERRED_DUE_TO_CONCURRENT_SECURITY_WORK",
          spec.DEFERRAL_REASON)
    # Flipped from `is False` when the wiring landed. The property the old
    # check defended — the spec module registers nothing itself — still holds;
    # registration lives in the three owning files, which derive from the spec.
    check("the wiring flag reports the registration as real",
          spec.WIRING_COMPLETE is True, str(spec.WIRING_COMPLETE))

    # Imported here rather than at module scope: these three are the files the
    # security mission is editing, and a module-scope import would make this
    # suite fail to *collect* if one of them is briefly broken, which reads as
    # "Batch C is red" rather than "somebody else's file is mid-edit".
    from services import undx_capability_registry as registry
    from services import undx_knowledge_map as knowledge_map
    from services import undx_policy as policy

    registered = set(registry.capability_ids())
    mapped = {cid for cid in (s["capability_id"] for s in spec.CAPABILITIES)
              if knowledge_map.get(cid) is not None}
    tooled = {s["capability_id"] for s in spec.CAPABILITIES
              if spec.tool_name(s["capability_id"]) in policy.PRODUCTION_TOOL_REGISTRY}

    for entry in spec.CAPABILITIES:
        cid = entry["capability_id"]
        in_registry = cid in registered
        in_map = cid in mapped
        in_policy = cid in tooled
        if spec.WIRING_COMPLETE:
            check(f"{cid} is registered in all three surfaces",
                  in_registry and in_map and in_policy,
                  f"registry={in_registry} map={in_map} policy={in_policy}")
        else:
            # Absent from all three. Present in one and not the others is the
            # half-registered state the deferral exists to avoid.
            check(f"{cid} is absent while wiring is deferred",
                  not (in_registry or in_map or in_policy),
                  f"registry={in_registry} map={in_map} policy={in_policy}")

    # The one capability that IS wired stays wired. This stage must not be
    # satisfiable by the registry simply being empty or unimportable.
    check("the existing private.facts.list capability is still registered",
          "private.facts.list" in registered,
          f"{len(registered)} capabilities in the registry")
    check("the existing private facts tool is still in the policy registry",
          "pulsesoc.private_facts.list" in policy.PRODUCTION_TOOL_REGISTRY)


# ---------------------------------------------------------------------------
# The declared surface
# ---------------------------------------------------------------------------

def stage_specs_cover_the_views() -> None:
    print("\n[specs]")

    declared = tuple(entry["view"] for entry in spec.CAPABILITIES)
    check("there is exactly one capability per view",
          sorted(declared) == sorted(retrieval.RECORD_VIEWS),
          f"{sorted(declared)} vs {sorted(retrieval.RECORD_VIEWS)}")
    check("no view is declared twice", len(set(declared)) == len(declared), str(declared))

    # Derived, not restated. Asserting the derivation rather than the six
    # literal strings is the point: a test that repeated the vocabulary would
    # be a third place for it to disagree with itself.
    check("VIEW_CHOICES is derived from retrieval.RECORD_VIEWS",
          spec.VIEW_CHOICES == tuple(sorted(retrieval.RECORD_VIEWS)),
          str(spec.VIEW_CHOICES))

    ids = [entry["capability_id"] for entry in spec.CAPABILITIES]
    check("no capability id is declared twice", len(set(ids)) == len(ids), str(ids))
    for cid in ids:
        check(f"{cid} is namespaced under the private office", cid.startswith("private."))
        check(f"{cid} is a list capability", cid.endswith(".list"))

    # A capability id that named a table would leak the storage layout into the
    # vocabulary the model speaks, and would survive a table rename as a lie.
    tables = {records.private_table_for(t) for t in records.RECORD_TYPES}
    for cid in ids:
        check(f"{cid} does not name a table",
              not any(table in cid for table in tables))
    for view in retrieval.RECORD_VIEWS:
        check(f"the view name {view!r} is not a table name", view not in tables)

    for entry in spec.CAPABILITIES:
        check(f"{entry['capability_id']} has a description", bool(entry["description"].strip()))
        check(f"{entry['capability_id']} declares intents", len(entry["intents"]) >= 2)
        check(f"{entry['capability_id']} has a native route",
              entry["native_route"].startswith("/pulse/private-office/"),
              entry["native_route"])
        check(f"{entry['capability_id']} routes to its own view",
              entry["native_route"].endswith("/" + entry["view"]),
              entry["native_route"])

    check("every capability resolves back from its view",
          all(spec.capability_for_view(v) is not None for v in retrieval.RECORD_VIEWS))
    check("an unknown view resolves to nothing rather than a default",
          spec.capability_for_view("private_obligations") is None
          and spec.capability_for_view("") is None)


def stage_read_only() -> None:
    """Six reads and no writes, asserted over the constants the wiring will use."""
    print("\n[read only]")

    check("the declared risk is read_only", spec.RISK == "read_only", spec.RISK)
    check("no confirmation is declared, because nothing is written",
          spec.CONFIRMATION == "never", spec.CONFIRMATION)
    check("the permission scope is the caller's own account",
          spec.PERMISSION == "self_account_only", spec.PERMISSION)
    check("the audit category names a read",
          spec.AUDIT_CATEGORY == "private_records_read", spec.AUDIT_CATEGORY)
    check("the service route is the sanctioned retrieval door",
          spec.SERVICE_ROUTE == "services.private_office.retrieval.retrieve_records",
          spec.SERVICE_ROUTE)

    # There is no companion write capability, for the same reason
    # `private.facts.list` has none. An obligation is worse than a fact in this
    # respect: it also carries a date somebody may act on.
    for entry in spec.CAPABILITIES:
        cid = entry["capability_id"]
        for verb in (".create", ".update", ".delete", ".record", ".resolve",
                     ".set", ".add", ".close", ".dismiss"):
            check(f"{cid} is not a {verb.strip('.')} capability", not cid.endswith(verb))
        low = entry["description"].lower()
        check(f"{cid} describes a read", low.startswith("list "), entry["description"])

    # "recorded", never "recommended". This capability reports that something
    # exists and was flagged by a named source; it does not rank or endorse.
    opportunity = spec.capability_for_view(retrieval.VIEW_OPPORTUNITIES)
    low = opportunity["description"].lower()
    for word in ("recommend", "advice", "advise", "suggest", "best", "should",
                 "invest", "buy", "opportunity to profit"):
        check(f"the opportunity description avoids {word!r}", word not in low,
              opportunity["description"])


def stage_no_field_can_name_an_account() -> None:
    print("\n[fields]")

    names = [field["name"] for field in spec.FIELDS]
    check("the field set is small and shared", names == ["status", "limit"], str(names))

    # The scope is structural: there is nothing here to put another owner into.
    for field in spec.FIELDS:
        name = field["name"].lower()
        for banned in ("user", "owner", "account", "member", "profile", "session",
                       "table", "record_id", "id", "sql", "query", "where"):
            check(f"the {field['name']!r} field does not name {banned!r}",
                  banned not in name, field["name"])
        check(f"the {field['name']!r} field is optional",
              field["required"] is False, str(field))
        check(f"the {field['name']!r} field has a default",
              "default" in field, str(field))

    limit = [f for f in spec.FIELDS if f["name"] == "limit"][0]
    check("limit is bounded in the field spec, not only in the service",
          limit["minimum"] == 1 and limit["maximum"] == spec.MAX_LIMIT, str(limit))
    check("the declared cap is not larger than retrieval's own",
          spec.MAX_LIMIT <= retrieval.MAX_RECORDS,
          f"{spec.MAX_LIMIT} vs {retrieval.MAX_RECORDS}")
    check("the default is inside the bound",
          1 <= spec.DEFAULT_LIMIT <= spec.MAX_LIMIT, str(spec.DEFAULT_LIMIT))

    # The signature is the second half of the same argument: the owner is a
    # keyword-only parameter the executor supplies from the authenticated
    # session, so no amount of model-authored `arguments` can reach it.
    signature = inspect.signature(spec.execute_view)
    owner = signature.parameters["owner_user_id"]
    check("owner_user_id is keyword-only on the service hook",
          owner.kind is inspect.Parameter.KEYWORD_ONLY, str(owner.kind))
    check("owner_user_id has no default a caller could omit",
          owner.default is inspect.Parameter.empty, str(owner.default))


def stage_tool_names_follow_the_existing_convention() -> None:
    print("\n[tool names]")

    # Spelled once so the registry entry and the policy table cannot disagree.
    check("the transformation reproduces the shipped facts tool name",
          spec.tool_name("private.facts.list") == "pulsesoc.private_facts.list",
          spec.tool_name("private.facts.list"))

    seen = set()
    for entry in spec.CAPABILITIES:
        name = spec.tool_name(entry["capability_id"])
        check(f"{name} is namespaced", name.startswith("pulsesoc.private_"), name)
        check(f"{name} ends in .list", name.endswith(".list"), name)
        seen.add(name)
    check("no two capabilities produce the same tool name",
          len(seen) == len(spec.CAPABILITIES), str(sorted(seen)))


# ---------------------------------------------------------------------------
# The service hook
# ---------------------------------------------------------------------------

def stage_hook_delegates_and_bounds() -> None:
    print("\n[service hook]")
    conn, cur = _connect()

    # INTERNAL, because the hook's default intent is deliberately the most
    # restricted one — `general` allows the GENERAL domain at an INTERNAL
    # ceiling and nothing else. The confidential obligation below is the other
    # half of that story.
    records.create_record(
        cur, record_type=records.TYPE_OBLIGATION, owner_user_id=USER_A,
        title="Boiler service contract", obligation_type="SERVICE",
        due_at=_iso_in(20), sensitivity="INTERNAL")
    records.create_record(
        cur, record_type=records.TYPE_OBLIGATION, owner_user_id=USER_A,
        title="Second charge repayment", obligation_type="LOAN",
        due_at=_iso_in(40), domain="FINANCIAL", sensitivity="CONFIDENTIAL")
    for index in range(40):
        records.create_record(
            cur, record_type=records.TYPE_EVENT, owner_user_id=USER_A,
            event_type="ACCOUNT_REVIEWED", occurred_at=_iso_in(-index - 1),
            title=f"Review {index}", sensitivity="INTERNAL")
    conn.commit()

    result = spec.execute_view(
        cur, capability_id="private.obligations.list", owner_user_id=USER_A)
    check("a declared capability reads the owner's own records",
          result["ok"] and result["counts"]["returned"] == 1, str(result["counts"]))
    check("the result carries no table name",
          not any(records.private_table_for(t) in repr(result)
                  for t in records.RECORD_TYPES))

    # The default intent does not reach a CONFIDENTIAL financial obligation, and
    # the short list that results must not read as an empty office. The ceiling
    # travels with the answer for exactly that reason — the same device
    # `private.facts.list` uses — so the executor can say "this is what I was
    # allowed to see" rather than "this is everything".
    check("the catch-all intent does not reach a confidential obligation",
          "Second charge repayment" not in repr(result["records"]),
          repr(result["records"])[:200])
    check("the answer reports the ceiling it was read at",
          result.get("sensitivity_ceiling") == "INTERNAL",
          str(result.get("sensitivity_ceiling")))

    wider = spec.execute_view(
        cur, capability_id="private.obligations.list", owner_user_id=USER_A,
        intent=retrieval.INTENT_PROPERTY_PORTFOLIO)
    check("an intent that covers the domain does reach it",
          wider["counts"]["returned"] == 2, str(wider["counts"]))
    check("the wider read reports its own, higher ceiling",
          wider.get("sensitivity_ceiling") == "CONFIDENTIAL",
          str(wider.get("sensitivity_ceiling")))

    # The hook has no intent of its own to offer, so an unknown one must be
    # refused by retrieval rather than quietly replaced with the default.
    bogus = spec.execute_view(
        cur, capability_id="private.obligations.list", owner_user_id=USER_A,
        intent="everything_about_me")
    check("an unknown intent is refused rather than defaulted",
          bogus["ok"] is False and bogus["denied"] == retrieval.DENIED_UNKNOWN_INTENT,
          str(bogus))

    # Refused rather than defaulted. Substituting a different collection for
    # the one that was asked for hides the bug and the probe alike.
    unknown = spec.execute_view(
        cur, capability_id="private.passcodes.list", owner_user_id=USER_A)
    check("an unknown capability id is refused, not defaulted",
          unknown["ok"] is False and unknown["denied"] == "unknown_capability",
          str(unknown))
    check("the refusal returns no records", unknown["records"] == []
          and unknown["counts"]["returned"] == 0, str(unknown))

    over = spec.execute_view(
        cur, capability_id="private.events.list", owner_user_id=USER_A,
        arguments={"limit": 5000})
    check("an over-large limit is clamped to the declared cap",
          over["counts"]["returned"] <= spec.MAX_LIMIT, str(over["counts"]))

    # A junk limit is a bug in the caller, not a licence to return everything.
    junk = spec.execute_view(
        cur, capability_id="private.events.list", owner_user_id=USER_A,
        arguments={"limit": "all of them"})
    check("a non-numeric limit falls back to the default rather than to no bound",
          junk["counts"]["returned"] <= spec.DEFAULT_LIMIT, str(junk["counts"]))

    negative = spec.execute_view(
        cur, capability_id="private.events.list", owner_user_id=USER_A,
        arguments={"limit": -3})
    check("a negative limit still returns a bounded page, not an error",
          1 <= negative["counts"]["returned"] <= spec.MAX_LIMIT, str(negative["counts"]))

    # The hook adds no gate of its own, so retrieval's denials must arrive
    # verbatim rather than being reshaped into a generic failure.
    ownerless = spec.execute_view(
        cur, capability_id="private.risks.list", owner_user_id=0)
    check("an ownerless call is denied by retrieval, not by a second gate",
          ownerless["ok"] is False and ownerless["denied"] == retrieval.DENIED_NO_OWNER,
          str(ownerless))
    check("the denial carries no records", ownerless["records"] == [], str(ownerless))

    conn.close()


def stage_isolation() -> None:
    """Two accounts, six capabilities, and nothing to substitute."""
    print("\n[isolation]")
    conn, cur = _connect()

    # Seeded at INTERNAL so the default intent can see them: this stage is
    # about owner scope, and a record the intent filters out would make B's
    # empty answer prove nothing.
    seeds = {
        retrieval.VIEW_OBLIGATIONS: dict(
            record_type=records.TYPE_OBLIGATION, title="A's ground rent",
            obligation_type="TAX", due_at=_iso_in(9)),
        retrieval.VIEW_EVENTS: dict(
            record_type=records.TYPE_EVENT, event_type="PROPERTY_SOLD",
            occurred_at=_iso_in(-30), title="A's sale"),
        retrieval.VIEW_DECISIONS: dict(
            record_type=records.TYPE_DECISION, question="A's refinance question?",
            summary="A's decision context."),
        retrieval.VIEW_REQUESTS: dict(
            record_type=records.TYPE_REQUEST, title="A's notary booking",
            category="LEGAL", description="A's description."),
        retrieval.VIEW_RISKS: dict(
            record_type=records.TYPE_RISK, title="A's uninsured outbuilding",
            risk_type="INSURANCE", summary="A's exposure summary."),
        retrieval.VIEW_OPPORTUNITIES: dict(
            record_type=records.TYPE_OPPORTUNITY, title="A's flagged lease",
            opportunity_type="PROPERTY"),
    }
    for fields in seeds.values():
        records.create_record(cur, owner_user_id=USER_A,
                              sensitivity="INTERNAL", **fields)
    conn.commit()

    for view, fields in seeds.items():
        capability = spec.capability_for_view(view)["capability_id"]

        # Presence of the seeded row rather than an exact count: this database
        # is shared with the hook stage above, and a count assertion here would
        # be a test of the seeding order rather than of ownership.
        mine = spec.execute_view(cur, capability_id=capability, owner_user_id=USER_A)
        check(f"A reads A's {view}",
              mine["ok"] and "A's" in repr(mine["records"]), str(mine["counts"]))

        theirs = spec.execute_view(cur, capability_id=capability, owner_user_id=USER_B)
        check(f"B's {view} listing is empty, not refused",
              theirs["ok"] and theirs["counts"]["returned"] == 0, str(theirs))
        check(f"none of A's {view} text reaches B",
              "A's" not in repr(theirs["records"]), repr(theirs["records"])[:200])

        # The id-substitution attempt has nowhere to go: `arguments` carrying an
        # owner or a record id is ignored rather than honoured, so B's answer is
        # byte-identical to the answer for an account that holds nothing.
        forged = spec.execute_view(
            cur, capability_id=capability, owner_user_id=USER_B,
            arguments={"owner_user_id": USER_A, "user_id": USER_A,
                       "record_id": 1, "id": 1, "status": ""})
        check(f"a forged owner argument does not widen B's {view} read",
              forged["counts"] == theirs["counts"]
              and forged["records"] == theirs["records"], str(forged["counts"]))

    conn.close()


def stage_hook_is_thin() -> None:
    """No second gate, and no second door to the tables."""
    print("\n[hook shape]")

    # Comments and docstrings are stripped before the check, for the same
    # reason the write-boundary guard strips them: prose is where a module
    # explains itself, and `tool_name`'s own docstring necessarily contains the
    # string ``pulsesoc.private_risks.list`` — which contains a table name by
    # coincidence of naming, not by reaching for a table.
    tree = ast.parse(inspect.getsource(spec))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)) and ast.get_docstring(node):
            node.body = node.body[1:]
    body = ast.unparse(tree)

    for statement in ("SELECT", "INSERT", "UPDATE", "DELETE", "CREATE TABLE"):
        check(f"the spec module issues no {statement}", statement not in body.upper())
    for table in (records.private_table_for(t) for t in records.RECORD_TYPES):
        check(f"the spec module does not name {table}", table not in body)

    check("the hook calls the sanctioned retrieval function",
          "retrieve_records(" in body)
    for banned in ("decide(", "is_owner", "has_entitlement", "unlock", "passcode"):
        check(f"the hook runs no authorization of its own ({banned})",
              banned not in body)


# ---------------------------------------------------------------------------
def main() -> int:
    _FAILURES.clear()
    setup_environment()
    stage_deferral_is_honest()
    stage_specs_cover_the_views()
    stage_read_only()
    stage_no_field_can_name_an_account()
    stage_tool_names_follow_the_existing_convention()
    stage_hook_delegates_and_bounds()
    stage_isolation()
    stage_hook_is_thin()
    print("\n" + "=" * 60)
    if _FAILURES:
        print(f"FAIL — {len(_FAILURES)} check(s) failed:")
        for item in _FAILURES:
            print(f"  - {item}")
        return 1
    print("PASS — every check held")
    return 0


def test_private_records_undx_spec():
    """pytest entry point."""
    assert main() == 0, "; ".join(_FAILURES)


if __name__ == "__main__":
    raise SystemExit(main())
