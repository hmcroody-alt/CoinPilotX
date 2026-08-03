"""Business OS — Advertising vertical, slice-4 funding service (flag-gated).

The minimum canonical workflow that lets a REVIEW-APPROVED campaign become
*financially ready* for future delivery — WITHOUT serving a single ad. It keeps
four concerns strictly separate:

    1. Review approval      -> campaign.status == 'approved'   (slice 3)
    2. Funding readiness    -> funding_status                  (THIS slice)
    3. Activation readiness -> derived: funded AND approved AND not archived
    4. Live delivery        -> NOT STARTED anywhere

Funding states (deliberately NOT mixed with the review lifecycle):
``unfunded | funding_pending | funded | funding_failed | released``.

Money integrity is delegated to the canonical ledger
(``services.business_os.ledger.ledger``): every reserve/release is an immutable,
idempotent, overdraft-guarded double-entry posting. This module never mutates a
bare balance — balances are always reconstructable from ledger entries. The
funding tables here hold only the configured budget, the funding STATE, and
references (transaction ids) back to the ledger entries that drove each move.

Idempotency is enforced at two layers, both at the DB level:

  * ``business_os_ad_funding_ops.idempotency_key`` is UNIQUE — a retried
    reserve/release collides and is served as a no-op; the same key reused for a
    DIFFERENT operation (op/campaign/amount) is detected and rejected 409.
  * ``ledger_transactions.idempotency_key`` is UNIQUE — a re-driven posting with
    the same key never writes a second entry, so a retry cannot reserve twice.

Reserve moves money advertiser-wallet -> campaign-escrow; release moves it back.
Both accounts are overdraft-guarded by the ledger (they are NOT in the ledger's
allow-negative prefix set), so a reservation that would overdraw the wallet is
rejected atomically and the campaign is marked ``funding_failed`` — never silently
funded, never clamped to zero.

Nothing here delivers, auctions, bids, paces, targets, or reports. "Funded" means
funds reserved in escrow and the campaign is activation-*ready*; it is not live.
"""

from __future__ import annotations

from typing import Any, Optional

from services import db
from services.business_os.advertising import service as _svc
from services.business_os.advertising.service import AdvertisingError
from services.business_os.ledger import ledger as _ledger


# --- constants --------------------------------------------------------------
SUPPORTED_CURRENCIES = {"usd"}
FUNDING_STATUSES = {
    "unfunded", "funding_pending", "funded", "funding_failed", "released",
}
_RESERVE = "reserve"
_RELEASE = "release"
# Only these funding states permit a (re)reservation attempt. "funded" is handled
# separately (idempotent retry vs. already-funded conflict).
_RESERVABLE_FROM = {"unfunded", "funding_failed", "released", "funding_pending"}


# --- small helpers ----------------------------------------------------------
def _is_unique(exc: Exception) -> bool:
    """Engine-agnostic UNIQUE / primary-key violation detection."""
    name = exc.__class__.__name__.lower()
    msg = str(exc).lower()
    if "integrityerror" in name or "uniqueviolation" in name:
        return True
    return "unique" in msg or "duplicate key" in msg


def _wallet_account(user_id: Any) -> str:
    """Advertiser's spendable wallet. Overdraft-guarded by the ledger."""
    return f"advertiser:{_svc._sid(user_id)}:wallet"


def _escrow_account(campaign_id: str) -> str:
    """Per-campaign escrow that holds reserved budget. Overdraft-guarded."""
    return f"ad_campaign_escrow:{campaign_id}"


def _ledger_key(op: str, key: str) -> str:
    """Namespaced ledger idempotency key so a reserve and a release can never
    collide even if a client (incorrectly) reuses the same funding key — that
    cross-operation reuse is already rejected earlier, this is defence in depth."""
    return f"ad_campaign:{op}:{key}"


def _clean_key(idempotency_key: Any) -> str:
    key = str(idempotency_key or "").strip()
    if not key:
        raise AdvertisingError(
            "An idempotency key is required.", 400, "idempotency_key_required")
    if len(key) > 200:
        raise AdvertisingError("Idempotency key too long.", 400, "bad_idempotency_key")
    return key


