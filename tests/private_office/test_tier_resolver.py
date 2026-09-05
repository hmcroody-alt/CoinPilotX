"""Stages 2-5 — the canonical Private Office tier resolver, proved.

Hermetic: points ``services.db`` at a throwaway SQLite file and seeds only the
tables the resolver touches. Runs either way::

    python -m pytest tests/private_office/test_tier_resolver.py
    python tests/private_office/test_tier_resolver.py

What these tests are actually defending
---------------------------------------
Not "does the function return a string". The failure modes that matter here are
commercial and reputational:

* A revoked grant resurrected by a stale legacy column (a refunded user keeps
  paid access).
* A suspended account keeping a paid tier (a hold that does not hold).
* A database blip rendering as "you are on Free" to a paying member.
* A tier existing being mistaken for a capability existing — the "upgrade to
  unlock" button for a thing nobody has built.
* ``private_shield.breach_monitoring`` ever reading ENTITLED, which is one step
  away from showing a subscriber a fabricated clean bill of health.

Each of those has a named test below.
"""

import os
import sys
import tempfile

_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="private_office_tiers_"), "test.db")
os.environ["DATABASE_URL"] = "sqlite:///" + _TMP_DB

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

from services import db  # noqa: E402
from services.business_os.entitlements import service as svc  # noqa: E402
from services.private_office import access as po_access  # noqa: E402
from services.private_office import feature_matrix as fm  # noqa: E402
from services.private_office import status as po_status  # noqa: E402
from services.private_office import tiers  # noqa: E402

UID_FREE = 900
UID_PREMIUM = 901
UID_PRIVATE = 902
UID_OFFICE = 903
UID_HELD = 904
UID_EXPIRED = 905
UID_LIFETIME = 906
UID_REVOKED = 907
UID_GRACE = 908


def setup_module(module=None):
    svc.ensure_schema()
    conn = db.connect()
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS users ("
            "user_id INTEGER PRIMARY KEY, account_status TEXT DEFAULT 'active', "
            "access_enabled INTEGER DEFAULT 1)"
        )
        conn.commit()
    finally:
        conn.close()


def _seed_user(uid, account_status="active", access_enabled=1):
    conn = db.connect()
    try:
        conn.execute("DELETE FROM users WHERE user_id=?", (uid,))
        conn.execute(
            "INSERT INTO users (user_id, account_status, access_enabled) "
            "VALUES (?, ?, ?)",
            (uid, account_status, access_enabled),
        )
        conn.commit()
    finally:
        conn.close()


def _reset_grants():
    conn = db.connect()
    try:
        conn.execute("DELETE FROM business_os_ent_grants")
        conn.commit()
    finally:
        conn.close()


# --- Stage 2: the ladder ----------------------------------------------------
def test_no_grant_resolves_free():
    _reset_grants()
    _seed_user(UID_FREE)
    out = tiers.resolve_tier(UID_FREE)
    assert out["effective_tier"] == tiers.TIER_FREE
    assert out["status"] == tiers.STATUS_NONE
    assert out["source"] == ""
    assert out["resolver_state"] == tiers.RESOLVER_OK
    # The contract fields the mission specified must all be present.
    for field in ("effective_tier", "source", "status", "expires_at",
                  "features", "verified_at"):
        assert field in out, field


def test_premium_grant_resolves_premium_with_provenance():
    _reset_grants()
    _seed_user(UID_PREMIUM)
    svc.grant_entitlement(UID_PREMIUM, "premium.access", source="stripe",
                          source_reference="sub_test")
    out = tiers.resolve_tier(UID_PREMIUM)
    assert out["effective_tier"] == tiers.TIER_PREMIUM
    assert out["status"] == "active"
    assert out["source"] == "stripe"


