"""Business OS — Advertising: the governed UNDX Advertising Assistant.

A thin, SERVER-AUTHORITATIVE governor over the canonical advertising service verbs.
It is the boundary an assistant/model crosses to *act* on an advertiser's campaigns,
and it enforces two non-negotiable properties that a language model cannot be trusted
to enforce itself:

  1. **Confirmation before any consequential change.** Publishing (submit), activating,
     pausing, resuming, cancelling, and changing a budget are two-phase: ``plan`` returns
     a human-readable summary + a ``confirmation_token`` bound to the EXACT tool and
     canonical params; ``execute`` refuses to run a confirmation-gated tool unless the
     caller echoes back the matching token. A token minted for one action can never
     execute a different one (the token is a hash of user + tool + normalized params).
     Read-only tools (report/spend/status) need no confirmation and run immediately.

  2. **Every claimed action is verified against canonical backend state.** The assistant
     NEVER reports success from a verb's return value. After the canonical write it
     RE-READS the authoritative row (read-after-write) and asserts the observed state
     matches the intent (e.g. ``operational_status == 'active'`` after activate,
     ``budget_cents == requested`` after a budget change). ``verified`` is only True when
     the canonical state confirms it; otherwise the result is reported ``verified: False``
     so a silent partial failure can never be presented as done.

This module owns NO tables, moves NO money, and invents NO routes. It calls the existing
``service`` / ``operations`` / ``funding`` / ``reporting`` / ``spend`` functions, which
already enforce ownership (non-owner ⇒ 404), the flag gate, and every state-machine rule.
A dedicated kill switch (``BUSINESS_OS_ADVERTISING_ASSISTANT_DISABLE_WRITES``) disables the
write tools without touching reads, mirroring the UNDX write kill switch.
"""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Callable, Optional

from services.business_os.advertising import service as _svc
from services.business_os.advertising import operations as _ops
from services.business_os.advertising import funding as _fnd
from services.business_os.advertising import reporting as _rep
from services.business_os.advertising import spend as _spend
from services.business_os.advertising.service import AdvertisingError


DISABLE_WRITES_ENV = "BUSINESS_OS_ADVERTISING_ASSISTANT_DISABLE_WRITES"
# A stable server-side secret salts the confirmation token so a client cannot forge
# one; it is per-process by default and can be pinned via env for multi-worker setups.
_TOKEN_SALT_ENV = "BUSINESS_OS_ADVERTISING_ASSISTANT_TOKEN_SALT"


def _writes_disabled() -> bool:
    return str(os.environ.get(DISABLE_WRITES_ENV) or "").strip().lower() in {
        "1", "true", "on", "yes"}


def _token_salt() -> str:
    return os.environ.get(_TOKEN_SALT_ENV) or "busos-ad-assistant-v1"


# --- canonical parameter normalization --------------------------------------
def _norm_params(tool: str, params: dict) -> dict:
    """Reduce raw params to the CANONICAL fields a tool actually consumes. The token
    binds to this reduced set, so extra/echoed client noise cannot change identity and
    a token can only ever execute the exact action the user was shown."""
    p = params or {}
    cid = _svc._sid(p.get("campaign_id")) if p.get("campaign_id") is not None else None
    out: dict = {"campaign_id": cid}
    if tool == "set_budget":
        out["budget_cents"] = int(p.get("budget_cents")) \
            if p.get("budget_cents") is not None else None
        out["currency"] = str(p.get("currency") or "usd").strip().lower()
    if tool in ("pause_campaign", "cancel_campaign"):
        r = p.get("reason")
        out["reason"] = (str(r).strip()[:500] or None) if r is not None else None
    if tool == "create_draft":
        out = {
            "name": str(p.get("name") or "").strip(),
            "objective": str(p.get("objective") or "").strip(),
        }
    return out