def _norm_currency(currency: Any) -> str:
    c = str(currency or "").strip().lower()
    if c not in SUPPORTED_CURRENCIES:
        raise AdvertisingError(f"Unsupported currency: {currency!r}.", 400, "bad_currency")
    return c


def _norm_amount(amount_cents: Any) -> int:
    if isinstance(amount_cents, bool):
        raise AdvertisingError(
            "Amount must be an integer number of cents.", 400, "bad_amount")
    if isinstance(amount_cents, int):
        n = amount_cents
    elif isinstance(amount_cents, str) and amount_cents.strip().lstrip("-").isdigit():
        n = int(amount_cents.strip())
    else:
        raise AdvertisingError(
            "Amount must be an integer number of cents.", 400, "bad_amount")
    if n <= 0:
        raise AdvertisingError(
            "Amount must be a positive number of cents.", 400, "bad_amount")
    return n


def _get_funding_row(conn, campaign_id: str) -> Optional[dict]:
    return _svc._row_to_dict(conn.execute(
        "SELECT * FROM business_os_ad_campaign_funding WHERE campaign_id = ?",
        (campaign_id,)).fetchone())


def _get_op(conn, key: str) -> Optional[dict]:
    return _svc._row_to_dict(conn.execute(
        "SELECT * FROM business_os_ad_funding_ops WHERE idempotency_key = ?",
        (key,)).fetchone())


def _op_matches(op: dict, operation: str, campaign_id: str,
                amount_cents: int, currency: str) -> bool:
    return (
        op.get("operation") == operation
        and op.get("campaign_id") == campaign_id
        and int(op.get("amount_cents") or -1) == int(amount_cents)
        and (op.get("currency") or "").lower() == currency
    )


def _funding_public(campaign: dict, funding: Optional[dict]) -> dict:
    """Client-safe funding projection. ``activation_ready`` is DERIVED live from
    the three separate inputs — it is never a stored authority."""
    review_status = campaign.get("status")
    archived = review_status == "archived"
    fstatus = (funding or {}).get("funding_status") or "unfunded"
    activation_ready = (
        fstatus == "funded" and review_status == "approved" and not archived)
    return {
        "campaign_id": campaign.get("campaign_id"),
        "advertiser_user_id": campaign.get("advertiser_user_id"),
        "review_status": review_status,
        "funding_status": fstatus,
        "budget_cents": (funding or {}).get("budget_cents"),
        "currency": (funding or {}).get("currency"),
        "reserved_amount_cents": (funding or {}).get("reserved_amount_cents"),
        "reservation_txn_id": (funding or {}).get("reservation_txn_id"),
        "release_txn_id": (funding or {}).get("release_txn_id"),
        "failure_reason": (funding or {}).get("failure_reason"),
        "activation_ready": bool(activation_ready),
        "updated_at": (funding or {}).get("updated_at"),
    }


# --- reads ------------------------------------------------------------------
def get_funding_view(campaign_id: str, *, requester_user_id: Optional[Any] = None,
                     conn=None) -> dict:
    """Funding readiness for one campaign. Ownership enforced when a requester is
    supplied (non-owner ⇒ 404); pass ``requester_user_id=None`` from admin paths."""
    _svc._require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        campaign = _svc.get_campaign(
            campaign_id, requester_user_id=requester_user_id, conn=conn)
        if campaign is None:
            raise AdvertisingError("Campaign not found.", 404, "not_found")
        return _funding_public(campaign, _get_funding_row(conn, campaign_id))
    finally:
        if owned:
            conn.close()


