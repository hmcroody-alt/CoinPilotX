"""The five feature reads UNDX gets over the shipped Private Office engines.

``services.private_office.undx_feature_reads_spec`` declares one read per
feature — documents, people, briefings, shield posture, concierge desk — and
the registry, the policy table, the knowledge map and the executor table all
derive from it. This suite holds that arrangement to its promises:

* every capability is registered in all four surfaces, or in none;
* all five are read-only, self-scoped, and carry no field that can name an
  account, a table, or a row in someone else's office;
* the service hook reads the caller's office and nobody else's, and a forged
  owner argument is ignored rather than honoured;
* every read leaves an audit row with purpose ``undx_context``, and no seeded
  secret text leaks into the audit trail;
* the truth blocks survive the trip: the concierge read carries ``desk``
  staffing honesty and the shield read carries the ``external`` coverage
  block, because an agent answering from these payloads must not be able to
  imply a human or an external check that does not exist.
"""

from __future__ import annotations

import ast
import inspect
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_feature_undx_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.private_office import audit  # noqa: E402
from services.private_office import briefings  # noqa: E402
from services.private_office import concierge  # noqa: E402
from services.private_office import documents  # noqa: E402
from services.private_office import feature_matrix  # noqa: E402
from services.private_office import records  # noqa: E402
from services.private_office import relationships  # noqa: E402
from services.private_office import schema  # noqa: E402
from services.private_office import shield  # noqa: E402
from services.private_office import undx_feature_reads_spec as spec  # noqa: E402

USER_A = 9821
USER_B = 9822

#: Text that must never surface in an audit row or in B's answers.
_SECRETS = ("Meridian escrow deed", "Dr. Ansel Keeler", "overdue berth fee")

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
    documents.ensure_documents_schema(cur)
    briefings.ensure_briefings_schema(cur)
    shield.ensure_shield_schema(cur)
    concierge.ensure_concierge_schema(cur)
    return conn, cur


