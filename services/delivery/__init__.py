"""Delivery intelligence: when a purchase should be expected to arrive.

The domain is deliberately provider-agnostic. A supplier adapter turns its own
vocabulary into a normalized transit range at the provider boundary; everything
in this package consumes that range and knows nothing about CJ, its endpoints,
or its field names. A second supplier is a new adapter, not a change here.
"""