# --- budget configuration ---------------------------------------------------
def set_campaign_budget(campaign_id: str, *, requester_user_id: Any,
                        budget_cents: Any, currency: Any,
                        context: Optional[dict] = None,
                        actor: Optional[Any] = None, conn=None) -> dict:
    """Configure (or update) an owned campaign's total budget before funding.

    Minimal fields only: a positive integer ``budget_cents`` and a supported
    ``currency``. Ownership enforced (non-owner ⇒ 404). An archived campaign
    cannot be budgeted (409). Once funds are in flight or reserved
    (``funding_pending``/``funded``) the budget is locked (409 ``funding_locked``)
    so it can never desync from an existing reservation. No bidding, pacing,
    targeting price, or forecasting is introduced.
    """
    _svc._require_enabled()
    budget_cents = _norm_amount(budget_cents)
    currency = _norm_currency(currency)
    owned = conn is None
    if owned:
        conn = db.connect()
    uid = _svc._sid(requester_user_id)
    try:
        campaign = _svc.get_campaign(
            campaign_id, requester_user_id=requester_user_id, conn=conn)  # 404
        if campaign.get("status") == "archived":
            raise AdvertisingError(
                "Archived campaigns cannot be budgeted.", 409, "archived")
        _svc._begin(conn)
        funding = _get_funding_row(conn, campaign_id)
        if funding is not None and funding.get("funding_status") in (
                "funding_pending", "funded"):
            _svc._rollback(conn)
            raise AdvertisingError(
                "Budget cannot change once the campaign is funded.",
                409, "funding_locked")
        now = _svc._now_iso()
        if funding is None:
            conn.execute(
                "INSERT INTO business_os_ad_campaign_funding "
                "(campaign_id, advertiser_user_id, budget_cents, currency, "
                "funding_status, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, 'unfunded', ?, ?)",
                (campaign_id, uid, budget_cents, currency, now, now))
            before = None
        else:
            before = {"budget_cents": funding.get("budget_cents"),
                      "currency": funding.get("currency")}
            conn.execute(
                "UPDATE business_os_ad_campaign_funding "
                "SET budget_cents = ?, currency = ?, updated_at = ? "
                "WHERE campaign_id = ?",
                (budget_cents, currency, now, campaign_id))
        _svc._audit(conn, campaign_id=campaign_id, advertiser_user_id=uid,
                    action="campaign_funding_budget",
                    actor=actor if actor is not None else requester_user_id,
                    before=before,
                    after={"budget_cents": budget_cents, "currency": currency})
        _svc._commit(conn)
        return _funding_public(
            _svc.get_campaign(campaign_id, requester_user_id=requester_user_id,
                              conn=conn),
            _get_funding_row(conn, campaign_id))
    except AdvertisingError:
        _svc._rollback(conn)
        raise
    except Exception:
        _svc._rollback(conn)
        raise
    finally:
        if owned:
            conn.close()


# --- state transitions (own short transactions, self-healing) ---------------
def _claim_reserve_pending(campaign_id, advertiser_uid, key, amount_cents,
                           currency, actor) -> None:
    conn = db.connect()
    try:
        _svc._begin(conn)
        if _get_op(conn, key) is None:
            try:
                conn.execute(
                    "INSERT INTO business_os_ad_funding_ops "
                    "(idempotency_key, campaign_id, operation, amount_cents, "
                    "currency, actor, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (key, campaign_id, _RESERVE, amount_cents, currency,
                     None if actor is None else str(actor), _svc._now_iso()))
            except Exception as exc:  # noqa: BLE001
                _svc._rollback(conn)
                if _is_unique(exc):
                    return  # concurrent claim of the same key — safe to proceed
                raise
        # Never regress an already-funded row (guards a concurrent finalize).
        conn.execute(
            "UPDATE business_os_ad_campaign_funding "
            "SET funding_status = 'funding_pending', reservation_key = ?, "
            "failure_reason = NULL, updated_at = ? "
            "WHERE campaign_id = ? AND funding_status != 'funded'",
            (key, _svc._now_iso(), campaign_id))
        _svc._audit(conn, campaign_id=campaign_id,
                    advertiser_user_id=advertiser_uid,
                    action="campaign_funding_pending", actor=actor,
                    after={"funding_status": "funding_pending",
                           "reservation_key": key})
        _svc._commit(conn)
    except Exception:
        _svc._rollback(conn)
        raise
    finally:
        conn.close()


