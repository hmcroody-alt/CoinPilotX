"""Business OS — Section 2: Store (business-scoped storefront + catalog).

The canonical storefront layer for a Section-1 Business. A Store is owned by a
``business_id`` (never by a bare user) and every access decision is resolved against
the S1 canonical membership/RBAC — there is no second identity or permission system
here. This domain owns storefront presentation, the business's product catalog, and
merchandising collections; it deliberately does NOT implement orders, carts, payments,
or payouts (those are the Orders / Payments canonical domains). Reuse over duplication.

Dark unless ``BUSINESS_OS_STORE`` is enabled. Additive, strangler-pattern: nothing
legacy and no marketplace table is altered or referenced.
"""
