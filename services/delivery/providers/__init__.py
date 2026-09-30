"""Supplier-specific logistics providers for the delivery domain.

Everything above this package speaks the ``SupplierLogisticsProvider`` protocol
declared in :mod:`services.delivery.quote`. Everything CJ-shaped lives below it.
That boundary is the point: the estimator, the router, the cache and the composer
contain no supplier name, so a second supplier is a new module here rather than a
branch threaded through five others.
"""
