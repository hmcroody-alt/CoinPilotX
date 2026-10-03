"""Where `static/js/pulse_runtime.js` is spelled, for every frame that needs it.

`pulseApi` and `toast` are called by bare name from page scripts all over
PulseSoc. They used to be defined inline by `pulse_social_shell`, which worked
for exactly as long as the shell was the only document that ever wrapped a page
script. `marketplace_storefront.public_document` became the second one when the
cart opened to visitors, and a bare `pulseApi()` throws in whichever frame did
not define it.

This module exists rather than the constant living in `marketplace_storefront`
because the shell wraps hundreds of pages that have nothing to do with the
marketplace, and a social shell reaching into a commerce module to find its own
runtime is the dependency pointing the wrong way.
"""

from __future__ import annotations

#: Bump with the file, like every other asset here: the `/static/` prefix is
#: served with a one-year immutable cache, so an edit without a new token
#: reaches nobody who has already loaded the old one.
RUNTIME_SRC = "/static/js/pulse_runtime.js?v=guest-cart-runtime-20261001b"


def runtime_html() -> str:
    """The script tag, spelled once.

    A function and not just the constant because the *absent* `defer` is the
    load-bearing part, and the easy mistake: nearly every other script tag in
    these documents carries one. A deferred definition would land after the
    shell's own inline page code, which runs during parsing and calls both.
    """
    return f'<script src="{RUNTIME_SRC}"></script>'
