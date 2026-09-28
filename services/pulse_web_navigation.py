"""Destinations withdrawn from the website's navigation.

Withdrawing a destination is not the same as removing it. ``/pulse/videos``
still answers, and must: ``/pulse/videos/<id>`` permalinks are handed out by the
video pages themselves, the creator tools link to it as "Manage Videos", and
people have already shared those URLs. Deleting the route would turn every one
of those into a 404, and there is no catch-all rule and no 404 handler on this
app. What is withdrawn is the *invitation* -- the tab offering it as a place to
go. Reels is the one video surface the site presents.

Web navigation is assembled in six independent places (the desktop top nav, the
universal dock, the home shell's nav and its drawer, the social shell's nav, and
the roast-battle shell), each with its own hand-written list. Six lists is how a
withdrawal ends up half-done, with the tab gone from the desktop header and
still sitting in the mobile dock. So the decision is made once here and each
list filters through ``visible()``.

Following ``services/app_promotion.is_app_first_href``: the question is asked of
the URL, not of the label. A nav entry that spells the label differently, or
carries a query string, is the same invitation and is still withdrawn.
"""

from __future__ import annotations

from typing import Iterable, Sequence, TypeVar

#: Paths the site must not offer as a navigation destination. The route stays
#: reachable; only the tab goes.
WITHDRAWN_FROM_NAV = frozenset({"/pulse/videos"})

_WITHDRAWN = {path.rstrip("/") or "/" for path in WITHDRAWN_FROM_NAV}

Item = TypeVar("Item", bound=Sequence)


def is_withdrawn(href: object) -> bool:
    """Whether ``href`` points at a destination the site no longer offers.

    Compares the path alone, so ``/pulse/videos?tab=all`` and
    ``/pulse/videos/`` are recognised as the same destination.
    """

    path = str(href or "").split("?", 1)[0].split("#", 1)[0]
    return (path.rstrip("/") or "/") in _WITHDRAWN


def visible(items: Iterable[Item], href_index: int = 1) -> list[Item]:
    """The nav entries that survive the withdrawal, in order.

    ``items`` are the ``(label, href)`` or ``(label, href, icon)`` tuples the
    nav builders already use; ``href_index`` is where the URL sits.
    """

    return [item for item in items if not is_withdrawn(item[href_index])]