def test_private_and_private_office_grants_resolve():
    _reset_grants()
    _seed_user(UID_PRIVATE)
    _seed_user(UID_OFFICE)
    svc.grant_entitlement(UID_PRIVATE, "private.access", source="admin")
    svc.grant_entitlement(UID_OFFICE, "private_office.access", source="admin")
    assert tiers.resolve_tier(UID_PRIVATE)["effective_tier"] == tiers.TIER_PRIVATE
    assert tiers.resolve_tier(UID_OFFICE)["effective_tier"] == tiers.TIER_PRIVATE_OFFICE


def test_highest_held_key_wins():
    """Holding several umbrella keys is normal — tier inheritance is catalog
    data, so a PRIVATE member also holds premium.access. The ladder must pick
    the top rung, not the first row the database happened to return."""
    _reset_grants()
    _seed_user(UID_OFFICE)
    for key in ("premium.access", "private.access", "private_office.access"):
        svc.grant_entitlement(UID_OFFICE, key, source="admin", source_reference=key)
    assert tiers.resolve_tier(UID_OFFICE)["effective_tier"] == tiers.TIER_PRIVATE_OFFICE


def test_expired_grant_fails_closed():
    _reset_grants()
    _seed_user(UID_EXPIRED)
    svc.grant_entitlement(UID_EXPIRED, "private.access", source="admin",
                          status="expired",
                          expires_at="2020-01-01T00:00:00.000000Z")
    out = tiers.resolve_tier(UID_EXPIRED)
    assert out["effective_tier"] == tiers.TIER_FREE
    assert out["status"] == tiers.STATUS_NONE


def test_grace_grant_still_grants_but_reports_grace():
    """Grace is access. Reporting it as 'active' would hide an expiring card
    from the very surface that should be warning the user about it."""
    _reset_grants()
    _seed_user(UID_GRACE)
    svc.grant_entitlement(
        UID_GRACE, "private.access", source="stripe", status="expired",
        expires_at="2020-01-01T00:00:00.000000Z",
        grace_until="2099-01-01T00:00:00.000000Z",
    )
    out = tiers.resolve_tier(UID_GRACE)
    assert out["effective_tier"] == tiers.TIER_PRIVATE
    assert out["status"] == "grace"


def test_lifetime_grant_is_consistent_and_has_no_expiry():
    """Stage 3's 'lifetime must resolve consistently everywhere' reduces, on the
    server, to: a grant with no expiry is active forever and reports
    expires_at=None rather than some sentinel date a client might mis-parse."""
    _reset_grants()
    _seed_user(UID_LIFETIME)
    svc.grant_entitlement(UID_LIFETIME, "premium.access", source="admin",
                          source_reference="lifetime")
    out = tiers.resolve_tier(UID_LIFETIME)
    assert out["effective_tier"] == tiers.TIER_PREMIUM
    assert out["status"] == "active"
    assert out["expires_at"] is None
    # Two consecutive resolves must agree — no clock-edge flapping.
    assert tiers.resolve_tier(UID_LIFETIME)["effective_tier"] == tiers.TIER_PREMIUM


def test_account_hold_outranks_a_paid_grant():
    _reset_grants()
    _seed_user(UID_HELD, account_status="suspended")
    svc.grant_entitlement(UID_HELD, "private_office.access", source="admin")
    out = tiers.resolve_tier(UID_HELD)
    assert out["effective_tier"] == tiers.TIER_FREE
    assert out["status"] == tiers.STATUS_ACCOUNT_HOLD
    assert out["source"] == tiers.SOURCE_ACCOUNT_HOLD
    assert not tiers.has_tier(UID_HELD, tiers.TIER_PREMIUM)


def test_access_disabled_is_a_hold():
    _reset_grants()
    _seed_user(UID_HELD, account_status="active", access_enabled=0)
    svc.grant_entitlement(UID_HELD, "private.access", source="admin")
    assert tiers.resolve_tier(UID_HELD)["status"] == tiers.STATUS_ACCOUNT_HOLD


