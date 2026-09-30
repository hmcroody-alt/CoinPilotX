#!/usr/bin/env python3
"""Prove ``tests/pulse_commerce/test_commerce_card_parity.py`` can fail.

The commerce attachment card is rendered twice — once in Python for the
server-rendered post page, once in JavaScript for the feed and the reels lane —
and the parity suite is the only thing standing between that duplication and two
web surfaces quietly disagreeing about one listing. A suite in that position is
worth exactly as much as its ability to go red, and a green run does not
demonstrate that. So this harness breaks the renderers on purpose, one edit at a
time, and asserts the suite notices each one.

Every mutation is applied to a **copy** of the repository in a scratch
directory. Nothing here writes to the working tree; the source files are opened
read-only and the pytest run is pointed at the copy. That is deliberate — the
obvious version of this script edits the real file and restores it in a
``finally``, which loses the source the moment the process is killed between
turns.

Usage::

    python3 scripts/protection/mutate_commerce_card_parity.py

Exits non-zero if any mutation survives.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

# Derived, never hardcoded: this script is run from the repo root, from
# scripts/, and from a worktree with a different name in all three cases.
REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PY_REL = os.path.join("services", "pulse_commerce_card.py")
JS_REL = os.path.join("static", "js", "pulse_commerce_card.js")
CSS_REL = os.path.join("static", "css", "pulse-commerce-attachment.css")
TEST_REL = os.path.join("tests", "pulse_commerce", "test_commerce_card_parity.py")

#: ``(name, file, find, replace, why it must be caught)``.
MUTATIONS = [
    (
        "js-renames-a-class",
        JS_REL,
        "pulse-commerce-price",
        "pulse-commerce-cost",
        "a class renamed in one renderer and not the other loses its styling",
    ),
    (
        "js-reverts-the-apostrophe-entity",
        JS_REL,
        '"&#x27;"',
        '"&#39;"',
        "the twins must be byte-identical, not merely equivalent",
    ),
    (
        "js-drops-the-protocol-relative-guard",
        JS_REL,
        'if (value.charAt(0) !== "/" || value.slice(0, 2) === "//") return "";',
        'if (value.charAt(0) !== "/") return "";',
        "//evil.example in an href is an off-site link from a product card",
    ),
    (
        "js-trusts-an-unknown-availability-code",
        JS_REL,
        "if (typeof declared.code === \"string\" && KNOWN.indexOf(declared.code) !== -1) {",
        "if (typeof declared.code === \"string\") {",
        "an unrecognised code must be re-derived, not trusted",
    ),
    (
        "js-reads-a-field-python-does-not",
        JS_REL,
        "var price = text(product.price_label);",
        "var price = text(product.discount_label) || text(product.price_label);",
        "a payload field added to one renderer only",
    ),
    (
        "py-discloses-a-withdrawn-price",
        PY_REL,
        "_PRICE_OK = frozenset({AVAILABLE, OUT_OF_STOCK, NOT_PRICED})",
        "_PRICE_OK = frozenset({AVAILABLE, OUT_OF_STOCK, NOT_PRICED, UNAVAILABLE})",
        "advertising a price for a product the seller withdrew",
    ),
    (
        "py-leaks-the-price-into-the-aria-label",
        PY_REL,
        "if not accessibility or not show_price:",
        "if not accessibility:",
        "the accessible surface disclosing what the visual one hides",
    ),
    (
        "py-enables-a-cta-with-no-route",
        PY_REL,
        'if not str(cta.get("route") or "").strip():\n        return False',
        "if False:\n        return False",
        "a button that navigates to a 404",
    ),
    (
        "py-ignores-the-availability-state-for-the-cta",
        PY_REL,
        "return availability_block(commerce) == AVAILABLE",
        "return True",
        "a sold-out product with an enabled buy button",
    ),
    (
        "py-stops-escaping",
        PY_REL,
        'return html.escape(str(value or ""), quote=True)',
        'return str(value or "")',
        "stored XSS through a listing title",
    ),
    (
        "py-raises-instead-of-returning-empty",
        PY_REL,
        "if not is_commerce_overlay(commerce):\n        return \"\"",
        "if not is_commerce_overlay(commerce):\n        raise ValueError(commerce)",
        "a failed commerce hydration taking the whole post down",
    ),
    (
        "css-drops-a-styled-class",
        CSS_REL,
        ".pulse-commerce-cta",
        ".pulse-commerce-cta-renamed",
        "a class the renderers emit that the stylesheet never mentions",
    ),
]


def run_suite(root: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "pytest", TEST_REL, "-x", "-q"],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=300,
    )


def main() -> int:
    survivors: list[tuple[str, str]] = []
    results: list[tuple[str, bool]] = []

    with tempfile.TemporaryDirectory(prefix="commerce-card-mutation-") as scratch:
        pristine = os.path.join(scratch, "pristine")
        for relative in (PY_REL, JS_REL, CSS_REL, TEST_REL):
            destination = os.path.join(pristine, relative)
            os.makedirs(os.path.dirname(destination), exist_ok=True)
            shutil.copy2(os.path.join(REPO, relative), destination)

        # The control run. A harness whose baseline is already red reports every
        # mutation as caught and proves nothing at all.
        baseline = run_suite(REPO)
        if baseline.returncode != 0:
            print("BASELINE IS RED -- fix the suite before trusting this harness")
            print(baseline.stdout[-4000:])
            return 2
        print("baseline: green\n")

        for name, relative, find, replace, why in MUTATIONS:
            source_path = os.path.join(pristine, relative)
            with open(source_path, "r", encoding="utf-8") as handle:
                original = handle.read()
            if original.count(find) != 1:
                survivors.append((name, f"anchor matched {original.count(find)}x, expected 1"))
                results.append((name, False))
                print(f"  BROKEN ANCHOR  {name}")
                continue

            work = os.path.join(scratch, "work")
            if os.path.exists(work):
                shutil.rmtree(work)
            # A symlink farm rather than a copy: bot.py alone is 111k lines and
            # the suite imports from services/.
            shutil.copytree(REPO, work, symlinks=True, copy_function=os.link, dirs_exist_ok=False,
                            ignore=shutil.ignore_patterns(".git", "node_modules", "*.db", ".venv"))
            mutated_path = os.path.join(work, relative)
            os.remove(mutated_path)
            with open(mutated_path, "w", encoding="utf-8") as handle:
                handle.write(original.replace(find, replace))

            result = run_suite(work)
            caught = result.returncode != 0
            results.append((name, caught))
            print(f"  {'caught ' if caught else 'SURVIVED'}  {name}  -- {why}")
            if not caught:
                survivors.append((name, why))

    print()
    print(f"{sum(1 for _, ok in results if ok)}/{len(results)} mutations caught")
    if survivors:
        print("\nSURVIVORS -- the suite does not defend these:")
        for name, why in survivors:
            print(f"  - {name}: {why}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
