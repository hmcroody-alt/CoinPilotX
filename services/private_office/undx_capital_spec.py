"""The UNDX surface for the Capital Graph's Portfolio projection.

One capability, read-only: the authenticated member's own projected holdings,
priced live at read time, with the evidence trail attached. It follows the
``undx_records_spec`` shape — a spec module the registry, the policy table, the
knowledge map and the executor all derive from, so the four surfaces agree by
construction rather than by review.

What this capability commits to
-------------------------------
* **Read only, no companion write.** Holdings are edited in Portfolio, on the
  member's own screen; the Capital Graph is a projection of that ledger and the
  agent gets the same one-way mirror. There is no field an agent could use to
  add, change or sell anything, and no write executor to reach.
* **No advice.** The result reports what is recorded and what the market said,
  with the price feed's own freshness confession attached. It does not rank,
  recommend, forecast, or suggest acting — the description and intents are
  phrased as questions about state, not requests for judgment.
* **Evidence-cited.** Every asset row carries the fact ids and provenance the
  projection wrote, so an answer can say *why* PulseSoc believes the member
  holds 0.75 BTC (their own Portfolio entry, projected at a named time) rather
  than asserting it from nowhere.
* **No field names an account.** Owner scope is structural: the executor passes
  the session's id as both owner and actor, and ``portfolio_view`` refuses any
  actor that is not the owner. There is nothing in the schema to put another
  id into.
* **Honest numbers or none.** ``totals.value`` is ``null`` unless every holding
  was priced by a live quote; the agent must relay the refusal, not fill it in.
"""

from __future__ import annotations

from services.private_office import portfolio_projection as _projection

# The same transformations ``private.facts.list`` and the record views use,
# imported rather than restated so no surface can spell them differently.
from services.private_office.undx_records_spec import executor_name, tool_name

CAPABILITY_ID = "private.capital.portfolio"

#: The tier gate the HTTP surface runs — ``access.decide`` with this id must
#: answer AVAILABLE before the executor opens the store, so the agent cannot
#: reach a portfolio the member's own screen would refuse.
FEATURE_ID = "capital_graph"

SPEC = {
    "capability_id": CAPABILITY_ID,
    "description": (
        "Read the authenticated member's own projected Portfolio holdings "
        "with live prices, freshness and evidence"
    ),
    "intents": (
        "what do i hold", "my portfolio", "my holdings",
        "what are my holdings worth", "my portfolio value",
        "what is in my capital graph", "how much bitcoin do i have",
    ),
    "native_route": "/pulse/private-office/capital-graph",
}

#: The registration constants, kept here so the files that consume them agree
#: by construction. No arguments at all: the capability reads one thing, the
#: caller's own portfolio, and a schema with zero fields is the strongest
#: possible statement that nothing can widen it.
RISK = "read_only"
CONFIRMATION = "never"
PERMISSION = "self_account_only"
AUDIT_CATEGORY = "private_capital_read"
SERVICE_ROUTE = "services.private_office.portfolio_projection.portfolio_view"

#: The allowlist an asset row crosses the agent boundary through. ``evidence``
#: stays; internal graph plumbing (``freshness`` carries the raw fact horizon)
#: is projected down to what an answer can honestly cite.
_ASSET_FIELDS = (
    "symbol", "name", "quantity", "lot_count", "cost_basis", "price",
    "value", "pnl_value", "priced", "change_24h", "projected_at", "evidence",
)


def execute(cur, *, owner_user_id: int) -> dict:
    """The service hook the executor calls. One line of real work.

    ``owner_user_id`` is the authenticated session's id; it is passed as both
    owner and actor because there is no argument surface that could carry
    anything else. ``portfolio_view`` runs its own owner gate, sweeps the
    outbox, prices live, and refuses to total an incomplete set — this function
    adds no authorization of its own, because a second gate is a second place
    for the two to disagree.
    """
    owner = int(owner_user_id or 0)
    view = _projection.portfolio_view(cur, owner_user_id=owner, actor_user_id=owner)
    if not view.get("ok"):
        return {"ok": False, "denied": view.get("denied") or {"reason": "denied"},
                "records": [], "counts": {"returned": 0}}
    records = [
        {key: asset.get(key) for key in _ASSET_FIELDS}
        for asset in view.get("assets") or []
    ]
    return {
        "ok": True,
        "records": records,
        "counts": {"returned": len(records)},
        "totals": view.get("totals") or {},
        "prices": view.get("prices") or {},
        "sync": view.get("sync") or {},
    }
