"""Pulse Communications 2.0 package.

This package is safe to import for health checks and static contract tests.
The v2 messaging system is published by default and can be rolled back by
setting PULSE_COMMUNICATIONS_V2_ENABLED=false.
"""

from .flags import PULSE_COMMUNICATIONS_V2_ENABLED, is_enabled

__all__ = ["PULSE_COMMUNICATIONS_V2_ENABLED", "is_enabled"]
