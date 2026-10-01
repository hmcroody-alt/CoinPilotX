"""Stages 7-8 — the UNDX ``private.facts.list`` capability.

Run either way::

    python -m pytest tests/private_office/test_private_facts_capability.py
    python tests/private_office/test_private_facts_capability.py

What these tests are actually defending
---------------------------------------
* **No second agent runtime.** The capability lives in the registry every
  other capability lives in, its executor is reached through the same
  ``undx_agent_tools.resolve``, and it is declared in the same three places
  the authorization surface cross-checks. A private capability that had its
  own registry would also have its own, unreviewed, gate.
* **Owner isolation is structural, not enforced.** The capability declares no
  field naming an account, and the executor takes the owner from the
  authenticated session. There is nothing to guess and no id to substitute —
  which is why B's fact is not refused to A but simply absent.
* **No existence oracle.** A's answer when B holds a FINANCIAL fact and A
  holds none is byte-identical to A's answer when nobody holds one. Stage 8
  asks that UNDX not reveal that B's record exists; the reason it cannot is
  that it never sees one.
* **One gate, two surfaces.** The executor and the HTTP route both render
  ``private_office.access.decide``. If they ever disagree, the agent reads out
  a row the screen hides, or refuses one the screen already opened.
* **A read, and only a read.** No companion write capability exists: a fact
  written by an agent from a conversation has a model's paraphrase as its
  provenance, and every row in this store must be able to answer "why does
  PulseSoc know this?".
"""

import contextlib
import hashlib
import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_facts_capability_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services import undx_agent_tools as agent_tools  # noqa: E402
from services import undx_capability_registry as registry  # noqa: E402
from services import undx_knowledge_map as knowledge_map  # noqa: E402
from services import undx_policy as policy  # noqa: E402
from services.business_os.entitlements import service as svc  # noqa: E402
from services.private_office import access  # noqa: E402
from services.private_office import facts  # noqa: E402
from services.private_office import feature_matrix  # noqa: E402
from services.private_office import model  # noqa: E402
from services.private_office import schema  # noqa: E402
from services.private_office import security as office_security  # noqa: E402
from services.private_office import tiers  # noqa: E402

CAPABILITY_ID = "private.facts.list"
TOOL_NAME = "pulsesoc.private_facts.list"
FEATURE_ID = "private_facts"

USER_A = 9401
USER_B = 9402
USER_C = 9403  # entitled, holds nothing at all — the honest empty store

_FAILURES: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        print(f"  PASS  {label}")
        return
    _FAILURES.append(f"{label}{(' — ' + detail) if detail else ''}")
    print(f"  FAIL  {label}{(' — ' + detail) if detail else ''}")


def _facts_are_live() -> bool:
    return FEATURE_ID in feature_matrix.implemented_feature_ids()


def _seed(owner, domain, fact_type, value, sensitivity=None):
    conn = db.connect()
    try:
        cur = conn.cursor()
        kwargs = dict(
            owner_user_id=owner, subject_type=facts.SUBJECT_NODE, subject_id="1",
            fact_type=fact_type, value=value, value_type=model.VALUE_STRING,
            provenance_type=model.PROVENANCE_USER_ASSERTED, domain=domain,
        )
        if sensitivity:
            kwargs["sensitivity"] = sensitivity
        result = facts.record_fact(cur, **kwargs)
        conn.commit()
        return result
    finally:
        conn.close()


