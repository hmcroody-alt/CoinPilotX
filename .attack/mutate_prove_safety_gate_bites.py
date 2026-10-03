"""Phase 125: prove the GREEN assertions fail when the defect they guard appears.

`test_no_page_invites_indexing_that_the_table_wants_kept_out` and
`test_a_page_..._sends_a_directive_at_all` pass today. A passing test proves
nothing about its own power -- it may be reading the wrong thing, matching with
a regex that never matches, or iterating an empty list. Agent 2 §6.3 and the
existing `scripts/protection/mutate_sitemap_and_robots_agreement.py` both exist
because an assertion in this exact area once survived reverting the fix.

So inject the two defects and require the gate to go red.

Mutations are applied with an `after_request` hook rather than by editing
source, so nothing on disk changes and there is no restore step to forget.

  M1  a noindex page starts advertising itself as indexable
  M2  a noindex page stops sending a directive at all (absence == index,follow)
  M3  control: sitemap_eligible forced False should make the sitemap
      contradiction test go green, proving it reads the real function

Expected: M1 fails, M2 fails, M3 green. Anything else means the gate is
decorative.
"""

from __future__ import annotations

import logging
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests", "protection"))

os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(ROOT, ".attack", "scratch.db"))

import bot  # noqa: E402
from services import search_visibility  # noqa: E402

logging.disable(logging.CRITICAL)

import test_every_page_agrees_with_the_robots_policy as gate  # noqa: E402

SUITE = gate.EveryRenderedPageAgreesWithTheRobotsPolicy

#: A path the table classifies noindex and that renders a 200 HTML document, so
#: it is inside the corpus and the mutation is visible to it.
VICTIM = "/login"

RE_ROBOTS = re.compile(rb"""<meta[^>]+name=['"]robots['"][^>]*>""", re.I)


def run(test_names):
    """Run named tests against a freshly-walked corpus; return failure count."""

    SUITE.setUpClass()
    suite = unittest.TestSuite([SUITE(n) for n in test_names])
    result = unittest.TextTestRunner(stream=open(os.devnull, "w"), verbosity=0).run(suite)
    return len(result.failures) + len(result.errors)


def main():
    app = bot.webhook_app
    app.config["TESTING"] = True
    mode = {"m": "off"}

    @app.after_request
    def _mutate(resp):
        from flask import request
        if mode["m"] == "off" or request.path != VICTIM:
            return resp
        if "html" not in (resp.headers.get("Content-Type") or "").lower():
            return resp
        body = resp.get_data()
        if mode["m"] == "M1":
            body = RE_ROBOTS.sub(b'<meta name="robots" content="index,follow">', body)
        elif mode["m"] == "M2":
            body = RE_ROBOTS.sub(b"", body)
        resp.set_data(body)
        return resp

    policy = search_visibility.robots_meta(VICTIM)
    print(f"victim {VICTIM}  table says: {policy}")
    assert "noindex" in policy, f"{VICTIM} is not noindex; pick another victim"

    safety = ["test_no_page_invites_indexing_that_the_table_wants_kept_out"]
    silence = ["test_a_page_the_table_wants_kept_out_sends_a_directive_at_all"] \
        if hasattr(SUITE, "test_a_page_the_table_wants_kept_out_sends_a_directive_at_all") \
        else safety

    print()
    mode["m"] = "off"
    base_safety = run(safety)
    print(f"BASELINE  safety gate failures           {base_safety}   (want 0)")

    mode["m"] = "M1"
    m1 = run(safety)
    print(f"M1        page advertises index,follow    {m1}   (want >0)")

    mode["m"] = "M2"
    m2 = run(silence)
    print(f"M2        page sends no directive at all  {m2}   (want >0)")

    mode["m"] = "off"
    sitemap = ["test_no_page_that_declines_indexing_is_offered_to_search_engines"]
    base_sitemap = run(sitemap)
    print(f"\nBASELINE  sitemap-contradiction failures {base_sitemap}   (want >0, the live defect)")

    real = search_visibility.sitemap_eligible
    search_visibility.sitemap_eligible = lambda path: False
    try:
        m3 = run(sitemap)
    finally:
        search_visibility.sitemap_eligible = real
    print(f"M3        sitemap_eligible forced False   {m3}   (want 0 -- proves it reads the real fn)")

    print()
    verdict = (base_safety == 0 and m1 > 0 and m2 > 0 and base_sitemap > 0 and m3 == 0)
    print("VERDICT:", "gate bites" if verdict else "GATE IS DECORATIVE -- investigate")
    return 0 if verdict else 1


if __name__ == "__main__":
    sys.exit(main())