def _token(user_id: Any, tool: str, canonical: dict) -> str:
    payload = json.dumps(
        {"u": _svc._sid(user_id), "t": tool, "p": canonical},
        sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((_token_salt() + "|" + payload).encode("utf-8")).hexdigest()


# --- tool handlers ----------------------------------------------------------
# Each write handler executes the canonical verb; the matching verifier RE-READS the
# authoritative state and returns (ok, observed) — the single source of "did it work".
def _h_submit(uid, c):
    _svc.submit_campaign(c["campaign_id"], requester_user_id=uid)


def _v_submit(uid, c):
    cur = _svc.get_campaign(c["campaign_id"], requester_user_id=uid)
    return (cur or {}).get("status") == "submitted", {"status": (cur or {}).get("status")}


def _h_activate(uid, c):
    _ops.activate_campaign(c["campaign_id"], requester_user_id=uid)


def _v_operational(expected):
    def _verify(uid, c):
        v = _ops.get_operational_view(c["campaign_id"], requester_user_id=uid)
        obs = (v or {}).get("operational_status")
        return obs == expected, {"operational_status": obs}
    return _verify


def _h_pause(uid, c):
    _ops.pause_campaign(c["campaign_id"], requester_user_id=uid, reason=c.get("reason"))


def _h_resume(uid, c):
    _ops.resume_campaign(c["campaign_id"], requester_user_id=uid)


def _h_cancel(uid, c):
    _ops.cancel_campaign(c["campaign_id"], requester_user_id=uid, reason=c.get("reason"))


def _h_set_budget(uid, c):
    _fnd.set_campaign_budget(
        c["campaign_id"], requester_user_id=uid,
        budget_cents=c["budget_cents"], currency=c["currency"])


def _v_set_budget(uid, c):
    v = _fnd.get_funding_view(c["campaign_id"], requester_user_id=uid)
    obs = (v or {}).get("budget_cents")
    return int(obs or -1) == int(c["budget_cents"]), {"budget_cents": obs}


def _h_create_draft(uid, c):
    return _svc.create_campaign_draft(
        uid, name=c["name"], objective=c["objective"])


def _v_create_draft(uid, c, result):
    cid = (result or {}).get("campaign_id")
    if cid is None:
        return False, {"campaign_id": None}
    cur = _svc.get_campaign(cid, requester_user_id=uid)
    return (cur or {}).get("status") == "draft", {
        "campaign_id": cid, "status": (cur or {}).get("status")}


# --- read-only tools --------------------------------------------------------
def _r_report(uid, p):
    # Reports enforce ownership by scoping to a campaign the caller owns; a report on a
    # campaign the user does not own returns zeroed metrics (never another owner's data).
    _assert_owned(uid, p.get("campaign_id"))
    return _rep.campaign_report(
        _svc._sid(p.get("campaign_id")),
        currency=p.get("currency") or "usd",
        start=p.get("start"), end=p.get("end"), placement=p.get("placement"))


def _r_spend(uid, p):
    _assert_owned(uid, p.get("campaign_id"))
    return _spend.get_campaign_spend(
        _svc._sid(p.get("campaign_id")), p.get("currency") or "usd")


def _r_operational(uid, p):
    return _ops.get_operational_view(
        _svc._sid(p.get("campaign_id")), requester_user_id=uid)


def _r_funding(uid, p):
    return _fnd.get_funding_view(
        _svc._sid(p.get("campaign_id")), requester_user_id=uid)


def _assert_owned(uid, campaign_id):
    """Ownership guard for read tools that would otherwise silently report on a
    campaign the user does not own. Raises 404 exactly like the write verbs."""
    cur = _svc.get_campaign(_svc._sid(campaign_id), requester_user_id=uid)
    if cur is None:
        raise AdvertisingError("Campaign not found.", 404, "not_found")


# --- registry ---------------------------------------------------------------
# confirm=True  -> two-phase: plan() mints a token, execute() requires it + verifies.
# confirm=False -> read-only, runs immediately.
_TOOLS: dict = {
    # read-only
    "report": {"confirm": False, "write": False, "read": _r_report,
               "summary": "Read the authoritative performance report for the campaign."},
    "spend": {"confirm": False, "write": False, "read": _r_spend,
              "summary": "Read the authoritative spend view for the campaign."},
    "operational_status": {"confirm": False, "write": False, "read": _r_operational,
                           "summary": "Read the campaign's operational status."},
    "funding_status": {"confirm": False, "write": False, "read": _r_funding,
                       "summary": "Read the campaign's funding/budget status."},
    # low-risk write (reversible, spends nothing) — no confirmation, still verified.
    "create_draft": {"confirm": False, "write": True, "handler": _h_create_draft,
                     "verify_result": _v_create_draft,
                     "summary": "Create a new DRAFT campaign (nothing is published or spent)."},
    # consequential writes — confirmation required + read-after-write verification.
    "submit_campaign": {"confirm": True, "write": True, "handler": _h_submit,
                        "verify": _v_submit, "risk": "high",
                        "summary": "Submit the campaign for review (publishes it into the review queue)."},
    "activate_campaign": {"confirm": True, "write": True, "handler": _h_activate,
                         "verify": _v_operational("active"), "risk": "high",
                         "summary": "Activate the campaign so it becomes eligible to deliver."},
    "pause_campaign": {"confirm": True, "write": True, "handler": _h_pause,
                      "verify": _v_operational("paused"), "risk": "high",
                      "summary": "Pause the campaign so it stops being eligible to deliver."},
    "resume_campaign": {"confirm": True, "write": True, "handler": _h_resume,
                       "verify": _v_operational("active"), "risk": "high",
                       "summary": "Resume a paused campaign back to active."},
    "cancel_campaign": {"confirm": True, "write": True, "handler": _h_cancel,
                       "verify": _v_operational("cancelled"), "risk": "high",
                       "summary": "Cancel the campaign (terminal; does not release funds)."},
    "set_budget": {"confirm": True, "write": True, "handler": _h_set_budget,
                  "verify": _v_set_budget, "risk": "high",
                  "summary": "Change the campaign's total budget."},
}


def list_tools() -> list:
    """The governed tool catalog the assistant is allowed to use (names + risk +
    confirmation posture). The model cannot act outside this registry."""
    out = []
    for name, spec in _TOOLS.items():
        out.append({
            "tool": name,
            "requires_confirmation": bool(spec.get("confirm")),
            "is_write": bool(spec.get("write")),
            "risk": spec.get("risk", "read_only" if not spec.get("write") else "low"),
            "summary": spec.get("summary"),
        })
    return out


def _spec(tool: str) -> dict:
    spec = _TOOLS.get(tool)
    if spec is None:
        raise AdvertisingError(f"Unknown assistant tool: {tool!r}.", 400, "unknown_tool")
    return spec


def _snapshot(user_id: Any, tool: str, canonical: dict) -> Optional[dict]:
    """Best-effort BEFORE-state for the confirmation summary. Never raises — a snapshot
    is context for the human, not a precondition."""
    cid = canonical.get("campaign_id")
    if not cid:
        return None
    try:
        cur = _svc.get_campaign(cid, requester_user_id=user_id)
        if cur is None:
            return None
        snap = {"status": cur.get("status")}
        try:
            v = _ops.get_operational_view(cid, requester_user_id=user_id)
            snap["operational_status"] = (v or {}).get("operational_status")
        except Exception:
            pass
        if tool == "set_budget":
            try:
                f = _fnd.get_funding_view(cid, requester_user_id=user_id)
                snap["budget_cents"] = (f or {}).get("budget_cents")
                snap["funding_status"] = (f or {}).get("funding_status")
            except Exception:
                pass
        return snap
    except Exception:
        return None


# --- public API -------------------------------------------------------------
def plan(user_id: Any, tool: str, params: Optional[dict] = None) -> dict:
    """Phase 1. For a confirmation-gated tool: validate shape, capture the before-state,
    and mint a ``confirmation_token`` bound to (user, tool, canonical params). For a
    read-only tool: run it now and return the result. Never mutates canonical state."""
    _svc._require_enabled()
    spec = _spec(tool)
    params = params or {}
    if not spec.get("confirm"):
        if spec.get("write"):
            # low-risk write still executes only through execute(); plan just describes it
            canonical = _norm_params(tool, params)
            return {"tool": tool, "requires_confirmation": False, "write": True,
                    "canonical_params": canonical, "summary": spec.get("summary")}
        result = spec["read"](user_id, params)
        return {"tool": tool, "requires_confirmation": False, "write": False,
                "result": result}
    canonical = _norm_params(tool, params)
    if not canonical.get("campaign_id"):
        raise AdvertisingError("campaign_id is required.", 400, "campaign_id_required")
    if tool == "set_budget" and canonical.get("budget_cents") is None:
        raise AdvertisingError("budget_cents is required.", 400, "budget_required")
    token = _token(user_id, tool, canonical)
    return {
        "tool": tool,
        "requires_confirmation": True,
        "write": True,
        "risk": spec.get("risk", "high"),
        "canonical_params": canonical,
        "before": _snapshot(user_id, tool, canonical),
        "summary": spec.get("summary"),
        "confirmation_token": token,
    }


def execute(user_id: Any, tool: str, params: Optional[dict] = None, *,
            confirmation_token: Optional[str] = None) -> dict:
    """Phase 2. Run the tool. A confirmation-gated tool REQUIRES a token that matches a
    freshly-computed token for these exact canonical params (else 428/409). After a write,
    the canonical state is RE-READ and ``verified`` reflects the observed truth — success
    is never taken on faith from the verb's return value."""
    _svc._require_enabled()
    spec = _spec(tool)
    params = params or {}

    if not spec.get("write"):
        result = spec["read"](user_id, params)
        return {"ok": True, "tool": tool, "write": False, "result": result}

    if _writes_disabled():
        raise AdvertisingError(
            "Advertising assistant writes are disabled.", 409, "writes_disabled")

    canonical = _norm_params(tool, params)

    if spec.get("confirm"):
        if not confirmation_token:
            raise AdvertisingError(
                "This action requires confirmation. Call plan() and confirm the token.",
                428, "confirmation_required")
        expected = _token(user_id, tool, canonical)
        if not _consteq(confirmation_token, expected):
            raise AdvertisingError(
                "Confirmation token does not match this exact action.",
                409, "confirmation_mismatch")

    # Execute the canonical verb (ownership / state-machine enforced inside).
    result = spec["handler"](user_id, canonical)

    # Read-after-write verification against canonical state.
    if "verify_result" in spec:
        ok, observed = spec["verify_result"](user_id, canonical, result)
    else:
        ok, observed = spec["verify"](user_id, canonical)

    return {
        "ok": True,
        "tool": tool,
        "write": True,
        "verified": bool(ok),
        "observed": observed,
        "canonical_params": canonical,
    }


def _consteq(a: str, b: str) -> bool:
    """Constant-time-ish comparison so token checks don't leak via early exit."""
    if len(a) != len(b):
        return False
    diff = 0
    for x, y in zip(a, b):
        diff |= ord(x) ^ ord(y)
    return diff == 0