def _iso_in(days: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


def setup_environment() -> None:
    schema.reset_schema_cache()
    records.reset_records_schema_cache()
    documents.reset_documents_schema_cache()
    briefings.reset_briefings_schema_cache()
    shield.reset_shield_schema_cache()
    concierge.reset_concierge_schema_cache()
    conn, cur = _connect()
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# Registration — all four surfaces, or none
# ---------------------------------------------------------------------------

def stage_registered_everywhere() -> None:
    print("\n[registration]")
    from services import undx_agent_tools as tools
    from services import undx_capability_registry as registry
    from services import undx_knowledge_map as knowledge
    from services import undx_policy as policy

    check("five capabilities are declared", len(spec.CAPABILITIES) == 5,
          str(len(spec.CAPABILITIES)))
    ids = [entry["capability_id"] for entry in spec.CAPABILITIES]
    check("no capability id is declared twice", len(set(ids)) == len(ids), str(ids))

    for entry in spec.CAPABILITIES:
        cid = entry["capability_id"]
        registered = (
            cid in registry.REGISTRY
            and spec.tool_name(cid) in policy.PRODUCTION_TOOL_REGISTRY
            and cid in knowledge.BY_ID
            and spec.executor_name(cid) in tools.EXECUTORS
        )
        check(f"{cid} is registered in all four surfaces", registered)

        reg = registry.REGISTRY.get(cid)
        if reg is None:
            continue
        check(f"{cid} risk is read_only in the registry",
              str(reg.risk) in ("read_only", "RiskLevel.READ_ONLY")
              or getattr(reg.risk, "value", "") == "read_only", str(reg.risk))
        check(f"{cid} never asks for confirmation",
              getattr(reg.confirmation, "value", str(reg.confirmation)).lower()
              in ("never", "confirmationpolicy.never"), str(reg.confirmation))
        check(f"{cid} scope is the caller's own account",
              "self" in getattr(reg.permission, "value", str(reg.permission)).lower(),
              str(reg.permission))
        check(f"{cid} audit category names a feature read",
              reg.audit_category == spec.AUDIT_CATEGORY, reg.audit_category)
        check(f"{cid} executor follows the naming convention",
              reg.executor == spec.executor_name(cid), reg.executor)

        pol = policy.PRODUCTION_TOOL_REGISTRY.get(spec.tool_name(cid), {})
        check(f"{spec.tool_name(cid)} is read_only in the policy table",
              pol.get("risk") == "read_only", str(pol))
        check(f"{spec.tool_name(cid)} is reached in-process (method None)",
              pol.get("method") is None, str(pol.get("method")))
        check(f"{spec.tool_name(cid)} routes into services.private_office",
              str(pol.get("route", "")).startswith("services.private_office."),
              str(pol.get("route")))


def stage_specs_are_sound() -> None:
    print("\n[spec soundness]")
    matrix_flags = {fid: s.flag_env for fid, s in feature_matrix.FEATURES.items()}

    for entry in spec.CAPABILITIES:
        cid = entry["capability_id"]
        check(f"{cid} is namespaced under the private office", cid.startswith("private."))
        check(f"{cid} gates on a real feature row",
              entry["feature_id"] in feature_matrix.FEATURES, entry["feature_id"])
        check(f"{cid} names its feature's own kill switch",
              matrix_flags.get(entry["feature_id"]) == entry["flag_env"],
              f"{entry['flag_env']} vs {matrix_flags.get(entry['feature_id'])}")
        check(f"{cid} audits under a declared action",
              entry["audit_action"] in audit.ACTIONS, entry["audit_action"])
        check(f"{cid} has a description", bool(entry["description"].strip()))
        check(f"{cid} declares intents", len(entry["intents"]) >= 2)
        check(f"{cid} names a real backend route",
              entry["backend_route"].startswith("GET /api/private-office/"),
              entry["backend_route"])

    features = [entry["feature_id"] for entry in spec.CAPABILITIES]
    check("each capability gates a different feature",
          len(set(features)) == len(features), str(features))


def stage_read_only_vocabulary() -> None:
    print("\n[read only]")
    check("the declared risk is read_only", spec.RISK == "read_only", spec.RISK)
    check("no confirmation is declared, because nothing is written",
          spec.CONFIRMATION == "never", spec.CONFIRMATION)
    check("the permission scope is the caller's own account",
          spec.PERMISSION == "self_account_only", spec.PERMISSION)

    for entry in spec.CAPABILITIES:
        low = entry["description"].lower()
        for verb in ("create", "update", "delete", "submit", "upload", "cancel",
                     "generate", "acknowledge", "assign", "send"):
            check(f"{entry['capability_id']} does not describe a {verb}",
                  verb not in low, entry["description"])

    names = [field["name"] for field in spec.FIELDS]
    check("the field set is limit and nothing else", names == ["limit"], str(names))
    for field in spec.FIELDS:
        check(f"the {field['name']!r} field is optional", field["required"] is False)
        check(f"the {field['name']!r} field is bounded",
              field.get("maximum") == spec.MAX_LIMIT, str(field))

    signature = inspect.signature(spec.execute_capability)
    check("the hook takes the owner as a keyword-only session id",
          signature.parameters["owner_user_id"].kind
          is inspect.Parameter.KEYWORD_ONLY)


# ---------------------------------------------------------------------------
# The hook itself
# ---------------------------------------------------------------------------

def _seed_office(cur, owner: int) -> None:
    documents.store_document(
        cur, owner_user_id=owner, filename="deed.txt",
        content=b"holder: Meridian escrow deed\n", title="Meridian escrow deed")
    relationships.add_person(
        cur, owner_user_id=owner, name="Dr. Ansel Keeler", role="advisor")
    # An overdue obligation gives the shield scan something true to find.
    records.create_record(
        cur, record_type=records.TYPE_OBLIGATION, owner_user_id=owner,
        title="overdue berth fee", obligation_type="TAX", due_at=_iso_in(-5),
        sensitivity="INTERNAL")
    shield.run_scan(cur, owner_user_id=owner)
    briefings.generate_briefing(cur, owner_user_id=owner)
    concierge.submit_request(
        cur, owner_user_id=owner, title="Meridian escrow deed review",
        category="LEGAL")


def stage_hook_reads_the_office() -> None:
    print("\n[hook reads]")
    conn, cur = _connect()
    _seed_office(cur, USER_A)
    conn.commit()

    results = {}
    for entry in spec.CAPABILITIES:
        cid = entry["capability_id"]
        result = spec.execute_capability(
            cur, capability_id=cid, owner_user_id=USER_A)
        results[cid] = result
        check(f"{cid} reads A's office", result["ok"], str(result))
        check(f"{cid} counts what it returns",
              result["counts"]["returned"] == len(result["records"]),
              str(result["counts"]))
    conn.commit()

    check("the documents read finds the seeded document",
          any("Meridian" in str(r.get("title")) for r in
              results["private.documents.list"]["records"]))
    check("the documents read never ships a storage key",
          all("storage_key" not in r for r in
              results["private.documents.list"]["records"]))
    check("the people read finds the seeded advisor",
          any("Keeler" in str(r.get("name")) for r in
              results["private.people.list"]["records"]))
    check("the briefings read finds the generated briefing",
          len(results["private.briefings.list"]["records"]) >= 1)
    check("the shield read finds the overdue obligation",
          any(r.get("kind") == shield.KIND_OVERDUE_OBLIGATION for r in
              results["private.shield.posture"]["records"]))
    check("the concierge read finds the filed request",
          any("escrow" in str(r.get("title")) for r in
              results["private.concierge.desk"]["records"]))

    posture = results["private.shield.posture"]["extras"].get("posture", {})
    check("the shield read carries the posture block",
          "open_findings" in posture, str(posture.keys() if posture else posture))
    external = posture.get("external", {})
    check("the posture names what no provider has checked",
          bool(external) and all(
              str(v.get("state", v) if isinstance(v, dict) else v)
              for v in ([external] if not isinstance(external, dict) else external.values())),
          str(external))

    desk = results["private.concierge.desk"]["extras"].get("desk", {})
    check("the concierge read carries the desk staffing block",
          "staffed" in desk and "operator_count" in desk, str(desk))
    check("an empty roster reads as unstaffed, not as a quiet human",
          desk.get("staffed") is False or int(desk.get("operator_count") or 0) > 0,
          str(desk))

    over = spec.execute_capability(
        cur, capability_id="private.documents.list", owner_user_id=USER_A,
        arguments={"limit": 10_000})
    check("an oversized limit is bounded, not honoured",
          over["ok"] and over["counts"]["returned"] <= spec.MAX_LIMIT,
          str(over["counts"]))

    junk = spec.execute_capability(
        cur, capability_id="private.documents.list", owner_user_id=USER_A,
        arguments={"limit": "a lot"})
    check("a junk limit falls back to the default rather than erroring",
          junk["ok"], str(junk))

    unknown = spec.execute_capability(
        cur, capability_id="private.everything.dump", owner_user_id=USER_A)
    check("an unknown capability is refused, not defaulted",
          not unknown["ok"] and unknown["denied"] == "unknown_capability",
          str(unknown))
    check("the refusal carries no records", unknown["records"] == [])

    conn.commit()
    conn.close()


def stage_isolation() -> None:
    print("\n[isolation]")
    conn, cur = _connect()

    for entry in spec.CAPABILITIES:
        cid = entry["capability_id"]
        theirs = spec.execute_capability(
            cur, capability_id=cid, owner_user_id=USER_B)
        check(f"B's {cid} answer is empty, not refused",
              theirs["ok"] and theirs["counts"]["returned"] == 0, str(theirs))
        blob = repr(theirs)
        for secret in _SECRETS:
            check(f"none of A's text reaches B via {cid} ({secret[:16]}…)",
                  secret not in blob)

        forged = spec.execute_capability(
            cur, capability_id=cid, owner_user_id=USER_B,
            arguments={"owner_user_id": USER_A, "user_id": USER_A, "id": 1})
        check(f"a forged owner argument does not widen B's {cid} read",
              forged["counts"] == theirs["counts"]
              and forged["records"] == theirs["records"], str(forged["counts"]))

    conn.commit()
    conn.close()


def stage_reads_are_audited() -> None:
    print("\n[audit]")
    conn, cur = _connect()
    cur.execute(
        f"SELECT * FROM {schema.AUDIT_TABLE} WHERE purpose='undx_context'"
        f" AND owner_user_id IN (?, ?)", (USER_A, USER_B))
    rows = [dict(r) for r in cur.fetchall()]

    actions_seen = {row["action"] for row in rows}
    for entry in spec.CAPABILITIES:
        check(f"{entry['capability_id']} left an undx_context audit row",
              entry["audit_action"] in actions_seen, str(sorted(actions_seen)))

    for row in rows:
        check(f"audit row {row['id']} is self-scoped",
              int(row["actor_user_id"]) == int(row["owner_user_id"]))
    blob = repr(rows)
    for secret in _SECRETS:
        check(f"the audit trail carries no member text ({secret[:16]}…)",
              secret not in blob)
    conn.close()


def stage_hook_is_thin() -> None:
    """No second gate, no SQL of its own, no door past the engines."""
    print("\n[hook shape]")
    tree = ast.parse(inspect.getsource(spec))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)) and ast.get_docstring(node):
            node.body = node.body[1:]
    body = ast.unparse(tree)

    for statement in ("SELECT ", "INSERT ", "UPDATE ", "DELETE ", "CREATE TABLE"):
        check(f"the spec module issues no {statement.strip()}",
              statement not in body.upper())
    for banned in ("decide(", "is_owner", "has_entitlement", "unlock", "passcode"):
        check(f"the hook runs no authorization of its own ({banned})",
              banned not in body)
    check("every read is audited in one place", body.count("_audit.record(") == 1)


# ---------------------------------------------------------------------------
def main() -> int:
    _FAILURES.clear()
    setup_environment()
    stage_registered_everywhere()
    stage_specs_are_sound()
    stage_read_only_vocabulary()
    stage_hook_reads_the_office()
    stage_isolation()
    stage_reads_are_audited()
    stage_hook_is_thin()
    print("\n" + "=" * 60)
    if _FAILURES:
        print(f"FAIL — {len(_FAILURES)} check(s) failed:")
        for item in _FAILURES:
            print(f"  - {item}")
        return 1
    print("PASS — every check held")
    return 0


def test_private_feature_undx_reads():
    """pytest entry point."""
    assert main() == 0, "; ".join(_FAILURES)


if __name__ == "__main__":
    raise SystemExit(main())
