"""Pulse Communications 2.0 staging package.

This package is safe to import for disabled health checks and static contract
tests. The actual v2 messaging system remains inactive while the feature flag
defaults to false.
"""

from .flags import PULSE_COMMUNICATIONS_V2_ENABLED

__all__ = ["PULSE_COMMUNICATIONS_V2_ENABLED"]