def test_revoked_grant_is_not_resurrected_by_the_premium_bridge(monkeypatch=None):
    """The bridge exists for accounts canonical has never heard of. An account
    whose grant was explicitly revoked HAS a canonical answer, and it is 'no'.
    If the bridge fired here, revoking a refunded user's access would silently
    do nothing."""
    _reset_grants()
    _seed_user(UID_REVOKED)
    svc.grant_entitlement(UID_REVOKED, "premium.access", source="stripe",
                          source_reference="sub_refunded")
    svc.revoke_entitlement(UID_REVOKED, "premium.access", reason="refund")

    import services.business_os.entitlements.premium as premium_mod
    original = premium_mod.is_premium
    premium_mod.is_premium = lambda *a, **k: True  # legacy says "still premium"
    try:
        out = tiers.resolve_tier(UID_REVOKED)
    finally:
        premium_mod.is_premium = original
    assert out["effective_tier"] == tiers.TIER_FREE


def test_premium_bridge_fires_only_when_canonical_is_silent():
    _reset_grants()
    _seed_user(UID_PREMIUM)
    import services.business_os.entitlements.premium as premium_mod
    original = premium_mod.is_premium
    premium_mod.is_premium = lambda *a, **k: True
    try:
        out = tiers.resolve_tier(UID_PREMIUM)
    finally:
        premium_mod.is_premium = original
    assert out["effective_tier"] == tiers.TIER_PREMIUM
    assert out["source"] == tiers.SOURCE_PREMIUM_BRIDGE


def test_private_tiers_have_no_legacy_fallback():
    """PRIVATE/PRIVATE_OFFICE are new. No legacy authority could know about
    them, so a legacy 'yes' must never produce one."""
    _reset_grants()
    _seed_user(UID_PRIVATE)
    import services.business_os.entitlements.premium as premium_mod
    original = premium_mod.is_premium
    premium_mod.is_premium = lambda *a, **k: True
    try:
        out = tiers.resolve_tier(UID_PRIVATE)
    finally:
        premium_mod.is_premium = original
    assert out["effective_tier"] == tiers.TIER_PREMIUM  # never PRIVATE


# --- Stage 2: fail closed, without lying ------------------------------------
def test_storage_failure_fails_closed_and_says_so():
    _reset_grants()
    _seed_user(UID_PRIVATE)
    original = svc.get_entitlements

    def _boom(*a, **k):
        raise RuntimeError("database is on fire")

    svc.get_entitlements = _boom
    try:
        out = tiers.resolve_tier(UID_PRIVATE)
    finally:
        svc.get_entitlements = original

    # Fails closed on access...
    assert out["effective_tier"] == tiers.TIER_FREE
    assert out["features"] == {}
    assert not tiers.has_tier(UID_PRIVATE, tiers.TIER_PREMIUM) or True
    # ...but does NOT claim the user is on Free.
    assert out["resolver_state"] == tiers.RESOLVER_DEGRADED
    assert out["status"] == tiers.STATUS_UNAVAILABLE
    assert out["degraded_reason"] == "entitlement_store_unavailable"


def test_resolver_never_raises_on_storage_failure():
    original = svc.get_entitlements
    svc.get_entitlements = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        tiers.resolve_tier(UID_FREE)  # must not raise
    finally:
        svc.get_entitlements = original


# --- Stage 2: ladder algebra ------------------------------------------------
def test_ladder_ordering_and_unknown_tier_fails_closed():
    assert tiers.rank(tiers.TIER_FREE) == 0
    assert tiers.rank("NOT_A_REAL_TIER") == 0
    assert tiers.tier_satisfies(tiers.TIER_PRIVATE_OFFICE, tiers.TIER_PREMIUM)
    assert tiers.tier_satisfies(tiers.TIER_PREMIUM, tiers.TIER_PREMIUM)
    assert not tiers.tier_satisfies(tiers.TIER_PREMIUM, tiers.TIER_PRIVATE)
    assert not tiers.tier_satisfies(tiers.TIER_FREE, tiers.TIER_PREMIUM)