def _mark_funded(campaign_id, advertiser_uid, key, amount_cents, currency,
                 txn_id, actor) -> None:
    conn = db.connect()
    try:
        _svc._begin(conn)
        conn.execute(
            "UPDATE business_os_ad_campaign_funding "
            "SET funding_status = 'funded', reserved_amount_cents = ?, "
            "currency = ?, reservation_key = ?, reservation_txn_id = ?, "
            "failure_reason = NULL, updated_at = ? WHERE campaign_id = ?",
            (amount_cents, currency, key, txn_id, _svc._now_iso(), campaign_id))
        conn.execute(
            "UPDATE business_os_ad_funding_ops SET ledger_txn_id = ? "
            "WHERE idempotency_key = ?", (txn_id, key))
        _svc._audit(conn, campaign_id=campaign_id,
                    advertiser_user_id=advertiser_uid,
                    action="campaign_funding_reserved", actor=actor,
                    after={"funding_status": "funded",
                           "reservation_txn_id": txn_id,
                           "reserved_amount_cents": amount_cents})
        _svc._commit(conn)
    except Exception:
        _svc._rollback(conn)
        raise
    finally:
        conn.close()


def _mark_failed(campaign_id, advertiser_uid, key, reason, actor) -> None:
    conn = db.connect()
    try:
        _svc._begin(conn)
        conn.execute(
            "UPDATE business_os_ad_campaign_funding "
            "SET funding_status = 'funding_failed', failure_reason = ?, "
            "updated_at = ? WHERE campaign_id = ? AND funding_status != 'funded'",
            (str(reason)[:500], _svc._now_iso(), campaign_id))
        _svc._audit(conn, campaign_id=campaign_id,
                    advertiser_user_id=advertiser_uid,
                    action="campaign_funding_failed", actor=actor,
                    reason=str(reason)[:500],
                    after={"funding_status": "funding_failed"})
        _svc._commit(conn)
    except Exception:
        _svc._rollback(conn)
        raise
    finally:
        conn.close()


def _claim_release(campaign_id, key, amount_cents, currency, actor) -> None:
    conn = db.connect()
    try:
        _svc._begin(conn)
        if _get_op(conn, key) is None:
            try:
                conn.execute(
                    "INSERT INTO business_os_ad_funding_ops "
                    "(idempotency_key, campaign_id, operation, amount_cents, "
                    "currency, actor, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (key, campaign_id, _RELEASE, amount_cents, currency,
                     None if actor is None else str(actor), _svc._now_iso()))
            except Exception as exc:  # noqa: BLE001
                _svc._rollback(conn)
                if _is_unique(exc):
                    return
                raise
        _svc._commit(conn)
    except Exception:
        _svc._rollback(conn)
        raise
    finally:
        conn.close()


def _mark_released(campaign_id, advertiser_uid, key, txn_id,
                   reservation_txn_id, actor) -> None:
    conn = db.connect()
    try:
        _svc._begin(conn)
        conn.execute(
            "UPDATE business_os_ad_campaign_funding "
            "SET funding_status = 'released', release_txn_id = ?, updated_at = ? "
            "WHERE campaign_id = ?",
            (txn_id, _svc._now_iso(), campaign_id))
        conn.execute(
            "UPDATE business_os_ad_funding_ops "
            "SET ledger_txn_id = ?, related_txn_id = ? WHERE idempotency_key = ?",
            (txn_id, reservation_txn_id, key))
        _svc._audit(conn, campaign_id=campaign_id,
                    advertiser_user_id=advertiser_uid,
                    action="campaign_funding_released", actor=actor,
                    after={"funding_status": "released",
                           "release_txn_id": txn_id,
                           "release_of": reservation_txn_id})
        _svc._commit(conn)
    except Exception:
        _svc._rollback(conn)
        raise
    finally:
        conn.close()


