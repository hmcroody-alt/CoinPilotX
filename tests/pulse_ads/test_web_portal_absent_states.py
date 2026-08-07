"""The web advertiser portal must not print a figure it does not have.

Every check here guards the same failure, which the mission brief calls a fake
zero and which the portal committed in three places at once:

  * the template shipped `0` and `$0.00` baked into the metric tiles, so those
    figures were on screen before a single byte had been fetched;
  * `setMetric` turned a missing value into the string `"0"` — the one branch
    that knows for certain the number is unknown printed the most confident
    answer available;
  * the load-failure handler inserted an error notice *above* those tiles
    without clearing them, so a broken portal read "Growth Center failed to
    load" over a full set of numbers contradicting it.

The third is the worst of them. An advertiser who sees an error and seven
plausible figures believes the figures, because a number looks like data and a
sentence looks like noise.

These are static-analysis tests. There is no JavaScript test harness in this
repository and no jsdom, so they read the shipped files and assert on their
content rather than executing them. That makes them coarse: they can prove the
prohibited construct is gone and the replacement is wired, and they cannot prove
the replacement renders correctly in a browser. `node --check` covers syntax;
the on-screen result still needs a human.
"""

import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TEMPLATE = os.path.join(REPO, "templates", "pulse_advertiser_portal.html")
SCRIPT = os.path.join(REPO, "static", "js", "pulse_advertiser_portal.js")
STYLES = os.path.join(REPO, "static", "css", "pulse_advertiser_portal.css")

# The tiles that report a figure. `amount_owed` is deliberately excluded from
# the "must exist" list below because its tile is hidden unless there is a debt,
# but it is still held to the no-baked-in-figure rule.
METRIC_KEYS = (
    "account_count",
    "campaign_count",
    "active_campaigns",
    "pending_reviews",
    "total_spend",
    "wallet_balance",
    "reserved_budget",
)


def read(path):
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def metric_tiles(markup):
    """Return {metric key: initial text} for every `data-metric` element."""
    found = {}
    for match in re.finditer(
        r'<strong[^>]*data-metric="([a-z_]+)"[^>]*>(.*?)</strong>', markup, re.S
    ):
        found[match.group(1)] = match.group(2).strip()
    return found


class TemplateShipsNoBakedInFigures(unittest.TestCase):
    def setUp(self):
        self.tiles = metric_tiles(read(TEMPLATE))

    def test_every_metric_tile_is_present(self):
        for key in METRIC_KEYS:
            self.assertIn(key, self.tiles, f"metric tile '{key}' disappeared from the template")

    def test_no_tile_ships_a_literal_number(self):
        """A tile's initial text is markup, not data. It must not look like data."""
        for key, text in self.tiles.items():
            self.assertFalse(
                re.fullmatch(r"-?\$?[\d,]+(\.\d+)?%?", text),
                f"metric tile '{key}' ships the literal figure {text!r}. "
                "That number is on screen before the portal has been fetched and "
                "stays there if the fetch fails.",
            )

    def test_tiles_start_in_a_loading_state(self):
        """`Loading…` is the brief's literal for a figure that has not arrived."""
        for key in METRIC_KEYS:
            self.assertEqual(
                self.tiles[key],
                "Loading…",
                f"metric tile '{key}' should open in the loading state",
            )

    def test_absent_tiles_are_marked_for_styling(self):
        """Status words set in the 30px number face still read as numbers."""
        markup = read(TEMPLATE)
        for key in METRIC_KEYS:
            tile = re.search(rf'<strong[^>]*data-metric="{key}"[^>]*>', markup)
            self.assertIsNotNone(tile)
            self.assertIn(
                "metric-absent",
                tile.group(0),
                f"metric tile '{key}' opens in a non-figure state but is not "
                "marked `metric-absent`, so it is typeset as a value",
            )

    def test_stylesheet_de_emphasises_absent_tiles(self):
        self.assertIn(
            ".metric-grid strong.metric-absent",
            read(STYLES),
            "the `metric-absent` class is applied but never styled, so "
            "'Unavailable' renders in the number face",
        )


class SetMetricRefusesToInventZero(unittest.TestCase):
    def setUp(self):
        self.source = read(SCRIPT)

    def test_missing_value_does_not_become_zero(self):
        body = re.search(r"function setMetric\(.*?\n  \}", self.source, re.S)
        self.assertIsNotNone(body, "setMetric not found")
        self.assertNotIn(
            '? "0"',
            body.group(0),
            "setMetric still prints '0' for a value the server did not send",
        )

    def test_missing_value_says_it_is_unavailable(self):
        body = re.search(r"function setMetric\(.*?\n  \}", self.source, re.S)
        self.assertIn("Unavailable", body.group(0))

    def test_render_metrics_does_not_pre_default_the_tiles(self):
        """`|| 0` upstream defeats setMetric before it is ever consulted."""
        body = re.search(r"function renderMetrics\(.*?\n  \}\n", self.source, re.S)
        self.assertIsNotNone(body, "renderMetrics not found")
        for key in METRIC_KEYS:
            call = re.search(rf'setMetric\("{key}",\s*([^)]*)\)', body.group(0))
            self.assertIsNotNone(call, f"renderMetrics no longer sets '{key}'")
            self.assertNotIn(
                "||",
                call.group(1),
                f"renderMetrics defaults '{key}' with `||` before passing it to "
                "setMetric, so an omitted field becomes a confident figure",
            )


class LoadFailureClearsTheTiles(unittest.TestCase):
    def setUp(self):
        self.source = read(SCRIPT)

    def test_a_reset_helper_exists(self):
        self.assertIn("function setMetricsUnavailable(", self.source)

    def test_reset_helper_covers_every_tile(self):
        body = re.search(r"function setMetricsUnavailable\(.*?\n  \}", self.source, re.S)
        self.assertIn("[data-metric]", body.group(0), "the reset must not enumerate a subset")
        self.assertIn("Unavailable", body.group(0))

    def test_reset_hides_the_owed_tile(self):
        """A debt figure left behind after a failure is a claim about money."""
        body = re.search(r"function setMetricsUnavailable\(.*?\n  \}", self.source, re.S)
        self.assertIn("data-owed-tile", body.group(0))

    def test_failure_path_calls_the_reset(self):
        body = re.search(r"function showPortalLoadFailure\(.*?\n  \}\n", self.source, re.S)
        self.assertIsNotNone(body, "showPortalLoadFailure not found")
        self.assertIn(
            "setMetricsUnavailable()",
            body.group(0),
            "the failure path renders an error over tiles it never cleared",
        )

    def test_failure_path_offers_a_recovery_action(self):
        """The brief forbids a generic error with no way forward."""
        body = re.search(r"function showPortalLoadFailure\(.*?\n  \}\n", self.source, re.S)
        self.assertIn("Try again", body.group(0))
        self.assertIn("loadPortal()", body.group(0))

    def test_failure_path_is_the_registered_handler(self):
        self.assertIn("loadPortal().catch(showPortalLoadFailure)", self.source)


if __name__ == "__main__":
    unittest.main()