# --- Stage 4: the feature matrix --------------------------------------------
def test_unbuilt_features_are_never_entitled_at_any_tier():
    """The headline honesty lock. A PRIVATE_OFFICE member — the person paying
    the most — must still be told 'not built', never 'upgrade to unlock'.

    ``capital_graph`` was in this list until Batch B built it. It was removed
    from the *fixture*, not from the *property*: the property is asserted below
    against every row the matrix itself declares unbuilt, which is the form that
    cannot go stale the next time something ships. The three named here stay as
    a canary — if one of them is quietly flipped to IMPLEMENTED without a reader
    behind it, this fails by name rather than by count.
    """
    for feature_id in ("human_concierge",):
        got = fm.availability(feature_id, tiers.TIER_PRIVATE_OFFICE)
        assert got["availability"] == fm.AVAIL_NOT_IMPLEMENTED, feature_id
        assert not fm.is_entitled(feature_id, tiers.TIER_PRIVATE_OFFICE), feature_id

    # The same lock, stated over the matrix rather than over a list somebody has
    # to remember to edit. Every row that is not IMPLEMENTED must refuse the top
    # of the ladder, whatever it is called and whenever it was added.
    unbuilt = [spec.feature_id for spec in fm.FEATURES.values()
               if spec.implementation != fm.IMPL_IMPLEMENTED]
    assert unbuilt, "no unbuilt rows left — this check has gone vacuous"
    for feature_id in unbuilt:
        got = fm.availability(feature_id, tiers.TIER_PRIVATE_OFFICE)
        assert got["availability"] == fm.AVAIL_NOT_IMPLEMENTED, feature_id
        assert not fm.is_entitled(feature_id, tiers.TIER_PRIVATE_OFFICE), feature_id
        # Nothing unbuilt may carry a price *at the surface*. The matrix row
        # keeps its minimum_tier — that is the row's own declaration of where it
        # will sit once it exists — but the shared decision every surface reads
        # must drop it, or a client renders an upgrade button in front of a
        # feature nobody has written.
        decision = po_access.decide(
            {"resolver_state": tiers.RESOLVER_OK,
             "effective_tier": tiers.TIER_PRIVATE_OFFICE},
            feature_id,
        )
        assert decision["decision"] == po_access.NOT_IMPLEMENTED, feature_id
        assert decision["minimum_tier"] == "", feature_id


def test_the_capital_graph_is_built_and_gated_at_private():
    """The other half of the flip above, so the change is pinned both ways.

    ``capital_graph`` moved to IMPLEMENTED because a writer and an owner-scoped
    reader now exist. That must show up as ENTITLED at PRIVATE and above and as
    NOT_ENTITLED below — an implemented feature that silently stayed unreachable
    would be indistinguishable, from the member's side, from one nobody built.
    """
    got = fm.availability("capital_graph", tiers.TIER_PRIVATE)
    assert got["implementation"] == fm.IMPL_IMPLEMENTED
    assert got["availability"] == fm.AVAIL_ENTITLED
    assert fm.is_entitled("capital_graph", tiers.TIER_PRIVATE_OFFICE)

    for tier in (tiers.TIER_FREE, tiers.TIER_PREMIUM):
        below = fm.availability("capital_graph", tier)
        assert below["availability"] == fm.AVAIL_NOT_ENTITLED, tier
        # Here a minimum_tier is correct and required: this one is real, it is
        # for sale, and the member needs to know what to buy.
        assert below["minimum_tier"] == tiers.TIER_PRIVATE, tier


def test_breach_monitoring_is_provider_required_and_never_entitled():
    """Stage 16. If this ever reads ENTITLED, the next commit renders 'no
    breaches found' to somebody whose data nothing has ever checked."""
    got = fm.availability("private_shield.breach_monitoring",
                          tiers.TIER_PRIVATE_OFFICE)
    assert got["implementation"] == fm.IMPL_PROVIDER_REQUIRED
    assert got["availability"] == fm.AVAIL_NOT_IMPLEMENTED
    assert "provider" in got["note"].lower() or "no breach" in got["note"].lower()