def setup_environment():
    svc.ensure_schema()
    conn = db.connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "user_id INTEGER PRIMARY KEY, account_status TEXT DEFAULT 'active', "
            "access_enabled INTEGER DEFAULT 1)"
        )
        conn.execute("DELETE FROM users")
        for uid in (USER_A, USER_B, USER_C):
            conn.execute(
                "INSERT INTO users (user_id, account_status, access_enabled) "
                "VALUES (?, ?, 1)", (uid, "active"),
            )
        cur = conn.cursor()
        schema.ensure_private_schema(cur, force=True)
        conn.commit()
    finally:
        conn.close()
    for uid in (USER_A, USER_B, USER_C):
        svc.grant_entitlement(uid, "private_office.access", source="admin")

    # A holds one GENERAL fact and one RESTRICTED one; B holds a FINANCIAL fact
    # that A must never learn about, by refusal or by absence-shaped hint.
    _seed(USER_A, model.DOMAIN_GENERAL, "preferred_name", "Fact belonging to A")
    _seed(USER_A, model.DOMAIN_IDENTITY, "passport_number", "A-RESTRICTED-VALUE",
          sensitivity=model.SENSITIVITY_RESTRICTED)
    _seed(USER_B, model.DOMAIN_FINANCIAL, "bank_name", "Fact belonging to B")

    # The second lock stands between the tier and the data. The members whose
    # reads these stages expect to SUCCEED unlock the canonical way.
    for uid in (USER_A, USER_B):
        _mint_unlock(uid)


# --- second lock plumbing (Stage 17) ----------------------------------------
# The executor asks `request_is_unlocked`, which validates the grant carried by
# the CURRENT Flask request against the session and device it was minted for.
# So an "unlocked" call here is a real one: a passcode created through the
# canonical service, a grant minted by `verify_and_unlock` under the same
# bindings the request context will present, and both headers on the request.
# Outside such a context the executor must refuse — which stage_gate asserts.

_OFFICE_PASSCODE = "917364"
_UNLOCKS: dict[int, tuple[str, str, str]] = {}  # uid -> (bearer, device, grant)


def _mint_unlock(uid: int) -> None:
    bearer = f"capability-test-bearer-{uid}"
    device = f"od1-capability-test-{uid}"
    session_binding = hashlib.sha256(bearer.encode("utf-8")).hexdigest()
    conn = db.connect()
    try:
        cur = conn.cursor()
        office_security.create_passcode(cur, uid, _OFFICE_PASSCODE)
        minted = office_security.verify_and_unlock(
            cur, uid, _OFFICE_PASSCODE,
            session_binding=session_binding, device_binding=device,
        )
        conn.commit()
    finally:
        conn.close()
    assert minted.get("ok"), f"unlock mint failed for {uid}: {minted}"
    _UNLOCKS[uid] = (bearer, device, str(minted["grant_token"]))


@contextlib.contextmanager
def _unlocked_request(uid: int):
    from flask import Flask
    bearer, device, grant = _UNLOCKS[uid]
    app = Flask(__name__)
    with app.test_request_context(headers={
        "Authorization": f"Bearer {bearer}",
        office_security.DEVICE_HEADER: device,
        office_security.GRANT_HEADER: grant,
    }):
        yield


def _call(user_id, _unlocked=False, **arguments):
    if not _unlocked or user_id not in _UNLOCKS:
        return agent_tools.resolve("private_facts_list")(user_id, dict(arguments))
    with _unlocked_request(user_id):
        return agent_tools.resolve("private_facts_list")(user_id, dict(arguments))