# --- reserve ----------------------------------------------------------------
def reserve_funds(campaign_id: str, *, requester_user_id: Any,
                  idempotency_key: Any, amount_cents: Any, currency: Any,
                  context: Optional[dict] = None,
                  actor: Optional[Any] = None) -> dict:
    """Reserve the configured budget for a review-approved, owned campaign.

    Validates (in order): flag on; advertiser eligible (flag + account hold +
    approval); campaign owned (404 if not); not archived (409); review status is
    ``approved`` (409 ``not_approved``); a budget is configured (409 ``no_budget``);
    the requested amount and currency EXACTLY match the configured budget
    (400 ``amount_mismatch`` / ``currency_mismatch``).

    Idempotency: a genuine retry (same key, same reserve/campaign/amount) is a
    no-op that re-drives the idempotent ledger posting and returns the funded
    state. The same key reused for a different operation is rejected 409
    ``idempotency_conflict``. A campaign already funded under a different key is
    rejected 409 ``already_funded`` — funds are reserved exactly once.

    Insufficient wallet balance is rejected atomically by the ledger's overdraft
    guard: the campaign is marked ``funding_failed`` (never funded, never clamped)
    and 402 ``insufficient_funds`` is raised.
    """
    _svc._require_enabled()
    key = _clean_key(idempotency_key)
    currency = _norm_currency(currency)
    amount_cents = _norm_amount(amount_cents)

    conn = db.connect()
    try:
        elig = _svc.advertiser_eligibility(requester_user_id, context=context, conn=conn)
        if not elig.get("eligible"):
            raise AdvertisingError(
                f"Not eligible to fund campaigns ({elig.get('reason')}).",
                403, "ineligible")
        campaign = _svc.get_campaign(
            campaign_id, requester_user_id=requester_user_id, conn=conn)  # 404
        advertiser_uid = campaign.get("advertiser_user_id")
        if campaign.get("status") == "archived":
            raise AdvertisingError(
                "Archived campaigns cannot be funded.", 409, "archived")
        if campaign.get("status") != "approved":
            raise AdvertisingError(
                f"Only review-approved campaigns can be funded (is "
                f"{campaign.get('status')}).", 409, "not_approved")
        funding = _get_funding_row(conn, campaign_id)
        if funding is None or funding.get("budget_cents") is None:
            raise AdvertisingError(
                "Configure a campaign budget before funding.", 409, "no_budget")
        if amount_cents != int(funding.get("budget_cents")):
            raise AdvertisingError(
                "Funding amount must match the configured budget.",
                400, "amount_mismatch")
        if currency != (funding.get("currency") or "").lower():
            raise AdvertisingError(
                "Funding currency must match the configured budget currency.",
                400, "currency_mismatch")

        existing_op = _get_op(conn, key)
        if existing_op is not None and not _op_matches(
                existing_op, _RESERVE, campaign_id, amount_cents, currency):
            raise AdvertisingError(
                "Idempotency key already used for a different operation.",
                409, "idempotency_conflict")

        status = funding.get("funding_status")
        if status == "funded":
            if funding.get("reservation_key") == key:
                return _funding_public(campaign, funding)  # idempotent success
            raise AdvertisingError(
                "Campaign is already funded.", 409, "already_funded")
        if status not in _RESERVABLE_FROM:
            raise AdvertisingError(
                f"Campaign cannot be funded from state {status!r}.",
                409, "not_reservable")
    finally:
        conn.close()

    # Claim the operation + mark pending (self-healing; safe to re-run).
    _claim_reserve_pending(campaign_id, advertiser_uid, key, amount_cents,
                           currency, actor)

    # Post the immutable, idempotent, overdraft-guarded reservation to the ledger.
    try:
        txn = _ledger.post_entry(
            idempotency_key=_ledger_key(_RESERVE, key),
            actor=str(actor if actor is not None else requester_user_id),
            amount_cents=amount_cents, currency=currency,
            entry_type="ad_campaign_reserve",
            source=_wallet_account(requester_user_id),
            destination=_escrow_account(campaign_id),
            reason="advertising campaign funding reservation",
            related_object=f"ad_campaign:{campaign_id}",
            metadata={"campaign_id": campaign_id, "funding_key": key})
    except _ledger.LedgerError as exc:
        _mark_failed(campaign_id, advertiser_uid, key, str(exc), actor)
        raise AdvertisingError(
            "Insufficient funds to reserve the campaign budget.",
            402, "insufficient_funds")

    _mark_funded(campaign_id, advertiser_uid, key, amount_cents, currency,
                 txn.get("transaction_id"), actor)
    return get_funding_view(campaign_id, requester_user_id=requester_user_id)


