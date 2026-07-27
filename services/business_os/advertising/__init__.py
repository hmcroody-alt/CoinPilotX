"""Business OS — Advertising vertical (server-authoritative, flag-gated).

Slice 1 scope: advertiser eligibility/account status, campaign *draft* creation,
campaign ownership + lifecycle state, server-side validation, and admin visibility.
Explicitly OUT of slice 1: wallet spending, billing, auction/delivery, advanced
targeting, reporting. Everything here is additive (`business_os_ad_*` tables) and
inert unless ``BUSINESS_OS_ADVERTISING`` is enabled; the legacy ``pulse_ads_service``
is never touched.
"""

from . import schema  # noqa: F401
from . import service  # noqa: F401