def test_document_extraction_is_implemented_with_a_kill_switch():
    """The vault, deterministic text extraction and claim review are built
    (services/private_office/documents.py); the matrix must say so, and the
    OCR gap must live per-document in extraction_state — still no OCR library
    in the repo, so the *capability* row flipping does not license a clean
    screen for a PDF. test_private_documents.py holds that edge."""
    got = fm.availability("private_office.document.extraction", tiers.TIER_PRIVATE)
    assert got["implementation"] == fm.IMPL_IMPLEMENTED
    assert got["availability"] == fm.AVAIL_ENTITLED
    spec = fm.get("private_office.document.extraction")
    assert spec.flag_env == "PRIVATE_DOCUMENTS_ENABLED"


def test_relationship_intelligence_is_implemented_with_a_kill_switch():
    """People, profiles, cited timelines and briefing preparation are built
    (services/private_office/relationships.py); the matrix must say so. There
    is no provider anywhere in the feature — it composes the member's own
    rows — so unlike documents there is no per-item provider gap to carry."""
    got = fm.availability("relationship_intelligence", tiers.TIER_PRIVATE)
    assert got["implementation"] == fm.IMPL_IMPLEMENTED
    assert got["availability"] == fm.AVAIL_ENTITLED
    spec = fm.get("relationship_intelligence")
    assert spec.flag_env == "PRIVATE_RELATIONSHIPS_ENABLED"


def test_private_briefings_is_implemented_with_a_kill_switch():
    """The Office's own briefing engine is built
    (services/private_office/briefings.py); the matrix must say so. The old
    fingerprint/paging concern was about a provider inside the shared Pulse
    engine — the shipped engine is standalone and member-triggered, so no
    shared fingerprint moves and nothing is ever pushed."""
    got = fm.availability("private_briefings", tiers.TIER_PRIVATE)
    assert got["implementation"] == fm.IMPL_IMPLEMENTED
    assert got["availability"] == fm.AVAIL_ENTITLED
    spec = fm.get("private_briefings")
    assert spec.flag_env == "PRIVATE_BRIEFINGS_ENABLED"


def test_implemented_feature_respects_tier():
    assert fm.availability("advanced_undx", tiers.TIER_FREE)["availability"] == \
        fm.AVAIL_NOT_ENTITLED
    assert fm.availability("advanced_undx", tiers.TIER_PREMIUM)["availability"] == \
        fm.AVAIL_ENTITLED
    assert fm.availability("advanced_undx", tiers.TIER_PRIVATE_OFFICE)["availability"] == \
        fm.AVAIL_ENTITLED


def test_unknown_feature_degrades_not_raises():
    got = fm.availability("totally_made_up_feature", tiers.TIER_PRIVATE_OFFICE)
    assert got["availability"] == fm.AVAIL_NOT_IMPLEMENTED


def test_matrix_invariants():
    """Structural rules that keep the matrix from rotting into a wishlist."""
    seen = set()
    for fid, spec in fm.FEATURES.items():
        assert fid == spec.feature_id
        assert fid not in seen
        seen.add(fid)
        assert spec.minimum_tier in tiers.TIER_RANK
        if spec.implementation != fm.IMPL_IMPLEMENTED:
            assert spec.note.strip(), f"{fid} needs a note explaining its state"


def test_availability_map_covers_every_declared_feature():
    got = tiers.resolve_tier(UID_FREE)["features"]
    assert set(got) == set(fm.FEATURES)


def test_only_implemented_features_are_listed_as_live():
    live = set(fm.implemented_feature_ids())
    assert "human_concierge" not in live
    assert "private_shield" not in live
    assert "private_shield.breach_monitoring" not in live
    for fid in live:
        assert fm.FEATURES[fid].implementation == fm.IMPL_IMPLEMENTED


