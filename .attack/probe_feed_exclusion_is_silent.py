"""Does a product leaving Shopping leave a trace?

`merchant_center_feed.feed_row` says, in its own prose, that "a silent skip is
how a product leaves Shopping with no trace" -- and then returns `None` for
every ineligible row. `feed_xml` logs `MERCHANT_FEED_ROW_FAILED` when a row
*raises*, and does `if row is None: continue` when a row is *refused*.

If that reading is right, the observability is inverted: the rare case (a
malformed row the module could not reason about) is loud, and the common case
(a row the module deliberately declined) is silent. The five production rows
excluded by the price-label/variant contradiction are in the silent bucket, so
nothing in the platform's own telemetry would ever surface the price
disagreement. A human reading the feed found it.

Measured here rather than asserted from the source, because "no log line" is
exactly the claim that a grep cannot settle -- a logger configured elsewhere,
or a `logging.warning` inside `eligibility`, would both make the code I read
wrong. So: capture the root logger, feed one row of each shape, and count.

Four shapes, three of which are real production exclusion reasons:

  A  complete              -> in the feed,     expect no log
  B  price contradiction   -> out of the feed, SILENT is the finding
  C  no description        -> out of the feed, SILENT is the finding
  D  unspellable availability -> raises, expect MERCHANT_FEED_ROW_FAILED

D is the control. If D is silent too, my reading of `feed_xml` is wrong and
there is no asymmetry to report. If D is loud and B and C are quiet, the
asymmetry is real and it is the wrong way round.
"""

from __future__ import annotations

import logging
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from services import marketplace_seo  # noqa: E402
from services import merchant_center_feed  # noqa: E402


def listing(**over):
    """A feed-eligible row. Every exclusion below is one field away from this."""

    row = {
        "id": 9001,
        "title": "Probe Product",
        "description": "A description comfortably over the thin-content floor "
                       "so that eligibility turns on the field under test and "
                       "not on this one.",
        "price_label": "$38.00",
        "currency": "USD",
        "media": [{"media_type": "image", "media_url": "https://cdn.example/a.jpg"}],
        "store_name": "Probe Store",
        "status": "published",
        "variants": [],
    }
    row.update(over)
    return row


#: Listing 36's real shape: a $38.00 label against a $2.29 variant.
CONTRADICTING_VARIANTS = [{"id": 1, "status": "active", "price_cents": 229,
                           "option_label": "Default"}]

MATCHING_VARIANTS = [{"id": 1, "status": "active", "price_cents": 3800,
                      "option_label": "Default"}]


class Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def run(label, row, expect_in_feed):
    cap = Capture()
    root = logging.getLogger()
    prior_level, prior_disabled = root.level, logging.root.manager.disable
    logging.disable(logging.NOTSET)
    root.addHandler(cap)
    root.setLevel(logging.DEBUG)
    try:
        xml = merchant_center_feed.feed_xml([row])
    finally:
        root.removeHandler(cap)
        root.setLevel(prior_level)
        logging.disable(prior_disabled)

    in_feed = "<item>" in xml
    verdict = marketplace_seo.eligibility(row)
    msgs = [r.getMessage()[:60] for r in cap.records]
    flag = ""
    if not in_feed and not msgs:
        flag = "   <<< LEAVES SHOPPING WITH NO TRACE"
    print(f"  {label:26s} in_feed={'Y' if in_feed else 'N'} "
          f"(expected {'Y' if expect_in_feed else 'N'})  "
          f"log_lines={len(msgs)}  reason={verdict.reason!r}{flag}")
    for m in msgs:
        print(f"  {'':26s}   log: {m}")
    return in_feed, len(msgs)


def main():
    print("=" * 78)
    print("IS A FEED EXCLUSION OBSERVABLE?")
    print("=" * 78)

    a_in, a_logs = run("A complete", listing(variants=MATCHING_VARIANTS), True)
    b_in, b_logs = run("B price contradiction",
                       listing(variants=CONTRADICTING_VARIANTS), False)
    c_in, c_logs = run("C no description", listing(description="short"), False)

    # The control has to actually reach `feed_xml`'s except-branch. A row with a
    # funny `status` does not: `availability` maps everything the lifecycle can
    # produce, so the first attempt at this control sailed into the feed and
    # proved nothing. Forcing the one condition the module says it will raise on
    # -- an availability with no Merchant Center spelling -- is the only way to
    # ask whether that branch logs.
    real_availability = marketplace_seo.availability
    marketplace_seo.availability = lambda row: "https://schema.org/NoSuchThing"
    try:
        d_in, d_logs = run("D unspellable availability",
                           listing(variants=MATCHING_VARIANTS), False)
    finally:
        marketplace_seo.availability = real_availability

    print()
    print("=" * 78)
    silent_exclusions = [n for n, logs, inf in
                         (("B price contradiction", b_logs, b_in),
                          ("C no description", c_logs, c_in))
                         if not inf and logs == 0]
    print(f"SILENT EXCLUSIONS: {len(silent_exclusions)}  {silent_exclusions}")
    print(f"CONTROL D logged:  {d_logs} line(s)  "
          f"({'loud, so the asymmetry is real' if d_logs else 'ALSO SILENT -- my reading of feed_xml is wrong'})")
    print("=" * 78)


if __name__ == "__main__":
    main()
