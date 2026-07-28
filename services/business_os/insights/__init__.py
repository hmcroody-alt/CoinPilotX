"""Business OS — Section 7: Insights (the ONE canonical business-insights domain).

Analytics on this platform is already computed by three stable, single-purpose engines:

  * ``business_os.attribution`` — multi-touch conversion credit (campaign / channel);
  * ``business_os.recommendations`` — item popularity + per-user recommendations;
  * ``business_os.performance`` — org metric rollups vs. targets (status labels).

This package is NOT a fourth analytics store. It owns NO new table. It is the canonical
*business insights surface* that UNIFIES those three engines behind one business-scoped,
RBAC-guarded read facade: given a business a member is authorized on, it assembles a single
overview — the business's performance summary, the platform attribution report, and item
popularity — by calling the engines' own read-only report functions. Nothing is recomputed
or duplicated here; if an analytic changes it changes in its one engine and this surface
inherits it.

Identity is always the authenticated caller; who may read a business's insights is resolved
against S1 canonical membership/RBAC (``business.service._effective_role``), never re-modeled
here. Enabling the surface is gated behind ``BUSINESS_OS_INSIGHTS``; when off it is dark.
"""