# ---------------------------------------------------------------------------
def stage_registered_in_the_existing_registry():
    """Stage 7 — one runtime, three agreeing records."""
    print("\n[registration]")
    spec = registry.REGISTRY.get(CAPABILITY_ID)
    check("the capability is in the existing registry", spec is not None)
    if spec is None:
        return
    check("it declares the canonical tool name", spec.tool_name == TOOL_NAME,
          spec.tool_name)
    check("it is read-only", not spec.is_write and spec.risk == registry.RiskLevel.READ_ONLY,
          f"{spec.risk} write={spec.is_write}")
    check("it never asks for confirmation",
          spec.confirmation == registry.ConfirmationPolicy.NEVER, str(spec.confirmation))
    check("its permission scope is the caller's own account",
          spec.permission == registry.PermissionScope.SELF_ACCOUNT_ONLY,
          str(spec.permission))
    check("it requires authentication", spec.requires_authentication)

    ledger = policy.PRODUCTION_TOOL_REGISTRY.get(TOOL_NAME)
    check("the production ledger carries the same tool", ledger is not None)
    if ledger is not None:
        allowed = registry._POLICY_RISK_CLASSES.get(str(ledger.get("risk", "")))
        check("registry and ledger agree on risk",
              allowed is not None and spec.risk in allowed, str(ledger.get("risk")))
        check("the ledger does not require confirmation the registry omits",
              not ledger.get("confirmation"))
        check("the ledger scopes on the caller's own key",
              ledger.get("canonical_key") == "user_id", str(ledger.get("canonical_key")))

    record = knowledge_map.BY_ID.get(CAPABILITY_ID)
    check("the knowledge map carries a record", record is not None)
    if record is not None:
        permitted = registry._PERMISSION_SCOPES.get(spec.permission, frozenset())
        check("map scope is admitted by the registry permission",
              record.authorization_scope in permitted, record.authorization_scope)
        check("registry and map agree on authentication",
              record.authentication_required == spec.requires_authentication)
        check("the map does not name a screen that has not been built",
              record.native_screen == "" or record.native_screen in knowledge_map.NATIVE_ROUTES,
              record.native_screen)

    # Same runtime, same resolver, same executor table.
    executor = agent_tools.resolve(spec.executor)
    check("the executor resolves through the existing agent runtime",
          executor is agent_tools.private_facts_list, spec.executor)


def stage_no_field_can_name_another_account():
    """Stage 8 — isolation as a property of the contract, not of a check."""
    print("\n[shape]")
    spec = registry.REGISTRY[CAPABILITY_ID]
    names = {f.name for f in spec.fields}
    check("the capability declares only domain and limit", names == {"domain", "limit"},
          str(sorted(names)))
    forbidden = {"user_id", "owner", "owner_user_id", "account", "account_id",
                 "member", "member_id", "subject_id", "fact_id", "id"}
    check("no field could name an account or a row", not (names & forbidden),
          str(sorted(names & forbidden)))
    check("there is no companion private-facts write capability",
          not [cid for cid, s in registry.REGISTRY.items()
               if cid.startswith("private.facts.") and s.is_write],
          str([cid for cid, s in registry.REGISTRY.items()
               if cid.startswith("private.facts.") and s.is_write]))


def stage_unauthenticated_is_refused():
    print("\n[authentication]")
    for caller in (0, None, -1):
        result = _call(caller)
        check(f"caller {caller!r} is refused",
              not result.ok and result.error_code == "authentication_required",
              f"ok={result.ok} code={result.error_code}")
        check(f"caller {caller!r} gets no records", result.records == [],
              str(result.records))


def stage_gate_matches_the_http_surface():
    """The executor and the route render the same decision."""
    print("\n[gate]")
    resolved = tiers.resolve_tier(USER_A)
    check("the seeded member is at the top of the ladder",
          resolved["effective_tier"] == tiers.TIER_PRIVATE_OFFICE,
          str(resolved.get("effective_tier")))
    decision = access.decide(resolved, FEATURE_ID)
    result = _call(USER_A, _unlocked=True)

    if _facts_are_live():
        check("the shared gate allows an entitled member",
              decision["decision"] == access.ALLOW, decision["decision"])
        check("so the executor succeeds behind a valid unlock grant", result.ok,
              f"{result.error_code} {result.error_message}")
        # Stage 17 — the UNDX hard lock. The same entitled member, without the
        # grant on the request, is refused with the one sentence that names no
        # fact, no count and no domain.
        locked = _call(USER_A)
        check("without a grant the executor refuses as LOCKED",
              not locked.ok and locked.error_code == "PRIVATE_OFFICE_LOCKED",
              f"ok={locked.ok} code={locked.error_code}")
        check("the locked refusal carries no records", locked.records == [],
              str(locked.records))
    else:
        check("the shared gate refuses an unbuilt capability",
              decision["decision"] == access.NOT_IMPLEMENTED, decision["decision"])
        check("rank does not conjure the capability: PRIVATE_OFFICE is still refused",
              not result.ok and result.error_code == "capability_not_available",
              f"ok={result.ok} code={result.error_code}")
        check("the refusal does not offer an upgrade",
              "minimum_tier" not in (result.data or {})
              and "upgrade" not in (result.error_message or "").lower(),
              f"{result.data} {result.error_message}")
        check("an unbuilt capability is not presented as retryable",
              not result.retryable)
        print("  NOTE  read path not exercised: private_facts is not live yet")