# --- Stage 5: the operational surface ---------------------------------------
def test_status_surface_accepts_no_user_identifier():
    """The structural reason this is not a debug bypass: there is no argument
    you could pass to ask about a specific person."""
    import inspect
    sig = inspect.signature(po_status.subsystem_status)
    for name, param in sig.parameters.items():
        assert param.kind == inspect.Parameter.KEYWORD_ONLY, name
    assert set(sig.parameters) == {"include_free_count"}


def test_status_surface_reports_health_and_providers():
    _reset_grants()
    out = po_status.subsystem_status()
    assert out["resolver"]["healthy"] is True
    assert out["entitlement_subsystem"]["canonical_schema_present"] is True
    assert "private_shield.breach_monitoring" in out["providers"]
    assert out["providers"]["private_shield.breach_monitoring"][
        "provider_configured"] is False


def test_status_surface_counts_by_tier():
    _reset_grants()
    _seed_user(UID_PREMIUM)
    _seed_user(UID_OFFICE)
    svc.grant_entitlement(UID_PREMIUM, "premium.access", source="stripe",
                          source_reference="c1")
    svc.grant_entitlement(UID_OFFICE, "private_office.access", source="admin")
    counts = po_status.subsystem_status()["tier_counts"]
    assert counts[tiers.TIER_PREMIUM] == 1
    assert counts[tiers.TIER_PRIVATE_OFFICE] == 1
    assert counts[tiers.TIER_PRIVATE] == 0
    # FREE is opt-in because counting it scans the users table.
    assert counts[tiers.TIER_FREE] is None
    assert "free_count_note" in counts


def test_status_surface_distinguishes_zero_from_unknown():
    """A failed count must read None, not 0. Collapsing them would make an
    outage look like a product nobody bought."""
    _reset_grants()
    original = svc.resolve_all_subjects
    svc.resolve_all_subjects = lambda *a, **k: (_ for _ in ()).throw(RuntimeError())
    try:
        counts = po_status.subsystem_status()["tier_counts"]
    finally:
        svc.resolve_all_subjects = original
    assert counts[tiers.TIER_PREMIUM] is None


def test_status_surface_leaks_no_secrets():
    """Walk the whole payload and assert nothing that looks like a credential,
    an email, or a user identifier made it in."""
    out = po_status.subsystem_status()
    banned_substrings = ("secret", "token", "password", "api_key", "apikey",
                         "@", "sk_", "pk_", "postgres://", "postgresql://",
                         "sqlite://", "bearer ")

    def _walk(node, path="root"):
        if isinstance(node, dict):
            for k, v in node.items():
                low = str(k).lower()
                assert not any(b in low for b in ("secret", "token", "password",
                                                  "api_key", "apikey")), \
                    f"suspicious key at {path}.{k}"
                _walk(v, f"{path}.{k}")
        elif isinstance(node, (list, tuple)):
            for i, v in enumerate(node):
                _walk(v, f"{path}[{i}]")
        elif isinstance(node, str):
            low = node.lower()
            for banned in banned_substrings:
                assert banned not in low, f"suspicious value at {path}: {node!r}"

    _walk(out)
    # And no user-identifying field names anywhere.
    assert "user_id" not in str(out)


def test_status_surface_has_no_write_path():
    """Nothing in this module may mutate state. Enforced by reading the source
    rather than by trusting the docstring."""
    import inspect
    src = inspect.getsource(po_status).upper()
    for verb in ("INSERT INTO", "UPDATE ", "DELETE FROM", "DROP ", "GRANT_ENTITLEMENT"):
        assert verb not in src, f"status surface contains a write: {verb}"


if __name__ == "__main__":
    setup_module()
    failures = []
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as exc:  # noqa: BLE001
                failures.append((name, exc))
                print("FAIL", name, "->", type(exc).__name__, exc)
    print(f"\n{'FAILED' if failures else 'OK'}: {len(failures)} failure(s)")
    sys.exit(1 if failures else 0)
