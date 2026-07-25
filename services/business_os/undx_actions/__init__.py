"""Governed UNDX business actions vertical (Business OS, Stage 6).

Informational-only, deterministic **governance decision projection** over proposed
UNDX business actions. An org declares governance *policies* (which action types are
allowed, denied, or require human approval, with an optional risk ceiling); an
append-only log records proposed *action requests* (an actor proposed to run an action
of some type against some subject, at a declared risk). The engine computes a
rebuildable per-org projection: for each pending request, a deterministic **decision**
(``allow`` / ``deny`` / ``require_approval``) by evaluating the highest-priority active
policy that matches, escalating to ``require_approval`` when the request's declared risk
exceeds the policy's ceiling, and defaulting to ``require_approval`` when nothing
matches (safe governance default).

Hard boundary — NOTHING executes the action. A decision is a *governance label* — a
reporting quantity summarizing what governance *would* permit — not an instruction and
not an execution. Nothing here runs a tool, sends a message, posts content, moves money,
or takes any side effect. Whether and how an ``allow`` decision is ever acted on is a
separate, separately-reviewed integration on top of the product's real action systems.

Gated behind ``BUSINESS_OS_UNDX_ACTIONS``. Follows the strangler pattern of the
attribution / recommendations / merchant-automation / creator-commerce verticals:
canonical ``business_os_undx_*`` tables, append-only truth + rebuildable projection,
idempotent ingest, dark-404 gating, curated error codes. Nothing legacy is read or
written.
"""