def stage_degraded_resolve_is_not_a_denial():
    """Stage 176B — 'could not look' and 'looked and found nothing' differ."""
    print("\n[degraded]")
    original = tiers.resolve_tier
    try:
        tiers.resolve_tier = lambda uid: {"resolver_state": tiers.RESOLVER_DEGRADED,
                                          "effective_tier": tiers.TIER_FREE}
        result = _call(USER_A)
        check("a degraded resolve refuses without denying entitlement",
              not result.ok and result.error_code == "entitlement_unavailable",
              f"ok={result.ok} code={result.error_code}")
        check("and says so as a retryable condition", result.retryable)
        check("a degraded resolve is not the not-entitled answer",
              result.error_code != "not_entitled", result.error_code)
    finally:
        tiers.resolve_tier = original


def stage_owner_isolation():
    """Stage 8 P0 — A never reads B, by any route the capability offers."""
    print("\n[owner isolation]")
    if not _facts_are_live():
        # The gate refuses everyone, so the isolation property cannot be
        # exercised through the executor yet. Assert it at the layer that is
        # reachable — the reader the executor calls — rather than skipping it,
        # and re-run through the executor once Stage 9 flips the matrix.
        conn = db.connect()
        try:
            cur = conn.cursor()
            schema.ensure_private_schema(cur)
            a_rows = facts.list_facts(cur, owner_user_id=USER_A,
                                      sensitivity_ceiling=agent_tools.UNDX_SENSITIVITY_CEILING,
                                      limit=50)
            b_rows = facts.list_facts(cur, owner_user_id=USER_B,
                                      sensitivity_ceiling=agent_tools.UNDX_SENSITIVITY_CEILING,
                                      limit=50)
            c_rows = facts.list_facts(cur, owner_user_id=USER_C, limit=50)
        finally:
            conn.close()
        blob_a = repr(a_rows)
        check("both members really do hold a fact", a_rows and b_rows,
              f"A={len(a_rows)} B={len(b_rows)}")
        check("A's rows contain nothing of B's", "belonging to B" not in blob_a)
        check("A can see A's own fact", "belonging to A" in blob_a)
        check("a member with nothing sees nothing", c_rows == [], str(c_rows))
        check("the UNDX ceiling withholds A's RESTRICTED fact",
              "A-RESTRICTED-VALUE" not in blob_a)
        print("  NOTE  isolation asserted at the reader; executor path is gated")
        return

    a_result = _call(USER_A, _unlocked=True, limit=25)
    b_result = _call(USER_B, _unlocked=True, limit=25)
    check("A's read succeeds", a_result.ok, a_result.error_code)
    check("B's read succeeds", b_result.ok, b_result.error_code)
    blob_a = repr(a_result.records)
    check("A's records contain nothing of B's", "belonging to B" not in blob_a)
    check("A can see A's own fact", "belonging to A" in blob_a)
    check("no record carries an owner column",
          not any("owner" in k for r in a_result.records for k in r),
          str([k for r in a_result.records for k in r]))

    # Substituting B's identity is not possible through the contract, but prove
    # that a caller who tries anyway is answered as themselves.
    with _unlocked_request(USER_A):
        smuggled = agent_tools.resolve("private_facts_list")(
            USER_A, {"user_id": USER_B, "owner_user_id": USER_B, "fact_id": 1,
                     "limit": 25},
        )
    check("arguments naming another member are inert",
          smuggled.ok and "belonging to B" not in repr(smuggled.records),
          repr(smuggled.records)[:200])


