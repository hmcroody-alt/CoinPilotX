# Agent 1 raw evidence

Captured 2026-10-03 against production pulsesoc.com as anonymous Googlebot.
Read-only GETs, rate-limited, single-threaded. No credentials, cookies or PII.

- `probe.sh` / `sweep.sh`  — the probes (quote-agnostic; see report §2)
- `all_sitemap_urls.txt`   — the 148-URL submitted corpus
- `sweep.tsv`              — status/robots/canonical/bytes for all 148
- `catalog.py` / `catalog.json` — per-PDP audit of all 41 indexable products
- `feed.xml`               — the live Merchant Center feed (36 items)
- `grid.html`, `pdp113.html` — raw HTML the findings were read from
- `product_urls.txt`, `pub_ids.txt`, `idx_n.txt` — id sets behind the funnel counts
- `reprobe_*` — the final re-probe, same deployed SHA, taken to detect material drift
  before publication. Feed membership and sitemap product membership are
  **set-identical** to the originals (36 and 41; zero dropped, zero added). The apparent
  36→31 feed drop seen during this re-probe was a `grep -c` line-counting artifact, not a
  regression — see `docs/search-os/agent-01/BASELINE_AND_HANDOFF.md` §2.5.
