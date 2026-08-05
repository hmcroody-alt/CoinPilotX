"""Shared entry point so a protection test file cannot be green without running.

Why this exists
---------------

The protection suites are written in two styles: script-style files with their
own ``main()``, and pytest-style files that are just module-level ``test_*``
functions. Nothing enforced that either style was actually executed anywhere.

Two independent holes followed from that:

  * ``scripts/protection/run_protection_suite.py`` listed three files by hand.
    The other eight - including every environment, admin-accountability and
    metric-truthfulness suite - were never run by it.

  * ``.github/workflows/realtime-audio.yml`` invoked the LiveKit suites with
    ``python3 -m unittest tests.protection.test_livestream_audio_token_grants``.
    That file defines no ``TestCase``, so unittest collected nothing and the step
    printed "Ran 0 tests / OK". The CI job protecting live audio token grants -
    the exact subsystem this repository has had incidents in - was green because
    it measured nothing.

A test suite that reports success without running is worse than no suite: it
consumes the attention that would otherwise go to real verification.

Every protection file now ends with::

    if __name__ == "__main__":
        raise SystemExit(run_module_tests(globals()))

and prints a machine-readable count, which the runner asserts is non-zero.
"""

from __future__ import annotations

import time
import traceback


COUNT_MARKER = "PROTECTION_TESTS_RUN="


def run_module_tests(namespace, setup=None) -> int:
    """Execute every module-level ``test_*`` callable in ``namespace``.

    ``setup`` may be a context manager factory for files that need environment
    fixtures - the LiveKit suites mint tokens and need credentials present.
    """
    names = sorted(
        name for name, value in namespace.items()
        if name.startswith("test_") and callable(value)
    )
    context = setup() if setup is not None else None
    if context is not None:
        context.__enter__()
    failures = []
    try:
        for name in names:
            started = time.time()
            try:
                namespace[name]()
                print(f"ok   {name}  ({time.time() - started:.2f}s)")
            except Exception:
                failures.append(name)
                print(f"FAIL {name}")
                traceback.print_exc()
    finally:
        if context is not None:
            context.__exit__(None, None, None)
    print(f"{COUNT_MARKER}{len(names)}")
    if failures:
        print(f"{len(failures)} of {len(names)} protection checks failed: {', '.join(failures)}")
        return 1
    print(f"all {len(names)} protection checks passed")
    return 0