# --- release ----------------------------------------------------------------
def release_funds(campaign_id: str, *, requester_user_id: Any,
                  idempotency_key: Any, context: Optional[dict] = None,
                  actor: Optional[Any] = None) -> dict:
    """Release a funded campaign's reserved budget back to the advertiser wallet.

    Ownership enforced (404). Only a ``funded`` campaign can be released; a
    duplicate release (already ``released``) is an idempotent no-op returning the
    current state. The release posting references the original reservation
    transaction so the money is traceable and restored exactly once. Reusing the
    key for a different operation is rejected 409 ``idempotency_conflict``.

    Release is an EXPLICIT verb — it is deliberately NOT triggered automatically by
    archive/withdraw, keeping the funding and review lifecycles independent.
    """
    _svc._require_enabled()
    key = _clean_key(idempotency_key)

    conn = db.connect()
    try:
        campaign = _svc.get_campaign(
            campaign_id, requester_user_id=requester_user_id, conn=conn)  # 404
        advertiser_uid = campaign.get("advertiser_user_id")
        funding = _get_funding_row(conn, campaign_id)
        if funding is None:
            raise AdvertisingError("No funding to release.", 409, "not_funded")
        reserved = int(funding.get("reserved_amount_cents") or 0)
        currency = (funding.get("currency") or "usd").lower()
        reservation_txn_id = funding.get("reservation_txn_id")

        existing_op = _get_op(conn, key)
        if existing_op is not None and not _op_matches(
                existing_op, _RELEASE, campaign_id, reserved, currency):
            raise AdvertisingError(
                "Idempotency key already used for a different operation.",
                409, "idempotency_conflict")

        status = funding.get("funding_status")
        if status == "released":
            return _funding_public(campaign, funding)  # idempotent duplicate
        if status != "funded":
            raise AdvertisingError(
                f"Only funded campaigns can be released (is {status}).",
                409, "not_funded")
        if reserved <= 0:
            raise AdvertisingError(
                "No reserved amount to release.", 409, "nothing_reserved")
    finally:
        conn.close()

    _claim_release(campaign_id, key, reserved, currency, actor)

    try:
        txn = _ledger.post_entry(
            idempotency_key=_ledger_key(_RELEASE, key),
            actor=str(actor if actor is not None else requester_user_id),
            amount_cents=reserved, currency=currency,
            entry_type="ad_campaign_release",
            source=_escrow_account(campaign_id),
            destination=_wallet_account(requester_user_id),
            reason="advertising campaign funding release",
            related_object=f"ad_campaign:{campaign_id}",
            provider_reference=reservation_txn_id or "",
            metadata={"campaign_id": campaign_id,
                      "release_of": reservation_txn_id, "funding_key": key})
    except _ledger.LedgerError as exc:
        raise AdvertisingError(
            f"Unable to release reserved funds: {exc}", 409, "release_failed")

    _mark_released(campaign_id, advertiser_uid, key, txn.get("transaction_id"),
                   reservation_txn_id, actor)
    return get_funding_view(campaign_id, requester_user_id=requester_user_id)


# --- the advertiser wallet, as one shared object ----------------------------
def wallet_account(user_id: Any) -> str:
    """The public name of an advertiser's wallet account.

    Exported because more than one screen renders this balance, and the account
    naming scheme must have exactly one owner. A second surface that built the
    string ``f"advertiser:{uid}:wallet"`` for itself would be correct right up
    until this module changed the scheme, at which point the two screens would
    disagree about a balance while both looked authoritative.
    """
    return _wallet_account(user_id)


