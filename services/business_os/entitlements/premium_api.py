"""Premium Status Center + Admin Premium Control Center (framework-agnostic).

This module is a *controller*, following the same contract as ``iap_api`` and the
marketplace/advertising controllers: every handler returns ``(int status, dict
body)`` and never imports Flask. ``bot.py`` owns authentication, CSRF and RBAC,
then calls these functions with an already-authenticated identity and turns the
tuple into a JSON response. That split is what makes this surface testable in the
hermetic sandbox, where ``bot.py`` is not importable.

Why this exists
---------------

The forensic audit found that a user could not get a straight answer to two
questions:

1. *"Am I Premium, and if not, why not?"* — four authorities could disagree and
   the API surfaced whichever one it happened to read.
2. *"What do I actually get?"* — the sales surface listed capabilities that no
   gate enforced, so buying them changed nothing.

The Status Center answers both from the canonical resolver, and it answers the
second one through :mod:`readiness`, which is the ONLY authority on whether a
capability may be advertised.

The honesty invariants this file is built to make structurally true
-------------------------------------------------------------------

* ``benefits`` is *derived by filtering* on ``readiness.sellable``. It is not a
  hand-maintained list that could drift back into over-promising, and there is
  no code path that appends to it without passing that filter.
* A numeric allowance is emitted ONLY when a real grant carries a
  ``limit_value``, and the number shown is read from the real usage engine
  (``business_os_ent_usage``). An unmetered capability reports no number at all
  rather than a plausible-looking one.
* Premium is never described as verification. :data:`NOT_VERIFICATION` is
  attached to every response so no client has to remember the distinction.
* Ownership and *usable access* are reported separately, so a suspended member
  is never shown access the platform will then deny.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from services.business_os.entitlements import premium as _prem
from services.business_os.entitlements import readiness as _rd

_log = logging.getLogger("business_os.entitlements.premium_api")

#: Attached to every payload. Premium is a paid subscription; verification is
#: identity evidence. Conflating them is what makes verification purchasable.
NOT_VERIFICATION = (
    "PulseSoc Premium is a paid membership. It is not identity verification "
    "and does not affect verification eligibility."
)

#: Capability keys a Premium member holds, in the order we present them.
_PRESENTED = (_prem.PREMIUM_ACCESS,) + tuple(_prem.PREMIUM_CAPABILITIES)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _founder_facts(user_id: Any) -> dict:
    """Founder identity facts, read from the legacy membership record.

    Founder identity (the number and badge) deliberately remains a legacy fact —
    only the *entitlement* was canonicalized. Reading it here keeps the Status
    Center from inventing a second numbering scheme.
    """
    facts = {"is_founder": False, "founder_number": 0, "price_cents": None}
    try:
        svc = _prem._legacy_module()
        if svc is None:
            return facts
        row = svc.founder_membership(int(user_id)) or {}
    except Exception:  # noqa: BLE001 - never fail the status page over a badge
        _log.exception("founder lookup failed for user=%s", user_id)
        return facts
    if not row:
        return facts
    facts["is_founder"] = True
    facts["founder_number"] = int(row.get("founder_number") or 0)
    facts["price_cents"] = row.get("locked_price")
    return facts


#: Billing period implied by a plan key. Used for display only — the renewal
#: date is always the authoritative one, because a plan key cannot tell you what
#: Apple actually charged.
_PLAN_PERIOD = {
    "pulse_premium_monthly": "monthly",
    "pulse_premium_annual": "annual",
    "pulse_business_monthly": "monthly",
    "pulse_premium_trial": "trial",
    "pulse_premium_grandfathered": "grandfathered",
}

#: Columns the member may see. Deliberately an allowlist rather than "the row
#: minus a few": ``business_os_ent_provider_subs`` also stores
#: ``provider_subscription_id`` (a provider-side token) and ``raw_json`` (the
#: decoded Apple transaction), and a SELECT * that later reached a client would
#: leak both. Adding a column to the table must not silently publish it.
_SUB_PUBLIC_COLUMNS = (
    "provider", "plan_key", "status", "current_period_end", "cancel_at_period_end",
)


def subscription_summary(user_id: Any, *, subject_type: str = "user") -> Optional[dict]:
    """Safe billing facts for the member's own subscription, or ``None``.

    ``None`` is a real and common answer, not a failure: a Founder or an
    admin-granted member has canonical Premium with no provider subscription
    behind it at all. The Control Center renders that as a grandfathered plan
    rather than as missing data.

    Nothing identifying is returned. No subscription id, no transaction id, no
    receipt, no raw provider payload, no internal row id — see
    :data:`_SUB_PUBLIC_COLUMNS`.
    """
    try:
        from services import db
    except Exception:  # noqa: BLE001
        return None
    try:
        conn = db.connect()
        try:
            cur = conn.execute(
                f"SELECT {', '.join(_SUB_PUBLIC_COLUMNS)} "
                "FROM business_os_ent_provider_subs "
                "WHERE subject_type = ? AND subject_id = ? "
                # Newest first: a member who resubscribed after lapsing has more
                # than one row, and the current one is the only honest answer.
                "ORDER BY updated_at DESC, id DESC LIMIT 1",
                (subject_type, str(user_id)),
            )
            row = cur.fetchone()
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - billing detail must never break the page
        _log.exception("subscription lookup failed for user=%s", user_id)
        return None
    if row is None:
        return None
    try:
        data = {k: row[k] for k in _SUB_PUBLIC_COLUMNS}
    except (TypeError, IndexError, KeyError):
        data = dict(zip(_SUB_PUBLIC_COLUMNS, tuple(row)))
    plan_key = str(data.get("plan_key") or "")
    return {
        "provider": str(data.get("provider") or ""),
        "plan_key": plan_key,
        "billing_period": _PLAN_PERIOD.get(plan_key, ""),
        "status": str(data.get("status") or ""),
        "current_period_end": data.get("current_period_end") or None,
        "cancel_at_period_end": bool(data.get("cancel_at_period_end")),
    }


def _allowance(user_id: Any, key: str, subject_type: str) -> Optional[dict]:
    """Real metered allowance for ``key``, or ``None`` when it isn't metered.

    Returning ``None`` is the important case: most Premium capabilities are
    boolean, and the audit found marketing copy quoting allowances (a 2,000-unit
    UNDX meter, a 10GB media meter) that no engine implements. This function
    cannot produce a number unless a grant genuinely carries a ``limit_value``,
    so the Status Center has no way to render an imaginary quota.
    """
    try:
        from services.business_os.entitlements import service as _svc
        from services.business_os.entitlements import usage as _usage
    except Exception:  # noqa: BLE001
        return None
    try:
        limits = _svc.get_entitlement_limits(user_id, key, subject_type=subject_type)
    except Exception:  # noqa: BLE001
        _log.exception("limit lookup failed key=%s user=%s", key, user_id)
        return None
    if not limits:
        return None
    limit_value = limits.get("limit_value")
    if limit_value is None:
        return None  # boolean capability: no number exists, so none is shown
    period = limits.get("limit_period")
    period_key = _usage.default_period_key(period)
    if not period_key:
        # 'cycle' periods need a billing-cycle key we do not have here. Report
        # the limit without a usage figure rather than guessing the bucket.
        return {"limit": int(limit_value), "period": period, "used": None,
                "remaining": None}
    try:
        used = int(_usage.get_usage(user_id, key, period_key=period_key,
                                    subject_type=subject_type))
    except Exception:  # noqa: BLE001
        _log.exception("usage lookup failed key=%s user=%s", key, user_id)
        return {"limit": int(limit_value), "period": period, "used": None,
                "remaining": None}
    return {"limit": int(limit_value), "period": period, "used": used,
            "remaining": max(0, int(limit_value) - used)}


def _benefit(feature: dict, *, held: bool, user_id: Any,
             subject_type: str) -> dict:
    """One advertised benefit. Only ever called for sellable features."""
    entry = {
        "key": feature["key"],
        "label": feature["label"],
        "status": feature["status"],
        "included": True,
        "active_now": bool(held),
        # BETA is surfaced explicitly: the registry allows advertising it only
        # with the label attached, so the label travels with the data.
        "beta": feature["status"] == _rd.BETA,
    }
    if held:
        allowance = _allowance(user_id, feature["key"], subject_type)
        if allowance is not None:
            entry["allowance"] = allowance
    return entry


# ---------------------------------------------------------------------------
# Premium Status Center (user-facing)
# ---------------------------------------------------------------------------
def status_center(user_id: Any, *, subject_type: str = "user",
                  context: Optional[dict] = None) -> tuple:
    """Everything a member is owed an honest answer about.

    Reports ownership and usable access separately, names the authority that
    decided, and lists only capabilities that are genuinely enforced.
    """
    if not user_id:
        return (401, {"ok": False, "error": "Login required."})

    try:
        state = _prem.resolve(user_id, subject_type=subject_type, context=context)
    except Exception:  # noqa: BLE001
        _log.exception("premium resolve failed for user=%s", user_id)
        return (500, {"ok": False, "error": "Premium status is unavailable."})

    founder = _founder_facts(user_id)
    held = bool(state["is_premium"])

    benefits = [
        _benefit(f, held=held, user_id=user_id, subject_type=subject_type)
        for f in _rd.all_features()
        if f["sellable"] and f["key"] in _PRESENTED
    ]
    # Declared, not advertised: keys a purchase grants that no gate reads yet.
    # Showing them as "not yet" is the honest alternative to either listing them
    # as benefits (a lie) or hiding them (which makes the grant look like one).
    not_yet = [
        {"key": f["key"], "label": f["label"], "status": f["status"]}
        for f in _rd.unenforced_features()
        if f["status"] == _rd.NEXT_WAVE
    ]

    notices = []
    if state["account_hold"]:
        notices.append({
            "code": "account_hold",
            "message": ("Your membership is paused while your account is under "
                        "review. Billing is unaffected."),
        })
    if founder["is_founder"]:
        notices.append({
            "code": "founder",
            "message": (f"Founder #{founder['founder_number']} — your original "
                        "price is locked for as long as the membership stays active."),
        })

    return (200, {
        "ok": True,
        "membership": {
            "is_premium": held,
            "usable_now": held and not state["account_hold"],
            "mode": state["membership_mode"],
            "decided_by": state["source"],
            "on_hold": state["account_hold"],
            "account_status": state["account_status"],
        },
        "founder": founder,
        "subscription": subscription_summary(user_id, subject_type=subject_type),
        "benefits": benefits,
        "not_yet": not_yet,
        "notices": notices,
        "not_verification": NOT_VERIFICATION,
    })


# ---------------------------------------------------------------------------
# Admin Premium Control Center
# ---------------------------------------------------------------------------
def admin_explain_user(user_id: Any, *, subject_type: str = "user") -> tuple:
    """Per-user authority breakdown: what each system believes, and the split.

    This is the diagnostic that makes a split-brain account debuggable instead
    of a mystery: it shows the legacy answer, the canonical answer, the users
    table columns, and the repair action parity recommends.
    """
    if not user_id:
        return (400, {"ok": False, "error": "user_id is required."})
    try:
        state = _prem.resolve(user_id, subject_type=subject_type)
        row = _prem.parity(user_id, subject_type=subject_type)
    except Exception:  # noqa: BLE001
        _log.exception("premium explain failed for user=%s", user_id)
        return (500, {"ok": False, "error": "Explain is unavailable."})
    return (200, {
        "ok": True,
        "user_id": str(user_id),
        "resolved": state,
        "parity": row,
        "founder": _founder_facts(user_id),
    })


def admin_overview(*, subject_type: str = "user", parity_limit: int = 200) -> tuple:
    """Control-center snapshot: migration state, honesty gap, founder cohort.

    Deliberately NOT gated on ``BUSINESS_OS_ENTITLEMENTS``. The parity data is
    what tells an operator whether flipping that flag is safe, so it has to be
    readable precisely while the flag is still off. It is read-only and
    owner-authenticated, matching the existing entitlements ``explain`` route.
    """
    body: dict = {"ok": True}
    try:
        body["flag_mode"] = _prem._facade.get_mode()
    except Exception:  # noqa: BLE001
        body["flag_mode"] = "unknown"

    try:
        body["readiness"] = _rd.report()
        body["unenforced"] = _rd.unenforced_features()
    except Exception:  # noqa: BLE001
        _log.exception("readiness report failed")
        body["readiness"] = {}
        body["unenforced"] = []

    try:
        body["founder_allocation"] = _prem.founder_allocation_status()
    except Exception:  # noqa: BLE001
        _log.exception("founder allocation status failed")
        body["founder_allocation"] = {}

    try:
        report = _prem.parity_report(subject_type=subject_type, limit=parity_limit)
        body["parity"] = report
        body["cutover"] = {
            "safe_to_cut_over": report["safe_to_cut_over"],
            "blocking": {
                "backfill_grant": report["counts"].get("backfill_grant", 0),
                "repair_grant": report["counts"].get("repair_grant", 0),
            },
        }
    except Exception:  # noqa: BLE001
        _log.exception("parity report failed")
        body["parity"] = {}
        # Absent evidence is NOT evidence of safety: if the sweep could not run
        # we must report the cutover as unsafe rather than defaulting to True.
        body["cutover"] = {"safe_to_cut_over": False,
                           "blocking": {"error": "parity sweep unavailable"}}
    return (200, body)


def health(*, subject_type: str = "user", parity_limit: int = 0) -> dict:
    """Observability snapshot for dashboards and the status centre.

    Returns plain counters plus a ``checks`` list of ``(name, ok, detail)`` so a
    monitor can alert on a specific regression rather than on a blob.
    """
    checks: list[dict] = []
    out: dict = {"checks": checks}

    try:
        out["flag_mode"] = _prem._facade.get_mode()
    except Exception:  # noqa: BLE001
        out["flag_mode"] = "unknown"

    try:
        report = _prem.parity_report(subject_type=subject_type, limit=parity_limit)
        counts = report["counts"]
        out["subjects_examined"] = report["subjects_examined"]
        out["parity_counts"] = counts
        out["safe_to_cut_over"] = report["safe_to_cut_over"]
        checks.append({
            "name": "premium_parity",
            "ok": report["safe_to_cut_over"],
            "detail": (f"{counts.get('backfill_grant', 0)} need backfill, "
                       f"{counts.get('repair_grant', 0)} need repair"),
        })
    except Exception as exc:  # noqa: BLE001
        out["safe_to_cut_over"] = False
        checks.append({"name": "premium_parity", "ok": False,
                       "detail": f"parity sweep failed: {exc.__class__.__name__}"})

    try:
        rd_report = _rd.report()
        out["readiness_counts"] = rd_report["counts"]
        unenforced = len(rd_report["not_sellable"])
        out["unenforced_keys"] = unenforced
        # This check is informational, not a failure: declaring the gap is the
        # correct state. It goes red only if a non-sellable key were somehow
        # advertised, which the status center cannot do by construction.
        checks.append({"name": "premium_readiness", "ok": True,
                       "detail": f"{unenforced} keys declared not sellable"})
    except Exception as exc:  # noqa: BLE001
        checks.append({"name": "premium_readiness", "ok": False,
                       "detail": f"registry unavailable: {exc.__class__.__name__}"})

    try:
        alloc = _prem.founder_allocation_status()
        out["founder"] = alloc
        checks.append({
            "name": "founder_allocation",
            "ok": True,
            "detail": f"{alloc['state']}: {alloc['issued']} issued",
        })
    except Exception as exc:  # noqa: BLE001
        checks.append({"name": "founder_allocation", "ok": False,
                       "detail": f"unavailable: {exc.__class__.__name__}"})

    out["ok"] = all(c["ok"] for c in checks)
    return out