def stage_absence_is_not_an_oracle():
    """Stage 8 — B's record must not be detectable through A's empty answer."""
    print("\n[existence]")
    if not _facts_are_live():
        print("  NOTE  not exercised over the executor: private_facts is not live yet")
        return
    # FINANCIAL: B holds one, A holds none. LEGAL: nobody holds one.
    held_by_other = _call(USER_A, _unlocked=True, domain=model.DOMAIN_FINANCIAL)
    held_by_nobody = _call(USER_A, _unlocked=True, domain=model.DOMAIN_LEGAL)
    check("both answers succeed", held_by_other.ok and held_by_nobody.ok)
    check("a domain another member populates is empty for A",
          held_by_other.records == [], str(held_by_other.records))
    check("an empty domain and another member's domain are indistinguishable",
          (held_by_other.records, held_by_other.error_code, held_by_other.ok)
          == (held_by_nobody.records, held_by_nobody.error_code, held_by_nobody.ok))
    check("the two answers differ only by the domain echoed back",
          {k: v for k, v in held_by_other.data.items() if k != "domain"}
          == {k: v for k, v in held_by_nobody.data.items() if k != "domain"},
          f"{held_by_other.data} vs {held_by_nobody.data}")


def stage_ceiling_and_bounds():
    print("\n[bounds]")
    check("the agent reads below the owner's own ceiling",
          model.SENSITIVITY_RANK[agent_tools.UNDX_SENSITIVITY_CEILING]
          < model.SENSITIVITY_RANK[model.SENSITIVITY_RESTRICTED])
    spec = registry.REGISTRY[CAPABILITY_ID]
    limit_field = next(f for f in spec.fields if f.name == "limit")
    check("the declared maximum matches the executor's cap",
          limit_field.maximum == agent_tools.UNDX_MAX_FACTS,
          f"{limit_field.maximum} vs {agent_tools.UNDX_MAX_FACTS}")
    if not _facts_are_live():
        print("  NOTE  ceiling not exercised over the executor: private_facts is not live yet")
        return
    result = _call(USER_A, _unlocked=True, limit=100)
    check("an over-large limit is clamped, not honoured",
          len(result.records) <= agent_tools.UNDX_MAX_FACTS, str(len(result.records)))
    check("the ceiling is reported so a short list is not read as an empty store",
          result.data.get("sensitivity_ceiling") == agent_tools.UNDX_SENSITIVITY_CEILING,
          str(result.data))
    check("A's RESTRICTED fact is withheld from the agent",
          "A-RESTRICTED-VALUE" not in repr(result.records))


# ---------------------------------------------------------------------------
def main() -> int:
    _FAILURES.clear()
    setup_environment()
    stage_registered_in_the_existing_registry()
    stage_no_field_can_name_another_account()
    stage_unauthenticated_is_refused()
    stage_gate_matches_the_http_surface()
    stage_degraded_resolve_is_not_a_denial()
    stage_owner_isolation()
    stage_absence_is_not_an_oracle()
    stage_ceiling_and_bounds()
    print("\n" + "=" * 60)
    if _FAILURES:
        print(f"FAIL — {len(_FAILURES)} check(s) failed:")
        for item in _FAILURES:
            print(f"  - {item}")
        return 1
    print("PASS — every check held")
    return 0


def test_private_facts_capability():
    """pytest entry point."""
    assert main() == 0, "; ".join(_FAILURES)


if __name__ == "__main__":
    raise SystemExit(main())