def wallet_view(user_id: Any, currency: str = "usd", *, conn=None) -> dict:
    """The advertiser's spendable ad wallet: one ledger balance, plus the truth
    about how money gets into it.

    ``balance_cents`` is a real ledger balance — not a sum of campaign budgets,
    not budget minus spend. Reserving a campaign's budget moves cents out of this
    account into ``ad_campaign_escrow:<campaign_id>``, so the wallet balance is
    already net of every live reservation and nothing needs to be subtracted from
    it afterwards.

    ``reserved_cents`` is the total currently sitting in that campaign escrow —
    reported separately because it is the advertiser's money but is not
    spendable. It is summed here, server-side, from ledger balances.

    **Top-up does not exist in this environment, and this function says so
    rather than leaving a caller to discover it.** Every posting that touches a
    wallet account in this codebase moves cents between the wallet and a campaign
    escrow; nothing credits the wallet from an external funding source. The
    wallet is also overdraft-guarded. So an advertiser who has never had cents
    posted in by some other means has a wallet balance of exactly zero and no
    in-product way to change that. ``funding_source`` reports this as
    ``"none_in_product"`` and ``auto_topup`` as ``"unsupported"`` so a client
    ships the top-up affordance absent instead of rendering a button that cannot
    work. Building that path is a payment-path decision, not a rendering one.
    """
    _svc._require_enabled()
    cur_code = str(currency or "usd").strip().lower()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        account = _wallet_account(user_id)
        balance = _ledger.get_balance(account, cur_code, conn=conn)

        # Reserved = what this advertiser's campaigns are holding in escrow.
        # Scoped by campaign ownership, so one advertiser can never see another's
        # reservation folded into their own figure.
        rows = conn.execute(
            "SELECT c.campaign_id FROM business_os_ad_campaigns c "
            "WHERE c.advertiser_user_id = ?",
            (_svc._sid(user_id),)).fetchall()
        reserved = 0
        escrow_accounts = []
        for r in rows:
            cid = (_svc._row_to_dict(r) or {}).get("campaign_id")
            if not cid:
                continue
            acct = _escrow_account(cid)
            held = _ledger.get_balance(acct, cur_code, conn=conn)
            if held > 0:
                reserved += held
                escrow_accounts.append(acct)

        return {
            "advertiser_user_id": _svc._sid(user_id),
            "currency": cur_code,
            "account": account,
            # Spendable now. Already net of reservations.
            "balance_cents": balance,
            # Advertiser's money, currently committed to campaigns.
            "reserved_cents": reserved,
            "reserved_campaign_count": len(escrow_accounts),
            "accounts": {"wallet": account, "campaign_escrow": escrow_accounts},
            # Stated, not omitted — see the docstring.
            "funding_source": "none_in_product",
            "auto_topup": "unsupported",
        }
    finally:
        if owned:
            conn.close()


# --- admin visibility (trusted callers; RBAC enforced at the route) ---------
def admin_get_funding(campaign_id: str, *, conn=None) -> dict:
    """Admin funding view: state + ledger references + escrow balance + the full
    append-only operation log. No ownership scoping (route enforces owner RBAC)."""
    _svc._require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        campaign = _svc.get_campaign(campaign_id, requester_user_id=None, conn=conn)
        if campaign is None:
            raise AdvertisingError("Campaign not found.", 404, "not_found")
        funding = _get_funding_row(conn, campaign_id)
        ops = [_svc._row_to_dict(r) for r in conn.execute(
            "SELECT * FROM business_os_ad_funding_ops WHERE campaign_id = ? "
            "ORDER BY id", (campaign_id,)).fetchall()]
        view = _funding_public(campaign, funding)
        currency = (funding or {}).get("currency") or "usd"
        escrow = _escrow_account(campaign_id)
        view["escrow_account"] = escrow
        try:
            view["escrow_balance_cents"] = _ledger.get_balance(escrow, currency)
        except Exception:
            view["escrow_balance_cents"] = None
        view["operations"] = ops
        return view
    finally:
        if owned:
            conn.close()


def admin_list_funding(*, funding_status: Optional[str] = None,
                       limit: int = 200, conn=None) -> list:
    """Admin cross-owner funding listing with an optional funding_status filter
    (e.g. ``funding_failed`` to inspect failed/inconsistent reservations)."""
    _svc._require_enabled()
    owned = conn is None
    if owned:
        conn = db.connect()
    try:
        if funding_status is not None:
            cur = conn.execute(
                "SELECT * FROM business_os_ad_campaign_funding "
                "WHERE funding_status = ? ORDER BY updated_at DESC LIMIT ?",
                (funding_status, int(limit)))
        else:
            cur = conn.execute(
                "SELECT * FROM business_os_ad_campaign_funding "
                "ORDER BY updated_at DESC LIMIT ?", (int(limit),))
        return [_svc._row_to_dict(r) for r in cur.fetchall()]
    finally:
        if owned:
            conn.close()
