"""PulseSoc Private Office — canonical substrate.

This package is the single owner of the Private Office tier ladder, feature
matrix, private fact store, private graph, and the Private Office service
registry. It is deliberately thin: it does NOT reimplement entitlements,
UNDX capability registration, governed execution, verification, or Pulse
Briefings. Those foundations already exist and stay canonical.

Ownership boundaries are frozen in ``PRIVATE_OFFICE_OWNERSHIP_CONTRACT.md``
at the repo root. Read it before adding a module here. In particular:

* Entitlement grants remain owned by ``services/business_os/entitlements``.
  This package only *maps* canonical grants onto a four-rung tier ladder.
* Agent capabilities register in ``services/undx_capability_registry.py``.
  A second capability registry is forbidden.
* Governed execution stays ``services/undx_tool_gateway.execute()``.
* Overall user risk stays owned by ``services/user_trust_engine.py``. Nothing
  in this package may emit a score about a *person*.

Submodules are not imported eagerly: importing ``services.private_office``
must stay cheap and side-effect free so workers can import it at startup.
"""

__all__ = [
    # Entitlement (Stages 1-2)
    "tiers",
    "feature_matrix",
    "status",
    # Substrate (Stages 6-13). `model` is the shared vocabulary, `schema` owns
    # the DDL, and `facts` / `graph` are the only writers — feature code calls
    # those rather than issuing its own INSERT, which
    # `tests/private_office/test_private_write_boundary.py` enforces.
    "model",
    "schema",
    "audit",
    "facts",
    "graph",
    "contradictions",
    # Observability (Stages 34-38). `telemetry` declares the only six events
    # this package may emit and structurally forbids a fact value reaching one;
    # `health` is the read-only operator surface. Neither accepts a user
    # identifier, and `health` is the module to extend rather than adding a
    # second status endpoint elsewhere.
    "telemetry",
    "health",
    # Retrieval (Stages 15-17). The only sanctioned way for anything outside
    # this package — UNDX above all — to obtain private context. It applies
    # owner, authorization, sensitivity, domain and purpose before any row
    # leaves, which is why callers must not assemble context from `facts` and
    # `graph` directly.
    "retrieval",
]
