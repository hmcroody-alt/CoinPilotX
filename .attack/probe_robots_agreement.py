"""Agent 12 attack probe: does every served page agree with the robots policy?

Agent 2 section 6.3 says nothing enforces agreement between what
`search_visibility.robots_meta(path)` declares and what a page actually
renders, for any path outside a sitemap. This walks `app.url_map` and checks.

Read-only: issues GETs through the Flask test client against a scratch DB copy.
"""

from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

os.environ.setdefault("DATABASE_URL", "sqlite:///" + os.path.join(ROOT, ".attack", "scratch.db"))

import bot  # noqa: E402
from services import search_visibility  # noqa: E402

RE_ROBOTS_META = re.compile(r"""<meta[^>]+name=['"]robots['"][^>]*>""", re.I)
RE_CONTENT = re.compile(r"""content=['"]([^'"]*)['"]""", re.I)


def rendered_directive(html):
    tag = RE_ROBOTS_META.search(html or "")
    if not tag:
        return None
    m = RE_CONTENT.search(tag.group(0))
    return m.group(1).strip() if m else None


def tokens(directive):
    """Compare semantically: order and whitespace must not matter."""
    if directive is None:
        return None
    return frozenset(t.strip().lower() for t in directive.split(",") if t.strip())


def main():
    app = bot.app
    # Static, parameterless GET routes only. A path with a converter would
    # need invented values, and an invented id is a different test.
    paths = set()
    for rule in app.url_map.iter_rules():
        if "GET" not in (rule.methods or ()):
            continue
        if rule.arguments:
            continue
        paths.add(rule.rule)

    print(f"url_map GET rules with no arguments: {len(paths)}")

    rows = []
    client = app.test_client()
    for path in sorted(paths):
        policy = search_visibility.robots_meta(path)
        try:
            resp = client.get(path, follow_redirects=False)
        except Exception as exc:  # noqa: BLE001
            rows.append((path, policy, None, None, f"EXC:{type(exc).__name__}"))
            continue
        status = resp.status_code
        header = resp.headers.get("X-Robots-Tag")
        body = ""
        if status == 200 and "html" in (resp.headers.get("Content-Type") or "").lower():
            body = resp.get_data(as_text=True)
        rows.append((path, policy, rendered_directive(body), header, status))

    # Only 200 HTML pages can be judged on their rendered meta.
    judged = [r for r in rows if r[4] == 200]
    print(f"200 responses: {len(judged)}")

    disagree = []
    missing = []
    for path, policy, meta, header, _status in judged:
        effective = meta if meta is not None else header
        if effective is None:
            missing.append((path, policy))
            continue
        if tokens(effective) != tokens(policy):
            disagree.append((path, policy, effective))

    print(f"\n=== DISAGREEMENTS: {len(disagree)} ===")
    for path, policy, effective in disagree:
        pol_idx = policy.startswith("index")
        eff_idx = not any(t == "noindex" for t in tokens(effective))
        # The dangerous direction: policy says keep it out, page invites it in.
        direction = "POLICY_NOINDEX_PAGE_INDEX" if (not pol_idx and eff_idx) else (
            "POLICY_INDEX_PAGE_NOINDEX" if (pol_idx and not eff_idx) else "SAME_INDEXABILITY_DIFFERENT_TOKENS"
        )
        print(f"  [{direction}] {path}\n      policy: {policy}\n      served: {effective}")

    print(f"\n=== NO DIRECTIVE AT ALL: {len(missing)} ===")
    for path, policy in missing:
        print(f"  {path}  (policy: {policy})")

    return disagree, missing


if __name__ == "__main__":
    main()
